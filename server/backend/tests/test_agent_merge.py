from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker
from app import manage_agents
from app.models import (Agent, AgentIDAlias, AgentStatusMinute, AlertLogDB,
                        BlockedSiteDailyStat, AgentGPU, AgentInstallation)
from .test_hardware_models import hardware_engine


@pytest.fixture
async def db(hardware_engine):
    async with async_sessionmaker(hardware_engine, expire_on_commit=False)() as session:
        yield session


@pytest.mark.parametrize("hostname,agent_id,expected", [
    ("D2-556-29-A97850", "D2-556-29-A97850", "D2-556-29"),
    ("D2-556-29", "D2-556-29", "D2-556-29"),
    ("D2-556-28-04958d", "D2-556-28-04958D", "D2-556-28"),
    ("DESKTOP-ABC", "D2-556-29-A97850", "D2-556-29"),
    ("D2-556-29", "old-id", "D2-556-29"),
    ("D2-556-29-ZZZZZZ", "unknown", None),
    ("D2-556-29-12345", "unknown", None),
    ("D2-556-29-A97850", "D2-556-28-04958D", None),
    ("DESKTOP-ABC", "unknown", None),
])
def test_candidate_seat_handles_only_mac_suffix(hostname, agent_id, expected):
    assert manage_agents.candidate_seat(hostname, agent_id) == expected


async def test_candidates_group_mac_suffixed_hostname_with_canonical_seat(db, monkeypatch, capsys):
    import json
    db.add_all([Agent(agent_id="D2-556-29-A97850", hostname="D2-556-29-A97850"),
                Agent(agent_id="D2-556-29", hostname="D2-556-29")])
    await db.commit()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(manage_agents, "AsyncSessionLocal", session)
    await manage_agents.run(SimpleNamespace(command="candidates", apply=False))
    output = capsys.readouterr().out
    groups, _ = json.JSONDecoder().raw_decode(output)
    assert len(groups["D2-556-29"]) == 2


async def test_merge_dry_run_then_apply_preserves_alerts(db, monkeypatch):
    source = Agent(agent_id="old-mac", hostname="D2-535-21")
    target = Agent(agent_id="D2-535-21", hostname="D2-535-21")
    db.add_all([source, target])
    await db.flush()
    source_pk, target_pk = source.id, target.id
    db.add(AlertLogDB(agent_id="old-mac", code="BLOCKED_URL"))
    await db.commit()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(manage_agents, "AsyncSessionLocal", session)
    args = SimpleNamespace(command="merge", source=source_pk, target=target_pk, apply=False)
    await manage_agents.run(args)
    assert await db.get(Agent, source_pk) is not None
    assert await db.get(AgentIDAlias, "old-mac") is None
    args.apply = True
    await manage_agents.run(args)
    assert await db.get(Agent, source_pk) is None
    assert (await db.get(AgentIDAlias, "old-mac")).agent_pk == target_pk
    assert (await db.scalar(select(AlertLogDB))).agent_id == "D2-535-21"


async def test_merge_refuses_overlapping_minutes(db, monkeypatch):
    db.add_all([Agent(agent_id="a", hostname="seat"), Agent(agent_id="b", hostname="seat")])
    await db.flush()
    agents = (await db.scalars(select(Agent).order_by(Agent.id))).all()
    minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    db.add_all([AgentStatusMinute(agent_id="a", minute=minute), AgentStatusMinute(agent_id="b", minute=minute)])
    await db.commit()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(manage_agents, "AsyncSessionLocal", session)
    with pytest.raises(ValueError, match="overlapping"):
        await manage_agents.run(SimpleNamespace(command="merge", source=agents[0].id, target=agents[1].id, apply=True))
    assert len((await db.scalars(select(Agent))).all()) == 2


