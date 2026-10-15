"""
창 제목 기반 탐지 · 항상 차단 프로그램 탐지 단위 테스트 (GEN-1149)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace
from unittest.mock import patch

import yaml

from agent.analyzers.state_analyzer import StateAnalyzer
from agent.analyzers.title_detector import WindowTitleDetector
from agent.collectors.data_collector import DataCollector
from agent.config import AppConfig, ProcessConfig, WindowTitleConfig
from agent.models import Alert, ChromeInfo, ProcessInfo, StatusReport, UserState
from agent.monitors.process_monitor import ProcessMonitor
from agent.monitors.window_monitor import VisibleWindow


def win(title, process_name="chrome.exe", pid=100, hwnd=1, fg=False):
    return VisibleWindow(hwnd=hwnd, pid=pid, process_name=process_name, title=title, is_foreground=fg)


# ─── WindowTitleDetector ──────────────────────────────────────────


class TestWindowTitleDetector:

    def setup_method(self):
        self.detector = WindowTitleDetector(WindowTitleConfig())

    def test_blocked_keyword_in_browser_window(self):
        """브라우저 창 제목의 차단 키워드 → BLOCKED_URL (창 제목 포함)"""
        alerts = self.detector.check([win("ChatGPT - Chrome")])
        assert len(alerts) == 1
        assert alerts[0].code == "BLOCKED_URL"
        assert alerts[0].window_title == "ChatGPT - Chrome"
        assert alerts[0].url is None and alerts[0].process_name is None

    def test_messenger_keyword_in_browser_window(self):
        """브라우저 창 제목의 메신저 키워드 → MESSENGER_DETECTED"""
        alerts = self.detector.check([win("(9+) Instagram - Chrome", "whale.exe")])
        assert [a.code for a in alerts] == ["MESSENGER_DETECTED"]

    def test_case_insensitive(self):
        """대소문자 무시"""
        alerts = self.detector.check([win("CHATGPT 사용법 - Microsoft Edge", "msedge.exe")])
        assert len(alerts) == 1

    def test_non_browser_window_ignored(self):
        """브라우저가 아닌 창(메모장 등)은 키워드가 있어도 무시"""
        alerts = self.detector.check([
            win("instagram 정리.txt - 메모장", "notepad.exe"),
            win("Claude", "claude.exe"),
        ])
        assert alerts == []

    def test_allowed_title_no_alert(self):
        """키워드 없는 브라우저 창은 알림 없음"""
        alerts = self.detector.check([win("2026 KBO 준플레이오프 - 나무위키 - Chrome")])
        assert alerts == []

    def test_skip_pids(self):
        """skip_pids(CDP 검사 중인 브라우저)의 창은 제외"""
        windows = [win("ChatGPT - Chrome", pid=1), win("ChatGPT - Chrome", pid=2)]
        alerts = self.detector.check(windows, skip_pids={1})
        assert len(alerts) == 1

    def test_both_categories(self):
        """한 창이 두 카테고리에 모두 해당하면 각각 1건"""
        alerts = self.detector.check([win("ChatGPT로 Instagram 캡션 쓰기 - Chrome")])
        assert sorted(a.code for a in alerts) == ["BLOCKED_URL", "MESSENGER_DETECTED"]

    def test_disabled(self):
        detector = WindowTitleDetector(WindowTitleConfig(enabled=False))
        assert detector.check([win("ChatGPT - Chrome")]) == []

    def test_foreground_flag_propagates(self):
        """창의 포그라운드 여부(hwnd 판정)가 알림으로 전달됨"""
        alerts = self.detector.check([win("ChatGPT - Chrome", fg=True), win("Instagram - Chrome", hwnd=2)])
        by_code = {a.code: a for a in alerts}
        assert by_code["BLOCKED_URL"].window_is_foreground is True
        assert by_code["MESSENGER_DETECTED"].window_is_foreground is False


# ─── StateAnalyzer (창 제목 알림) ─────────────────────────────────


class TestStateWithWindowTitle:

    def _alert(self, title, fg):
        return Alert(level="warning", code="BLOCKED_URL", message="t",
                     window_title=title, window_is_foreground=fg)

    def test_foreground_window_is_danger(self):
        report = StatusReport(agent_id="A", foreground_window="ChatGPT - Chrome")
        assert StateAnalyzer.evaluate(report, [self._alert("ChatGPT - Chrome", True)]) == UserState.DANGER

    def test_background_window_is_warning(self):
        report = StatusReport(agent_id="A", foreground_window="todo mate - Chrome")
        assert StateAnalyzer.evaluate(report, [self._alert("ChatGPT - Chrome", False)]) == UserState.WARNING

    def test_judged_by_window_handle_not_earlier_title(self):
        """수집 주기 앞부분에 읽은 포그라운드 제목이 달라도(탭 전환·알림 숫자 변화)
        탐지 시점에 그 창이 포그라운드였으면 경고"""
        report = StatusReport(agent_id="A", foreground_window="(3) Instagram - Chrome")
        assert StateAnalyzer.evaluate(report, [self._alert("ChatGPT - Chrome", True)]) == UserState.DANGER

    def test_same_title_but_background_is_warning(self):
        """제목이 같아도 탐지 시점에 포그라운드가 아니었으면 주의 (같은 제목 창이 여러 개인 경우)"""
        report = StatusReport(agent_id="A", foreground_window="ChatGPT - Chrome")
        assert StateAnalyzer.evaluate(report, [self._alert("ChatGPT - Chrome", False)]) == UserState.WARNING


# ─── DataCollector 보조 함수 ──────────────────────────────────────


class TestCollectorHelpers:

    def test_alert_target_strips_notification_count(self):
        """중복 판정 키에서 창 제목 앞의 알림 개수 표시는 제외"""
        a1 = Alert(code="MESSENGER_DETECTED", window_title="(3) Instagram - Chrome")
        a2 = Alert(code="MESSENGER_DETECTED", window_title="(9+) Instagram - Chrome")
        assert DataCollector._alert_target(a1) == DataCollector._alert_target(a2) == "Instagram - Chrome"

    def test_alert_target_priority(self):
        """대상 우선순위: 프로세스명 > URL > 창 제목"""
        assert DataCollector._alert_target(Alert(process_name="claude.exe", url="u")) == "claude.exe"
        assert DataCollector._alert_target(Alert(url="https://chatgpt.com/", window_title="t")) == "https://chatgpt.com/"

    def test_cdp_browser_pids(self):
        procs = [
            ProcessInfo(pid=1, name="chrome.exe", cmdline="chrome.exe --remote-debugging-port=9222"),
            ProcessInfo(pid=2, name="chrome.exe", cmdline="chrome.exe"),
            ProcessInfo(pid=3, name="whale.exe", cmdline=None),
        ]
        assert DataCollector._cdp_browser_pids(procs, ChromeInfo(debug_mode=True), 9222) == {1}
        assert DataCollector._cdp_browser_pids(procs, ChromeInfo(debug_mode=False), 9222) == set()


# ─── ProcessMonitor 항상 차단 프로그램 ─────────────────────────────


def _fake_proc(pid, name):
    info = {"pid": pid, "name": name, "exe": None, "memory_info": None,
            "create_time": None, "username": None, "cmdline": None}
    return SimpleNamespace(pid=pid, info=info)


class TestAlwaysBlocked:

    def _collect(self, config, names):
        fakes = [_fake_proc(i + 10, n) for i, n in enumerate(names)]
        with patch("agent.monitors.process_monitor.psutil.process_iter", return_value=fakes):
            return ProcessMonitor(config).collect()

    def test_detected_in_all_mode(self):
        procs, alerts = self._collect(ProcessConfig(mode="all"), ["claude.exe", "code.exe"])
        assert [(a.code, a.process_name) for a in alerts] == [("BLOCKED_PROCESS", "claude.exe")]

    def test_included_even_in_whitelist_mode(self):
        """화이트리스트에 없어도 항상 차단 프로그램은 보고 목록에 포함"""
        config = ProcessConfig(mode="whitelist", whitelist=["code.exe"])
        procs, alerts = self._collect(config, ["ChatGPT.exe"])
        assert any(p.name == "ChatGPT.exe" for p in procs)
        assert "BLOCKED_PROCESS" in [a.code for a in alerts]

    def test_no_duplicate_in_blacklist_mode(self):
        """blacklist 모드에서 같은 프로그램이 두 번 알림되지 않음"""
        config = ProcessConfig(mode="blacklist", blacklist=["claude.exe"])
        _, alerts = self._collect(config, ["claude.exe"])
        assert [a.code for a in alerts] == ["BLOCKED_PROCESS"]


# ─── 기본값 (설정 섹션이 없는 기존 PC) ───────────────────────────


class TestDefaultsForOldConfig:

    def test_old_config_gets_defaults(self):
        raw = yaml.safe_load("monitoring:\n  process:\n    mode: all\n")
        m = AppConfig(**raw).monitoring
        assert "kakaotalk.exe" in m.messenger.process_names
        assert "instagram.com" in m.messenger.url_patterns
        assert "claude.exe" in m.process.always_blocked
        assert "ChatGPT" in m.window_title.blocked_keywords
        assert m.window_title.enabled is True
