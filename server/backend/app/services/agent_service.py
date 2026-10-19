"""
에이전트 서비스 레이어

에이전트 등록/보고 처리 비즈니스 로직을 담당합니다.
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Agent, AlertLogDB, BlockedSiteDailyStat, StatusReportDB
from app.schemas import (
    ActiveDetectionSchema,
    AgentRegisterRequest,
    AgentSummary,
    AlertSchema,
    DashboardSummary,
    StatusReportRequest,
)

logger = logging.getLogger(__name__)

# 직전 보고로부터 이만큼(초)을 넘는 간격은 오프라인 공백으로 보고 시간 누적에서 제외한다.
MAX_REPORT_GAP_SECONDS = 10

# 차단 사이트 통계 키(BlockedSiteDailyStat.url_pattern)의 최대 길이. 넘는 키는 저장 오류로 보고 전체가 실패하므로 건너뛴다.
SITE_KEY_MAX_LENGTH = 256

# 차단 사이트 시간 누적에서 정수 초로 저장하고 남은 소수부 (에이전트별, 메모리 보관 — 재시작 시 최대 1초 손실)
_gap_carry: dict[str, float] = {}

# ─── 현재 탐지 목록 (메모리 보관) ─────────────────────────────────
# 에이전트가 매 보고마다 보내는 "지금 탐지 중인 항목"의 최신값.
# 매초 바뀌는 휘발성 상태라 DB에 쌓지 않고 메모리에만 둔다
# (WebSocket 매니저와 같은 단일 백엔드 레플리카 전제). 이 시간 넘게 갱신이 없으면 비어 있는 것으로 본다.
ACTIVE_DETECTIONS_MAX_AGE_SECONDS = 10
_active_detections: dict[str, tuple[datetime, list[ActiveDetectionSchema]]] = {}


def set_active_detections(agent_id: str, items: list[ActiveDetectionSchema]) -> None:
    """에이전트의 현재 탐지 목록을 최신값으로 교체한다."""
    _active_detections[agent_id] = (datetime.now(timezone.utc), list(items))


def get_active_detections(
    agent_id: str, now: Optional[datetime] = None
) -> list[ActiveDetectionSchema]:
    """에이전트의 현재 탐지 목록. 갱신이 오래됐거나(오프라인 등) 없으면 빈 목록."""
    entry = _active_detections.get(agent_id)
    if entry is None:
        return []
    updated_at, items = entry
    now = now or datetime.now(timezone.utc)
    if (now - updated_at).total_seconds() > ACTIVE_DETECTIONS_MAX_AGE_SECONDS:
        return []
    return items


def clear_active_detections(agent_id: str) -> None:
    """에이전트의 현재 탐지 목록을 비운다 (명시적 오프라인 등)."""
    _active_detections.pop(agent_id, None)


# ─── DANGER 유지 · 화면 표시 알림 (메모리 보관) ────────────────────
# DANGER(차단 대상이 화면 맨 앞)는 학생이 잠깐 띄웠다 내리면 수집 주기 하나만 잡혀 대시보드에서 놓치기 쉽다.
# 마지막 DANGER 보고 후 settings.danger_hold_seconds 동안 DANGER와 그 이유를 유지하고,
# 차단 대상이 화면 앞으로 나온 순간은 빨간 '화면 표시' 알림(level=danger)으로 남긴다.
# 현재 탐지 목록과 같은 이유로 메모리에만 둔다 (서버 재시작 시 유지 중이던 DANGER 표시만 사라짐).
ON_SCREEN_ALERT_CODE = "ON_SCREEN"
# 창 제목 앞의 알림 개수 표시 "(3) " — 수시로 바뀌므로 같은 창 판정에서 뗀다 (에이전트와 같은 규칙)
_NOTIFICATION_PREFIX = re.compile(r"^\(\d+\+?\)\s*")
_DETECTION_LABELS = {
    "BLOCKED_PROCESS": "차단 프로그램",
    "UNAUTHORIZED_PROCESS": "허용되지 않은 프로그램",
    "BLOCKED_URL": "차단 사이트",
    "MESSENGER_DETECTED": "메신저",
}
_danger_holds: dict[str, tuple[datetime, str]] = {}  # agent_id → (유지 종료 시각, 이유)
_on_screen_prev: dict[str, set[tuple[str, str]]] = {}  # agent_id → 직전 보고에서 화면 앞이던 (code, target)
_on_screen_alerted: dict[str, dict[tuple[str, str], datetime]] = {}  # agent_id → (code, target) → 마지막 알림 시각


def _danger_reason(req: StatusReportRequest) -> str:
    """DANGER 이유: 화면 앞에 떠 있던 탐지 대상 (없으면 포그라운드 창 제목)."""
    on_screen = [d.target for d in (req.active_detections or []) if d.is_foreground and d.target]
    if on_screen:
        reason = ", ".join(on_screen[:2])
        return reason + (f" 외 {len(on_screen) - 2}건" if len(on_screen) > 2 else "")
    # 화면 앞 탐지 항목을 특정하지 못한 경우
    return req.foreground_window or "차단 대상 화면 표시"


def register_danger(req: StatusReportRequest, now: Optional[datetime] = None) -> None:
    """실시간 DANGER 보고면 DANGER 유지를 (다시) 시작한다.

    DB(agents.current_state)에는 에이전트가 보낸 원래 상태를 저장하고, 유지는 읽을 때 겹쳐 적용한다
    (display_state, summarize_agent). 그래야 보고가 끊겨도 유지 시간이 지나면 정확히 풀린다.
    유지는 현재 탐지 목록이 담긴 실시간 보고에서만 시작한다. 오프라인 버퍼에서 재전송된
    과거 보고(목록 없음)의 DANGER로 지금 PC를 다시 DANGER로 보이지 않게 하기 위함이다.
    """
    if req.user_state == "danger" and req.active_detections is not None:
        now = now or datetime.now(timezone.utc)
        _danger_holds[req.agent_id] = (now + timedelta(seconds=settings.danger_hold_seconds), _danger_reason(req))


def get_danger_reason(agent_id: str, now: Optional[datetime] = None) -> Optional[str]:
    """DANGER 유지 중이면 그 이유, 아니면 None."""
    hold = _danger_holds.get(agent_id)
    if hold is None or (now or datetime.now(timezone.utc)) >= hold[0]:
        return None
    return hold[1]


def display_state(agent: Agent, now: Optional[datetime] = None) -> str:
    """대시보드에 보일 상태: DANGER 유지 중이면 danger, 아니면 저장된 원래 상태."""
    return "danger" if get_danger_reason(agent.agent_id, now) is not None else agent.current_state


def _on_screen_key(item: ActiveDetectionSchema) -> tuple[str, str]:
    """화면 표시 중복 판정 키. 창 제목은 앞의 알림 개수 표시를 뗀다 ('(3) X'→'(4) X'도 같은 창)."""
    target = _NOTIFICATION_PREFIX.sub("", item.target) if item.source == "title" else item.target
    return item.code, target


def find_on_screen_events(
    req: StatusReportRequest, now: Optional[datetime] = None
) -> list[ActiveDetectionSchema]:
    """이번 보고에서 새로 화면 앞으로 나온 탐지 항목 (화면 표시 알림을 남길 대상). 상태는 바꾸지 않는다.

    직전 보고에서도 화면 앞이던 대상과, 쿨다운 안에 이미 알림을 남긴 대상은 제외한다.
    알림 저장이 확정된 뒤 mark_on_screen_state 로 상태를 반영한다 (저장이 실패하면 다음 보고에서 다시 판단).
    현재 탐지 목록이 없는 보고(구버전 에이전트·오프라인 버퍼 재전송)는 판단하지 않는다.
    """
    if req.active_detections is None:
        return []
    now = now or datetime.now(timezone.utc)
    cooldown = timedelta(seconds=settings.on_screen_alert_cooldown_seconds)
    previous = _on_screen_prev.get(req.agent_id, set())
    alerted = _on_screen_alerted.get(req.agent_id, {})

    events: list[ActiveDetectionSchema] = []
    seen: set[tuple[str, str]] = set()
    for item in req.active_detections:
        if not (item.is_foreground and item.target):
            continue
        key = _on_screen_key(item)
        if key in previous or key in seen:
            continue
        last = alerted.get(key)
        if last is not None and now - last < cooldown:
            continue
        seen.add(key)
        events.append(item)
    return events


def mark_on_screen_state(
    req: StatusReportRequest, events: list[ActiveDetectionSchema], now: Optional[datetime] = None
) -> None:
    """화면 표시 알림 저장이 확정된 뒤, 직전 화면 상태와 알림 시각을 반영한다."""
    if req.active_detections is None:
        return
    now = now or datetime.now(timezone.utc)
    _on_screen_prev[req.agent_id] = {
        _on_screen_key(d) for d in req.active_detections if d.is_foreground and d.target
    }
    cooldown = timedelta(seconds=settings.on_screen_alert_cooldown_seconds)
    alerted = _on_screen_alerted.setdefault(req.agent_id, {})
    for key in [k for k, t in alerted.items() if now - t >= cooldown]:
        del alerted[key]  # 쿨다운이 지난 기록 정리 (대화마다 바뀌는 URL 등으로 계속 쌓이지 않게)
    for item in events:
        alerted[_on_screen_key(item)] = now


def on_screen_alert(item: ActiveDetectionSchema, detected_at: datetime) -> AlertSchema:
    """화면 표시 알림 1건 (대시보드 최근 알림에서 빨간색으로 보이도록 level=danger)."""
    label = _DETECTION_LABELS.get(item.code, "탐지 대상")
    return AlertSchema(
        level="danger",
        code=ON_SCREEN_ALERT_CODE,
        message=f"{label} 화면 표시: {item.target}",
        process_name=item.target if item.source == "process" else None,
        url=item.target if item.source == "url" else None,
        detected_at=detected_at,
    )


def clear_danger_state(agent_id: str) -> None:
    """에이전트의 DANGER 유지·화면 표시 기록을 비운다 (오프라인 등)."""
    _danger_holds.pop(agent_id, None)
    _on_screen_prev.pop(agent_id, None)
    _on_screen_alerted.pop(agent_id, None)


def alert_for_broadcast(alert: AlertSchema, now: datetime) -> dict:
    """WebSocket 전송용 알림. 시각은 저장할 때와 같은 기준(시간대 없는 값은 UTC)으로 ISO 형식에 시간대를 붙인다.

    시간대 없이 보내면 브라우저가 로컬 시각으로 해석해 같은 알림이 새로고침 전후로 다르게 보인다.
    """
    detected_at = alert.detected_at or now
    if detected_at.tzinfo is None:
        detected_at = detected_at.replace(tzinfo=timezone.utc)
    return alert.model_copy(update={"detected_at": detected_at}).model_dump(mode="json")


def summarize_agent(agent: Agent, now: Optional[datetime] = None) -> AgentSummary:
    """에이전트 행 → 대시보드 카드 요약 (DANGER 유지 중이면 DANGER와 그 이유)."""
    summary = AgentSummary.model_validate(agent)
    reason = get_danger_reason(agent.agent_id, now)
    if reason is not None:
        summary.current_state = "danger"
        summary.danger_reason = reason
    return summary


async def upsert_agent(db: AsyncSession, req: AgentRegisterRequest) -> Agent:
    """에이전트를 등록하거나 기존 에이전트 정보를 갱신합니다."""
    result = await db.execute(select(Agent).where(Agent.agent_id == req.agent_id))
    agent = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    
    # 강의실 이름(lab_name) 파싱
    import re
    parsed_lab_name = None
    target_name = req.hostname or req.agent_id
    if target_name:
        chunks = re.split(r'[-_]', target_name)
        if len(chunks) >= 2:
            parsed_lab_name = chunks[1]

    if agent is None:
        agent = Agent(
            agent_id=req.agent_id,
            hostname=req.hostname,
            lab_name=parsed_lab_name,
            ip_address=req.ip_address,
            mac_address=req.mac_address,
            os_version=req.os_version,
            agent_version=req.agent_version,
            is_online=True,
            last_seen_at=now,
        )
        db.add(agent)
        logger.info(f"신규 에이전트 등록: {req.agent_id} ({req.hostname}) | IP: {req.ip_address} - 강의실: {parsed_lab_name}")
    else:
        # 빈 값으로 기존 정보를 덮어쓰지 않는다 (보고 기반 자동 등록 시 일부 필드가 비어 올 수 있음)
        if req.hostname:
            agent.hostname = req.hostname
        if parsed_lab_name:
            agent.lab_name = parsed_lab_name
        if req.ip_address:
            agent.ip_address = req.ip_address
        if req.os_version:
            agent.os_version = req.os_version
        if req.agent_version:
            agent.agent_version = req.agent_version
        agent.is_online = True
        agent.last_seen_at = now
        logger.info(f"에이전트 재등록: {req.agent_id} | IP: {agent.ip_address} - 강의실: {agent.lab_name}")

    await db.flush()
    return agent



async def save_status_report(
    db: AsyncSession,
    req: StatusReportRequest,
    extra_alerts: Optional[list[AlertSchema]] = None,
) -> StatusReportDB:
    """상태 보고서를 DB에 저장하고 에이전트 현재 상태를 업데이트합니다.

    Args:
        extra_alerts: 서버에서 만든 알림 (화면 표시 알림 등)
    """
    chrome = req.browser_details.chrome
    now = datetime.now(timezone.utc)

    # 보고서 저장
    report = StatusReportDB(
        agent_id=req.agent_id,
        reported_at=req.timestamp or now,
        user_state=req.user_state,
        alert_count=len(req.alerts),
        cpu_percent=req.system_info.cpu_percent,
        memory_percent=req.system_info.memory_percent,
        process_count=len(req.processes),
        chrome_tab_count=chrome.tab_count,
        foreground_window=req.foreground_window,
    )
    db.add(report)
    await db.flush()  # report.id 생성

    # 알림 저장
    for alert in [*req.alerts, *(extra_alerts or [])]:
        alert_log = AlertLogDB(
            report_id=report.id,
            agent_id=req.agent_id,
            level=alert.level,
            code=alert.code,
            message=alert.message,
            process_name=alert.process_name,
            url=alert.url,
            detected_at=alert.detected_at or now,
        )
        db.add(alert_log)

    # 에이전트 현재 상태 캐시 업데이트 (+ 대시보드용 최신 보고서 요약 비정규화)
    update_values = {
        "current_state": req.user_state,  # DANGER 유지는 읽을 때 겹쳐 적용 (display_state)
        "last_seen_at": now,
        "is_online": True,
        # 최신 보고서 요약 — 대시보드 목록이 이 값만 읽으면 되도록 여기서 함께 갱신
        # (별도 쿼리 없이 기존 UPDATE 한 번에 얹는다)
        "last_report_at": report.reported_at,
        "process_count": report.process_count,
        "chrome_tab_count": report.chrome_tab_count,
        "alert_count": report.alert_count,
        "cpu_percent": report.cpu_percent,
        "memory_percent": report.memory_percent,
        "foreground_window": report.foreground_window,
    }
    if req.agent_version:
        update_values["agent_version"] = req.agent_version
    # 보고서에 담겨 온 IP/호스트명을 에이전트 레코드에 반영 (빈 값은 무시)
    if req.ip_address:
        update_values["ip_address"] = req.ip_address
    if req.hostname:
        update_values["hostname"] = req.hostname

    await db.execute(
        update(Agent)
        .where(Agent.agent_id == req.agent_id)
        .values(**update_values)
    )

    logger.info(f"상태 보고: {req.agent_id} | IP: {req.ip_address or 'N/A'} | 상태: {req.user_state}")

    return report


async def get_agent_summary(db: AsyncSession, agent_id: str) -> Optional[AgentSummary]:
    """단일 에이전트의 요약 정보를 반환합니다.

    최신 보고서 요약값(process_count/cpu 등)은 save_status_report에서 agents 행에
    비정규화해 두므로, 여기서 status_reports를 조회하지 않는다(대량 테이블 스캔 제거).
    """
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        return None

    return summarize_agent(agent)


async def get_dashboard_summary(db: AsyncSession) -> DashboardSummary:
    """전체 대시보드 요약 통계를 계산합니다. WebSocket 브로드캐스트에 사용됩니다."""
    result = await db.execute(select(Agent))
    agents = result.scalars().all()

    total = len(agents)
    online = sum(1 for a in agents if a.is_online)
    now = datetime.now(timezone.utc)
    states = [display_state(a, now) for a in agents if a.is_online]  # DANGER 유지 반영
    safe = states.count("safe")
    warning = states.count("warning")
    danger = states.count("danger")

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    alert_result = await db.execute(
        select(func.count(AlertLogDB.id)).where(AlertLogDB.detected_at >= since)
    )
    recent_alerts = alert_result.scalar() or 0

    return DashboardSummary(
        total_agents=total,
        online_agents=online,
        safe_count=safe,
        warning_count=warning,
        danger_count=danger,
        recent_alert_count=recent_alerts,
    )

async def check_and_mark_offline_agents(db: AsyncSession) -> list[str]:
    """1분 이상 통신이 없는 에이전트를 오프라인으로 자동 전환합니다."""
    threshold = datetime.now(timezone.utc) - timedelta(minutes=1)
    
    result = await db.execute(
        select(Agent).where(Agent.is_online == True, Agent.last_seen_at < threshold)
    )
    offline_agents = result.scalars().all()
    
    offline_ids = []
    for agent in offline_agents:
        agent.is_online = False
        offline_ids.append(agent.agent_id)
        logger.info(f"에이전트 오프라인 전환: {agent.agent_id} (마지막 접속: {agent.last_seen_at})")
        
    if offline_ids:
        await db.flush()

    return offline_ids


# ─── 차단 사이트 통계 ─────────────────────────────────────────────


def _match_blocked_pattern(url: str) -> Optional[str]:
    """URL이 차단 패턴에 부분일치하면 해당 패턴을, 아니면 None을 반환합니다."""
    low = (url or "").lower()
    for pat in settings.blocked_url_patterns:
        if pat.lower() in low:
            return pat.lower()
    return None


def _open_blocked_sites(req: StatusReportRequest) -> dict[str, bool]:
    """보고 시점에 열려 있던 차단 사이트 → 화면에 떠 있었는지(활성) 여부.

    - CDP 탭: 설정된 차단 패턴과 일치하는 탭 (활성 탭이면 활성)
    - 현재 탐지 목록의 차단 사이트(BLOCKED_URL): CDP 없는 PC에서 주소창·창 제목으로 탐지한 것
      (화면 앞 창이면 활성). 사이트 키는 알림 집계(_blocked_alert_site)와 같게 맞춘다.
    같은 사이트가 여러 곳에 있으면 한 번만 세고, 하나라도 활성이면 활성으로 본다.
    """
    sites: dict[str, bool] = {}
    if settings.blocked_url_patterns:
        for tab in req.browser_details.chrome.tabs:
            pat = _match_blocked_pattern(tab.url)
            if pat is not None:
                sites[pat] = sites.get(pat, False) or bool(tab.is_active)

    # 필드가 없는 보고(구버전 에이전트·오프라인 버퍼 재전송)는 CDP 탭만 본다
    for item in req.active_detections or []:
        if item.code != "BLOCKED_URL":
            continue
        if item.source == "url":
            site = _blocked_site_key(item.target)
        elif item.source == "title":
            site = _blocked_alert_site(None, item.target)
        else:
            site = None
        if site is not None and len(site) <= SITE_KEY_MAX_LENGTH:
            sites[site] = sites.get(site, False) or bool(item.is_foreground)
    return sites


async def accumulate_blocked_site_stats(
    db: AsyncSession,
    req: StatusReportRequest,
    prev_last_seen: Optional[datetime],
) -> None:
    """보고서에 열려 있던 차단 사이트의 활성/백그라운드 시간을 일자별로 누적합니다.

    개별 탭·창은 DB에 저장하지 않으므로, 직전 보고 이후 경과한 시간(delta)을
    지금 열려 있는 각 차단 사이트의 활성 여부에 따라 누적한다 (대상은 _open_blocked_sites 참고).
    """
    matched = _open_blocked_sites(req)
    if not matched:
        return

    now = datetime.now(timezone.utc)

    # 직전 보고로부터 경과 시간(초). 첫 보고/큰 공백은 누적에서 제외.
    # 컬럼이 정수(초)라 보고마다 소수부를 버리면 간격이 일정할 때(예: 3.2초마다) 계속 손실되므로,
    # 소수부는 에이전트별로 다음 보고에 넘겨 장기 누적 오차를 1초 미만으로 유지한다.
    delta = 0
    if prev_last_seen is not None:
        if prev_last_seen.tzinfo is None:
            prev_last_seen = prev_last_seen.replace(tzinfo=timezone.utc)
        gap = (now - prev_last_seen).total_seconds()
        span = max(0.0, min(gap, MAX_REPORT_GAP_SECONDS)) + _gap_carry.pop(req.agent_id, 0.0)
        delta = int(span)
        _gap_carry[req.agent_id] = span - delta

    today = now.date()
    for pat, is_active in matched.items():
        result = await db.execute(
            select(BlockedSiteDailyStat).where(
                BlockedSiteDailyStat.agent_id == req.agent_id,
                BlockedSiteDailyStat.url_pattern == pat,
                BlockedSiteDailyStat.stat_date == today,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = BlockedSiteDailyStat(
                agent_id=req.agent_id,
                url_pattern=pat,
                stat_date=today,
                active_seconds=0,
                background_seconds=0,
                first_access_at=now,
                last_access_at=now,
            )
            db.add(row)

        if delta > 0:
            if is_active:
                row.active_seconds += delta
            else:
                row.background_seconds += delta

        if row.first_access_at is None:
            row.first_access_at = now
        row.last_access_at = now


async def cleanup_old_data(db: AsyncSession, retention_days: int) -> dict:
    """보관 기간이 지난 원시 데이터와 통계를 삭제합니다.

    SQLite는 대량 DELETE 시 FK CASCADE가 보장되지 않으므로 각 테이블을 명시적으로 삭제한다.
    자식(alert_logs) → 부모(status_reports) 순서로 삭제한다.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_date = cutoff_dt.date()

    alerts_res = await db.execute(
        delete(AlertLogDB).where(AlertLogDB.detected_at < cutoff_dt)
    )
    reports_res = await db.execute(
        delete(StatusReportDB).where(StatusReportDB.reported_at < cutoff_dt)
    )
    stats_res = await db.execute(
        delete(BlockedSiteDailyStat).where(BlockedSiteDailyStat.stat_date < cutoff_date)
    )
    await db.flush()

    deleted = {
        "alert_logs": alerts_res.rowcount or 0,
        "status_reports": reports_res.rowcount or 0,
        "blocked_site_daily_stats": stats_res.rowcount or 0,
    }
    logger.info(f"보관 정리 완료(>{retention_days}일): {deleted}")
    return deleted


