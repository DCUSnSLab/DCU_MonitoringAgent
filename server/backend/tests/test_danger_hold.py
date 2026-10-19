"""DANGER 유지 · 화면 표시 알림 테스트 (GEN-1149).

DANGER(차단 대상을 화면 맨 앞에 띄움)는 마지막 DANGER 보고 후 일정 시간 유지하고 그 이유를 보여 주며,
차단 대상이 화면 앞으로 나온 순간은 빨간(level=danger) '화면 표시' 알림으로 남긴다.
DB(agents.current_state)에는 에이전트가 보낸 원래 상태를 저장하고, 유지는 읽을 때 겹쳐 적용한다.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Agent, AlertLogDB, StatusReportDB
from app.routers import agents, dashboard
from app.schemas import ActiveDetectionSchema, AgentOfflineRequest, StatusReportRequest
from app.services import agent_service

T0 = datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc)
HOLD = settings.danger_hold_seconds
COOLDOWN = settings.on_screen_alert_cooldown_seconds


@pytest.fixture(autouse=True)
def _clear_memory():
    stores = (agent_service._danger_holds, agent_service._on_screen_since,
              agent_service._on_screen_alerted, agent_service._active_detections)
    for store in stores:
        store.clear()
    yield
    for store in stores:
        store.clear()


@pytest.fixture
def broadcasts(monkeypatch):
    """WebSocket 브로드캐스트 내용을 기록 (연결된 대시보드 없이 페이로드 확인)."""
    sent = []

    async def fake_broadcast(message_type, data):
        sent.append((message_type, data))

    monkeypatch.setattr(agents.ws_manager, "broadcast", fake_broadcast)
    return sent


def det(target, code="BLOCKED_URL", source="url", fg=True):
    return {"code": code, "target": target, "source": source, "is_foreground": fg}


def report(state="danger", detections=(), fg_window=None, with_detections=True, agent_id="A", alerts=()):
    payload = {"agent_id": agent_id, "hostname": "PC-521-01", "user_state": state,
               "foreground_window": fg_window, "alerts": list(alerts)}
    if with_detections:
        payload["active_detections"] = list(detections)
    return StatusReportRequest(**payload)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def stored(state, agent_id="A"):
    """DB에 저장된 에이전트 행 대신 쓰는 최소 객체 (display_state 용)."""
    return SimpleNamespace(agent_id=agent_id, current_state=state)


def events_at(req, seconds):
    """화면 표시 판단 후 저장이 성공했다고 보고 상태까지 반영."""
    events = agent_service.find_on_screen_events(req, at(seconds))
    agent_service.mark_on_screen_state(req, events, at(seconds))
    return [(e.code, e.target) for e in events]


# ─── DANGER 유지 ─────────────────────────────────────────────────

def test_danger_held_after_target_leaves_screen():
    """DANGER 후 원래 상태가 WARNING/SAFE로 바뀌어도 유지 시간 동안 DANGER와 이유 유지"""
    agent_service.register_danger(report("danger", [det("chatgpt.com/c/1")]), at(0))
    assert agent_service.display_state(stored("warning"), at(HOLD - 1)) == "danger"
    assert agent_service.get_danger_reason("A", at(HOLD - 1)) == "chatgpt.com/c/1"
    assert agent_service.display_state(stored("safe"), at(HOLD)) == "safe"
    assert agent_service.get_danger_reason("A", at(HOLD)) is None


def test_hold_expires_without_further_reports():
    """유지는 저장값이 아니라 읽을 때 적용하므로, 보고가 끊겨도 유지 시간이 지나면 원래 상태로 보임"""
    agent_service.register_danger(report("danger", [det("chatgpt.com/c/1")]), at(0))
    # 이후 에이전트가 WARNING을 한 번 보고하고 끊김 → DB에는 원래 상태 'warning'
    assert agent_service.display_state(stored("warning"), at(HOLD + 30)) == "warning"


def test_new_danger_extends_hold_with_latest_reason():
    agent_service.register_danger(report("danger", [det("chatgpt.com/c/1")]), at(0))
    agent_service.register_danger(report("danger", [det("KakaoTalk.exe", "MESSENGER_DETECTED", "process")]), at(8))
    assert agent_service.get_danger_reason("A", at(8 + HOLD - 1)) == "KakaoTalk.exe"


def test_reason_lists_on_screen_targets_only():
    """이유는 화면 앞 항목만 (최대 2개 + 나머지 건수), 뒤에 있는 항목은 제외"""
    agent_service.register_danger(report("danger", [
        det("claude.exe", "BLOCKED_PROCESS", "process"),
        det("chatgpt.com/c/1"),
        det("instagram.com", "MESSENGER_DETECTED"),
        det("gemini.google.com/app", fg=False),
    ]), at(0))
    assert agent_service.get_danger_reason("A", at(1)) == "claude.exe, chatgpt.com/c/1 외 1건"


def test_reason_falls_back_to_foreground_window():
    agent_service.register_danger(report("danger", [det("chatgpt.com/c/1", fg=False)], fg_window="ChatGPT - Chrome"), at(0))
    assert agent_service.get_danger_reason("A", at(1)) == "ChatGPT - Chrome"


def test_danger_episode_since_and_last():
    """이어진 DANGER 보고는 같은 구간(시작 시각 유지), 보고 간격보다 오래 끊긴 뒤의 DANGER는 새 구간"""
    for s in (0, 3, 6):
        agent_service.register_danger(report("danger", [det("chatgpt.com/c/1")]), at(s))
    hold = agent_service.get_danger_hold("A", at(7))
    assert (hold.since, hold.last_at) == (at(0), at(6))

    agent_service.register_danger(report("danger", [det("chatgpt.com/c/1")]), at(30))  # 24초 뒤 다시 DANGER
    hold = agent_service.get_danger_hold("A", at(31))
    assert (hold.since, hold.last_at) == (at(30), at(30))


def test_buffered_report_does_not_start_hold():
    """현재 탐지 목록이 없는 재전송 보고의 DANGER로는 유지를 시작하지 않음"""
    agent_service.register_danger(report("danger", with_detections=False), at(0))
    assert agent_service.get_danger_reason("A", at(1)) is None


def test_hold_is_per_agent():
    agent_service.register_danger(report("danger", [det("chatgpt.com/c/1")], agent_id="A"), at(0))
    assert agent_service.display_state(stored("warning", "B"), at(1)) == "warning"


# ─── 화면 표시 알림 ──────────────────────────────────────────────

def test_event_when_target_comes_on_screen():
    """뒤에 있던 대상이 화면 앞으로 나온 순간에만 알림 1건"""
    assert events_at(report("warning", [det("chatgpt.com/c/1", fg=False)]), 0) == []
    assert events_at(report("danger", [det("chatgpt.com/c/1")]), 3) == [("BLOCKED_URL", "chatgpt.com/c/1")]
    assert events_at(report("danger", [det("chatgpt.com/c/1")]), 6) == []  # 계속 화면 앞이면 추가 알림 없음


def test_repeated_toggle_alerted_once_per_cooldown():
    """화면 앞·뒤를 반복해도 같은 대상은 쿨다운 동안 1건, 쿨다운 후 다시 나오면 다시 1건"""
    on, off = report("danger", [det("chatgpt.com/c/1")]), report("warning", [det("chatgpt.com/c/1", fg=False)])
    assert len(events_at(on, 0)) == 1
    events_at(off, 3)
    assert events_at(on, 6) == []
    events_at(off, COOLDOWN)
    assert len(events_at(on, COOLDOWN + 3)) == 1


def test_title_notification_count_change_is_same_window():
    """제목 앞 알림 개수만 바뀌는 창('(3) X'→'(4) X')은 계속 화면 앞인 같은 창으로 보고 1건"""
    titles = ["(3) Instagram - Chrome", "(4) Instagram - Chrome", "(9+) Instagram - Chrome", "Instagram - Chrome"]
    total = sum(len(events_at(report("danger", [det(t, "MESSENGER_DETECTED", "title")]), i * 3)) for i, t in enumerate(titles))
    assert total == 1


def test_on_screen_since_kept_while_on_screen():
    """화면 앞에 계속 떠 있는 동안은 처음 시각 유지, 내려가면 지우고 다시 나오면 그때부터"""
    on, off = report("danger", [det("chatgpt.com/c/1")]), report("warning", [det("chatgpt.com/c/1", fg=False)])
    key = ("BLOCKED_URL", "chatgpt.com/c/1")
    for s in (0, 3, 6):
        events_at(on, s)
    assert agent_service._on_screen_since["A"] == {key: at(0)}
    events_at(off, 9)
    assert agent_service._on_screen_since["A"] == {}
    events_at(on, 12)
    assert agent_service._on_screen_since["A"] == {key: at(12)}


def test_with_on_screen_since_fills_foreground_items_only():
    events_at(report("danger", [det("(3) Instagram - Chrome", "MESSENGER_DETECTED", "title")]), 0)
    items = [ActiveDetectionSchema(**det("(5) Instagram - Chrome", "MESSENGER_DETECTED", "title")),
             ActiveDetectionSchema(**det("KakaoTalk.exe", "MESSENGER_DETECTED", "process", fg=False))]
    filled = agent_service.with_on_screen_since("A", items)
    # 알림 개수만 바뀐 같은 창은 처음 시각을 그대로 보여 줌, 뒤에 있는 항목은 비움
    assert [d.on_screen_since for d in filled] == [at(0), None]


def test_each_new_target_alerted():
    events_at(report("danger", [det("chatgpt.com/c/1")]), 0)
    events = events_at(
        report("danger", [det("chatgpt.com/c/1"), det("KakaoTalk.exe", "MESSENGER_DETECTED", "process")]), 3
    )
    assert events == [("MESSENGER_DETECTED", "KakaoTalk.exe")]


def test_find_does_not_change_state_until_marked():
    """판단만 하고 저장 확정(mark) 전에는 상태를 바꾸지 않음 → 저장 실패 시 다음 보고에서 다시 알림"""
    req = report("danger", [det("chatgpt.com/c/1")])
    assert len(agent_service.find_on_screen_events(req, at(0))) == 1
    assert len(agent_service.find_on_screen_events(req, at(3))) == 1  # 반영 전이라 다시 대상
    assert "A" not in agent_service._on_screen_since


def test_old_cooldown_records_pruned():
    """쿨다운이 지난 기록은 정리 (대화마다 바뀌는 URL로 메모리가 계속 쌓이지 않게)"""
    events_at(report("danger", [det("chatgpt.com/c/1")]), 0)
    events_at(report("danger", [det("chatgpt.com/c/2")]), COOLDOWN + 1)
    assert set(agent_service._on_screen_alerted["A"]) == {("BLOCKED_URL", "chatgpt.com/c/2")}


def test_buffered_report_ignored_for_events():
    """현재 탐지 목록 없는 보고는 알림을 만들지 않고 직전 화면 상태도 바꾸지 않음"""
    events_at(report("danger", [det("chatgpt.com/c/1")]), 0)
    assert events_at(report("danger", with_detections=False), 3) == []
    assert set(agent_service._on_screen_since["A"]) == {("BLOCKED_URL", "chatgpt.com/c/1")}


def test_on_screen_alert_fields():
    url_alert = agent_service.on_screen_alert(ActiveDetectionSchema(**det("chatgpt.com/c/1")), at(0))
    assert (url_alert.level, url_alert.code, url_alert.message, url_alert.url, url_alert.process_name) == (
        "danger", "ON_SCREEN", "차단 사이트 화면 표시: chatgpt.com/c/1", "chatgpt.com/c/1", None
    )
    proc = agent_service.on_screen_alert(ActiveDetectionSchema(**det("claude.exe", "BLOCKED_PROCESS", "process")), at(0))
    assert (proc.message, proc.process_name, proc.url) == ("차단 프로그램 화면 표시: claude.exe", "claude.exe", None)
    title = agent_service.on_screen_alert(ActiveDetectionSchema(**det("Instagram - Chrome", "MESSENGER_DETECTED", "title")), at(0))
    assert (title.message, title.process_name, title.url) == ("메신저 화면 표시: Instagram - Chrome", None, None)


def test_alert_for_broadcast_has_utc_offset():
    """WS로 보내는 알림 시각에 시간대를 붙임 (시간대 없는 에이전트 시각은 저장과 같이 UTC로 봄)"""
    naive = agent_service.AlertSchema(code="BLOCKED_URL", message="m", detected_at=datetime(2026, 10, 19, 6, 0, 0))
    assert agent_service.alert_for_broadcast(naive, at(0))["detected_at"] in ("2026-10-19T06:00:00Z", "2026-10-19T06:00:00+00:00")
    missing = agent_service.AlertSchema(code="BLOCKED_URL", message="m")
    assert agent_service.alert_for_broadcast(missing, at(5))["detected_at"] in ("2026-10-19T06:00:05Z", "2026-10-19T06:00:05+00:00")


def test_clear_danger_state():
    agent_service.register_danger(report("danger", [det("chatgpt.com/c/1")]), at(0))
    events_at(report("danger", [det("chatgpt.com/c/1")]), 0)
    agent_service.clear_danger_state("A")
    assert "A" not in agent_service._danger_holds
    assert "A" not in agent_service._on_screen_since and "A" not in agent_service._on_screen_alerted


# ─── 보고 수신 · 대시보드 API ─────────────────────────────────────

async def test_report_flow_holds_danger_and_records_on_screen_alert(db, broadcasts):
    """DANGER 보고 → 화면 표시 알림(빨강) 저장, 이어진 WARNING 보고에도 대시보드에는 DANGER 유지"""
    await agents.receive_report(report("warning", [det("chatgpt.com/c/1", fg=False)]), db)
    await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)
    await agents.receive_report(report("warning", [det("chatgpt.com/c/1", fg=False)]), db)

    agent = (await db.execute(select(Agent).where(Agent.agent_id == "A"))).scalar_one()
    assert agent.current_state == "warning"  # 저장은 원래 상태
    assert agent_service.summarize_agent(agent).current_state == "danger"  # 보일 때는 유지 적용
    states = (await db.execute(select(StatusReportDB.user_state).order_by(StatusReportDB.id))).scalars().all()
    assert states == ["warning", "danger", "warning"]

    alerts = (await db.execute(select(AlertLogDB).where(AlertLogDB.code == "ON_SCREEN"))).scalars().all()
    assert [(a.level, a.message) for a in alerts] == [("danger", "차단 사이트 화면 표시: chatgpt.com/c/1")]

    # 마지막 WS 업데이트도 DANGER와 이유, 구간 정보를 담음 (화면에서는 내렸으므로 danger_on_screen=False)
    last_update = [d for t, d in broadcasts if t == "agent_update"][-1]
    assert (last_update["current_state"], last_update["danger_reason"]) == ("danger", "chatgpt.com/c/1")
    assert last_update["danger_on_screen"] is False
    assert last_update["danger_since"] and last_update["danger_last_at"]
    assert "T" in last_update["danger_since"]  # 브라우저가 해석할 수 있는 ISO 형식

    # 상단 요약(REST·WS)도 원래 상태 WARNING이 아니라 유지 중인 DANGER로 셈
    for summary in (await dashboard.get_summary(db), await agent_service.get_dashboard_summary(db)):
        assert (summary.danger_count, summary.warning_count) == (1, 0)
    last_summary = [d for t, d in broadcasts if t == "summary_update"][-1]
    assert (last_summary["danger_count"], last_summary["warning_count"]) == (1, 0)


async def test_dashboard_shows_danger_reason(db, broadcasts):
    await agents.receive_report(report("danger", [det("claude.exe", "BLOCKED_PROCESS", "process")]), db)

    listed = await dashboard.list_agents(db)
    assert [(a.current_state, a.danger_reason, a.danger_on_screen) for a in listed] == [("danger", "claude.exe", True)]
    detail = await dashboard.get_agent_detail("A", db)
    assert detail.danger_reason == "claude.exe"
    assert detail.recent_alerts[0].level == "danger"
    # 화면 앞 탐지 항목에는 화면에 뜬 시각이 채워짐
    assert [d.on_screen_since is not None for d in detail.active_detections] == [True]

    assert (await dashboard.get_summary(db)).danger_count == 1
    assert (await agent_service.get_dashboard_summary(db)).danger_count == 1


async def test_ws_alerts_live_only_and_timezone_aware(db, broadcasts):
    """WS로 보내는 새 알림: 실시간 보고만, 시각에는 시간대 포함 (재전송 보고의 과거 알림은 보내지 않음)"""
    agent_alert = {"code": "BLOCKED_URL", "message": "차단된 URL 접근: chatgpt.com", "url": "chatgpt.com",
                   "detected_at": "2026-10-19T06:00:00"}
    await agents.receive_report(report("danger", [det("chatgpt.com")], alerts=[agent_alert]), db)
    live = [d for t, d in broadcasts if t == "agent_update"][-1]["alerts"]
    assert [a["code"] for a in live] == ["BLOCKED_URL", "ON_SCREEN"]
    assert all(a["detected_at"].endswith(("Z", "+00:00")) for a in live)

    await agents.receive_report(report("danger", with_detections=False, alerts=[agent_alert]), db)
    assert [d for t, d in broadcasts if t == "agent_update"][-1]["alerts"] == []


async def test_failed_save_does_not_consume_on_screen_alert(db, broadcasts, monkeypatch):
    """보고 저장이 실패하면 화면 표시 상태를 반영하지 않아, 다음 보고에서 같은 알림을 다시 남김"""
    await agents.receive_report(report("warning", [det("chatgpt.com/c/1", fg=False)]), db)
    original = agent_service.accumulate_blocked_site_stats

    async def boom(*args, **kwargs):
        raise RuntimeError("db error")

    monkeypatch.setattr(agent_service, "accumulate_blocked_site_stats", boom)
    with pytest.raises(RuntimeError):
        await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)
    await db.rollback()
    monkeypatch.setattr(agent_service, "accumulate_blocked_site_stats", original)

    await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)
    alerts = (await db.execute(select(AlertLogDB).where(AlertLogDB.code == "ON_SCREEN"))).scalars().all()
    assert len(alerts) == 1


async def test_failed_commit_does_not_consume_on_screen_alert(db, broadcasts, monkeypatch):
    """저장 확정(commit)이 실패해도 화면 표시 상태를 반영하지 않음 → 다음 보고에서 알림을 다시 남김"""
    await agents.receive_report(report("warning", [det("chatgpt.com/c/1", fg=False)]), db)
    original_commit = db.commit

    async def failing_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)
    monkeypatch.setattr(db, "commit", original_commit)
    await db.rollback()

    await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)
    alerts = (await db.execute(select(AlertLogDB).where(AlertLogDB.code == "ON_SCREEN"))).scalars().all()
    assert len(alerts) == 1


async def test_offline_clears_danger_state(db, broadcasts):
    await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)
    await agents.receive_offline(AgentOfflineRequest(agent_id="A"), db)

    assert "A" not in agent_service._danger_holds and "A" not in agent_service._on_screen_since
    agent = (await db.execute(select(Agent).where(Agent.agent_id == "A"))).scalar_one()
    assert agent_service.summarize_agent(agent).danger_reason is None


async def test_on_screen_alert_not_counted_as_blocked_site_access(db, broadcasts):
    """화면 표시 알림은 차단 사이트 접속 횟수 통계에 섞이지 않음 (접속은 원래 알림만)"""
    await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)

    rows = await agent_service.get_blocked_site_stats(db)
    chatgpt = [r for r in rows if r["url_pattern"] == "chatgpt.com"]
    assert chatgpt and all(r["access_count"] == 0 for r in chatgpt)
