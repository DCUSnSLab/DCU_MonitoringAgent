"""보고 처리 경로 DB 부하 개선 테스트 (GEN-1683).

- 보고 1건당 agents 는 SELECT 1회 · UPDATE 1회만, status_reports 는 쓰지 않음
- 상태 이력은 에이전트 × 1분 집계(agent_status_minutes)로 저장, 늦게 온 재전송은 같은 분 행에 더해 합침
- 보고 본문은 원본 바이트로 검증하고, 정상 보고 접속 로그는 남기지 않음
"""
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import event, func, select

from app import main
from app.config import settings
from app.database import get_db
from app.models import Agent, AgentStatusMinute, AlertLogDB, OTARelease, StatusReportDB
from app.routers import agents
from app.schemas import StatusReportRequest
from app.services import agent_service, report_aggregator as agg_module
from app.services.report_aggregator import (
    MinuteAggregator,
    MinuteBucket,
    ReportStats,
    report_aggregator,
    report_stats,
    write_buckets,
)

UTC = timezone.utc


def report(agent_id="A", hostname="D2-535-21", state="safe", cpu=10.0, mem=40.0,
           procs=3, tabs=2, alerts=(), live=True, ts=None, fg="VS Code", version=""):
    return StatusReportRequest(
        agent_id=agent_id,
        hostname=hostname,
        ip_address="10.0.0.21",
        timestamp=ts,
        agent_version=version,
        user_state=state,
        system_info={"cpu_percent": cpu, "memory_percent": mem},
        processes=[{"pid": i, "name": f"p{i}.exe"} for i in range(procs)],
        browser_details={"chrome": {"tab_count": tabs, "tabs": []}},
        alerts=list(alerts),
        active_detections=[] if live else None,
        foreground_window=fg,
    )


ALERT = {"level": "warning", "code": "BLOCKED_PROCESS", "message": "차단된 프로그램 실행: claude.exe",
         "process_name": "claude.exe"}


class SqlLog:
    """실행된 SQL 문 기록 (보고 1건당 DB 작업 수 확인용)"""

    def __init__(self, db):
        self.statements: list[str] = []
        self._engine = db.bind.sync_engine
        event.listen(self._engine, "before_cursor_execute", self._on)

    def _on(self, conn, cursor, statement, params, context, executemany):
        self.statements.append(" ".join(statement.split()))

    def close(self):
        event.remove(self._engine, "before_cursor_execute", self._on)

    def count(self, pattern: str) -> int:
        return sum(1 for s in self.statements if re.match(pattern, s, re.IGNORECASE))


# ─── 보고 1건당 DB 작업 ─────────────────────────────────────────────

async def test_existing_agent_report_reads_and_updates_agents_once(db):
    await agents.receive_report(report(), db)  # 첫 보고로 에이전트 생성

    log = SqlLog(db)
    try:
        await agents.receive_report(report(state="warning", alerts=[ALERT], version="1.0.30"), db)
    finally:
        log.close()

    assert log.count(r"SELECT .* FROM agents") == 1
    assert log.count(r"UPDATE agents") == 1
    assert log.count(r"INSERT INTO status_reports") == 0
    assert log.count(r"INSERT INTO alert_logs") == 1
    assert log.count(r"SELECT .* FROM ota_releases") == 1  # OTA 확인은 이번 범위에서 그대로
    assert log.count(r"INSERT INTO agent_status_minutes") == 0  # 1분 집계는 주기 태스크가 저장


async def test_first_report_creates_agent_without_previous_time(db):
    agent, prev = await agent_service.load_agent_for_report(db, report(hostname="D2-521-07"))
    assert prev is None
    assert (agent.agent_id, agent.hostname, agent.lab_name) == ("A", "D2-521-07", "521")

    await db.commit()
    agent, prev = await agent_service.load_agent_for_report(db, report())
    assert agent.agent_id == "A" and prev is None  # 아직 보고 처리 전이라 last_seen_at 없음


async def test_report_updates_agent_summary_fields(db):
    await agents.receive_report(report(), db)
    await agents.receive_report(
        report(hostname="D2-536-02", state="danger", cpu=77.5, mem=61.0, procs=5, tabs=4,
               alerts=[ALERT], fg="ChatGPT - Chrome"), db)

    agent = (await db.execute(select(Agent).where(Agent.agent_id == "A"))).scalar_one()
    assert (agent.hostname, agent.lab_name, agent.ip_address) == ("D2-536-02", "536", "10.0.0.21")
    assert (agent.current_state, agent.is_online) == ("danger", True)
    assert (agent.process_count, agent.chrome_tab_count, agent.alert_count) == (5, 4, 1)
    assert (agent.cpu_percent, agent.memory_percent) == (77.5, 61.0)
    assert agent.foreground_window == "ChatGPT - Chrome"
    assert agent.last_seen_at is not None