async def get_lab_list(db: AsyncSession) -> list[str]:
    """등록된 에이전트의 강의실 이름 목록(중복 제거)을 반환합니다."""
    result = await db.execute(
        select(Agent.lab_name)
        .where(Agent.lab_name.isnot(None))
        .distinct()
        .order_by(Agent.lab_name)
    )
    return [r[0] for r in result.all()]


def _day_range_bounds(
    date_from: Optional[date], date_to: Optional[date]
) -> tuple[Optional[datetime], Optional[datetime]]:
    """날짜(YYYY-MM-DD) 범위를 [시작, 끝(다음날 0시)] UTC datetime 경계로 변환한다."""
    lo = (
        datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc)
        if date_from
        else None
    )
    hi = (
        datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        if date_to
        else None
    )
    return lo, hi


def _blocked_site_key(url: str) -> Optional[str]:
    """차단 알림 URL을 사이트 키로 변환한다.

    설정된 차단 패턴에 부분일치하면 그 패턴을, 아니면 호스트명을 추출해 반환한다.
    (설정에 없는 사이트도 통계에 누락 없이 표시되도록 폴백)
    """
    pat = _match_blocked_pattern(url)
    if pat is not None:
        return pat
    raw = (url or "").strip().lower()
    if not raw:
        return None
    # 스킴 제거
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    # 경로/쿼리 제거 → 호스트만
    host = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _blocked_alert_site(url: Optional[str], message: Optional[str]) -> Optional[str]:
    """차단 알림 한 건의 사이트 키.

    URL이 있으면 URL로, 없으면(에이전트가 창 제목으로 탐지한 알림) 메시지에 담긴
    창 제목의 사이트명(settings.blocked_title_sites)으로 판정한다. 판정할 수 없으면 None.
    """
    if url:
        return _blocked_site_key(url)
    text = (message or "").lower()
    for keyword, site in settings.blocked_title_sites.items():
        if keyword.lower() in text:
            return site
    return None


