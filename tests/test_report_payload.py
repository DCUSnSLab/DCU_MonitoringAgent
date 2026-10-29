"""
보고 전송 크기·로그 축소 테스트 (GEN-1683)

- 서버가 저장·표시하지 않는 필드는 전송·오프라인 버퍼에서 뺀다 (탐지에는 그대로 사용)
- 보고서는 JSON 으로 한 번만 직렬화해 보낸다
- 매 사이클 요약 로그는 DEBUG, 사용자 상태가 바뀐 사이클만 INFO
"""

import json
import logging
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.collectors.data_collector import DataCollector
from agent.config import AppConfig, ConfigManager, ServerConfig
from agent.models import (
    ActiveDetection, Alert, BrowserDetails, ChromeInfo, ChromeTabInfo, ProcessInfo, StatusReport, SystemInfo, UserState,
)
from agent.network.api_client import ApiClient

DROPPED_PROCESS_FIELDS = {"cmdline", "username", "create_time", "exe_path", "status", "cpu_percent"}


def _report():
    return StatusReport(
        agent_id="A",
        hostname="D2-535-21",
        mac_address="00:11:22:AA:BB:CC",
        user_state=UserState.WARNING,
        processes=[ProcessInfo(
            pid=10, name="claude.exe", exe_path="C:\\x\\claude.exe", memory_mb=120.5,
            create_time=datetime(2026, 10, 20, 9, 0), username="student",
            cmdline="claude.exe --type=renderer --lang=ko", window_title="Claude", is_foreground=True,
        )],
        browser_details=BrowserDetails(chrome=ChromeInfo(
            is_running=True, debug_mode=True, tab_count=1,
            tabs=[ChromeTabInfo(tab_id="T1", title="ChatGPT", url="https://chatgpt.com/", is_active=True,
                                favicon_url="https://chatgpt.com/favicon.ico")],
        )),
        alerts=[Alert(level="warning", code="BLOCKED_PROCESS", message="차단된 프로그램 실행: claude.exe",
                      process_name="claude.exe")],
        active_detections=[ActiveDetection(code="BLOCKED_PROCESS", target="claude.exe", source="process",
                                           is_foreground=True)],
        foreground_window="Claude",
    )


def _client(tmp_path):
    client = ApiClient(ServerConfig(enabled=True, base_url="http://server"), agent_id="A")
    client._buffer_dir = str(tmp_path)
    return client


class TestSendReport:

    def test_body_serialized_once_without_unused_fields(self, tmp_path):
        client = _client(tmp_path)
        response = MagicMock(status_code=200)
        response.json.return_value = {"status": "ok", "update_available": False}
        client._session.post = MagicMock(return_value=response)

        assert client.send_report(_report()) is True

        args, kwargs = client._session.post.call_args
        assert args[0] == "http://server/api/agents/report"
        assert "json" not in kwargs  # dict 로 되돌려 다시 직렬화하지 않음
        assert isinstance(kwargs["data"], bytes)
        payload = json.loads(kwargs["data"].decode("utf-8"))

        proc = payload["processes"][0]
        assert DROPPED_PROCESS_FIELDS.isdisjoint(proc)
        assert proc == {"pid": 10, "name": "claude.exe", "memory_mb": 120.5,
                        "window_title": "Claude", "is_foreground": True}
        assert "favicon_url" not in payload["browser_details"]["chrome"]["tabs"][0]
        # 서버가 쓰는 값은 그대로
        assert payload["user_state"] == "warning"
        assert payload["alerts"][0]["code"] == "BLOCKED_PROCESS"
        assert payload["active_detections"][0]["target"] == "claude.exe"
        assert payload["browser_details"]["chrome"]["tabs"][0]["url"] == "https://chatgpt.com/"
        assert payload["foreground_window"] == "Claude"
        assert payload["mac_address"] == "00:11:22:AA:BB:CC"

    def test_payload_much_smaller_than_full_report(self, tmp_path):
        report = _report()
        report.processes = [p.copy(update={"pid": i}) for i, p in enumerate(report.processes * 200)]
        full = len(report.json())
        client = _client(tmp_path)
        client._session.post = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))
        client.send_report(report)
        sent = len(client._session.post.call_args.kwargs["data"])
        assert sent < full * 0.6

    def test_ota_update_still_detected(self, tmp_path):
        client = _client(tmp_path)
        response = MagicMock(status_code=200)
        response.json.return_value = {"update_available": True, "latest_version": "1.0.31", "latest_checksum": "c"}
        client._session.post = MagicMock(return_value=response)
        seen = []
        client.set_update_callback(lambda v, c: seen.append((v, c)))
        client.send_report(_report())
        assert seen == [("1.0.31", "c")]

    def test_offline_buffer_also_trimmed(self, tmp_path):
        client = _client(tmp_path)
        assert client.buffer_report_offline(_report()) is True
        [name] = os.listdir(tmp_path)
        with open(os.path.join(tmp_path, name), encoding="utf-8") as f:
            saved = json.load(f)
        assert "active_detections" not in saved  # 재전송 시 현재 탐지 목록을 덮어쓰지 않도록 (기존 동작)
        assert saved["mac_address"] == "00:11:22:AA:BB:CC"
        assert DROPPED_PROCESS_FIELDS.isdisjoint(saved["processes"][0])
        assert "favicon_url" not in saved["browser_details"]["chrome"]["tabs"][0]


