import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, update

from app.config import settings
from app.database import Base, AsyncSessionLocal, engine
from app.models import Agent
from app.routers import agents, dashboard, ws
from app.services import agent_service
from app.services.ws_manager import ws_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# 이 시간(초) 동안 보고 없으면 오프라인으로 판정
OFFLINE_THRESHOLD_SECONDS = 60


async def _offline_watchdog():
    """주기적으로 오프라인 에이전트를 감지하고 대시보드에 브로드캐스트합니다."""
    while True:
        await asyncio.sleep(30)  # 30초마다 체크
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=OFFLINE_THRESHOLD_SECONDS)
            async with AsyncSessionLocal() as db:
                # 마지막 보고 시각이 cutoff보다 오래됐고 여전히 온라인 상태인 에이전트 찾기
                result = await db.execute(
                    select(Agent).where(
                        Agent.is_online == True,
                        Agent.last_seen_at < cutoff,
                    )
                )
                offline_agents = result.scalars().all()

                if offline_agents:
                    ids = [a.agent_id for a in offline_agents]
                    await db.execute(
                        update(Agent)
                        .where(Agent.agent_id.in_(ids))
                        .values(is_online=False)
                    )
                    await db.commit()

                    for agent in offline_agents:
                        logger.info(f"에이전트 오프라인 감지: {agent.agent_id} ({agent.hostname})")
                        # 에이전트 카드 상태 즉시 반영
                        await ws_manager.broadcast("agent_offline", {
                            "agent_id": agent.agent_id,
                            "hostname": agent.hostname,
                            "is_online": False,
                            "current_state": agent.current_state,
                        })

                    # 요약 통계 재계산 후 브로드캐스트
                    async with AsyncSessionLocal() as db2:
                        summary = await agent_service.get_dashboard_summary(db2)
                    await ws_manager.broadcast("summary_update", summary.model_dump())
        except Exception as e:
            logger.error(f"오프라인 감지 오류: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 DB 테이블 자동 생성 + 오프라인 감지 태스크 시작"""
    logger.info("DCU Monitoring Server 시작 중...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("DB 테이블 초기화 완료")

    # 백그라운드 오프라인 감지 시작
    watchdog = asyncio.create_task(_offline_watchdog())
    logger.info(f"오프라인 감지 태스크 시작 (임계값: {OFFLINE_THRESHOLD_SECONDS}초)")

    yield

    watchdog.cancel()
    logger.info("서버 종료")



app = FastAPI(
    title="DCU Monitoring Server",
    description="실습실 PC 모니터링 에이전트 중앙 서버",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(agents.router)
app.include_router(dashboard.router)
app.include_router(ws.router)


@app.get("/")
async def root():
    return {"service": "DCU Monitoring Server", "status": "running"}
