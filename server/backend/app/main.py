import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text, update

from app.config import settings
from app.database import Base, AsyncSessionLocal, engine
from app.models import Agent
from app.routers import agents, auth, dashboard, ws, ota, statistics
from app.services import agent_service
from app.services.report_aggregator import report_aggregator, report_stats, write_buckets
from app.services.ws_manager import ws_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


class _SkipRoutineReportAccessLog(logging.Filter):
    """정상 처리된 에이전트 보고의 접속 로그는 남기지 않는다 (PC 수 × 초당 1줄이라 로그가 묻힘).

    오류 응답(4xx/5xx)과 다른 경로(대시보드·OTA 등)는 그대로 남긴다. 보고 처리량은 1분 요약 로그로 본다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn 접속 로그 인자: (클라이언트 주소, 메서드, 경로, HTTP 버전, 상태 코드)
        if isinstance(args, tuple) and len(args) == 5:
            path, status = args[2], args[4]
            if isinstance(path, str) and path.startswith("/api/agents/report") and isinstance(status, int) and status < 400:
                return False
        return True


logging.getLogger("uvicorn.access").addFilter(_SkipRoutineReportAccessLog())

# 이 시간(초) 동안 보고 없으면 오프라인으로 판정
OFFLINE_THRESHOLD_SECONDS = 60
# 대시보드 요약 통계 전송 주기(초)와 연결 확인 신호 주기(초)
DASHBOARD_SUMMARY_INTERVAL_SECONDS = 1.0
DASHBOARD_HEARTBEAT_SECONDS = 15.0
# 1분 집계 저장 확인 주기(초)와 보고 처리 요약 로그 주기(초)
MINUTE_FLUSH_INTERVAL_SECONDS = 5.0
REPORT_SUMMARY_LOG_SECONDS = 60.0


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
                        agent_service.clear_danger_state(agent.agent_id)
                        # 에이전트 카드 상태 반영 (전송 대기열에 넣기만 하고 기다리지 않음)
                        ws_manager.publish("agent_offline", {
                            "agent_id": agent.agent_id,
                            "hostname": agent.hostname,
                            "is_online": False,
                            "current_state": agent.current_state,
                            "active_detections": [],
                        })
                    ws_manager.request_summary()
        except Exception as e:
            logger.error(f"오프라인 감지 오류: {e}")


async def _dashboard_pusher():
    """대시보드용 주기 전송: 요약 통계는 요청이 있을 때 1초에 한 번만 계산해 보내고, 연결 확인 신호를 보낸다.

    요약은 전에는 보고마다 계산했다(에이전트 전체 조회 + 최근 1시간 알림 수). 보고가 몰려도 1초에 한 번만 계산한다.
    연결 확인 신호는 보고가 없는 시간에도 대시보드가 '연결이 살아 있음'을 알 수 있게 한다
    (일정 시간 아무 메시지도 없으면 대시보드가 스스로 재연결).
    """
    loop = asyncio.get_running_loop()
    last_heartbeat = loop.time()
    while True:
        await asyncio.sleep(DASHBOARD_SUMMARY_INTERVAL_SECONDS)
        try:
            if ws_manager.take_summary_request() and ws_manager.connection_count > 0:
                async with AsyncSessionLocal() as db:
                    summary = await agent_service.get_dashboard_summary(db)
                # DB 세션을 닫은 뒤 전송 대기열에 넣는다
                ws_manager.publish("summary_update", summary.model_dump(mode="json"))
            if loop.time() - last_heartbeat >= DASHBOARD_HEARTBEAT_SECONDS:
                ws_manager.publish("heartbeat", {})
                last_heartbeat = loop.time()
        except Exception as e:
            logger.error(f"대시보드 주기 전송 오류: {e}")


async def _flush_minute_buckets(buckets) -> int:
    """1분 집계를 저장한다. 실패하면 집계를 되돌려 다음 주기에 다시 저장한다."""
    if not buckets:
        return 0
    try:
        async with AsyncSessionLocal() as db:
            saved = await write_buckets(db, buckets)
            await db.commit()
        return saved
    except Exception as e:
        report_aggregator.restore(buckets)
        logger.error(f"1분 집계 저장 오류 (다음 주기에 다시 시도, 대기 {report_aggregator.pending_count}건): {e}")
        return 0


async def _minute_flusher():
    """에이전트 상태 1분 집계를 주기적으로 저장하고, 보고 처리 요약을 1분에 1줄 남긴다."""
    loop = asyncio.get_running_loop()
    last_summary = loop.time()
    saved_rows = 0
    while True:
        await asyncio.sleep(MINUTE_FLUSH_INTERVAL_SECONDS)
        saved_rows += await _flush_minute_buckets(report_aggregator.take_ready())
        if loop.time() - last_summary >= REPORT_SUMMARY_LOG_SECONDS:
            line = report_stats.take_summary()
            if line:
                logger.info(f"최근 1분 {line} · 1분 집계 저장 {saved_rows}행")
            saved_rows = 0
            last_summary = loop.time()


async def _retention_cleanup():
    """주기적으로 보관 기간이 지난 원시 데이터/통계를 정리합니다 (하루 1회)."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await agent_service.cleanup_old_data(db, settings.retention_days)
                await db.commit()
        except Exception as e:
            logger.error(f"보관 정리 오류: {e}")
        await asyncio.sleep(86400)  # 24시간


