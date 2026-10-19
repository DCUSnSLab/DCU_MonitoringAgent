"""DANGER 유지 · 화면 표시 알림 테스트 (GEN-1149).

DANGER(차단 대상을 화면 맨 앞에 띄움)는 마지막 DANGER 보고 후 일정 시간 유지하고 그 이유를 보여 주며,
차단 대상이 화면 앞으로 나온 순간은 빨간(level=danger) '화면 표시' 알림으로 남긴다.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Agent, AlertLogDB, StatusReportDB
from app.routers import agents, dashboard
from app.schemas import ActiveDetectionSchema, StatusReportRequest
from app.services import agent_service

T0 = datetime(2026, 10, 19, 6, 0, 0, tzinfo=timezone.utc)
HOLD = settings.danger_hold_seconds
COOLDOWN = settings.on_screen_alert_cooldown_seconds


@pytest.fixture(autouse=True)
def _clear_memory():
    for store in (agent_service._danger_holds, agent_service._on_screen_prev,
                  agent_service._on_screen_alerted, agent_service._active_detections):
        store.clear()
    yield
    for store in (agent_service._danger_holds, agent_service._on_screen_prev,
                  agent_service._on_screen_alerted, agent_service._active_detections):
        store.clear()


def det(target, code="BLOCKED_URL", source="url", fg=True):
    return {"code": code, "target": target, "source": source, "is_foreground": fg}


def report(state="danger", detections=(), fg_window=None, with_detections=True, agent_id="A"):
    payload = {"agent_id": agent_id, "hostname": "PC-521-01", "user_state": state, "foreground_window": fg_window}
    if with_detections:
        payload["active_detections"] = list(detections)
    return StatusReportRequest(**payload)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


# ─── DANGER 유지 ─────────────────────────────────────────────────

def test_danger_held_after_target_leaves_screen():
    """DANGER 후 차단 대상을 내려도 유지 시간 동안 DANGER와 이유 유지"""
    assert agent_service.apply_danger_hold(report("danger", [det("chatgpt.com/c/1")]), at(0)) == (
        "danger", "chatgpt.com/c/1"
    )
    assert agent_service.apply_danger_hold(report("warning", [det("chatgpt.com/c/1", fg=False)]), at(HOLD - 1)) == (
        "danger", "chatgpt.com/c/1"
    )
    assert agent_service.apply_danger_hold(report("safe"), at(HOLD)) == ("safe", None)
    assert "A" not in agent_service._danger_holds


def test_new_danger_extends_hold_with_latest_reason():
    agent_service.apply_danger_hold(report("danger", [det("chatgpt.com/c/1")]), at(0))
    agent_service.apply_danger_hold(report("danger", [det("KakaoTalk.exe", "MESSENGER_DETECTED", "process")]), at(8))
    assert agent_service.apply_danger_hold(report("warning"), at(8 + HOLD - 1)) == ("danger", "KakaoTalk.exe")


def test_reason_lists_on_screen_targets_only():
    """이유는 화면 앞 항목만 (최대 2개 + 나머지 건수), 뒤에 있는 항목은 제외"""
    req = report("danger", [
        det("claude.exe", "BLOCKED_PROCESS", "process"),
        det("chatgpt.com/c/1"),
        det("instagram.com", "MESSENGER_DETECTED"),
        det("gemini.google.com/app", fg=False),
    ])
    assert agent_service.apply_danger_hold(req, at(0))[1] == "claude.exe, chatgpt.com/c/1 외 1건"


def test_reason_falls_back_to_foreground_window():
    req = report("danger", [det("chatgpt.com/c/1", fg=False)], fg_window="ChatGPT - Chrome")
    assert agent_service.apply_danger_hold(req, at(0))[1] == "ChatGPT - Chrome"


def test_buffered_report_does_not_start_hold():
    """현재 탐지 목록이 없는 재전송 보고의 DANGER로는 유지를 시작하지 않음"""
    assert agent_service.apply_danger_hold(report("danger", with_detections=False), at(0)) == ("danger", None)
    assert agent_service.apply_danger_hold(report("warning", with_detections=False), at(1)) == ("warning", None)


def test_get_danger_reason_expires():
    agent_service.apply_danger_hold(report("danger", [det("chatgpt.com/c/1")]), at(0))
    assert agent_service.get_danger_reason("A", at(HOLD - 1)) == "chatgpt.com/c/1"
    assert agent_service.get_danger_reason("A", at(HOLD)) is None
    assert agent_service.get_danger_reason("NOPE", at(0)) is None


def test_hold_is_per_agent():
    agent_service.apply_danger_hold(report("danger", [det("chatgpt.com/c/1")], agent_id="A"), at(0))
    assert agent_service.apply_danger_hold(report("warning", agent_id="B"), at(1)) == ("warning", None)


# ─── 화면 표시 알림 ──────────────────────────────────────────────

def targets(events):
    return [(e.code, e.target) for e in events]


def test_event_when_target_comes_on_screen():
    """뒤에 있던 대상이 화면 앞으로 나온 순간에만 알림 1건"""
    assert targets(agent_service.take_on_screen_events(report("warning", [det("chatgpt.com/c/1", fg=False)]), at(0))) == []
    assert targets(agent_service.take_on_screen_events(report("danger", [det("chatgpt.com/c/1")]), at(3))) == [
        ("BLOCKED_URL", "chatgpt.com/c/1")
    ]
    # 계속 화면 앞이면 추가 알림 없음
    assert agent_service.take_on_screen_events(report("danger", [det("chatgpt.com/c/1")]), at(6)) == []


def test_repeated_toggle_alerted_once_per_cooldown():
    """화면 앞·뒤를 반복해도 같은 대상은 쿨다운 동안 1건, 쿨다운 후 다시 나오면 다시 1건"""
    on, off = report("danger", [det("chatgpt.com/c/1")]), report("warning", [det("chatgpt.com/c/1", fg=False)])
    assert len(agent_service.take_on_screen_events(on, at(0))) == 1
    agent_service.take_on_screen_events(off, at(3))
    assert agent_service.take_on_screen_events(on, at(6)) == []
    agent_service.take_on_screen_events(off, at(COOLDOWN))
    assert len(agent_service.take_on_screen_events(on, at(COOLDOWN + 3))) == 1


def test_each_new_target_alerted():
    agent_service.take_on_screen_events(report("danger", [det("chatgpt.com/c/1")]), at(0))
    events = agent_service.take_on_screen_events(
        report("danger", [det("chatgpt.com/c/1"), det("KakaoTalk.exe", "MESSENGER_DETECTED", "process")]), at(3)
    )
    assert targets(events) == [("MESSENGER_DETECTED", "KakaoTalk.exe")]


def test_old_cooldown_records_pruned():
    """쿨다운이 지난 기록은 정리 (대화마다 바뀌는 URL로 메모리가 계속 쌓이지 않게)"""
    agent_service.take_on_screen_events(report("danger", [det("chatgpt.com/c/1")]), at(0))
    agent_service.take_on_screen_events(report("danger", [det("chatgpt.com/c/2")]), at(COOLDOWN + 1))
    assert set(agent_service._on_screen_alerted["A"]) == {("BLOCKED_URL", "chatgpt.com/c/2")}


def test_buffered_report_ignored_for_events():
    """현재 탐지 목록 없는 보고는 알림을 만들지 않고 직전 화면 상태도 바꾸지 않음"""
    agent_service.take_on_screen_events(report("danger", [det("chatgpt.com/c/1")]), at(0))
    assert agent_service.take_on_screen_events(report("danger", with_detections=False), at(3)) == []
    assert agent_service._on_screen_prev["A"] == {("BLOCKED_URL", "chatgpt.com/c/1")}


def test_on_screen_alert_fields():
    url_alert = agent_service.on_screen_alert(ActiveDetectionSchema(**det("chatgpt.com/c/1")), at(0))
    assert (url_alert.level, url_alert.code, url_alert.message, url_alert.url, url_alert.process_name) == (
        "danger", "ON_SCREEN", "차단 사이트 화면 표시: chatgpt.com/c/1", "chatgpt.com/c/1", None
    )
    proc = agent_service.on_screen_alert(ActiveDetectionSchema(**det("claude.exe", "BLOCKED_PROCESS", "process")), at(0))
    assert (proc.message, proc.process_name, proc.url) == ("차단 프로그램 화면 표시: claude.exe", "claude.exe", None)
    title = agent_service.on_screen_alert(ActiveDetectionSchema(**det("Instagram - Chrome", "MESSENGER_DETECTED", "title")), at(0))
    assert (title.message, title.process_name, title.url) == ("메신저 화면 표시: Instagram - Chrome", None, None)


def test_clear_danger_state():
    agent_service.apply_danger_hold(report("danger", [det("chatgpt.com/c/1")]), at(0))
    agent_service.take_on_screen_events(report("danger", [det("chatgpt.com/c/1")]), at(0))
    agent_service.clear_danger_state("A")
    assert "A" not in agent_service._danger_holds
    assert "A" not in agent_service._on_screen_prev and "A" not in agent_service._on_screen_alerted


# ─── 보고 수신 · 대시보드 API ─────────────────────────────────────

async def test_report_flow_holds_danger_and_records_on_screen_alert(db):
    """DANGER 보고 → 화면 표시 알림(빨강) 저장, 이어진 WARNING 보고에도 대시보드 상태는 DANGER 유지"""
    await agents.receive_report(report("warning", [det("chatgpt.com/c/1", fg=False)]), db)
    await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)
    await agents.receive_report(report("warning", [det("chatgpt.com/c/1", fg=False)]), db)

    agent = (await db.execute(select(Agent).where(Agent.agent_id == "A"))).scalar_one()
    assert agent.current_state == "danger"
    # 보고서 기록에는 에이전트가 보낸 원래 상태
    states = (await db.execute(select(StatusReportDB.user_state).order_by(StatusReportDB.id))).scalars().all()
    assert states == ["warning", "danger", "warning"]

    alerts = (await db.execute(select(AlertLogDB).where(AlertLogDB.code == "ON_SCREEN"))).scalars().all()
    assert [(a.level, a.message) for a in alerts] == [("danger", "차단 사이트 화면 표시: chatgpt.com/c/1")]


async def test_dashboard_shows_danger_reason(db):
    await agents.receive_report(report("danger", [det("claude.exe", "BLOCKED_PROCESS", "process")]), db)

    listed = await dashboard.list_agents(db)
    assert [(a.current_state, a.danger_reason) for a in listed] == [("danger", "claude.exe")]
    detail = await dashboard.get_agent_detail("A", db)
    assert detail.danger_reason == "claude.exe"
    assert detail.recent_alerts[0].level == "danger"

    summary = await dashboard.get_summary(db)
    assert summary.danger_count == 1


async def test_on_screen_alert_not_counted_as_blocked_site_access(db):
    """화면 표시 알림은 차단 사이트 접속 횟수 통계에 섞이지 않음 (접속은 원래 알림만)"""
    db.add(Agent(agent_id="A", hostname="PC-521-01", is_online=True))
    await db.flush()
    await agents.receive_report(report("danger", [det("chatgpt.com/c/1")]), db)

    rows = await agent_service.get_blocked_site_stats(db)
    assert all(r["access_count"] == 0 for r in rows if r["url_pattern"] == "chatgpt.com")
