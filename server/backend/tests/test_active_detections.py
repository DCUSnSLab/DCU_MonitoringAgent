"""현재 탐지 목록(active_detections) 수신·보관·상세 API 단위 테스트 (GEN-1149)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Agent
from app.routers import dashboard
from app.schemas import ActiveDetectionSchema, StatusReportRequest
from app.services import agent_service


@pytest.fixture(autouse=True)
def _clear_store():
    agent_service._active_detections.clear()
    yield
    agent_service._active_detections.clear()


def item(target="claude.exe", code="BLOCKED_PROCESS", source="process", fg=False):
    return ActiveDetectionSchema(code=code, target=target, source=source, is_foreground=fg)


# ─── 스키마 호환 ─────────────────────────────────────────────────

def test_report_accepts_active_detections():
    req = StatusReportRequest(agent_id="A", active_detections=[
        {"code": "BLOCKED_URL", "target": "ChatGPT - Chrome", "source": "title", "is_foreground": True},
    ])
    assert req.active_detections[0].target == "ChatGPT - Chrome"
    assert req.active_detections[0].is_foreground is True


def test_old_agent_payload_defaults_empty():
    """구버전 에이전트(필드 없음)는 빈 목록"""
    assert StatusReportRequest(agent_id="A").active_detections == []


def test_new_alert_field_ignored():
    """에이전트 알림의 신규 필드(window_title)는 서버에서 무시되고 오류가 나지 않음"""
    req = StatusReportRequest(agent_id="A", alerts=[
        {"code": "BLOCKED_URL", "message": "m", "url": None, "window_title": "ChatGPT - Chrome"},
    ])
    assert req.alerts[0].code == "BLOCKED_URL" and req.alerts[0].url is None


# ─── 메모리 보관 ─────────────────────────────────────────────────

def test_set_and_get():
    agent_service.set_active_detections("A", [item()])
    assert [d.target for d in agent_service.get_active_detections("A")] == ["claude.exe"]


def test_replaced_by_latest_report():
    agent_service.set_active_detections("A", [item("claude.exe"), item("ChatGPT.exe")])
    agent_service.set_active_detections("A", [item("ChatGPT.exe")])
    assert [d.target for d in agent_service.get_active_detections("A")] == ["ChatGPT.exe"]


def test_stale_returns_empty():
    """갱신이 오래되면(오프라인 등) 빈 목록"""
    agent_service.set_active_detections("A", [item()])
    later = datetime.now(timezone.utc) + timedelta(
        seconds=agent_service.ACTIVE_DETECTIONS_MAX_AGE_SECONDS + 1
    )
    assert agent_service.get_active_detections("A", now=later) == []


def test_clear_and_unknown():
    agent_service.set_active_detections("A", [item()])
    agent_service.clear_active_detections("A")
    assert agent_service.get_active_detections("A") == []
    assert agent_service.get_active_detections("NOPE") == []


# ─── 상세 API ────────────────────────────────────────────────────

async def test_agent_detail_includes_active_detections(db):
    db.add(Agent(agent_id="A", hostname="PC-521-01", is_online=True))
    await db.flush()
    agent_service.set_active_detections("A", [item(fg=True)])

    detail = await dashboard.get_agent_detail("A", db)

    assert [(d.target, d.is_foreground) for d in detail.active_detections] == [("claude.exe", True)]