async def test_previous_last_seen_used_for_blocked_site_time(db, monkeypatch):
    """직전 보고 시각은 통합된 SELECT 에서 가져와 차단 사이트 시간 누적에 그대로 전달"""
    seen = []
    original = agent_service.accumulate_blocked_site_stats

    async def spy(db_, req, prev_last_seen):
        seen.append(prev_last_seen)
        return await original(db_, req, prev_last_seen)

    monkeypatch.setattr(agent_service, "accumulate_blocked_site_stats", spy)
    await agents.receive_report(report(), db)
    await agents.receive_report(report(), db)

    agent = (await db.execute(select(Agent).where(Agent.agent_id == "A"))).scalar_one()
    assert seen[0] is None
    assert seen[1] is not None and seen[1] <= agent.last_seen_at


async def test_alerts_saved_without_report_id(db):
    await agents.receive_report(report(alerts=[ALERT]), db)
    rows = (await db.execute(select(AlertLogDB))).scalars().all()
    assert [(a.code, a.report_id) for a in rows] == [("BLOCKED_PROCESS", None)]
    assert (await db.execute(select(func.count()).select_from(StatusReportDB))).scalar() == 0


async def test_response_keeps_shape_and_ota_check(db):
    db.add(OTARelease(version="1.0.31", filename="a.exe", checksum="abc"))
    await db.commit()
    body = await agents.receive_report(report(version="1.0.30"), db)
    assert body == {"status": "ok", "report_id": None, "update_available": True,
                    "latest_version": "1.0.31", "latest_checksum": "abc"}


async def test_report_feeds_minute_aggregator_after_commit(db, monkeypatch):
    """저장이 실패한 보고는 1분 집계에 넣지 않음"""
    async def failing_commit():
        raise RuntimeError("commit failed")

    original_commit = db.commit
    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await agents.receive_report(report(), db)
    monkeypatch.setattr(db, "commit", original_commit)
    await db.rollback()
    assert report_aggregator.pending_count == 0

    await agents.receive_report(report(), db)
    assert sum(b.samples for b in report_aggregator.take_all()) == 1


# ─── 1분 집계 ──────────────────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def at(minute, second=0):
    return datetime(2026, 10, 20, 1, minute, second, tzinfo=UTC)


def test_bucket_accumulates_sums_max_and_last_values():
    agg = MinuteAggregator(FakeClock())
    agg.add(report(state="safe", cpu=10, mem=40, procs=3, tabs=2, fg="A"), at(5, 1))
    agg.add(report(state="danger", cpu=90, mem=50, procs=7, tabs=1, alerts=[ALERT], fg="B"), at(5, 2))
    agg.add(report(state="warning", cpu=20, mem=45, procs=4, tabs=5, fg="C"), at(5, 3))

    [b] = agg.take_all()
    assert (b.agent_id, b.minute, b.samples) == ("A", at(5), 3)
    assert (b.cpu_sum, b.cpu_max, b.memory_sum, b.memory_max) == (120, 90, 135, 50)
    assert (b.process_count_sum, b.process_count_max) == (14, 7)
    assert (b.chrome_tab_count_sum, b.chrome_tab_count_max) == (8, 5)
    assert b.alert_count == 1
    assert b.state_samples == {"safe": 1, "warning": 1, "danger": 1}
    assert (b.last_state, b.foreground_window) == ("warning", "C")


def test_reports_split_by_minute_and_agent():
    agg = MinuteAggregator(FakeClock())
    agg.add(report(agent_id="A"), at(5, 59))
    agg.add(report(agent_id="A"), at(6, 0))
    agg.add(report(agent_id="B"), at(6, 30))
    keys = sorted((b.agent_id, b.minute) for b in agg.take_all())
    assert keys == [("A", at(5)), ("A", at(6)), ("B", at(6))]


