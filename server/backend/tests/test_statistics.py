"""차단 사이트 통계 서비스 단위 테스트.

핵심 회귀 방지:
  - 기능 배포 이전의 과거 차단 내역(alert_logs만 존재, BlockedSiteDailyStat 없음)도
    blocked-sites 통계에 반드시 나타나야 한다. (사용자가 보고한 "아무것도 안 나옴" 버그)
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Agent, AlertLogDB, BlockedSiteDailyStat, StatusReportDB
from app.schemas import StatusReportRequest
from app.services import agent_service


# ─── 헬퍼 ────────────────────────────────────────────────────────

def utc(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


async def add_agent(db, agent_id, hostname, lab=None, ip=None, online=True):
    db.add(Agent(
        agent_id=agent_id, hostname=hostname, lab_name=lab,
        ip_address=ip, is_online=online,
    ))
    await db.flush()


async def add_blocked_alert(db, agent_id, url, when, code="BLOCKED_URL"):
    """status_report + alert_log(BLOCKED_URL) 한 건 추가."""
    rep = StatusReportDB(agent_id=agent_id, reported_at=when, user_state="danger")
    db.add(rep)
    await db.flush()
    db.add(AlertLogDB(
        report_id=rep.id, agent_id=agent_id, level="danger", code=code,
        message="blocked", url=url, detected_at=when,
    ))
    await db.flush()


async def add_daily_stat(db, agent_id, pattern, d, active, background,
                         first=None, last=None):
    db.add(BlockedSiteDailyStat(
        agent_id=agent_id, url_pattern=pattern, stat_date=d,
        active_seconds=active, background_seconds=background,
        first_access_at=first, last_access_at=last,
    ))
    await db.flush()


def report_with_tabs(agent_id, tabs):
    """tabs: list[(url, is_active)] → StatusReportRequest."""
    return StatusReportRequest(
        agent_id=agent_id,
        browser_details={"chrome": {
            "is_running": True,
            "tab_count": len(tabs),
            "tabs": [{"tab_id": str(i), "title": "t", "url": u, "is_active": a}
                     for i, (u, a) in enumerate(tabs)],
        }},
    )


# ─── 패턴/키 매칭 ────────────────────────────────────────────────

def test_match_blocked_pattern():
    assert agent_service._match_blocked_pattern("https://chatgpt.com/c/1") == "chatgpt.com"
    assert agent_service._match_blocked_pattern("https://gemini.google.com/app") == "gemini.google.com"
    assert agent_service._match_blocked_pattern("https://naver.com") is None
    assert agent_service._match_blocked_pattern("") is None


def test_blocked_site_key_uses_pattern():
    assert agent_service._blocked_site_key("https://chatgpt.com/c/1") == "chatgpt.com"


def test_blocked_site_key_domain_fallback():
    # 설정에 없는 사이트는 호스트명으로 폴백
    assert agent_service._blocked_site_key("https://claude.ai/chat/abc") == "claude.ai"
    assert agent_service._blocked_site_key("http://www.example.com/x?y=1") == "example.com"
    assert agent_service._blocked_site_key("bad.site/path") == "bad.site"
    assert agent_service._blocked_site_key("") is None


# ─── 핵심 회귀: alert_logs만 있어도 통계가 나와야 한다 ───────────

async def test_blocked_sites_from_alert_logs_only(db):
    """BlockedSiteDailyStat가 전혀 없어도 alert_logs 기반으로 행이 나와야 한다."""
    await add_agent(db, "pc-501-01", "pc-501-01", lab="501", ip="10.0.0.1")
    base = utc(2026, 6, 1, 10)
    await add_blocked_alert(db, "pc-501-01", "https://chatgpt.com/c/1", base)
    await add_blocked_alert(db, "pc-501-01", "https://chatgpt.com/c/1", base + timedelta(minutes=1))
    await add_blocked_alert(db, "pc-501-01", "https://gemini.google.com/app", base + timedelta(minutes=2))

    rows = await agent_service.get_blocked_site_stats(
        db, date_from=date(2026, 6, 1), date_to=date(2026, 6, 1)
    )
    by_site = {r["url_pattern"]: r for r in rows}
    assert set(by_site) == {"chatgpt.com", "gemini.google.com"}

    cg = by_site["chatgpt.com"]
    assert cg["access_count"] == 2
    assert cg["active_seconds"] == 0          # 시간 데이터는 아직 없음
    assert cg["background_seconds"] == 0
    assert cg["hostname"] == "pc-501-01"
    assert cg["ip_address"] == "10.0.0.1"
    assert cg["lab_name"] == "501"
    assert cg["first_access_at"] == base
    assert cg["last_access_at"] == base + timedelta(minutes=1)
    assert by_site["gemini.google.com"]["access_count"] == 1


async def test_blocked_sites_enriched_with_time(db):
    """alert_logs(이벤트) + BlockedSiteDailyStat(시간)이 병합되어야 한다."""
    await add_agent(db, "a1", "a1", lab="501")
    base = utc(2026, 6, 1, 9)
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", base)
    await add_daily_stat(db, "a1", "chatgpt.com", date(2026, 6, 1),
                         active=120, background=45,
                         first=base, last=base + timedelta(hours=1))

    rows = await agent_service.get_blocked_site_stats(
        db, date_from=date(2026, 6, 1), date_to=date(2026, 6, 1)
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["url_pattern"] == "chatgpt.com"
    assert r["access_count"] == 1
    assert r["active_seconds"] == 120
    assert r["background_seconds"] == 45
    # 최초/최근 접속은 두 소스 중 가장 이른/늦은 값
    assert r["first_access_at"] == base
    assert r["last_access_at"] == base + timedelta(hours=1)


async def test_blocked_sites_time_only_no_alert(db):
    """알림 없이 시간만 누적된 경우(활성 시간만 있는 사이트)도 표시되어야 한다."""
    await add_agent(db, "a1", "a1", lab="501")
    await add_daily_stat(db, "a1", "chatgpt.com", date(2026, 6, 1),
                         active=30, background=0)
    rows = await agent_service.get_blocked_site_stats(
        db, date_from=date(2026, 6, 1), date_to=date(2026, 6, 1)
    )
    assert len(rows) == 1
    assert rows[0]["active_seconds"] == 30
    assert rows[0]["access_count"] == 0


async def test_blocked_sites_lab_filter(db):
    await add_agent(db, "a1", "a1", lab="501")
    await add_agent(db, "a2", "a2", lab="502")
    base = utc(2026, 6, 1, 8)
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", base)
    await add_blocked_alert(db, "a2", "https://chatgpt.com/x", base)

    rows = await agent_service.get_blocked_site_stats(db, lab="501")
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "a1"


async def test_blocked_sites_date_filter(db):
    await add_agent(db, "a1", "a1", lab="501")
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", utc(2026, 5, 1, 8))
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", utc(2026, 6, 1, 8))

    rows = await agent_service.get_blocked_site_stats(
        db, date_from=date(2026, 6, 1), date_to=date(2026, 6, 1)
    )
    assert len(rows) == 1
    assert rows[0]["access_count"] == 1  # 5월 건은 제외


async def test_blocked_sites_domain_fallback_appears(db):
    """설정 패턴에 없는 차단 사이트(claude.ai)도 누락 없이 표시되어야 한다."""
    await add_agent(db, "a1", "a1", lab="501")
    await add_blocked_alert(db, "a1", "https://claude.ai/chat/1", utc(2026, 6, 1, 8))
    rows = await agent_service.get_blocked_site_stats(
        db, date_from=date(2026, 6, 1), date_to=date(2026, 6, 1)
    )
    assert len(rows) == 1
    assert rows[0]["url_pattern"] == "claude.ai"
    assert rows[0]["access_count"] == 1


# ─── 상세 드릴다운 ───────────────────────────────────────────────

async def test_blocked_site_detail_logs_and_daily(db):
    """특정 에이전트×사이트 상세: 개별 로그 + 일자별 내역 + 시간 enrich."""
    await add_agent(db, "a1", "a1", lab="501", ip="10.0.0.5")
    d1 = utc(2026, 6, 1, 10)
    d2 = utc(2026, 6, 2, 11)
    await add_blocked_alert(db, "a1", "https://chatgpt.com/c/1", d1)
    await add_blocked_alert(db, "a1", "https://chatgpt.com/c/2", d1 + timedelta(minutes=1))
    await add_blocked_alert(db, "a1", "https://chatgpt.com/c/3", d2)
    # 다른 사이트(상세에서 제외되어야 함)
    await add_blocked_alert(db, "a1", "https://gemini.google.com/app", d1)
    # 활성/백그라운드 시간(6/2)
    await add_daily_stat(db, "a1", "chatgpt.com", date(2026, 6, 2), active=300, background=120)

    detail = await agent_service.get_blocked_site_detail(
        db, agent_id="a1", site="chatgpt.com",
        date_from=date(2026, 6, 1), date_to=date(2026, 6, 2),
    )
    assert detail["hostname"] == "a1"
    assert detail["ip_address"] == "10.0.0.5"
    assert detail["url_pattern"] == "chatgpt.com"
    assert detail["access_count"] == 3            # gemini 제외
    assert detail["active_seconds"] == 300
    assert detail["background_seconds"] == 120
    assert detail["first_access_at"] == d1
    assert detail["last_access_at"] == d2
    # 로그는 시간 내림차순
    assert detail["logs"][0]["detected_at"] == d2
    assert all(l["url"] and "chatgpt.com" in l["url"] for l in detail["logs"])
    # 일자별: 6/1(2건,시간0), 6/2(1건,active 300)
    daily = {d["date"]: d for d in detail["daily"]}
    assert daily["2026-06-01"]["access_count"] == 2
    assert daily["2026-06-01"]["active_seconds"] == 0
    assert daily["2026-06-02"]["access_count"] == 1
    assert daily["2026-06-02"]["active_seconds"] == 300


async def test_blocked_site_detail_log_limit(db):
    """로그가 limit을 넘으면 잘리되 log_total은 전체 건수."""
    await add_agent(db, "a1", "a1", lab="501")
    base = utc(2026, 6, 1, 9)
    for i in range(5):
        await add_blocked_alert(db, "a1", "https://chatgpt.com/x", base + timedelta(minutes=i))
    detail = await agent_service.get_blocked_site_detail(
        db, agent_id="a1", site="chatgpt.com", log_limit=3
    )
    assert detail["log_total"] == 5
    assert len(detail["logs"]) == 3


# ─── 활성/백그라운드 시간 누적 ──────────────────────────────────

async def test_accumulate_active_tab(db):
    await add_agent(db, "a1", "a1", lab="501")
    prev = datetime.now(timezone.utc) - timedelta(seconds=5)
    req = report_with_tabs("a1", [("https://chatgpt.com/c/1", True)])
    await agent_service.accumulate_blocked_site_stats(db, req, prev)

    row = (await db.execute(
        select(BlockedSiteDailyStat)
    )).scalars().first()
    assert row is not None
    assert row.url_pattern == "chatgpt.com"
    assert 3 <= row.active_seconds <= 7       # delta≈5 (실행 시간 여유)
    assert row.background_seconds == 0
    assert row.first_access_at is not None


async def test_accumulate_background_tab(db):
    await add_agent(db, "a1", "a1", lab="501")
    prev = datetime.now(timezone.utc) - timedelta(seconds=5)
    req = report_with_tabs("a1", [("https://gemini.google.com/app", False)])
    await agent_service.accumulate_blocked_site_stats(db, req, prev)

    row = (await db.execute(
        select(BlockedSiteDailyStat)
    )).scalars().first()
    assert row.url_pattern == "gemini.google.com"
    assert row.active_seconds == 0
    assert 3 <= row.background_seconds <= 7


async def test_accumulate_clamps_large_gap(db):
    """오프라인 공백(큰 간격)은 MAX_REPORT_GAP_SECONDS로 클램프되어야 한다."""
    await add_agent(db, "a1", "a1", lab="501")
    prev = datetime.now(timezone.utc) - timedelta(seconds=600)
    req = report_with_tabs("a1", [("https://chatgpt.com/x", True)])
    await agent_service.accumulate_blocked_site_stats(db, req, prev)
    row = (await db.execute(
        select(BlockedSiteDailyStat)
    )).scalars().first()
    assert row.active_seconds == agent_service.MAX_REPORT_GAP_SECONDS


async def test_accumulate_first_report_no_delta(db):
    """첫 보고(prev_last_seen=None)는 시간 누적 0, 단 접근 시각은 기록."""
    await add_agent(db, "a1", "a1", lab="501")
    req = report_with_tabs("a1", [("https://chatgpt.com/x", True)])
    await agent_service.accumulate_blocked_site_stats(db, req, None)
    row = (await db.execute(
        select(BlockedSiteDailyStat)
    )).scalars().first()
    assert row.active_seconds == 0
    assert row.background_seconds == 0
    assert row.last_access_at is not None


async def test_accumulate_ignores_non_blocked_tabs(db):
    await add_agent(db, "a1", "a1", lab="501")
    prev = datetime.now(timezone.utc) - timedelta(seconds=5)
    req = report_with_tabs("a1", [("https://naver.com", True)])
    await agent_service.accumulate_blocked_site_stats(db, req, prev)
    rows = (await db.execute(
        select(BlockedSiteDailyStat)
    )).scalars().all()
    assert rows == []


# ─── 타임라인 ────────────────────────────────────────────────────

async def test_timeline_hourly_and_daily(db):
    await add_agent(db, "a1", "a1", lab="501")
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", utc(2026, 6, 1, 9))
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", utc(2026, 6, 1, 9, 30))
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", utc(2026, 6, 2, 14))

    tl = await agent_service.get_blocked_access_timeline(
        db, date_from=date(2026, 6, 1), date_to=date(2026, 6, 2)
    )
    assert tl["total"] == 3
    hourly = {h["hour"]: h["count"] for h in tl["hourly"]}
    assert hourly[9] == 2
    assert hourly[14] == 1
    assert len(tl["hourly"]) == 24
    daily = {d["date"]: d["count"] for d in tl["daily"]}
    assert daily["2026-06-01"] == 2
    assert daily["2026-06-02"] == 1


# ─── 보관 정리 ───────────────────────────────────────────────────

async def test_cleanup_old_data(db):
    await add_agent(db, "a1", "a1", lab="501")
    old = datetime.now(timezone.utc) - timedelta(days=200)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", old)
    await add_blocked_alert(db, "a1", "https://chatgpt.com/x", recent)
    await add_daily_stat(db, "a1", "chatgpt.com",
                         (datetime.now(timezone.utc) - timedelta(days=200)).date(), 10, 0)
    await add_daily_stat(db, "a1", "chatgpt.com",
                         (datetime.now(timezone.utc) - timedelta(days=1)).date(), 20, 0)

    deleted = await agent_service.cleanup_old_data(db, retention_days=120)
    assert deleted["alert_logs"] == 1
    assert deleted["status_reports"] == 1
    assert deleted["blocked_site_daily_stats"] == 1

    remaining = await agent_service.get_blocked_site_stats(db)
    # 최근 데이터만 남음
    assert sum(r["access_count"] for r in remaining) == 1
