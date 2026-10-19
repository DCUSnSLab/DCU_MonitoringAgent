"""
에이전트 → 서버 통신 라우터

에이전트가 호출하는 엔드포인트:
  GET  /api/health           – 헬스체크
  POST /api/agents/register  – 최초 등록
  POST /api/agents/report    – 주기적 상태 보고
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas import AgentRegisterRequest, StatusReportRequest, AgentOfflineRequest
from app.services import agent_service
from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


def verify_api_key(x_api_key: str = Header(default="")):
    """API Key 검증 (설정된 경우에만)"""
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")


@router.get("/api/health")
async def health_check():
    """에이전트 연결 확인용 헬스체크"""
    return {"status": "ok", "service": "DCU Monitoring Server"}


@router.post("/api/agents/register")
async def register_agent(
    req: AgentRegisterRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """에이전트 등록 또는 재등록"""
    agent = await agent_service.upsert_agent(db, req)
    # 대시보드에 신규 에이전트 알림
    summary = await agent_service.get_agent_summary(db, req.agent_id)
    # 저장을 확정한 뒤 대시보드에 알린다 (요약은 다른 DB 세션에서 계산하므로 확정 전이면 이전 상태로 셈)
    await db.commit()
    if summary:
        # 대시보드 전송은 대기열에 넣고 바로 진행 (네트워크를 기다리며 DB 연결을 쥐지 않음)
        ws_manager.publish("agent_online", summary.model_dump(mode="json"))
        ws_manager.request_summary()
    return {"status": "registered", "agent_id": agent.agent_id}


@router.post("/api/agents/report")
async def receive_report(
    req: StatusReportRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """에이전트 상태 보고서 수신 및 저장"""
    # 차단 사이트 시간 누적을 위해 upsert 전에 직전 보고 시각을 확보한다.
    # (upsert_agent / save_status_report가 last_seen_at을 현재 시각으로 갱신하므로 그 전에 읽어야 함)
    from app.models import Agent
    prev_result = await db.execute(
        select(Agent.last_seen_at).where(Agent.agent_id == req.agent_id)
    )
    prev_last_seen = prev_result.scalar_one_or_none()

    # 미등록 에이전트 자동 등록 (보고서에 담겨 온 IP/호스트명 활용)
    from app.schemas import AgentRegisterRequest as Reg
    await agent_service.upsert_agent(
        db,
        Reg(
            agent_id=req.agent_id,
            hostname=req.hostname or req.agent_id,  # fallback
            ip_address=req.ip_address,
            agent_version=req.agent_version,
        ),
    )

    now = datetime.now(timezone.utc)
    # DANGER 유지: 잠깐 띄웠다 내린 경우도 대시보드에서 놓치지 않게 일정 시간 DANGER와 이유를 유지
    agent_service.register_danger(req, now)
    # 차단 대상이 화면 앞으로 나온 순간은 빨간 '화면 표시' 알림으로 남긴다
    on_screen_events = agent_service.find_on_screen_events(req, now)
    on_screen_alerts = [agent_service.on_screen_alert(item, now) for item in on_screen_events]

    report = await agent_service.save_status_report(db, req, extra_alerts=on_screen_alerts)

    # 지금 탐지 중인 항목 최신값 보관 (상세 모달 "현재 탐지 중" 표시용)
    # 필드가 없는 보고(구버전 에이전트·오프라인 버퍼 재전송)는 현재 목록을 건드리지 않는다.
    if req.active_detections is not None:
        agent_service.set_active_detections(req.agent_id, req.active_detections)

    # 차단 사이트 활성/백그라운드 시간 누적
    await agent_service.accumulate_blocked_site_stats(db, req, prev_last_seen)

    # 보고·알림 저장을 확정한 뒤 화면 표시 상태를 반영한다.
    # (저장이 실패하면 반영하지 않아, 다음 보고에서 같은 화면 표시 알림을 다시 판단한다)
    await db.commit()
    agent_service.mark_on_screen_state(req, on_screen_events, now)

    # 대시보드에 실시간 업데이트 push
    summary = await agent_service.get_agent_summary(db, req.agent_id)
    if summary:
        update = {
            # 시각 필드(DANGER 구간 등)를 브라우저가 그대로 해석하도록 ISO 형식으로 직렬화
            **summary.model_dump(mode="json"),
            "processes": [p.model_dump() for p in req.processes[:20]],
            "chrome_tabs": [t.model_dump() for t in req.browser_details.chrome.tabs],
            # 실시간 보고의 새 알림(화면 표시 알림 포함)을 보내 열린 상세 모달의 최근 알림에 바로 붙인다.
            # 오프라인 버퍼 재전송(현재 탐지 목록 없음)의 과거 알림은 새 알림처럼 붙지 않도록 보내지 않는다.
            "alerts": (
                [agent_service.alert_for_broadcast(a, now) for a in [*req.alerts, *on_screen_alerts]]
                if req.active_detections is not None else []
            ),
        }
        # 현재 탐지 목록이 담긴 보고일 때만 전달 (없으면 대시보드의 기존 목록 유지)
        if req.active_detections is not None:
            update["active_detections"] = [
                d.model_dump(mode="json")
                for d in agent_service.with_on_screen_since(req.agent_id, req.active_detections)
            ]
        # 대시보드 전송은 대기열에 넣고 바로 진행 (응답 없는 대시보드가 있어도 보고 처리·DB 연결이 묶이지 않음)
        ws_manager.publish("agent_update", update)

    # 전체 요약 통계는 보고마다 계산하지 않고, 주기 태스크가 1초에 한 번 계산해 보낸다 (main._dashboard_pusher)
    ws_manager.request_summary()

    logger.debug(f"보고 수신: {req.agent_id} → {req.user_state} (알림 {len(req.alerts)}개)")

    # OTA 업데이트 확인: 에이전트 버전과 서버 최신 버전 비교
    update_available = False
    latest_version = ""
    latest_checksum = ""
    if req.agent_version:
        from sqlalchemy import desc as sql_desc
        from app.models import OTARelease
        ota_result = await db.execute(
            select(OTARelease).order_by(sql_desc(OTARelease.uploaded_at)).limit(1)
        )
        latest_release = ota_result.scalar_one_or_none()
        if latest_release and latest_release.version != req.agent_version:
            update_available = True
            latest_version = latest_release.version
            latest_checksum = latest_release.checksum

    return {
        "status": "ok",
        "report_id": report.id,
        "update_available": update_available,
        "latest_version": latest_version,
        "latest_checksum": latest_checksum,
    }


@router.post("/api/agents/offline")
async def receive_offline(
    req: AgentOfflineRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """에이전트 종료 시 즉시 오프라인 상태 처리"""
    from sqlalchemy import update
    from app.models import Agent

    await db.execute(
        update(Agent)
        .where(Agent.agent_id == req.agent_id)
        .values(is_online=False)
    )
    agent_service.clear_active_detections(req.agent_id)
    agent_service.clear_danger_state(req.agent_id)

    agent = await agent_service.get_agent_summary(db, req.agent_id)
    # 오프라인 저장을 확정한 뒤 대시보드에 알린다 (요약은 다른 DB 세션에서 계산)
    await db.commit()
    if agent:
        ws_manager.publish("agent_offline", {
            "agent_id": agent.agent_id,
            "hostname": agent.hostname,
            "is_online": False,
            "current_state": agent.current_state,
            "active_detections": [],
        })
    ws_manager.request_summary()

    logger.info(f"에이전트 오프라인 명시적 알림 수신: {req.agent_id}")
    return {"status": "ok"}

