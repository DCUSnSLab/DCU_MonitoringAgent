from datetime import datetime, timezone, timedelta
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from app import main
from app.config import settings
from app.database import get_db
from app.models import Agent, AgentGPU, AgentInstallation
from sqlalchemy.ext.asyncio import async_sessionmaker
from .test_hardware_models import hardware_engine


@pytest.fixture
async def db(hardware_engine):
    async with async_sessionmaker(hardware_engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "auto_approve_installations", True)
    async def override():
        yield db
    main.app.dependency_overrides[get_db] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test",
                                headers={"X-API-Key": settings.api_key}) as c:
        yield c
    main.app.dependency_overrides.clear()


def registration():
    return {"agent_id": "D2-535-21", "hostname": "D2-535-21", "installation_id": str(uuid4()), "token": "a" * 64}


async def test_registration_retry_conflict_and_report_auth(client, db):
    reg = registration()
    assert (await client.post("/api/agents/installations", json=reg)).json()["status"] == "active"
    assert (await client.post("/api/agents/installations", json=reg)).json()["status"] == "active"
    second = {**reg, "installation_id": str(uuid4()), "token": "b" * 64}
    assert (await client.post("/api/agents/installations", json=second)).json()["status"] == "pending"
    payload = {"agent_id": reg["agent_id"], "installation_id": reg["installation_id"], "active_detections": []}
    assert (await client.post("/api/agents/report", json=payload)).status_code == 403
    client.headers["X-Installation-Token"] = reg["token"]
    assert (await client.post("/api/agents/report", json=payload)).status_code == 200
    assert (await client.post("/api/agents/report", json={**payload, "agent_id": "other"})).status_code == 403
    assert (await client.post("/api/agents/report", json={"agent_id": reg["agent_id"]})).status_code == 403
    assert (await client.post("/api/agents/offline", json=payload)).status_code == 200


async def test_hardware_failure_preserve_remove_and_report_cannot_overwrite(client, db):
    reg = registration()
    await client.post("/api/agents/installations", json=reg)
    client.headers["X-Installation-Token"] = reg["token"]
    info = {"agent_id": reg["agent_id"], "installation_id": reg["installation_id"], "hostname": "RENAMED",
            "collected_at": datetime.now(timezone.utc).isoformat(), "memory_total_bytes": 64 * 1024**3,
            "gpus": [{"device_key": "0", "model": "RTX"}, {"device_key": "1", "model": "RTX"}]}
    assert (await client.post("/api/agents/hardware", json=info)).status_code == 200
    assert (await client.post("/api/agents/hardware", json=info)).status_code == 200
    assert len((await db.scalars(select(AgentGPU))).all()) == 2
    assert (await client.post("/api/agents/hardware", json={**info, "gpus": None})).status_code == 200
    assert len((await db.scalars(select(AgentGPU))).all()) == 2
    assert (await client.post("/api/agents/report", json={"agent_id": reg["agent_id"],
        "installation_id": reg["installation_id"], "hostname": "OLD", "active_detections": []})).status_code == 200
    agent = await db.scalar(select(Agent))
    assert agent.hostname == "RENAMED"
    assert (await client.post("/api/agents/hardware", json={**info, "gpus": []})).status_code == 200
    assert len((await db.scalars(select(AgentGPU))).all()) == 0


async def test_auto_approval_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "auto_approve_installations", False)
    assert (await client.post("/api/agents/installations", json=registration())).json()["status"] == "pending"


async def test_offline_reinstall_replaces_old_installation_and_revoked_token_is_rejected(client, db, monkeypatch):
    old = registration()
    await client.post("/api/agents/installations", json=old)
    agent = await db.scalar(select(Agent))
    agent.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    agent.is_online = False
    await db.commit()
    monkeypatch.setattr(settings, "auto_approve_installations", False)
    new = {**old, "installation_id": str(uuid4()), "token": "b" * 64}
    assert (await client.post("/api/agents/installations", json=new)).json()["status"] == "active"
    previous = await db.get(AgentInstallation, old["installation_id"], populate_existing=True)
    assert previous.status == "revoked" and previous.revoked_at is not None
    assert (await client.post("/api/agents/installations", json=old)).json()["status"] == "revoked"
    client.headers["X-Installation-Token"] = old["token"]
    assert (await client.post("/api/agents/report", json={
        "agent_id": old["agent_id"], "installation_id": old["installation_id"]})).status_code == 403


@pytest.mark.parametrize("continued_reporting", [False, True])
async def test_pending_rechecks_last_seen_after_old_agent_goes_offline(client, db, continued_reporting):
    old = registration()
    await client.post("/api/agents/installations", json=old)
    agent = await db.scalar(select(Agent))
    agent.is_online = True
    agent.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()
    new = {**old, "installation_id": str(uuid4()), "token": "b" * 64}
    assert (await client.post("/api/agents/installations", json=new)).json()["status"] == "pending"
    pending = await db.get(AgentInstallation, new["installation_id"])
    registered = pending.registered_at.replace(tzinfo=timezone.utc)
    agent.is_online = False
    if continued_reporting:
        agent.last_seen_at = registered + timedelta(seconds=1)
    await db.commit()
    expected = "pending" if continued_reporting else "active"
    assert (await client.post("/api/agents/installations", json=new)).json()["status"] == expected
    assert (await client.post("/api/agents/installations", json=new)).json()["status"] == expected


async def test_only_latest_pending_installation_can_replace_old_installation(client, db):
    old = registration()
    await client.post("/api/agents/installations", json=old)
    agent = await db.scalar(select(Agent))
    agent.is_online = True
    agent.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()
    first = {**old, "installation_id": str(uuid4()), "token": "b" * 64}
    latest = {**old, "installation_id": str(uuid4()), "token": "c" * 64}
    for req in (first, latest):
        assert (await client.post("/api/agents/installations", json=req)).json()["status"] == "pending"
    earlier = await db.get(AgentInstallation, first["installation_id"])
    newer = await db.get(AgentInstallation, latest["installation_id"])
    earlier.registered_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    newer.registered_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    agent.is_online = False
    await db.commit()
    assert (await client.post("/api/agents/installations", json=first)).json()["status"] == "pending"
    assert (await client.post("/api/agents/installations", json=latest)).json()["status"] == "active"
    for req in (old, first):
        assert (await db.get(AgentInstallation, req["installation_id"], populate_existing=True)).status == "revoked"


def test_authenticated_buffer_keeps_original_minute_without_live_clock_offset():
    from app.schemas import StatusReportRequest
    from app.services.report_aggregator import MinuteAggregator
    sample = datetime(2026, 10, 1, 1, 2, 3, tzinfo=timezone.utc)
    req = StatusReportRequest(agent_id="seat", installation_id=str(uuid4()), timestamp=sample)
    assert MinuteAggregator().sample_time(req, datetime.now(timezone.utc)) == sample
