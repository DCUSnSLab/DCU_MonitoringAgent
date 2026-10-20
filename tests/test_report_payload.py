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
from agent.config import ServerConfig
from agent.models import (
    ActiveDetection, Alert, BrowserDetails, ChromeInfo, ChromeTabInfo, ProcessInfo, StatusReport, UserState,
)
from agent.network.api_client import ApiClient

DROPPED_PROCESS_FIELDS = {"cmdline", "username", "create_time", "exe_path", "status", "cpu_percent"}


def _report():
    return StatusReport(
        agent_id="A",
        hostname="D2-535-21",
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
        assert DROPPED_PROCESS_FIELDS.isdisjoint(saved["processes"][0])
        assert "favicon_url" not in saved["browser_details"]["chrome"]["tabs"][0]


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