def test_take_ready_after_idle_or_when_agent_moved_on():
    clock = FakeClock()
    agg = MinuteAggregator(clock)
    agg.add(report(agent_id="A"), at(5, 58))
    agg.add(report(agent_id="B"), at(5, 58))
    clock.t += 1
    agg.add(report(agent_id="A"), at(6, 0))  # A 는 다음 분으로 넘어감, B 는 보고가 끊김

    assert agg.take_ready() == []  # 이전 분이 끝난 직전이라 늦게 오는 보고를 잠시 기다림

    clock.t += agg_module.SETTLE_SECONDS
    assert [(b.agent_id, b.minute) for b in agg.take_ready()] == [("A", at(5))]

    clock.t += agg_module.IDLE_SECONDS
    ready = sorted((b.agent_id, b.minute) for b in agg.take_ready())
    assert ready == [("A", at(6)), ("B", at(5))]  # 새 샘플 없이 오래 지나면 저장
    assert agg.pending_count == 0


def test_buffered_report_uses_agent_time_corrected_by_live_offset():
    """재전송 보고는 에이전트 시각(시간대 없는 PC 로컬 시각)을 실시간 보고에서 잰 차이로 UTC 보정"""
    agg = MinuteAggregator(FakeClock())
    now = at(30, 10)
    kst = timedelta(hours=9)
    # 실시간 보고: PC 시계는 KST 로 3초 빠름
    agg.add(report(ts=(now + kst + timedelta(seconds=3)).replace(tzinfo=None)), now)
    # 20분 전 PC 시각으로 찍힌 재전송 보고
    old_pc_time = (now + kst + timedelta(seconds=3) - timedelta(minutes=20)).replace(tzinfo=None)
    agg.add(report(live=False, ts=old_pc_time), now)

    minutes = sorted(b.minute for b in agg.take_all())
    assert minutes == [at(10), at(30)]


def test_buffered_report_without_known_offset_uses_receive_time():
    agg = MinuteAggregator(FakeClock())
    agg.add(report(live=False, ts=datetime(2026, 10, 20, 9, 0, 0)), at(30, 10))
    [b] = agg.take_all()
    assert b.minute == at(30)


def test_restore_merges_back_failed_buckets():
    agg = MinuteAggregator(FakeClock())
    agg.add(report(cpu=10), at(5, 1))
    failed = agg.take_all()
    agg.add(report(cpu=30), at(5, 40))
    agg.restore(failed)
    [b] = agg.take_all()
    assert (b.samples, b.cpu_sum, b.cpu_max) == (2, 40, 30)


def test_restore_drops_oldest_when_over_limit(monkeypatch):
    monkeypatch.setattr(agg_module, "MAX_PENDING_BUCKETS", 2)
    agg = MinuteAggregator(FakeClock())
    agg.restore([MinuteBucket(agent_id="A", minute=at(m)) for m in (1, 2, 3)])
    assert sorted(b.minute for b in agg.take_all()) == [at(2), at(3)]


def test_long_window_title_truncated():
    agg = MinuteAggregator(FakeClock())
    agg.add(report(fg="x" * 2000), at(5))
    [b] = agg.take_all()
    assert len(b.foreground_window) == agg_module.WINDOW_TITLE_MAX


# ─── 1분 집계 저장 ──────────────────────────────────────────────────

async def _rows(db):
    return (await db.execute(select(AgentStatusMinute).order_by(AgentStatusMinute.minute))).scalars().all()


async def test_write_buckets_inserts_one_row_per_agent_minute(db):
    await agents.receive_report(report(agent_id="A"), db)
    await agents.receive_report(report(agent_id="B", hostname="D2-521-01"), db)
    report_aggregator.take_all()

    agg = MinuteAggregator(FakeClock())
    agg.add(report(agent_id="A", state="danger", cpu=50), at(5, 1))
    agg.add(report(agent_id="A", state="safe", cpu=30), at(5, 2))
    agg.add(report(agent_id="B"), at(5, 2))
    assert await write_buckets(db, agg.take_all()) == 2
    await db.commit()

    rows = {r.agent_id: r for r in await _rows(db)}
    a = rows["A"]
    assert (a.samples, a.cpu_sum, a.cpu_max, a.danger_samples, a.safe_samples, a.last_state) == (
        2, 80, 50, 1, 1, "safe")
    assert rows["B"].samples == 1


