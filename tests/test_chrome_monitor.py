"""
Phase 3: 크롬 모니터링 단위 테스트
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
from agent.config import ChromeConfig
from agent.monitors.chrome_monitor import ChromeMonitor
from agent.models import ChromeInfo, ChromeTabInfo, Alert


# ─── ChromeMonitor 테스트 ─────────────────────────────────────────


class TestChromeMonitor:

    def setup_method(self):
        self.config = ChromeConfig(
            enabled=True,
            debug_port=9222,
            blocked_urls=["chat.openai.com", "chatgpt.com", "claude.ai"]
        )
        self.monitor = ChromeMonitor(self.config)

    def test_collect_when_disabled(self):
        """모니터링 비활성화 시 빈 ChromeInfo 반환 확인"""
        config = ChromeConfig(enabled=False)
        monitor = ChromeMonitor(config)
        info, alerts = monitor.collect()
        assert info.is_running == False
        assert len(alerts) == 0

    def test_cdp_available_check(self):
        """CDP 가용 여부 확인 메서드가 정상 동작함을 확인"""
        result = self.monitor.is_cdp_available()
        assert isinstance(result, bool)
        print(f"\n  CDP 가용 여부: {result}")

    def test_collect_chrome_status(self):
        """크롬 실행 상태 수집 확인"""
        info, alerts = self.monitor.collect()
        assert isinstance(info, ChromeInfo)
        assert isinstance(alerts, list)
        assert isinstance(info.is_running, bool)
        print(f"\n  크롬 실행 중: {info.is_running}")
        print(f"  CDP 모드: {info.debug_mode}")
        print(f"  탭 수: {info.tab_count}")
        for tab in info.tabs:
            print(f"    [{tab.tab_id[:8]}] {tab.title[:40]} | {tab.url[:60]}")

    def test_blocked_url_detection(self):
        """차단 URL 감지 알림 생성 확인"""
        tabs = [
            ChromeTabInfo(
                tab_id="abc1",
                title="ChatGPT",
                url="https://chat.openai.com/thread/test"
            ),
            ChromeTabInfo(
                tab_id="abc2",
                title="Google",
                url="https://www.google.com"
            ),
        ]
        alerts = self.monitor._check_blocked_urls(tabs)
        # chat.openai.com은 차단 목록에 있으므로 알림 1개
        assert len(alerts) == 1
        assert alerts[0].code == "BLOCKED_URL"
        assert "chat.openai.com" in alerts[0].url

    def test_multiple_blocked_urls(self):
        """여러 차단 URL 동시 감지 확인"""
        tabs = [
            ChromeTabInfo(tab_id="t1", title="ChatGPT", url="https://chat.openai.com/"),
            ChromeTabInfo(tab_id="t2", title="Claude", url="https://claude.ai/chat"),
            ChromeTabInfo(tab_id="t3", title="Google", url="https://www.google.com"),
        ]
        alerts = self.monitor._check_blocked_urls(tabs)
        assert len(alerts) == 2  # chatgpt + claude 2개

    def test_no_alert_for_allowed_url(self):
        """허용 URL에는 알림이 없음을 확인"""
        tabs = [
            ChromeTabInfo(tab_id="t1", title="Google", url="https://www.google.com"),
            ChromeTabInfo(tab_id="t2", title="YouTube", url="https://www.youtube.com"),
        ]
        alerts = self.monitor._check_blocked_urls(tabs)
        assert len(alerts) == 0

    def test_is_chrome_running(self):
        """크롬 실행 여부 감지 확인"""
        result = self.monitor._is_chrome_running()
        assert isinstance(result, bool)
        print(f"\n  크롬 실행 중: {result}")

    def test_cdp_setup_guide(self):
        """CDP 설정 가이드 문자열 생성 확인"""
        guide = self.monitor.get_cdp_setup_guide()
        assert "9222" in guide
        assert "remote-debugging-port" in guide
        print(f"\n  설정 가이드:\n{guide}")


# ─── 통합 실행 ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Phase 3 수동 테스트 - Chrome CDP Monitor")
    print("=" * 60)

    config = ChromeConfig(
        enabled=True,
        debug_port=9222,
        blocked_urls=["chat.openai.com", "chatgpt.com", "claude.ai", "gemini.google.com"]
    )
    monitor = ChromeMonitor(config)

    print(f"\n[CDP 설정 가이드]")
    print(monitor.get_cdp_setup_guide())

    print(f"[CDP 연결 상태]")
    available = monitor.is_cdp_available()
    print(f"  CDP 가용: {available}")

    print(f"\n[크롬 모니터링 결과]")
    info, alerts = monitor.collect()
    print(f"  크롬 실행 중: {info.is_running}")
    print(f"  CDP 모드: {info.debug_mode}")
    print(f"  탭 수: {info.tab_count}")

    if info.tabs:
        print(f"\n  탭 목록:")
        for tab in info.tabs:
            active_mark = "▶" if tab.is_active else " "
            print(f"    {active_mark} [{tab.tab_id[:8]}] {tab.title[:50]}")
            print(f"       URL: {tab.url[:80]}")

    if alerts:
        print(f"\n  [!] 알림 {len(alerts)}개:")
        for a in alerts:
            print(f"    [{a.level.upper()}] {a.message}")
    else:
        print(f"\n  알림 없음")

    if not available and info.is_running:
        print(f"\n[주의] 크롬이 실행 중이지만 CDP가 비활성화되어 있습니다.")
        print(f"위 설정 가이드를 참고하여 크롬 바로가기를 수정해주세요.")