def test_physical_mac_shared_with_id_and_cached_across_reports(monkeypatch):
    from agent.utils import machine_id
    addresses = MagicMock(return_value="001122AABBCC")
    monkeypatch.setattr(machine_id, "get_physical_mac", addresses)
    monkeypatch.setenv("COMPUTERNAME", "TEST-PC")
    for name in ("ProcessMonitor", "WindowMonitor", "ChromeMonitor", "AddressBarMonitor", "WindowTitleDetector"):
        monkeypatch.setattr("agent.collectors.data_collector." + name, MagicMock())
    monkeypatch.setattr(DataCollector, "_get_local_ip", staticmethod(lambda: "127.0.0.1"))
    config = AppConfig()
    config.agent.id = ConfigManager._derive_agent_id()
    collector = DataCollector(config)
    collector._process_monitor.get_system_info.return_value = SystemInfo()
    collector._process_monitor.collect.return_value = ([], [])
    collector._window_monitor.get_window_titles_by_pid.return_value = {}
    collector._window_monitor.get_foreground_pid.return_value = None
    collector._window_monitor.get_foreground_window.return_value = None
    collector._window_monitor.get_visible_windows.return_value = []
    collector._chrome_monitor.collect.return_value = (ChromeInfo(), [])
    collector._browser_window_alerts = MagicMock(return_value=[])
    first, second = collector.collect(), collector.collect()
    assert first.agent_id == second.agent_id == "TEST-PC-AABBCC"
    assert first.mac_address == second.mac_address == "00:11:22:AA:BB:CC"
    collector._get_local_ip = MagicMock(return_value="203.250.35.10")
    collector.refresh_ip_address()
    assert collector.collect().ip_address == "203.250.35.10"
    assert first.ip_address == "127.0.0.1"  # 과거 보고서는 변경하지 않는다.
    assert addresses.call_count == 2  # ID 생성과 수집기 초기화 때만 조회


def test_reconnect_refreshes_ip_before_replaying_buffer(tmp_path, monkeypatch):
    collector = DataCollector.__new__(DataCollector)
    collector._ip_address = "169.254.1.2"
    collector._get_local_ip = MagicMock(return_value="203.250.35.10")
    client = _client(tmp_path)
    client._on_connected = collector.refresh_ip_address
    old_report = _report().copy(update={"ip_address": "169.254.1.2"})
    assert client.buffer_report_offline(old_report)
    sent = []
    def send(endpoint, body):
        assert collector._ip_address == "203.250.35.10"
        sent.append(body)
        return True
    client._post = send
    client.check_connection = lambda: True
    client._running = True
    monkeypatch.setattr("agent.network.api_client.time.sleep", lambda _: setattr(client, "_running", False))
    client._sender_loop()
    assert sent[0]["ip_address"] == "169.254.1.2"
    assert not list(tmp_path.glob("*.json"))
    collector._get_local_ip.assert_called_once()


def test_campus_ip_is_not_queried_again():
    collector = DataCollector.__new__(DataCollector)
    collector._ip_address = "203.250.35.10"
    collector._get_local_ip = MagicMock()
    collector.refresh_ip_address()
    collector._get_local_ip.assert_not_called()


class TestCycleLog:

    def _collector(self):
        collector = DataCollector.__new__(DataCollector)
        collector._last_logged_state = None
        return collector

    def test_info_only_when_state_changes(self, caplog):
        collector = self._collector()
        report = _report()
        report.alerts = []
        states = [UserState.SAFE, UserState.SAFE, UserState.SAFE, UserState.DANGER, UserState.DANGER, UserState.SAFE]
        with caplog.at_level(logging.DEBUG, logger="agent"):
            for state in states:
                report.user_state = state
                collector._log_summary(report)
        levels = [(r.levelname, r.getMessage().split("]")[0]) for r in caplog.records]
        assert levels == [
            ("INFO", "[state:SAFE"), ("DEBUG", "[state:SAFE"), ("DEBUG", "[state:SAFE"),
            ("INFO", "[state:DANGER"), ("DEBUG", "[state:DANGER"), ("INFO", "[state:SAFE"),
        ]

    def test_new_alerts_still_logged_as_warning(self, caplog):
        collector = self._collector()
        with caplog.at_level(logging.INFO, logger="agent"):
            collector._log_summary(_report())
            collector._log_summary(_report())
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2  # 알림은 수집기에서 이미 1분 단위로 중복 제거된 새 알림
