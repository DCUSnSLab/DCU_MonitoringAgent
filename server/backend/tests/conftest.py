"""테스트 공통 픽스처 — in-memory SQLite 비동기 세션."""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models  # noqa: F401  (모델 등록 보장)
from app.services import agent_service


@pytest.fixture(autouse=True)
def _reset_gap_carry():
    """시간 누적의 소수부 이월값(메모리)이 테스트 사이에 새지 않도록 비운다."""
    agent_service._gap_carry.clear()
    yield
    agent_service._gap_carry.clear()


@pytest_asyncio.fixture
async def db():
    """테스트마다 새로운 in-memory SQLite DB와 세션을 제공한다."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()