async def get_blocked_site_stats(
    db: AsyncSession,
    lab: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    agent_id: Optional[str] = None,
) -> list[dict]:
    """에이전트 × 차단 사이트별 통계를 반환한다.

    접근 이벤트(횟수/최초·최근 접속)는 alert_logs(BLOCKED_URL)를 단일 진실 소스로 도출하고,
    활성/백그라운드 시간은 BlockedSiteDailyStat에서 enrich한다. 따라서 기능 배포 이전의
    과거 차단 내역(alert_logs만 존재)도 즉시 조회되며, 활성/백그라운드 시간은 누적된 만큼 보강된다.
    """
    lo, hi = _day_range_bounds(date_from, date_to)

    # ── 1) alert_logs 기반 접근 이벤트 집계 (source of truth) ──
    ev_stmt = (
        select(AlertLogDB.agent_id, AlertLogDB.url, AlertLogDB.message, AlertLogDB.detected_at)
        .join(Agent, Agent.agent_id == AlertLogDB.agent_id, isouter=True)
        .where(AlertLogDB.code == "BLOCKED_URL")
    )
    if lab:
        ev_stmt = ev_stmt.where(Agent.lab_name == lab)
    if agent_id:
        ev_stmt = ev_stmt.where(AlertLogDB.agent_id == agent_id)
    if lo is not None:
        ev_stmt = ev_stmt.where(AlertLogDB.detected_at >= lo)
    if hi is not None:
        ev_stmt = ev_stmt.where(AlertLogDB.detected_at < hi)

    merged: dict[tuple, dict] = {}
    for aid, url, message, dt in (await db.execute(ev_stmt)).all():
        key_site = _blocked_alert_site(url, message)
        if key_site is None:
            continue
        key = (aid, key_site)
        m = merged.setdefault(key, {
            "access_count": 0,
            "active_seconds": 0,
            "background_seconds": 0,
            "first_access_at": None,
            "last_access_at": None,
        })
        m["access_count"] += 1
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if m["first_access_at"] is None or dt < m["first_access_at"]:
                m["first_access_at"] = dt
            if m["last_access_at"] is None or dt > m["last_access_at"]:
                m["last_access_at"] = dt

    # ── 2) BlockedSiteDailyStat 기반 활성/백그라운드 시간 enrich ──
    t_stmt = (
        select(
            BlockedSiteDailyStat.agent_id,
            BlockedSiteDailyStat.url_pattern,
            func.sum(BlockedSiteDailyStat.active_seconds),
            func.sum(BlockedSiteDailyStat.background_seconds),
            func.min(BlockedSiteDailyStat.first_access_at),
            func.max(BlockedSiteDailyStat.last_access_at),
        )
        .join(Agent, Agent.agent_id == BlockedSiteDailyStat.agent_id, isouter=True)
        .group_by(BlockedSiteDailyStat.agent_id, BlockedSiteDailyStat.url_pattern)
    )
    if lab:
        t_stmt = t_stmt.where(Agent.lab_name == lab)
    if agent_id:
        t_stmt = t_stmt.where(BlockedSiteDailyStat.agent_id == agent_id)
    if date_from:
        t_stmt = t_stmt.where(BlockedSiteDailyStat.stat_date >= date_from)
    if date_to:
        t_stmt = t_stmt.where(BlockedSiteDailyStat.stat_date <= date_to)

    for aid, pattern, active_s, background_s, first_at, last_at in (await db.execute(t_stmt)).all():
        key = (aid, pattern)
        m = merged.setdefault(key, {
            "access_count": 0,
            "active_seconds": 0,
            "background_seconds": 0,
            "first_access_at": None,
            "last_access_at": None,
        })
        m["active_seconds"] += int(active_s or 0)
        m["background_seconds"] += int(background_s or 0)
        for ts in (first_at, last_at):
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if m["first_access_at"] is None or ts < m["first_access_at"]:
                m["first_access_at"] = ts
            if m["last_access_at"] is None or ts > m["last_access_at"]:
                m["last_access_at"] = ts

    # ── 3) 에이전트 표시 정보(hostname/ip/lab) 매핑 ──
    agent_ids = {aid for (aid, _) in merged.keys()}
    agents_map: dict[str, tuple] = {}
    if agent_ids:
        ag_rows = await db.execute(
            select(Agent.agent_id, Agent.hostname, Agent.ip_address, Agent.lab_name)
            .where(Agent.agent_id.in_(agent_ids))
        )
        agents_map = {r[0]: (r[1], r[2], r[3]) for r in ag_rows.all()}

    out = []
    for (aid, site), m in merged.items():
        hostname, ip, lab_name = agents_map.get(aid, (None, None, None))
        out.append({
            "agent_id": aid,
            "hostname": hostname,
            "ip_address": ip,
            "lab_name": lab_name,
            "url_pattern": site,
            "active_seconds": m["active_seconds"],
            "background_seconds": m["background_seconds"],
            "access_count": m["access_count"],
            "first_access_at": m["first_access_at"],
            "last_access_at": m["last_access_at"],
        })
    # 활성+백그라운드 시간 → 접근 횟수 순으로 정렬
    out.sort(
        key=lambda r: (r["active_seconds"] + r["background_seconds"], r["access_count"]),
        reverse=True,
    )
    return out