async def test_bulk_creates_canonical_seat_preserves_history_and_is_repeatable(db, monkeypatch, capsys):
    from uuid import uuid4
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    first = Agent(agent_id="D2-556-29-A97850", hostname="D2-556-29-A97850",
                  registered_at=now-timedelta(days=3), last_seen_at=now-timedelta(days=1), ip_address="old")
    second = Agent(agent_id="D2-556-29-BB0000", hostname="D2-556-29",
                   registered_at=now-timedelta(days=2), last_seen_at=now, ip_address="latest")
    unknown = Agent(agent_id="random-id", hostname="DESKTOP-ABC")
    db.add_all([first, second, unknown])
    await db.flush()
    ids = [first.id, second.id]
    db.add_all([AlertLogDB(agent_id=first.agent_id, code="FIRST"), AlertLogDB(agent_id=second.agent_id, code="SECOND"),
                AgentStatusMinute(agent_id=first.agent_id, minute=now-timedelta(minutes=1), samples=5),
                AgentStatusMinute(agent_id=second.agent_id, minute=now, samples=7),
                BlockedSiteDailyStat(agent_id=first.agent_id, url_pattern="chatgpt.com", stat_date=(now-timedelta(days=1)).date(), active_seconds=10),
                BlockedSiteDailyStat(agent_id=second.agent_id, url_pattern="chatgpt.com", stat_date=now.date(), active_seconds=20),
                AgentInstallation(installation_id=str(uuid4()), agent_pk=first.id, status="revoked")])
    await db.commit()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(manage_agents, "AsyncSessionLocal", session)
    args = SimpleNamespace(command="merge-all", apply=False)
    await manage_agents.run(args)
    assert await db.scalar(select(func.count()).select_from(Agent)) == 3
    assert await db.scalar(select(func.count()).select_from(AgentIDAlias)) == 0
    args.apply = True
    await manage_agents.run(args)
    canonical = await db.scalar(select(Agent).where(Agent.agent_id == "D2-556-29"))
    assert canonical and canonical.ip_address == "latest" and canonical.seat_confirmed
    assert canonical.registered_at.replace(tzinfo=timezone.utc) == now-timedelta(days=3)
    assert await db.scalar(select(func.count()).select_from(Agent)) == 2
    assert await db.scalar(select(func.count()).select_from(AlertLogDB)) == 2
    assert await db.scalar(select(func.sum(AgentStatusMinute.samples))) == 12
    assert await db.scalar(select(func.sum(BlockedSiteDailyStat.active_seconds))) == 30
    for model in (AlertLogDB, AgentStatusMinute, BlockedSiteDailyStat):
        assert all(row.agent_pk == canonical.id and row.agent_id == canonical.agent_id
                   for row in (await db.scalars(select(model))).all())
    assert (await db.scalar(select(AgentInstallation))).agent_pk == canonical.id
    assert await db.scalar(select(func.count()).select_from(AgentIDAlias)) == 2
    await manage_agents.run(args)
    assert await db.scalar(select(func.count()).select_from(Agent)) == 2
    assert await db.scalar(select(func.count()).select_from(AgentIDAlias)) == 2
    assert '"merged_rows": 0' in capsys.readouterr().out


@pytest.mark.parametrize("conflict", ["minute", "daily", "hardware", "active", "alias", "identity"])
async def test_bulk_skips_entire_conflicting_group_without_creating_target(db, conflict):
    from uuid import uuid4
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    a = Agent(agent_id="D2-556-29-A97850", hostname="D2-556-29-A97850")
    b = Agent(agent_id="D2-556-29-BB0000", hostname="D2-556-29")
    db.add_all([a,b])
    await db.flush()
    if conflict == "minute":
        db.add_all([AgentStatusMinute(agent_id=x.agent_id, minute=now) for x in (a,b)])
    elif conflict == "daily":
        db.add_all([BlockedSiteDailyStat(agent_id=x.agent_id, url_pattern="x", stat_date=now.date()) for x in (a,b)])
    elif conflict == "hardware":
        db.add(AgentGPU(agent_pk=a.id, device_key="gpu"))
    elif conflict == "active":
        db.add(AgentInstallation(installation_id=str(uuid4()), agent_pk=a.id, status="active", token_hash="a"*64, approved_at=now))
    else:
        other = Agent(agent_id="D2-556-29" if conflict == "identity" else "other",
                      hostname="D3-111-01" if conflict == "identity" else "DESKTOP-OTHER")
        db.add(other)
        await db.flush()
        if conflict == "alias":
            db.add(AgentIDAlias(legacy_agent_id=a.agent_id, agent_pk=other.id))
    await db.commit()
    before = await db.scalar(select(func.count()).select_from(Agent))
    result = await manage_agents.merge_all(db)
    await db.commit()
    assert result["merged_rows"] == 0 and result["created_seats"] == 0
    assert result["skipped_groups"]
    assert await db.scalar(select(func.count()).select_from(Agent)) == before
    assert await db.get(Agent,a.id) is not None and await db.get(Agent,b.id) is not None


async def test_bulk_uses_existing_target_and_continues_after_skipped_group(db):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bad = [Agent(agent_id=f"D2-556-29-{suffix}",hostname="D2-556-29") for suffix in ("A97850","BB0000")]
    target = Agent(agent_id="D2-556-28",hostname="D2-556-28")
    source = Agent(agent_id="D2-556-28-AABBCC",hostname="D2-556-28-AABBCC")
    db.add_all(bad+[target,source])
    await db.flush()
    db.add_all([AgentStatusMinute(agent_id=x.agent_id,minute=now) for x in bad])
    await db.commit()
    result = await manage_agents.merge_all(db)
    await db.commit()
    assert result["merged_rows"] == 1 and result["created_seats"] == 0
    assert len(result["skipped_groups"]) == 1
    assert (await db.get(AgentIDAlias,"D2-556-28-AABBCC")).agent_pk == target.id