# create_all 은 이미 있는 테이블에 열을 추가하지 않는다. 운영 DB(PostgreSQL)에 새 열을 넣을 때 여기에 적는다.
# (SQLite 테스트 DB 는 매번 새로 만들어 create_all 로 열이 생기므로 건너뛴다)
_COLUMN_ADDITIONS = (
    # GEN-1692: OTA 배포 채널
    "ALTER TABLE ota_releases ADD COLUMN IF NOT EXISTS channel VARCHAR(16) NOT NULL DEFAULT 'stable'",
    "CREATE INDEX IF NOT EXISTS ix_ota_releases_channel ON ota_releases (channel)",
)


async def _apply_column_additions(conn):
    if conn.dialect.name != "postgresql":
        return
    for statement in _COLUMN_ADDITIONS:
        await conn.execute(text(statement))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 DB 테이블 자동 생성 + 오프라인 감지/보관 정리 태스크 시작"""
    logger.info("DCU Monitoring Server 시작 중...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_column_additions(conn)
    logger.info("DB 테이블 초기화 완료")

    # 백그라운드 오프라인 감지 시작
    watchdog = asyncio.create_task(_offline_watchdog())
    logger.info(f"오프라인 감지 태스크 시작 (임계값: {OFFLINE_THRESHOLD_SECONDS}초)")

    # 백그라운드 보관 정리 시작
    cleanup = asyncio.create_task(_retention_cleanup())
    logger.info(f"보관 정리 태스크 시작 (보관 기간: {settings.retention_days}일)")

    # 대시보드 요약 통계·연결 확인 신호 주기 전송
    pusher = asyncio.create_task(_dashboard_pusher())

    # 에이전트 상태 1분 집계 저장
    flusher = asyncio.create_task(_minute_flusher())

    yield

    watchdog.cancel()
    cleanup.cancel()
    pusher.cancel()
    flusher.cancel()
    # 아직 저장하지 않은 1분 집계를 모두 저장 (배포·재시작 시 손실 방지)
    saved = await _flush_minute_buckets(report_aggregator.take_all())
    logger.info(f"서버 종료 (남은 1분 집계 {saved}행 저장)")



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
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(ws.router)
app.include_router(ota.router)
app.include_router(statistics.router)


@app.get("/")
async def root():
    return {"service": "DCU Monitoring Server", "status": "running"}
