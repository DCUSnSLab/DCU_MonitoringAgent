"""창 제목 기반 차단 알림(URL 없음)의 차단 사이트 통계 집계 테스트 (GEN-1149)."""
from datetime import datetime, timezone

from app.models import Agent, AlertLogDB, StatusReportDB
from app.services import agent_service


def utc(h, mi=0):
    return datetime(2026, 10, 15, h, mi, tzinfo=timezone.utc)


async def add_agent(db, agent_id="A"):
    db.add(Agent(agent_id=agent_id, hostname="PC-521-01", lab_name="521", is_online=True))
    await db.flush()


async def add_alert(db, when, url=None, message="", agent_id="A"):
    rep = StatusReportDB(agent_id=agent_id, reported_at=when, user_state="warning")
    db.add(rep)
    await db.flush()
    db.add(AlertLogDB(
        report_id=rep.id, agent_id=agent_id, level="warning", code="BLOCKED_URL",
        message=message, url=url, detected_at=when,
    ))
    await db.flush()


def test_alert_site_from_title_message():
    assert agent_service._blocked_alert_site(None, "차단 사이트 창 감지: ChatGPT - Chrome") == "chatgpt.com"
    assert agent_service._blocked_alert_site(None, "차단 사이트 창 감지: Google Gemini - Chrome") == "gemini.google.com"
    assert agent_service._blocked_alert_site(None, "차단 사이트 창 감지: 대화 - Claude - Chrome") == "claude.ai"
    assert agent_service._blocked_alert_site(None, "알 수 없는 창") is None


def test_url_takes_priority_and_claude_pattern():
    assert agent_service._blocked_alert_site("https://claude.ai/chat/1", "무시") == "claude.ai"


async def test_title_alert_counted_with_url_alert(db):
    """같은 사이트의 URL 알림과 창 제목 알림은 한 행으로 합쳐 집계"""
    await add_agent(db)
    await add_alert(db, utc(9), url="https://chatgpt.com/c/abc", message="차단된 URL 접근")
    await add_alert(db, utc(10), message="차단 사이트 창 감지: ChatGPT - Chrome")

    rows = await agent_service.get_blocked_site_stats(db)

    assert [(r["url_pattern"], r["access_count"]) for r in rows] == [("chatgpt.com", 2)]


async def test_detail_includes_title_alert(db):
    await add_agent(db)
    await add_alert(db, utc(10), message="차단 사이트 창 감지: ChatGPT - Chrome")

    detail = await agent_service.get_blocked_site_detail(db, "A", "chatgpt.com")

    assert detail["access_count"] == 1
    assert detail["logs"][0]["url"] is None


async def test_timeline_total_matches_table(db):
    """사이트를 판정할 수 없는 알림은 표·시간대 분포 모두에서 제외해 합계 일치"""
    await add_agent(db)
    await add_alert(db, utc(9), url="https://chatgpt.com/", message="차단된 URL 접근")
    await add_alert(db, utc(10), message="차단 사이트 창 감지: Google Gemini - Chrome")
    await add_alert(db, utc(11), message="판정 불가 알림")

    rows = await agent_service.get_blocked_site_stats(db)
    timeline = await agent_service.get_blocked_access_timeline(db)

    assert sum(r["access_count"] for r in rows) == timeline["total"] == 2
