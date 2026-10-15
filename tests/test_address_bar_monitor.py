"""
주소창 URL 읽기(UI Automation) 탐지 단위 테스트 (GEN-1149)

실제 UI Automation 대신 가짜 객체를 넣어, 주소창 값에 따른 판정·캐시·판정 순서를 검증한다.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from agent.analyzers.state_analyzer import StateAnalyzer
from agent.analyzers.title_detector import WindowTitleDetector
from agent.collectors.data_collector import DataCollector
from agent.config import AddressBarConfig, AppConfig, ChromeConfig, MessengerConfig, WindowTitleConfig
from agent.models import ChromeInfo, StatusReport, UserState
from agent.monitors.address_bar_monitor import AddressBarMonitor
from agent.monitors.window_monitor import VisibleWindow

FOCUS, VALUE = "focus", "value"  # 가짜 UI Automation 속성 ID
FAKE_MOD = SimpleNamespace(
    TreeScope_Descendants=4,
    UIA_HasKeyboardFocusPropertyId=FOCUS,
    UIA_ValueValuePropertyId=VALUE,
)
BLOCKED_URLS = ["chat.openai.com", "chatgpt.com", "gemini.google.com", "claude.ai"]


class FakeOmnibox:
    def __init__(self, value, focused=False):
        self.value, self.focused, self.dead = value, focused, False

    def GetCurrentPropertyValue(self, prop):
        if self.dead:
            raise OSError("UIA_E_ELEMENTNOTAVAILABLE")
        return self.focused if prop == FOCUS else self.value


class FakeUIA:
    """hwnd별 주소창 요소를 돌려주는 가짜 IUIAutomation (없으면 None = 못 찾음)"""

    def __init__(self, omniboxes):
        self.omniboxes = omniboxes
        self.find_calls = []

    def ElementFromHandle(self, hwnd):
        uia = self

        class Root:
            def FindFirst(self, scope, condition):
                uia.find_calls.append(hwnd)
                return uia.omniboxes.get(hwnd)

        return Root()


def win(title, process_name="chrome.exe", pid=100, hwnd=1, fg=False):
    return VisibleWindow(hwnd=hwnd, pid=pid, process_name=process_name, title=title, is_foreground=fg)


def make_monitor(omniboxes, address_bar=None, messenger=None):
    monitor = AddressBarMonitor(
        address_bar or AddressBarConfig(),
        ChromeConfig(blocked_urls=BLOCKED_URLS),
        messenger or MessengerConfig(),
    )
    monitor._uia_mod, monitor._uia = FAKE_MOD, FakeUIA(omniboxes)
    return monitor


@pytest.fixture(autouse=True)
def _not_hung():
    with patch.object(AddressBarMonitor, "_is_hung", return_value=False):
        yield


# ─── 주소창 URL 판정 ─────────────────────────────────────────────


class TestAddressBarDetection:

    def test_chatgpt_conversation_detected_by_url(self):
        """대화 제목으로 창 제목이 바뀐 ChatGPT도 주소창 URL로 탐지"""
        monitor = make_monitor({1: FakeOmnibox("chatgpt.com/c/68ef12ab")})
        urls, alerts = monitor.collect([win("React 상태 관리 질문 - Chrome", fg=True)])

        assert urls == {1: "chatgpt.com/c/68ef12ab"}
        assert len(alerts) == 1
        a = alerts[0]
        assert (a.code, a.url, a.window_title, a.window_is_foreground) == (
            "BLOCKED_URL", "chatgpt.com/c/68ef12ab", "React 상태 관리 질문 - Chrome", True
        )

    def test_messenger_url(self):
        monitor = make_monitor({1: FakeOmnibox("instagram.com/direct/inbox/")})
        _, alerts = monitor.collect([win("Instagram • 받은 메시지함 - Chrome")])
        assert [a.code for a in alerts] == ["MESSENGER_DETECTED"]

    def test_edge_and_whale_supported(self):
        monitor = make_monitor({1: FakeOmnibox("claude.ai/new"), 2: FakeOmnibox("gemini.google.com/app")})
        _, alerts = monitor.collect([
            win("Claude - Microsoft Edge", "msedge.exe", hwnd=1),
            win("Gemini - Whale", "whale.exe", pid=200, hwnd=2),
        ])
        assert [a.url for a in alerts] == ["claude.ai/new", "gemini.google.com/app"]

    def test_case_insensitive(self):
        monitor = make_monitor({1: FakeOmnibox("ChatGPT.com/")})
        _, alerts = monitor.collect([win("ChatGPT - Chrome")])
        assert len(alerts) == 1

    def test_allowed_url_read_but_no_alert(self):
        """'Claude Monet' 문서처럼 제목 키워드만 겹치는 페이지는 URL로 판정해 알림 없음"""
        monitor = make_monitor({1: FakeOmnibox("ko.wikipedia.org/wiki/클로드_모네")})
        urls, alerts = monitor.collect([win("Claude Monet - 위키백과 - Chrome")])
        assert urls == {1: "ko.wikipedia.org/wiki/클로드_모네"}
        assert alerts == []

    def test_messenger_disabled(self):
        monitor = make_monitor({1: FakeOmnibox("instagram.com/")}, messenger=MessengerConfig(enabled=False))
        _, alerts = monitor.collect([win("Instagram - Chrome")])
        assert alerts == []


# ─── 주소창 읽기 조건 ─────────────────────────────────────────────


class TestAddressBarReading:

    def test_focused_omnibox_skipped(self):
        """입력 중인 주소창은 실제 주소가 아닐 수 있어 읽지 않음 (창 제목 판정으로 넘김)"""
        monitor = make_monitor({1: FakeOmnibox("chatgpt.com", focused=True)})
        assert monitor.read_urls([win("새 탭 - Chrome")]) == {}

    def test_empty_omnibox_skipped(self):
        monitor = make_monitor({1: FakeOmnibox("")})
        assert monitor.read_urls([win("새 시크릿 탭 - Chrome")]) == {}

    def test_only_target_browsers(self):
        """대상 브라우저가 아닌 창(Firefox, 메모장)과 skip_pids 창은 읽지 않음"""
        monitor = make_monitor({1: FakeOmnibox("chatgpt.com"), 2: FakeOmnibox("chatgpt.com")})
        windows = [
            win("ChatGPT — Mozilla Firefox", "firefox.exe", pid=1, hwnd=3),
            win("메모 - 메모장", "notepad.exe", pid=2, hwnd=4),
            win("ChatGPT - Chrome", pid=5, hwnd=1),
            win("ChatGPT - Chrome", pid=6, hwnd=2),
        ]
        assert monitor.read_urls(windows, skip_pids={6}) == {1: "chatgpt.com"}
        assert monitor._uia.find_calls == [1]

    def test_omnibox_element_cached(self):
        """주소창 요소는 한 번만 찾고 이후 주기에는 값만 읽음"""
        omnibox = FakeOmnibox("todomate.net")
        monitor = make_monitor({1: omnibox})
        monitor.read_urls([win("todo mate - Chrome")])
        omnibox.value = "chatgpt.com"
        assert monitor.read_urls([win("ChatGPT - Chrome")]) == {1: "chatgpt.com"}
        assert monitor._uia.find_calls == [1]

    def test_stale_element_refound(self):
        """요소가 무효해지면 그 주기는 건너뛰고 다음 주기에 다시 찾음"""
        old = FakeOmnibox("chatgpt.com")
        monitor = make_monitor({1: old})
        monitor.read_urls([win("ChatGPT - Chrome")])

        old.dead = True
        monitor._uia.omniboxes[1] = FakeOmnibox("claude.ai")
        assert monitor.read_urls([win("Claude - Chrome")]) == {}
        assert monitor.read_urls([win("Claude - Chrome")]) == {1: "claude.ai"}
        assert monitor._uia.find_calls == [1, 1]

    def test_window_without_omnibox_retried_later(self):
        """주소창이 없는 창(앱 창·팝업)은 RETRY_SECONDS 동안 다시 찾지 않음"""
        monitor = make_monitor({})
        windows = [win("ChatGPT", hwnd=7)]
        with patch("agent.monitors.address_bar_monitor.time.monotonic", return_value=1000.0):
            assert monitor.read_urls(windows) == {}
            assert monitor.read_urls(windows) == {}
        assert monitor._uia.find_calls == [7]

        later = 1000.0 + AddressBarMonitor.RETRY_SECONDS
        with patch("agent.monitors.address_bar_monitor.time.monotonic", return_value=later):
            monitor.read_urls(windows)
        assert monitor._uia.find_calls == [7, 7]

    def test_closed_window_forgotten(self):
        monitor = make_monitor({1: FakeOmnibox("chatgpt.com")})
        monitor.read_urls([win("ChatGPT - Chrome")])
        monitor.read_urls([])
        assert monitor._omnibox_cache == {} and monitor._missing_since == {}

    def test_hung_window_skipped(self):
        """응답 없는 창은 UI Automation 호출 없이 건너뜀"""
        monitor = make_monitor({1: FakeOmnibox("chatgpt.com")})
        with patch.object(AddressBarMonitor, "_is_hung", return_value=True):
            assert monitor.read_urls([win("ChatGPT - Chrome")]) == {}
        assert monitor._uia.find_calls == []

    def test_disabled(self):
        monitor = make_monitor({1: FakeOmnibox("chatgpt.com")}, address_bar=AddressBarConfig(enabled=False))
        assert monitor.collect([win("ChatGPT - Chrome")]) == ({}, [])

    def test_uia_init_failure_disables_once(self):
        """UI Automation 초기화 실패 시 비활성 (매 주기 재시도하지 않음)"""
        monitor = AddressBarMonitor(AddressBarConfig(), ChromeConfig(blocked_urls=BLOCKED_URLS))
        with patch.object(AddressBarMonitor, "_create_uia", side_effect=OSError("no UIA")) as create:
            assert monitor.collect([win("ChatGPT - Chrome")]) == ({}, [])
            assert monitor.collect([win("ChatGPT - Chrome")]) == ({}, [])
        assert create.call_count == 1

    def test_uia_not_created_without_browser_window(self):
        monitor = AddressBarMonitor(AddressBarConfig(), ChromeConfig())
        with patch.object(AddressBarMonitor, "_create_uia") as create:
            monitor.read_urls([win("메모 - 메모장", "notepad.exe")])
        create.assert_not_called()


# ─── 판정 순서 (CDP → 주소창 URL → 창 제목) ──────────────────────


def make_collector(omniboxes):
    collector = DataCollector.__new__(DataCollector)
    collector._config = AppConfig()
    collector._address_bar_monitor = make_monitor(omniboxes)
    collector._title_detector = WindowTitleDetector(WindowTitleConfig())
    return collector


class TestBrowserWindowJudgeOrder:

    def test_url_judged_window_not_rechecked_by_title(self):
        """주소창으로 판정한 창은 제목 키워드로 다시 알리지 않음 (같은 탭 중복 방지)"""
        collector = make_collector({1: FakeOmnibox("chatgpt.com")})
        alerts = collector._browser_window_alerts([win("ChatGPT - Chrome")], ChromeInfo())
        assert [(a.code, a.url) for a in alerts] == [("BLOCKED_URL", "chatgpt.com")]

    def test_title_keyword_false_positive_avoided(self):
        collector = make_collector({1: FakeOmnibox("ko.wikipedia.org/wiki/클로드_모네")})
        assert collector._browser_window_alerts([win("Claude Monet - 위키백과 - Chrome")], ChromeInfo()) == []

    def test_unreadable_windows_fall_back_to_title(self):
        """주소창을 못 읽은 창(Firefox, 주소창 입력 중)은 창 제목으로 판정"""
        collector = make_collector({2: FakeOmnibox("gemini", focused=True)})
        windows = [
            win("ChatGPT — Mozilla Firefox", "firefox.exe", pid=1, hwnd=1),
            win("Google Gemini - Chrome", pid=2, hwnd=2),
        ]
        alerts = collector._browser_window_alerts(windows, ChromeInfo())
        assert sorted(a.window_title for a in alerts) == ["ChatGPT — Mozilla Firefox", "Google Gemini - Chrome"]
        assert all(a.url is None for a in alerts)

    def test_cdp_browser_windows_skipped(self):
        """CDP로 검사 중인 브라우저의 창은 주소창·창 제목 모두 건너뜀"""
        collector = make_collector({1: FakeOmnibox("chatgpt.com")})

        class CdpProcess:
            def __init__(self, pid):
                pass

            def cmdline(self):
                return ["chrome.exe", "--remote-debugging-port=9222"]

        with patch("agent.collectors.data_collector.psutil.Process", CdpProcess):
            alerts = collector._browser_window_alerts([win("ChatGPT - Chrome")], ChromeInfo(debug_mode=True))
        assert alerts == []
        assert collector._address_bar_monitor._uia.find_calls == []


# ─── 상태 판정 · 현재 탐지 목록 ───────────────────────────────────


class TestAddressBarAlertState:

    def _alert(self, fg):
        monitor = make_monitor({1: FakeOmnibox("chatgpt.com/c/1")})
        _, alerts = monitor.collect([win("코드 리뷰 요청 - Chrome", fg=fg)])
        return alerts[0]

    def test_foreground_window_is_danger(self):
        report = StatusReport(agent_id="A")
        assert StateAnalyzer.evaluate(report, [self._alert(True)]) == UserState.DANGER

    def test_background_window_is_warning(self):
        report = StatusReport(agent_id="A")
        assert StateAnalyzer.evaluate(report, [self._alert(False)]) == UserState.WARNING

    def test_active_detection_uses_url(self):
        items = DataCollector._active_detections(StatusReport(agent_id="A"), [self._alert(True)])
        assert [(i.code, i.source, i.target, i.is_foreground) for i in items] == [
            ("BLOCKED_URL", "url", "chatgpt.com/c/1", True)
        ]


# ─── 설정 기본값 ─────────────────────────────────────────────────


def test_old_config_gets_address_bar_defaults():
    """address_bar 섹션이 없는 기존 PC 설정에서도 기본 활성"""
    m = AppConfig(**yaml.safe_load("monitoring:\n  process:\n    mode: all\n")).monitoring
    assert m.address_bar.enabled is True
    assert m.address_bar.browser_processes == ["chrome.exe", "msedge.exe", "whale.exe"]
