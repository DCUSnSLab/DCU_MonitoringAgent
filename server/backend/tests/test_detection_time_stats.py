"""주소창·창 제목으로 탐지한 차단 사이트의 활성/백그라운드 시간 누적 테스트 (GEN-1149).

CDP 없는 PC는 보고서에 크롬 탭 목록이 없으므로, 에이전트의 현재 탐지 목록(active_detections)으로
시간을 누적한다. 같은 사이트는 보고 1건당 한 번만 센다.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Agent, BlockedSiteDailyStat
from app.schemas import StatusReportRequest
from app.services import agent_service


async def add_agent(db, agent_id="A"):
    db.add(Agent(agent_id=agent_id, hostname="PC-521-01", lab_name="521", is_online=True))
    await db.flush()


def detection(target, source="url", fg=False, code="BLOCKED_URL"):
    return {"code": code, "target": target, "source": source, "is_foreground": fg}


def report(detections=None, tabs=None, agent_id="A"):
    """detections: active_detections 목록(None이면 필드 없음), tabs: [(url, is_active)] CDP 탭."""
    payload = {"agent_id": agent_id}
    if detections is not None:
        payload["active_detections"] = detections
    if tabs:
        payload["browser_details"] = {"chrome": {
            "is_running": True,
            "debug_mode": True,
            "tab_count": len(tabs),
            "tabs": [{"tab_id": str(i), "title": "t", "url": u, "is_active": a}
                     for i, (u, a) in enumerate(tabs)],
        }}
    return StatusReportRequest(**payload)


async def accumulate(db, req, gap_seconds=5):
    prev = datetime.now(timezone.utc) - timedelta(seconds=gap_seconds)
    await agent_service.accumulate_blocked_site_stats(db, req, prev)
    rows = (await db.execute(select(BlockedSiteDailyStat))).scalars().all()
    return {r.url_pattern: r for r in rows}


# ─── 주소창·창 제목 탐지 ──────────────────────────────────────────

async def test_address_bar_detection_on_screen_is_active(db):
    """주소창으로 탐지한 사이트가 화면 앞이면 활성 시간 누적 (주소창 표시값엔 https:// 없음)"""
    await add_agent(db)
    rows = await accumulate(db, report([detection("chatgpt.com/c/68ef", fg=True)]))
    assert set(rows) == {"chatgpt.com"}
    assert 3 <= rows["chatgpt.com"].active_seconds <= 7
    assert rows["chatgpt.com"].background_seconds == 0


async def test_address_bar_detection_behind_is_background(db):
    await add_agent(db)
    rows = await accumulate(db, report([detection("gemini.google.com/app")]))
    assert rows["gemini.google.com"].active_seconds == 0
    assert 3 <= rows["gemini.google.com"].background_seconds <= 7


async def test_title_detection_mapped_to_site(db):
    """창 제목으로 탐지한 항목은 제목 키워드로 사이트를 정해 누적"""
    await add_agent(db)
    rows = await accumulate(db, report([detection("Google Gemini - Chrome", source="title", fg=True)]))
    assert 3 <= rows["gemini.google.com"].active_seconds <= 7


async def test_unknown_title_ignored(db):
    """사이트를 정할 수 없는 창 제목은 누적하지 않음"""
    await add_agent(db)
    rows = await accumulate(db, report([detection("알 수 없는 창 - Chrome", source="title")]))
    assert rows == {}


async def test_unconfigured_site_uses_host(db):
    """서버 차단 패턴에 없는 사이트는 알림 집계와 같은 호스트명 키로 누적"""
    await add_agent(db)
    rows = await accumulate(db, report([detection("www.perplexity.ai/search/abc")]))
    assert set(rows) == {"perplexity.ai"}


# ─── 같은 사이트 한 번만 ──────────────────────────────────────────

async def test_same_site_in_several_windows_counted_once(db):
    """같은 사이트 창이 여러 개여도 보고 1건당 한 번, 하나라도 화면 앞이면 활성"""
    await add_agent(db)
    rows = await accumulate(db, report([
        detection("chatgpt.com/c/1"),
        detection("chatgpt.com/c/2", fg=True),
        detection("ChatGPT - Chrome", source="title"),
    ]))
    row = rows["chatgpt.com"]
    assert 3 <= row.active_seconds <= 7
    assert row.background_seconds == 0


async def test_cdp_tab_and_detection_same_site_counted_once(db):
    """CDP 탭과 현재 탐지 목록에 같은 사이트가 있어도 한 번만 누적"""
    await add_agent(db)
    rows = await accumulate(db, report(
        detections=[detection("https://chatgpt.com/c/1", fg=True)],
        tabs=[("https://chatgpt.com/c/1", True)],
    ))
    row = rows["chatgpt.com"]
    assert 3 <= row.active_seconds <= 7
    assert row.background_seconds == 0


async def test_cdp_background_tab_and_foreground_window(db):
    """CDP 탭은 뒤에 있지만 다른 브라우저 창(주소창 탐지)이 화면 앞이면 활성으로 한 번만"""
    await add_agent(db)
    rows = await accumulate(db, report(
        detections=[detection("claude.ai/new", fg=True)],
        tabs=[("https://claude.ai/chat/1", False)],
    ))
    row = rows["claude.ai"]
    assert 3 <= row.active_seconds <= 7
    assert row.background_seconds == 0


# ─── 제외 대상 · 호환 ─────────────────────────────────────────────

async def test_messenger_and_process_detections_ignored(db):
    """차단 사이트 통계는 BLOCKED_URL만 대상 (메신저·차단 프로그램 제외)"""
    await add_agent(db)
    rows = await accumulate(db, report([
        detection("instagram.com", code="MESSENGER_DETECTED", fg=True),
        detection("claude.exe", source="process", code="BLOCKED_PROCESS", fg=True),
    ]))
    assert rows == {}


async def test_report_without_detections_keeps_cdp_only(db):
    """필드가 없는 보고(구버전 에이전트·버퍼 재전송)는 기존처럼 CDP 탭만으로 누적"""
    await add_agent(db)
    assert await accumulate(db, report()) == {}
    rows = await accumulate(db, report(tabs=[("https://chatgpt.com/c/1", False)]))
    assert set(rows) == {"chatgpt.com"}


async def test_detection_first_report_records_access_without_time(db):
    """첫 보고(직전 보고 없음)는 시간 0이지만 접근 시각은 기록"""
    await add_agent(db)
    await agent_service.accumulate_blocked_site_stats(db, report([detection("claude.ai/new", fg=True)]), None)
    row = (await db.execute(select(BlockedSiteDailyStat))).scalars().one()
    assert (row.url_pattern, row.active_seconds, row.background_seconds) == ("claude.ai", 0, 0)
    assert row.first_access_at is not None and row.last_access_at is not None


async def test_detection_time_shows_in_blocked_site_stats(db):
    """누적된 시간이 차단 사이트 통계 표에 나타남"""
    await add_agent(db)
    await accumulate(db, report([detection("chatgpt.com/c/1", fg=True)]))
    stats = await agent_service.get_blocked_site_stats(db)
    row = next(r for r in stats if r["url_pattern"] == "chatgpt.com")
    assert row["active_seconds"] > 0
