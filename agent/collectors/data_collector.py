"""
데이터 수집기 모듈

ProcessMonitor, ChromeMonitor, WindowMonitor의 데이터를 통합하여
StatusReport를 생성하고 알림 규칙을 적용합니다.

Producer-Consumer 패턴:
  - collect() → StatusReport 생성
  - 서버 전송 모듈(Phase 6)이 큐에서 소비
"""

import os
import re
import json
import logging
import queue
import socket
import uuid
import platform
from datetime import datetime
from typing import List, Optional, Set

import psutil

from agent.config import AppConfig
from agent.models import (
    ActiveDetection,
    AgentRegistration,
    Alert,
    BrowserDetails,
    ChromeInfo,
    StatusReport,
)
from agent.monitors.address_bar_monitor import AddressBarMonitor
from agent.monitors.chrome_monitor import ChromeMonitor
from agent.monitors.process_monitor import ProcessMonitor
from agent.monitors.window_monitor import VisibleWindow, WindowMonitor
from agent.analyzers.state_analyzer import StateAnalyzer
from agent.analyzers.title_detector import WindowTitleDetector
from agent.utils.logger import get_logger


logger = get_logger("collectors.data")

# 창 제목 앞의 알림 개수 표시(예: "(3) ", "(9+) ")는 수시로 바뀌므로 중복 판정에서 제외한다.
_NOTIFICATION_PREFIX = re.compile(r"^\(\d+\+?\)\s*")


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
        self._address_bar_monitor = AddressBarMonitor(
            config.monitoring.address_bar, config.monitoring.chrome, config.monitoring.messenger
        )
        self._title_detector = WindowTitleDetector(config.monitoring.window_title)

        # 보고서 큐 (서버 전송용, 최대 N개)
        max_buf = config.server.offline_buffer_size
        self._report_queue: queue.Queue = queue.Queue(maxsize=max_buf)

        # 알림 이력 (중복 알림 방지용 - 최근 1분)
        self._recent_alert_keys: dict = {}

        # 마지막으로 INFO 로 남긴 사용자 상태 (상태가 바뀔 때만 INFO 로그)
        self._last_logged_state: Optional[str] = None

        logger.info("DataCollector 초기화 완료")
        logger.info(f"  프로세스 모드: {config.monitoring.process.mode}")
        logger.info(f"  크롬 CDP: {'활성' if config.monitoring.chrome.enabled else '비활성'}")
        logger.info(f"  주소창 읽기: {'활성' if config.monitoring.address_bar.enabled else '비활성'}")
        logger.info(f"  창 제목 탐지: {'활성' if config.monitoring.window_title.enabled else '비활성'}")

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

        # 5-1. 브라우저 창 단위 탐지 (CDP 없이 주소창 URL → 창 제목 순으로 메신저·차단 사이트 탐지)
        windows = self._window_monitor.get_visible_windows()
        all_alerts.extend(self._browser_window_alerts(windows, chrome_info))

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

        # 7-2. 현재 탐지 중인 항목 (알림은 1분 단위로 중복 제거되므로, 지금 켜진 목록은 따로 전달)
        report.active_detections = self._active_detections(report, all_alerts)

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
            # 알림 키: code + 대상(프로세스명 / URL / 창 제목)
            key = f"{alert.code}:{self._alert_target(alert)}"
            if key not in self._recent_alert_keys:
                self._recent_alert_keys[key] = now
                result.append(alert)
            else:
                logger.debug(f"중복 알림 제거: {key}")

        return result

    @staticmethod
    def _active_detections(report: StatusReport, alerts: List[Alert]) -> List[ActiveDetection]:
        """이번 주기의 전체 알림(중복 제거 전)을 대상별 1건으로 요약합니다.

        같은 프로그램의 여러 프로세스(예: claude.exe 수십 개)나 같은 사이트 창은 한 항목으로 합치고,
        그중 하나라도 화면에 떠 있으면 is_foreground=True 로 표시한다.
        """
        items = {}
        for alert in alerts:
            if alert.process_name:
                source, target = "process", alert.process_name
            elif alert.url:
                source, target = "url", alert.url
            elif alert.window_title:
                source, target = "title", alert.window_title
            else:
                continue

            on_screen = StateAnalyzer.is_on_screen(report, alert)
            key = (alert.code, DataCollector._alert_target(alert))
            if key in items:
                items[key].is_foreground = items[key].is_foreground or on_screen
            else:
                items[key] = ActiveDetection(
                    code=alert.code, target=target, source=source, is_foreground=on_screen
                )
        return list(items.values())

    def _browser_window_alerts(
        self, windows: List[VisibleWindow], chrome_info: ChromeInfo
    ) -> List[Alert]:
        """CDP 없이 브라우저 창 단위로 메신저·차단 사이트를 탐지합니다.

        판정 순서:
          1) CDP가 URL로 검사 중인 브라우저의 창은 제외 (같은 탭의 중복 알림 방지)
          2) 주소창 URL을 읽은 창은 URL로 판정
             (대화 제목으로 창 제목이 바뀌는 ChatGPT도 탐지, 'Claude Monet' 같은 제목 키워드 오탐 방지)
          3) 주소창을 읽지 못한 창(Firefox, 주소창 입력 중 등)만 창 제목 키워드로 판정
        """
        cdp_pids = self._cdp_browser_pids(
            windows, chrome_info, self._config.monitoring.chrome.debug_port
        )
        address_urls, alerts = self._address_bar_monitor.collect(windows, skip_pids=cdp_pids)
        alerts.extend(self._title_detector.check(
            windows, skip_pids=cdp_pids, skip_hwnds=set(address_urls)
        ))
        return alerts

    @staticmethod
    def _cdp_browser_pids(
        windows: List[VisibleWindow], chrome_info: ChromeInfo, debug_port: int
    ) -> Set[int]:
        """CDP로 URL 검사 중인 브라우저 프로세스 PID (창 제목 탐지에서 제외할 대상).

        CDP 응답이 있을 때만, 창을 가진 크롬 계열 프로세스 중 실행 인자에 우리 디버깅 포트가
        붙은 것을 고른다. 프로세스 모드(whitelist 등)로 걸러진 목록에 의존하지 않도록
        창의 PID를 직접 조회한다. 같은 PC에서 일반 크롬도 함께 쓰면 그 창들은 계속 제목으로 검사된다.
        """
        if not chrome_info.debug_mode:
            return set()
        port_flag = f"--remote-debugging-port={debug_port}"
        candidates = {w.pid for w in windows if w.process_name in ChromeMonitor.CHROME_PROCESS_NAMES}
        pids: Set[int] = set()
        for pid in candidates:
            try:
                if port_flag in " ".join(psutil.Process(pid).cmdline()):
                    pids.add(pid)
            except Exception:
                continue
        return pids

    @staticmethod
    def _alert_target(alert: Alert) -> str:
        """중복 판정에 쓸 알림 대상 (프로세스명 > URL > 알림 개수 표시를 뗀 창 제목)."""
        if alert.process_name:
            return alert.process_name
        if alert.url:
            return alert.url
        return _NOTIFICATION_PREFIX.sub("", alert.window_title or "")

    def _log_summary(self, report: StatusReport):
        """수집 결과를 요약하여 로그로 출력합니다.

        수집은 1초마다 돌기 때문에 매 사이클 요약은 DEBUG 로 남기고,
        사용자 상태(SAFE/WARNING/DANGER)가 바뀐 사이클만 INFO 로 남긴다 (GEN-1683).
        새 알림은 1분 단위로 중복 제거되므로 WARNING 으로 그대로 남긴다.
        """
        chrome = report.browser_details.chrome
        state_name = report.user_state.name if report.user_state else "UNKNOWN"
        changed = state_name != self._last_logged_state
        self._last_logged_state = state_name
        level = logging.INFO if changed else logging.DEBUG
        try:
            logger.log(
                level,
                f"[state:{state_name}] proc:{len(report.processes)} | "
                f"tabs:{chrome.tab_count} | alerts:{len(report.alerts)} | "
                f"fg:{report.foreground_window or 'N/A'}"
            )
        except UnicodeEncodeError:
            logger.log(level, f"[state:{state_name}] proc:{len(report.processes)} tabs:{chrome.tab_count} alerts:{len(report.alerts)}")
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
        """물리 랜카드의 MAC 주소를 반환합니다 (에이전트 ID 와 같은 기준)."""
        try:
            from agent.utils.machine_id import get_mac_address
            return get_mac_address()
        except Exception:
            return "00:00:00:00:00:00"
