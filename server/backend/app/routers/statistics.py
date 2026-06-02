"""
통계 라우터

대시보드 통계 페이지가 호출하는 엔드포인트:
  GET /api/statistics/labs           – 강의실 목록(필터용)
  GET /api/statistics/blocked-sites  – 에이전트×차단사이트별 활성/백그라운드 시간, 방문 횟수
  GET /api/statistics/timeline       – 차단 접근의 시간대별/일자별 분포
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BlockedAccessTimeline, BlockedSiteStatRow
from app.services import agent_service

router = APIRouter(prefix="/api/statistics")


@router.get("/labs", response_model=list[str])
async def list_labs(db: AsyncSession = Depends(get_db)):
    """필터 드롭다운용 강의실 목록"""
    return await agent_service.get_lab_list(db)


@router.get("/blocked-sites", response_model=list[BlockedSiteStatRow])
async def blocked_sites(
    lab: Optional[str] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """에이전트별/차단 사이트별 활성·백그라운드 누적 시간과 방문 횟수"""
    return await agent_service.get_blocked_site_stats(
        db, lab=lab, date_from=date_from, date_to=date_to, agent_id=agent_id
    )


@router.get("/timeline", response_model=BlockedAccessTimeline)
async def timeline(
    lab: Optional[str] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """차단 사이트 접근의 시간대(0~23)별 및 일자별 분포"""
    return await agent_service.get_blocked_access_timeline(
        db, lab=lab, date_from=date_from, date_to=date_to, agent_id=agent_id
    )
