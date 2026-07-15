"""
대시보드 조회 API 라우터

관리자 브라우저가 초기 로드 시 호출하는 REST 엔드포인트:
  GET /api/dashboard/summary            – 전체 요약 통계
  GET /api/dashboard/agents             – 전체 에이전트 목록
  GET /api/dashboard/agents/{agent_id}  – 특정 에이전트 상세
  GET /api/dashboard/alerts             – 최근 전체 경고 이력
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Agent, AlertLogDB, StatusReportDB
from app.schemas import (
    AgentDetail,
    AgentSummary,
    AlertLogResponse,
    DashboardSummary,
)
from app.services import agent_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard")


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(db: AsyncSession = Depends(get_db)):
    """전체 요약 통계"""
    result = await db.execute(select(Agent))
    agents = result.scalars().all()

    total = len(agents)
    online = sum(1 for a in agents if a.is_online)
    safe = sum(1 for a in agents if a.current_state == "safe")
    warning = sum(1 for a in agents if a.current_state == "warning")
    danger = sum(1 for a in agents if a.current_state == "danger")

    # 최근 1시간 내 알림 수
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


@router.get("/agents", response_model=list[AgentSummary])
async def list_agents(db: AsyncSession = Depends(get_db)):
    """전체 에이전트 목록 및 현재 상태 요약.

    각 에이전트의 최신 요약은 agents 행에 비정규화돼 있으므로 단일 쿼리로 조회한다.
    (과거: 에이전트마다 status_reports에서 최신 보고서를 찾는 N+1 쿼리 → 수 분 소요)
    """
    result = await db.execute(select(Agent).order_by(Agent.registered_at))
    agents = result.scalars().all()
    return [AgentSummary.model_validate(a) for a in agents]


@router.get("/agents/{agent_id}", response_model=AgentDetail)
async def get_agent_detail(agent_id: str, db: AsyncSession = Depends(get_db)):
    """특정 에이전트 상세 정보 (최신 보고서 기준)"""
    summary = await agent_service.get_agent_summary(db, agent_id)
    if not summary:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Agent not found")

    # 에이전트 기본 정보
    agent_result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = agent_result.scalar_one_or_none()

    # 최신 보고서의 상세 정보 (processes, tabs, alerts)는
    # WebSocket으로 실시간 전달되므로 여기서는 최근 알림만 반환
    alert_result = await db.execute(
        select(AlertLogDB)
        .where(AlertLogDB.agent_id == agent_id)
        .order_by(AlertLogDB.detected_at.desc())
        .limit(50)
    )
    recent_alerts = alert_result.scalars().all()

    alert_schemas = [
        {
            "level": a.level,
            "code": a.code,
            "message": a.message,
            "process_name": a.process_name,
            "url": a.url,
            "detected_at": a.detected_at,
        }
        for a in recent_alerts
    ]

    return AgentDetail(
        **summary.model_dump(),
        registered_at=agent.registered_at if agent else None,
        recent_alerts=alert_schemas,
    )


@router.get("/alerts", response_model=list[AlertLogResponse])
async def list_alerts(
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """최근 전체 경고 이력"""
    result = await db.execute(
        select(AlertLogDB, Agent.hostname)
        .join(Agent, Agent.agent_id == AlertLogDB.agent_id, isouter=True)
        .order_by(AlertLogDB.detected_at.desc())
        .limit(limit)
    )
    rows = result.all()

    return [
        AlertLogResponse(
            id=row.AlertLogDB.id,
            agent_id=row.AlertLogDB.agent_id,
            hostname=row.hostname,
            level=row.AlertLogDB.level,
            code=row.AlertLogDB.code,
            message=row.AlertLogDB.message,
            process_name=row.AlertLogDB.process_name,
            url=row.AlertLogDB.url,
            detected_at=row.AlertLogDB.detected_at,
        )
        for row in rows
    ]
