"""
DCU Monitoring Agent 메인 진입점

에이전트의 전체 생명주기를 관리합니다:
- 설정 로드 → 로깅 초기화 → DataCollector 시작 → TrayApp 실행
- TrayApp이 메인 스레드, 모니터링 루프가 백그라운드 스레드
"""

import sys
import os
import time
import signal
import argparse
import threading
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.config import ConfigManager
from agent.utils.logger import setup_logger, get_logger
from agent.collectors.data_collector import DataCollector
from agent.network.api_client import ApiClient


class MonitoringAgent:
    """
    DCU 모니터링 에이전트 메인 클래스

    모든 모니터링 모듈을 관리하고 에이전트의 생명주기를 제어합니다.
    """

    def __init__(self, config_path: str = None):
        # 설정 로드
        self._config_mgr = ConfigManager(config_path)
        self._config = self._config_mgr.config

        # 로깅 초기화
        log_cfg = self._config.logging
        self._logger = setup_logger(
            name="agent",
            level=log_cfg.level,
            log_file=log_cfg.file,
            max_size_mb=log_cfg.max_size_mb,
            backup_count=log_cfg.backup_count,
            console_output=log_cfg.console_output,
        )
        self._log = get_logger("main")

        # 상태 플래그
        self._running = False
        self._monitor_thread: threading.Thread = None

        # 모니터 인스턴스
        self._data_collector: DataCollector = None
        self._api_client: ApiClient = None

    @property
    def config(self):
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        """에이전트를 시작합니다."""
        self._log.info("=" * 60)
        self._log.info("DCU Monitoring Agent 시작")
        self._log.info(f"  에이전트 ID  : {self._config.agent.id}")
        self._log.info(f"  실습실      : {self._config.agent.lab_name}")
        self._log.info(f"  버전        : {self._config.agent.version}")
        self._log.info(f"  모니터링 주기: {self._config.monitoring.interval_seconds}초")
        self._log.info(f"  프로세스 모드: {self._config.monitoring.process.mode}")
        self._log.info(f"  크롬 모니터링: {'활성' if self._config.monitoring.chrome.enabled else '비활성'}")
        self._log.info(f"  종료 방지   : {'활성' if self._config.security.prevent_exit else '비활성'}")
        self._log.info("=" * 60)

        self._running = True
        self._data_collector = DataCollector(self._config)

        # 에이전트 등록 정보 로그
        reg = self._data_collector.get_registration_info()
        self._log.info(f"  호스트명     : {reg.hostname}")
        self._log.info(f"  IP 주소     : {reg.ip_address}")
        self._log.info(f"  OS          : {reg.os_version}")
        # API 클라이언트 시작 (서버 활성화 시)
        if self._config.server.enabled:
            self._api_client = ApiClient(self._config.server, self._config.agent.id)
            self._api_client.start()
        else:
            self._log.info("  서버 전송   : 비활성 (로컬 모드)")

        self._log.info("=" * 60)

        # 모니터링 루프를 별도 스레드에서 실행
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name="MonitoringThread",
            daemon=True,
        )
        self._monitor_thread.start()
        self._log.info("모니터링 스레드 시작됨")

    def stop(self, password: str = None):
        """
        에이전트를 중지합니다.

        Args:
            password: 종료 방지가 활성화된 경우 필요한 비밀번호
        """
        if self._config.security.prevent_exit and password != self._config.security.exit_password:
            self._log.warning("잘못된 비밀번호로 종료 시도됨")
            return False

        self._log.info("에이전트 종료 중...")
        self._running = False

        # 1. 모니터링 관련 스레드가 보고서를 보내는 작업을 완전히 끝내도록 대기
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=10)

        # 2. 모든 데이터 전송이 끝난 후 오프라인 상태 알림 가장 마지막에 전송
        if self._api_client:
            self._api_client.stop()  # 백그라운드 재전송 버퍼 스레드 먼저 중단
            if self._config.server.enabled:
                try:
                    self._api_client.send_offline_status()
                except Exception as e:
                    self._log.debug(f"종료 전 오프라인 알림 전송 실패: {e}")

        self._log.info("에이전트 종료 완료")
        return True

    def _monitoring_loop(self):
        """메인 모니터링 루프 (별도 스레드에서 실행)"""
        interval = self._config.monitoring.interval_seconds

        while self._running:
            try:
                cycle_start = time.time()
                self._collect_data()

                # 주기만큼 대기 (0.5초 단위로 나눠 빠른 종료 반응)
                elapsed = time.time() - cycle_start
                remaining = max(0, interval - elapsed)
                while remaining > 0 and self._running:
                    time.sleep(min(0.5, remaining))
                    remaining -= 0.5

            except Exception as e:
                self._log.error(f"모니터링 루프 오류: {e}", exc_info=True)
                time.sleep(1)

    def _collect_data(self):
        """DataCollector를 통해 통합 데이터 수집 후 API 전송"""
        if not self._data_collector:
            return
        report = self._data_collector.collect()

        # UI가 연결된 경우 상태 업데이트
        if hasattr(self, "tray_app") and self.tray_app:
            self.tray_app.update_status(report.user_state.value)

        # 서버 전송 (활성화 시)
        if self._api_client and self._config.server.enabled:
            if not self._api_client.send_report(report):
                self._api_client.buffer_report_offline(report)


# ─── 실행 모드 ────────────────────────────────────────────────────


def run_tray_mode(agent: "MonitoringAgent"):
    """시스템 트레이 모드로 실행합니다 (기본 모드)."""
    from agent.ui.tray_app import TrayApp

    agent.start()

    tray = TrayApp(agent=agent, on_stop_callback=lambda: sys.exit(0))
    agent.tray_app = tray

    # TrayApp.run()은 메인 스레드를 블로킹 → 종료 시까지 대기
    tray.run()


def run_console_mode(agent: "MonitoringAgent", test_mode: bool = False):
    """콘솔 모드로 실행합니다 (--console 또는 --test)."""

    def signal_handler(signum, frame):
        print("\n종료 신호 수신...")
        agent.stop(password=agent.config.security.exit_password)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    agent.start()

    if test_mode:
        print("[테스트 모드] 10초 후 자동 종료됩니다...")
        time.sleep(10)
        agent.stop(password=agent.config.security.exit_password)
    else:
        try:
            while agent.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            agent.stop(password=agent.config.security.exit_password)


def parse_args():
    """명령행 인수를 파싱합니다."""
    parser = argparse.ArgumentParser(
        description="DCU Monitoring Agent - 실습실 컴퓨터 모니터링 에이전트"
    )
    parser.add_argument("-c", "--config", type=str, default=None,
                        help="설정 파일 경로 (기본: config/agent_config.yaml)")
    parser.add_argument("--test", action="store_true",
                        help="테스트 모드로 실행 (10초 후 자동 종료, 콘솔 출력)")
    parser.add_argument("--console", action="store_true",
                        help="콘솔 모드로 실행 (트레이 UI 없음)")
    parser.add_argument("--debug", action="store_true",
                        help="디버그 레벨 로깅")
    return parser.parse_args()


def main():
    """메인 함수"""
    args = parse_args()

    agent = MonitoringAgent(config_path=args.config)

    if args.debug:
        agent._config.logging.level = "DEBUG"

    if args.test or args.console:
        run_console_mode(agent, test_mode=args.test)
    else:
        run_tray_mode(agent)


if __name__ == "__main__":
    main()
