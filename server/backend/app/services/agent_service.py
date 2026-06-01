"""
에이전트 서비스 레이어

에이전트 등록/보고 처리 비즈니스 로직을 담당합니다.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AlertLogDB, StatusReportDB
from app.schemas import AgentRegisterRequest, AgentSummary, DashboardSummary, StatusReportRequest

logger = logging.getLogger(__name__)


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
        agent.hostname = req.hostname
        agent.lab_name = parsed_lab_name
        agent.ip_address = req.ip_address
        agent.os_version = req.os_version
        agent.agent_version = req.agent_version
        agent.is_online = True
        agent.last_seen_at = now
        logger.info(f"에이전트 재등록: {req.agent_id} | IP: {req.ip_address} - 강의실: {parsed_lab_name}")

    await db.flush()
    return agent



async def save_status_report(db: AsyncSession, req: StatusReportRequest) -> StatusReportDB:
    """상태 보고서를 DB에 저장하고 에이전트 현재 상태를 업데이트합니다."""
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
    for alert in req.alerts:
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

    # 에이전트 현재 상태 캐시 업데이트
    update_values = {
        "current_state": req.user_state,
        "last_seen_at": now,
        "is_online": True,
    }
    if req.agent_version:
        update_values["agent_version"] = req.agent_version

    await db.execute(
        update(Agent)
        .where(Agent.agent_id == req.agent_id)
        .values(**update_values)
    )

    return report


async def get_agent_summary(db: AsyncSession, agent_id: str) -> Optional[AgentSummary]:
    """단일 에이전트의 요약 정보를 반환합니다."""
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        return None

    # 마지막 보고서 조회
    rpt_result = await db.execute(
        select(StatusReportDB)
        .where(StatusReportDB.agent_id == agent_id)
        .order_by(StatusReportDB.reported_at.desc())
        .limit(1)
    )
    last_report = rpt_result.scalar_one_or_none()

    return AgentSummary(
        agent_id=agent.agent_id,
        hostname=agent.hostname,
        lab_name=agent.lab_name,
        ip_address=agent.ip_address,
        current_state=agent.current_state,
        is_online=agent.is_online,
        last_seen_at=agent.last_seen_at,
        process_count=last_report.process_count if last_report else 0,
        chrome_tab_count=last_report.chrome_tab_count if last_report else 0,
        alert_count=last_report.alert_count if last_report else 0,
        foreground_window=last_report.foreground_window if last_report else None,
        cpu_percent=last_report.cpu_percent if last_report else 0.0,
        memory_percent=last_report.memory_percent if last_report else 0.0,
        agent_version=agent.agent_version,
        os_version=agent.os_version,
    )


async def get_dashboard_summary(db: AsyncSession) -> DashboardSummary:
    """전체 대시보드 요약 통계를 계산합니다. WebSocket 브로드캐스트에 사용됩니다."""
    result = await db.execute(select(Agent))
    agents = result.scalars().all()

    total = len(agents)
    online = sum(1 for a in agents if a.is_online)
    safe = sum(1 for a in agents if a.is_online and a.current_state == "safe")
    warning = sum(1 for a in agents if a.is_online and a.current_state == "warning")
    danger = sum(1 for a in agents if a.is_online and a.current_state == "danger")

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