async def test_late_samples_merged_into_saved_minute(db):
    """이미 저장한 분에 재전송 샘플이 늦게 와도 같은 행에 더해짐 (최대값은 큰 쪽, 마지막 값은 늦은 샘플)"""
    await agents.receive_report(report(), db)
    report_aggregator.take_all()

    agg = MinuteAggregator(FakeClock())
    agg.add(report(state="warning", cpu=20, procs=3, fg="later"), at(5, 50))
    await write_buckets(db, agg.take_all())
    await db.commit()

    agg.add(report(state="danger", cpu=95, procs=9, alerts=[ALERT], fg="earlier"), at(5, 10))
    await write_buckets(db, agg.take_all())
    await db.commit()

    [row] = await _rows(db)
    assert (row.samples, row.cpu_sum, row.cpu_max) == (2, 115, 95)
    assert (row.process_count_sum, row.process_count_max, row.alert_count) == (12, 9, 1)
    assert (row.warning_samples, row.danger_samples) == (1, 1)
    # 늦게 저장됐지만 더 이른 샘플이라 마지막 상태·창 제목은 바꾸지 않음
    assert (row.last_state, row.foreground_window) == ("warning", "later")


async def test_cleanup_removes_old_minute_rows(db):
    await agents.receive_report(report(), db)
    report_aggregator.take_all()
    agg = MinuteAggregator(FakeClock())
    old = datetime.now(UTC) - timedelta(days=200)
    agg.add(report(), old)
    agg.add(report(), datetime.now(UTC) - timedelta(days=1))
    await write_buckets(db, agg.take_all())
    await db.commit()

    deleted = await agent_service.cleanup_old_data(db, retention_days=120)
    assert deleted["agent_status_minutes"] == 1
    assert len(await _rows(db)) == 1


async def test_flush_failure_keeps_buckets_for_retry(monkeypatch):
    async def boom(db, buckets):
        raise RuntimeError("db down")

    monkeypatch.setattr(main, "write_buckets", boom)
    report_aggregator.add(report(), datetime.now(UTC))
    pending = report_aggregator.take_all()
    assert await main._flush_minute_buckets(pending) == 0
    assert report_aggregator.pending_count == 1


# ─── HTTP 진입점 · 로그 ─────────────────────────────────────────────

@pytest.fixture
async def client(db):
    async def test_db():
        yield db

    main.app.dependency_overrides[get_db] = test_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.pop(get_db, None)


async def test_report_endpoint_validates_raw_body_and_records_stats(client):
    headers = {"X-API-Key": settings.api_key, "Content-Type": "application/json"}
    body = report(alerts=[ALERT]).model_dump_json()
    r = await client.post("/api/agents/report", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert (report_stats.reports, report_stats.alerts, report_stats.agents) == (1, 1, {"A"})

    bad = await client.post("/api/agents/report", content=b'{"hostname": "x"}', headers=headers)
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"][-1] == "agent_id"

    broken = await client.post("/api/agents/report", content=b"{not json", headers=headers)
    assert broken.status_code == 422

    denied = await client.post("/api/agents/report", content=body, headers={"X-API-Key": "wrong"})
    assert denied.status_code == 401


async def test_old_agent_payload_with_extra_fields_accepted(client):
    """배포된 구버전 에이전트(cmdline·username 등 서버에 없는 필드 포함) 보고도 그대로 받음"""
    payload = report().model_dump(mode="json")
    payload["message_type"] = "status_report"
    payload["processes"] = [{"pid": 1, "name": "a.exe", "cmdline": "a.exe --x", "username": "student",
                             "create_time": "2026-10-20T09:00:00"}]
    r = await client.post("/api/agents/report", json=payload, headers={"X-API-Key": settings.api_key})
    assert r.status_code == 200


def _access_record(path, status):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0,
                             '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "POST", path, "1.1", status), None)


def test_access_log_skips_only_successful_reports():
    f = main._SkipRoutineReportAccessLog()
    assert f.filter(_access_record("/api/agents/report", 200)) is False
    assert f.filter(_access_record("/api/agents/report", 500)) is True
    assert f.filter(_access_record("/api/agents/report", 401)) is True
    assert f.filter(_access_record("/api/dashboard/agents", 200)) is True
    assert f.filter(logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "plain", None, None)) is True


def test_report_stats_summary_line_once():
    stats = ReportStats()
    assert stats.take_summary() is None
    stats.record("A", 2, 3.0)
    stats.record("B", 0, 5.0)
    stats.record("A", 1, 4.0)
    line = stats.take_summary()
    assert line == "보고 3건 · PC 2대 · 새 알림 3건 · 처리 평균 4.0 ms / 최대 5.0 ms"
    assert stats.take_summary() is None


async def test_report_path_logs_nothing_at_info(db, caplog):
    await agents.receive_report(report(), db)  # 신규 등록은 INFO 1회
    caplog.clear()
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            await agents.receive_report(report(), db)
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO] == []
