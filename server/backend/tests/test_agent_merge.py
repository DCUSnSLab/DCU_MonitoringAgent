from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from app import manage_agents
from app.models import Agent, AgentIDAlias, AgentStatusMinute, AlertLogDB


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
