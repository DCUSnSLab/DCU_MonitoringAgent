"""
데이터 수집기 모듈

ProcessMonitor, ChromeMonitor, WindowMonitor의 데이터를 통합하여
StatusReport를 생성하고 알림 규칙을 적용합니다.

Producer-Consumer 패턴:
  - collect() → StatusReport 생성
  - 서버 전송 모듈(Phase 6)이 큐에서 소비
"""

import os
import json
import queue
import socket
import uuid
import platform
from datetime import datetime
from typing import List, Optional

from agent.config import AppConfig
from agent.models import (
    AgentRegistration,
    Alert,
    BrowserDetails,
    StatusReport,
)
from agent.monitors.chrome_monitor import ChromeMonitor
from agent.monitors.process_monitor import ProcessMonitor
from agent.monitors.window_monitor import WindowMonitor
from agent.analyzers.state_analyzer import StateAnalyzer
from agent.utils.logger import get_logger


logger = get_logger("collectors.data")


class DataCollector:
    """
    모든 모니터링 모듈의 데이터를 통합하는 수집기

    Usage:
        collector = DataCollector(config)
        report = collector.collect()  # StatusReport 반환
    """

    def __init__(self, config: AppConfig):
        self._config = config
        self._agent_id = config.agent.id

        # 호스트명/IP는 자주 바뀌지 않으므로 1회만 조회해 캐시한다.
        # (collect()는 1초마다 호출되므로 매 사이클 소켓 연결을 피한다)
        self._hostname = socket.gethostname()
        self._ip_address = self._get_local_ip()

        # 모니터 초기화
        self._process_monitor = ProcessMonitor(config.monitoring.process, config.monitoring.messenger)
        self._window_monitor = WindowMonitor()
        self._chrome_monitor = ChromeMonitor(config.monitoring.chrome, config.monitoring.messenger)

        # 보고서 큐 (서버 전송용, 최대 N개)
        max_buf = config.server.offline_buffer_size
        self._report_queue: queue.Queue = queue.Queue(maxsize=max_buf)

        # 알림 이력 (중복 알림 방지용 - 최근 1분)
        self._recent_alert_keys: dict = {}

        logger.info("DataCollector 초기화 완료")
        logger.info(f"  프로세스 모드: {config.monitoring.process.mode}")
        logger.info(f"  크롬 CDP: {'활성' if config.monitoring.chrome.enabled else '비활성'}")

    # ─── 공개 메서드 ──────────────────────────────────────────────

    def collect(self) -> StatusReport:
        """
        한 사이클의 모니터링 데이터를 수집하여 StatusReport를 반환합니다.
        """
        timestamp = datetime.now()
        all_alerts: List[Alert] = []

        # 1. 시스템 정보 수집
        system_info = self._process_monitor.get_system_info()

        # 2. 창 제목 수집 (프로세스에 추가하기 위해 먼저 수집)
        window_titles = self._window_monitor.get_window_titles_by_pid()
        foreground_pid = self._window_monitor.get_foreground_pid()
        foreground_window = self._window_monitor.get_foreground_window()

        # 3. 프로세스 수집
        processes, proc_alerts = self._process_monitor.collect()
        all_alerts.extend(proc_alerts)

        # 4. 각 프로세스에 창 제목 및 포그라운드 여부 추가
        for proc in processes:
            if proc.pid in window_titles:
                proc.window_title = window_titles[proc.pid]
            if foreground_pid and proc.pid == foreground_pid:
                proc.is_foreground = True

        # 5. 크롬 탭 수집
        chrome_info, chrome_alerts = self._chrome_monitor.collect()
        all_alerts.extend(chrome_alerts)

        # 6. 중복 알림 제거
        deduped_alerts = self._deduplicate_alerts(all_alerts)

        # 7. StatusReport 생성
        report = StatusReport(
            agent_id=self._agent_id,
            hostname=self._hostname,
            ip_address=self._ip_address,
            timestamp=timestamp,
            agent_version=self._config.agent.version,
            system_info=system_info,
            processes=processes,
            browser_details=BrowserDetails(chrome=chrome_info),
            alerts=deduped_alerts,
            foreground_window=foreground_window,
        )

        # 7-1. 사용자 상태 분류 (경고 팝업 중복 방지를 위해 deduped 되기 전의 '전체' 알림 기준)
        report.user_state = StateAnalyzer.evaluate(report, all_alerts)

        # 8. 큐에 추가 (오버플로우 시 가장 오래된 항목 제거)
        self._enqueue_report(report)

        # 로그 출력
        self._log_summary(report)

        return report

    def get_registration_info(self) -> AgentRegistration:
        """에이전트 등록 정보를 반환합니다."""
        return AgentRegistration(
            agent_id=self._agent_id,
            hostname=self._hostname,
            ip_address=self._ip_address,
            mac_address=self._get_mac_address(),
            os_version=platform.platform(),
            agent_version=self._config.agent.version,
        )

    def get_queued_report(self, timeout: float = 0.1) -> Optional[StatusReport]:
        """
        큐에서 보고서를 하나 꺼냅니다 (서버 전송용).
        큐가 비어있으면 None 반환.
        """
        try:
            return self._report_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def queue_size(self) -> int:
        """현재 큐에 쌓인 보고서 수를 반환합니다."""
        return self._report_queue.qsize()

    def save_report_to_file(self, report: StatusReport, filepath: str) -> bool:
        """
        보고서를 로컬 JSON 파일로 저장합니다 (서버 미연결 시 버퍼).
        """
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report.dict(), f, ensure_ascii=False, indent=2, default=str)
            return True
        except Exception as e:
            logger.error(f"보고서 파일 저장 실패: {e}")
            return False

    # ─── 내부 메서드 ──────────────────────────────────────────────

    def _enqueue_report(self, report: StatusReport):
        """큐가 가득 찼으면 가장 오래된 항목을 버리고 추가합니다."""
        if self._report_queue.full():
            try:
                self._report_queue.get_nowait()
                logger.debug("큐 오버플로우: 가장 오래된 보고서 제거")
            except queue.Empty:
                pass
        try:
            self._report_queue.put_nowait(report)
        except queue.Full:
            pass

    def _deduplicate_alerts(self, alerts: List[Alert]) -> List[Alert]:
        """
        동일한 알림이 반복되지 않도록 1분 내 중복을 제거합니다.
        """
        now = datetime.now()
        result = []
        # 1분 이상 지난 키 정리
        expired = [k for k, t in self._recent_alert_keys.items()
                   if (now - t).total_seconds() > 60]
        for k in expired:
            del self._recent_alert_keys[k]

        for alert in alerts:
            # 알림 키: code + process_name 또는 url 조합
            key = f"{alert.code}:{alert.process_name or alert.url or ''}"
            if key not in self._recent_alert_keys:
                self._recent_alert_keys[key] = now
                result.append(alert)
            else:
                logger.debug(f"중복 알림 제거: {key}")

        return result

    def _log_summary(self, report: StatusReport):
        """수집 결과를 요약하여 로그로 출력합니다."""
        chrome = report.browser_details.chrome
        state_name = report.user_state.name if report.user_state else "UNKNOWN"
        try:
            logger.info(
                f"[state:{state_name}] proc:{len(report.processes)} | "
                f"tabs:{chrome.tab_count} | alerts:{len(report.alerts)} | "
                f"fg:{report.foreground_window or 'N/A'}"
            )
        except UnicodeEncodeError:
            logger.info(f"[state:{state_name}] proc:{len(report.processes)} tabs:{chrome.tab_count} alerts:{len(report.alerts)}")
        for alert in report.alerts:
            logger.warning(f"  [{alert.level.upper()}] {alert.message}")

    @staticmethod
    def _get_local_ip() -> str:
        """로컬 IP 주소를 반환합니다."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def _get_mac_address() -> str:
        """MAC 주소를 반환합니다."""
        try:
            mac = uuid.getnode()
            return ":".join(
                f"{(mac >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6))
            )
        except Exception:
            return "00:00:00:00:00:00"
