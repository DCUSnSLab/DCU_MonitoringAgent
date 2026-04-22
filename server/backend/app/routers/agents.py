"""
에이전트 → 서버 통신 라우터

에이전트가 호출하는 엔드포인트:
  GET  /api/health           – 헬스체크
  POST /api/agents/register  – 최초 등록
  POST /api/agents/report    – 주기적 상태 보고
"""

import logging

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
    if summary:
        await ws_manager.broadcast("agent_online", summary.model_dump())
    return {"status": "registered", "agent_id": agent.agent_id}


@router.post("/api/agents/report")
async def receive_report(
    req: StatusReportRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """에이전트 상태 보고서 수신 및 저장"""
    # 미등록 에이전트 자동 등록
    from app.schemas import AgentRegisterRequest as Reg
    await agent_service.upsert_agent(
        db,
        Reg(
            agent_id=req.agent_id,
            hostname=req.agent_id,  # fallback
        ),
    )

    report = await agent_service.save_status_report(db, req)

    # 대시보드에 실시간 업데이트 push
    summary = await agent_service.get_agent_summary(db, req.agent_id)
    if summary:
        await ws_manager.broadcast("agent_update", {
            **summary.model_dump(),
            "processes": [p.model_dump() for p in req.processes[:20]],
            "chrome_tabs": [t.model_dump() for t in req.browser_details.chrome.tabs],
            "alerts": [a.model_dump() for a in req.alerts],
        })

    # 전체 요약 통계도 실시간 브로드캐스트 (헤더 카드 즉시 갱신용)
    dashboard_summary = await agent_service.get_dashboard_summary(db)
    await ws_manager.broadcast("summary_update", dashboard_summary.model_dump())

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

    agent = await agent_service.get_agent_summary(db, req.agent_id)
    if agent:
        await ws_manager.broadcast("agent_offline", {
            "agent_id": agent.agent_id,
            "hostname": agent.hostname,
            "is_online": False,
            "current_state": agent.current_state,
        })

    dashboard_summary = await agent_service.get_dashboard_summary(db)
    await ws_manager.broadcast("summary_update", dashboard_summary.model_dump())
    
    logger.info(f"에이전트 오프라인 명시적 알림 수신: {req.agent_id}")
    return {"status": "ok"}