async def get_blocked_site_detail(
    db: AsyncSession,
    agent_id: str,
    site: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    log_limit: int = 300,
) -> dict:
    """특정 에이전트 × 차단 사이트의 드릴다운 상세를 반환한다.

    - 일자별 활성/백그라운드 시간(blocked_site_daily_stats) + 일자별 접근 횟수(alert_logs)
    - 개별 차단 접근 로그(alert_logs, 최근 log_limit건)
    - 요약(총 활성/백그라운드 시간, 총 접근 횟수, 최초/최근 접속)
    """
    lo, hi = _day_range_bounds(date_from, date_to)

    # 에이전트 표시 정보
    ag = (await db.execute(
        select(Agent.hostname, Agent.ip_address, Agent.lab_name)
        .where(Agent.agent_id == agent_id)
    )).first()
    hostname, ip, lab_name = ag if ag else (None, None, None)

    # 1) 개별 차단 로그 (해당 site로 매핑되는 BLOCKED_URL 알림)
    log_stmt = (
        select(AlertLogDB.detected_at, AlertLogDB.level, AlertLogDB.url, AlertLogDB.message)
        .where(AlertLogDB.code == "BLOCKED_URL", AlertLogDB.agent_id == agent_id)
    )
    if lo is not None:
        log_stmt = log_stmt.where(AlertLogDB.detected_at >= lo)
    if hi is not None:
        log_stmt = log_stmt.where(AlertLogDB.detected_at < hi)
    log_stmt = log_stmt.order_by(AlertLogDB.detected_at.desc())

    all_logs = []          # site 일치 로그(시간 내림차순)
    daily_access: dict[str, int] = {}
    first_at = None
    last_at = None
    for detected_at, level, url, message in (await db.execute(log_stmt)).all():
        if _blocked_alert_site(url, message) != site:
            continue
        if detected_at is not None and detected_at.tzinfo is None:
            detected_at = detected_at.replace(tzinfo=timezone.utc)
        all_logs.append({"detected_at": detected_at, "level": level, "url": url, "message": message})
        if detected_at is not None:
            day = detected_at.date().isoformat()
            daily_access[day] = daily_access.get(day, 0) + 1
            if first_at is None or detected_at < first_at:
                first_at = detected_at
            if last_at is None or detected_at > last_at:
                last_at = detected_at

    access_count = len(all_logs)

    # 2) 일자별 활성/백그라운드 시간
    t_stmt = (
        select(
            BlockedSiteDailyStat.stat_date,
            BlockedSiteDailyStat.active_seconds,
            BlockedSiteDailyStat.background_seconds,
            BlockedSiteDailyStat.first_access_at,
            BlockedSiteDailyStat.last_access_at,
        )
        .where(
            BlockedSiteDailyStat.agent_id == agent_id,
            BlockedSiteDailyStat.url_pattern == site,
        )
    )
    if date_from:
        t_stmt = t_stmt.where(BlockedSiteDailyStat.stat_date >= date_from)
    if date_to:
        t_stmt = t_stmt.where(BlockedSiteDailyStat.stat_date <= date_to)

    daily_time: dict[str, dict] = {}
    total_active = 0
    total_background = 0
    for stat_date, active_s, background_s, f_at, l_at in (await db.execute(t_stmt)).all():
        day = stat_date.isoformat()
        daily_time[day] = {"active": int(active_s or 0), "background": int(background_s or 0)}
        total_active += int(active_s or 0)
        total_background += int(background_s or 0)
        for ts in (f_at, l_at):
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if first_at is None or ts < first_at:
                first_at = ts
            if last_at is None or ts > last_at:
                last_at = ts

    # 3) 일자별 병합 (접근 횟수 + 활성/백그라운드 시간)
    all_days = sorted(set(daily_access) | set(daily_time), reverse=True)
    daily = []
    for day in all_days:
        t = daily_time.get(day, {})
        daily.append({
            "date": day,
            "active_seconds": t.get("active", 0),
            "background_seconds": t.get("background", 0),
            "access_count": daily_access.get(day, 0),
        })

    return {
        "agent_id": agent_id,
        "hostname": hostname,
        "ip_address": ip,
        "lab_name": lab_name,
        "url_pattern": site,
        "active_seconds": total_active,
        "background_seconds": total_background,
        "access_count": access_count,
        "first_access_at": first_at,
        "last_access_at": last_at,
        "daily": daily,
        "logs": all_logs[:log_limit],
        "log_total": access_count,
    }


