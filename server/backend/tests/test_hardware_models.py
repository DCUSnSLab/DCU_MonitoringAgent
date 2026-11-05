"""GEN-1773 저장 제약. TEST_HARDWARE_DATABASE_URL 지정 시 격리된 PostgreSQL 스키마에서도 실행."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import (
    Agent, AgentCPU, AgentDisk, AgentGPU, AgentHardware, AgentIDAlias,
    AgentInstallation, AgentNetworkAdapter,
)

NEW_MODELS = (AgentHardware, AgentCPU, AgentGPU, AgentDisk, AgentNetworkAdapter,
              AgentInstallation, AgentIDAlias)


@pytest_asyncio.fixture
async def hardware_engine():
    url = os.environ.get("TEST_HARDWARE_DATABASE_URL")
    admin = None
    schema = "hw_test_" + uuid4().hex
    if url:
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine("sqlite+aiosqlite://")

        @event.listens_for(engine.sync_engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()
        if admin:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


@pytest_asyncio.fixture
async def hardware_db(hardware_engine):
    async with async_sessionmaker(hardware_engine, expire_on_commit=False)() as db:
        agent = Agent(agent_id="D2-535-21-ABCDEF", hostname="D2-535-21")
        db.add(agent)
        await db.commit()
        yield db, agent.id


async def test_legacy_agent_is_unconfirmed_and_multiple_same_model_gpus_allowed(hardware_db):
    db, pk = hardware_db
    assert (await db.get(Agent, pk)).seat_confirmed is False
    db.add_all([
        AgentGPU(agent_pk=pk, device_key="gpu-0", model="Intel UHD", gpu_type="integrated"),
        AgentGPU(agent_pk=pk, device_key="gpu-1", model="RTX", dedicated_memory_bytes=12 * 1024**3),
        AgentGPU(agent_pk=pk, device_key="gpu-2", model="RTX", dedicated_memory_bytes=12 * 1024**3),
        AgentHardware(agent_pk=pk, memory_total_bytes=64 * 1024**3,
                      collected_at=datetime.now(timezone.utc)),
    ])
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(AgentGPU)) == 3
    assert (await db.get(AgentHardware, pk)).memory_total_bytes == 64 * 1024**3


@pytest.mark.parametrize("model", [AgentCPU, AgentGPU, AgentDisk, AgentNetworkAdapter])
async def test_device_key_is_unique_per_agent(hardware_db, model):
    db, pk = hardware_db
    other = Agent(agent_id="other", hostname="other")
    db.add(other)
    await db.flush()
    db.add_all([model(agent_pk=pk, device_key="slot-0"),
                model(agent_pk=other.id, device_key="slot-0")])
    await db.commit()  # 모르는 사양은 NULL 허용. 다른 자리의 동일 장치 키는 허용.
    db.add(model(agent_pk=pk, device_key="slot-0"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_active_installation_limit_and_replacement(hardware_db):
    db, pk = hardware_db
    now = datetime.now(timezone.utc)
    old = AgentInstallation(installation_id=str(uuid4()), agent_pk=pk, status="active",
                            approved_at=now, token_hash="a" * 64)
    pending = AgentInstallation(installation_id=str(uuid4()), agent_pk=pk)
    db.add_all([old, pending, AgentInstallation(installation_id=str(uuid4()), agent_pk=pk)])
    await db.commit()  # 승인 대기는 여러 개 가능
    old_id, pending_id = old.installation_id, pending.installation_id
    pending.status, pending.approved_at, pending.token_hash = "active", now, "b" * 64
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    old = await db.get(AgentInstallation, old_id)
    old.status, old.revoked_at = "revoked", now
    await db.flush()  # 기존 활성 설치 해제 후 새 설치 승인
    pending = await db.get(AgentInstallation, pending_id)
    pending.status, pending.approved_at, pending.token_hash = "active", now, "b" * 64
    await db.commit()


@pytest.mark.parametrize("kind", ["negative_capacity", "invalid_status", "active_without_token", "orphan"])
async def test_invalid_records_are_rejected(hardware_db, kind):
    db, pk = hardware_db
    records = {
        "negative_capacity": AgentDisk(agent_pk=pk, device_key="disk", capacity_bytes=-1),
        "invalid_status": AgentInstallation(installation_id=str(uuid4()), agent_pk=pk, status="invalid"),
        "active_without_token": AgentInstallation(installation_id=str(uuid4()), agent_pk=pk, status="active"),
        "orphan": AgentCPU(agent_pk=pk + 10000, device_key="cpu"),
    }
    db.add(records[kind])
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_alias_unique_and_new_records_cascade(hardware_db):
    db, pk = hardware_db
    alias = "D2-535-21-OLDMAC"
    db.add_all([
        AgentIDAlias(legacy_agent_id=alias, agent_pk=pk),
        AgentCPU(agent_pk=pk, device_key="cpu"),
        AgentGPU(agent_pk=pk, device_key="gpu"),
        AgentDisk(agent_pk=pk, device_key="disk"),
        AgentNetworkAdapter(agent_pk=pk, device_key="nic"),
        AgentHardware(agent_pk=pk, collected_at=datetime.now(timezone.utc)),
        AgentInstallation(installation_id=str(uuid4()), agent_pk=pk),
    ])
    await db.commit()
    assert await db.scalar(select(AgentIDAlias.agent_pk).where(AgentIDAlias.legacy_agent_id == alias)) == pk
    db.add(AgentIDAlias(legacy_agent_id=alias, agent_pk=pk))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    await db.execute(delete(Agent).where(Agent.id == pk))
    await db.commit()
    for model in NEW_MODELS:
        assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_postgresql_startup_preserves_legacy_data_and_can_be_reapplied(hardware_engine):
    if hardware_engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL startup initialization")
    from app.main import _apply_column_additions
    from app.reference_migration import apply
    # 가상 기존 DB에서 서버 시작 초기화를 두 번 검증한다. 백업/실제 병합 데이터는 사용하지 않는다.
    async with hardware_engine.begin() as conn:
        await conn.execute(text("INSERT INTO agents (agent_id, hostname, current_state, is_online, "
                                "process_count, chrome_tab_count, alert_count, cpu_percent, memory_percent) "
                                "VALUES ('legacy', 'DESKTOP-OLD', 'safe', false, 0, 0, 0, 0, 0)"))
        for model in reversed(NEW_MODELS):
            await conn.run_sync(lambda c, t=model.__table__: t.drop(c))
        await conn.execute(text("ALTER TABLE agents DROP COLUMN seat_confirmed"))
    async with hardware_engine.begin() as conn:
        for _ in range(2):
            await conn.run_sync(Base.metadata.create_all)
            await _apply_column_additions(conn)
            await apply(conn)
        row = (await conn.execute(text("SELECT agent_id, hostname, seat_confirmed FROM agents"))).one()
        assert tuple(row) == ("legacy", "DESKTOP-OLD", False)
        for model in NEW_MODELS:
            columns = await conn.run_sync(lambda c, t=model.__tablename__: inspect(c).get_columns(t))
            assert {c["name"] for c in columns} == set(model.__table__.columns.keys())


async def test_fixed_reference_migration_populates_new_and_existing_reports(hardware_engine):
    from app.reference_migration import apply
    async with hardware_engine.begin() as conn:
        await conn.execute(text("INSERT INTO agents (agent_id, hostname, current_state, is_online, "
            "process_count, chrome_tab_count, alert_count, cpu_percent, memory_percent) "
            "VALUES ('ref', 'DESKTOP-OLD', 'safe', false, 0, 0, 0, 0, 0)"))
        await conn.execute(text("INSERT INTO alert_logs (agent_id, level, code, message) VALUES ('ref', 'warning', 'test', '')"))
        await apply(conn)
        await apply(conn)
        await conn.execute(text("INSERT INTO alert_logs (agent_id, level, code, message) VALUES ('ref', 'warning', 'test2', '')"))
        rows = (await conn.execute(text("SELECT l.agent_pk, a.id FROM alert_logs l JOIN agents a ON a.agent_id=l.agent_id"))).all()
        assert len(rows) == 2
        assert all(pk == expected for pk, expected in rows)
