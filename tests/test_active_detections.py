"""
현재 탐지 목록(active_detections) 요약 단위 테스트 (GEN-1149)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.collectors.data_collector import DataCollector
from agent.models import (
    Alert, BrowserDetails, ChromeInfo, ChromeTabInfo, ProcessInfo, StatusReport,
)


def _proc_alert(name):
    return Alert(level="warning", code="BLOCKED_PROCESS", message="m", process_name=name)


class TestActiveDetections:

    def test_merges_same_program_processes(self):
        """같은 프로그램의 여러 프로세스 알림은 1건으로 합침"""
        report = StatusReport(agent_id="A", processes=[ProcessInfo(pid=i, name="claude.exe") for i in range(5)])
        items = DataCollector._active_detections(report, [_proc_alert("claude.exe") for _ in range(5)])
        assert len(items) == 1
        assert (items[0].code, items[0].target, items[0].source) == ("BLOCKED_PROCESS", "claude.exe", "process")
        assert items[0].is_foreground is False

    def test_foreground_if_any_process_on_screen(self):
        """여러 프로세스 중 하나라도 포그라운드면 is_foreground=True"""
        report = StatusReport(agent_id="A", processes=[
            ProcessInfo(pid=1, name="claude.exe"),
            ProcessInfo(pid=2, name="claude.exe", is_foreground=True),
        ])
        items = DataCollector._active_detections(report, [_proc_alert("claude.exe"), _proc_alert("claude.exe")])
        assert items[0].is_foreground is True

    def test_url_and_title_sources(self):
        """URL·창 제목 기반 알림도 대상별로 요약, 화면 표시 여부 반영"""
        chrome = ChromeInfo(is_running=True, tabs=[
            ChromeTabInfo(url="https://www.instagram.com/", is_active=True),
        ])
        report = StatusReport(
            agent_id="A",
            browser_details=BrowserDetails(chrome=chrome),
            foreground_window="todo mate - Chrome",
        )
        alerts = [
            Alert(code="MESSENGER_DETECTED", url="https://www.instagram.com/"),
            Alert(code="BLOCKED_URL", window_title="ChatGPT - Chrome"),
            Alert(code="BLOCKED_URL", window_title="Google Gemini - Chrome", window_is_foreground=True),
        ]
        items = {(i.code, i.source, i.target): i for i in DataCollector._active_detections(report, alerts)}
        assert items[("MESSENGER_DETECTED", "url", "https://www.instagram.com/")].is_foreground is True
        assert items[("BLOCKED_URL", "title", "ChatGPT - Chrome")].is_foreground is False
        assert items[("BLOCKED_URL", "title", "Google Gemini - Chrome")].is_foreground is True

    def test_notification_count_variants_merged(self):
        """창 제목 앞 알림 개수만 다른 같은 창은 1건"""
        report = StatusReport(agent_id="A")
        alerts = [
            Alert(code="MESSENGER_DETECTED", window_title="(3) Instagram - Chrome"),
            Alert(code="MESSENGER_DETECTED", window_title="(4) Instagram - Chrome"),
        ]
        assert len(DataCollector._active_detections(report, alerts)) == 1

    def test_alert_without_target_skipped(self):
        report = StatusReport(agent_id="A")
        assert DataCollector._active_detections(report, [Alert(code="X")]) == []

    def test_report_serializes_active_detections(self):
        """서버로 보내는 JSON에 active_detections 포함"""
        import json
        report = StatusReport(agent_id="A")
        report.active_detections = DataCollector._active_detections(
            StatusReport(agent_id="A", processes=[ProcessInfo(pid=1, name="claude.exe")]),
            [_proc_alert("claude.exe")],
        )
        data = json.loads(report.json())
        assert data["active_detections"][0]["target"] == "claude.exe"