async def get_blocked_access_timeline(
    db: AsyncSession,
    lab: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    agent_id: Optional[str] = None,
) -> dict:
    """차단 사이트 접근(BLOCKED_URL 알림)의 시간대별/일자별 분포를 반환합니다.

    사이트 통계 표와 합계가 맞도록, 사이트를 판정할 수 없는 알림은 제외한다.
    """
    base = (
        select(AlertLogDB.detected_at, AlertLogDB.url, AlertLogDB.message)
        .join(Agent, Agent.agent_id == AlertLogDB.agent_id, isouter=True)
        .where(AlertLogDB.code == "BLOCKED_URL")
    )
    lo, hi = _day_range_bounds(date_from, date_to)
    if lab:
        base = base.where(Agent.lab_name == lab)
    if agent_id:
        base = base.where(AlertLogDB.agent_id == agent_id)
    if lo is not None:
        base = base.where(AlertLogDB.detected_at >= lo)
    if hi is not None:
        base = base.where(AlertLogDB.detected_at < hi)

    result = await db.execute(base)
    detected_list = [
        dt for dt, url, message in result.all()
        if _blocked_alert_site(url, message) is not None
    ]

    # 시간대(0~23) 히스토그램
    hourly = {h: 0 for h in range(24)}
    daily: dict[str, int] = {}
    for dt in detected_list:
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hourly[dt.hour] = hourly.get(dt.hour, 0) + 1
        day_key = dt.date().isoformat()
        daily[day_key] = daily.get(day_key, 0) + 1

    return {
        "hourly": [{"hour": h, "count": hourly[h]} for h in range(24)],
        "daily": [{"date": d, "count": daily[d]} for d in sorted(daily.keys())],
        "total": len(detected_list),
    }
