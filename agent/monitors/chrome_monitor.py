"""
크롬 브라우저 모니터링 모듈 (Chrome DevTools Protocol)

CDP를 이용해 크롬의 모든 탭 정보(URL, 제목)를 실시간으로 수집합니다.

[CDP 활성화 방법]
  기존 크롬과 충돌을 피하려면 --user-data-dir로 독립 프로필 지정 필수:

  powershell:
    Start-Process "chrome.exe" -ArgumentList `
      "--remote-debugging-port=9222", "--user-data-dir=C:\\chrome-debug"

  실습실 배포 시 크롬 바로가기 대상(Target)에 두 옵션 추가:
    "C:\\...\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\\chrome-debug

CDP 엔드포인트: http://127.0.0.1:{port}/json
"""

import json
import subprocess
import time
from typing import List, Optional, Tuple
from urllib.request import urlopen
from urllib.error import URLError

import psutil

from agent.config import ChromeConfig, MessengerConfig
from agent.models import Alert, ChromeInfo, ChromeTabInfo
from agent.utils.logger import get_logger

logger = get_logger("monitors.chrome")


class ChromeMonitor:
    """
    Chrome DevTools Protocol 기반 크롬 모니터링 클래스

    CDP HTTP 엔드포인트에서 열려있는 탭 정보를 주기적으로 수집하고
    차단 URL 패턴과 비교하여 알림을 생성합니다.
    """

    CHROME_PROCESS_NAMES = {"chrome.exe", "chromium.exe", "msedge.exe"}

    def __init__(self, config: ChromeConfig, messenger: Optional[MessengerConfig] = None):
        self._config = config
        # localhost 대신 127.0.0.1을 사용하여 IPv6 DNS 해석 타임아웃(2초 지연)을 방지합니다.
        self._cdp_url = f"http://127.0.0.1:{config.debug_port}/json"
        self._last_tabs: List[ChromeTabInfo] = []
        # 메신저 URL 부분일치 패턴(소문자)
        self._messenger_patterns = (
            [p.lower() for p in messenger.url_patterns]
            if messenger and messenger.enabled else []
        )

    # ─── 공개 메서드 ──────────────────────────────────────────────

    def collect(self) -> Tuple[ChromeInfo, List[Alert]]:
        """
        크롬 상태와 탭 정보를 수집합니다.

        Returns:
            (ChromeInfo, 알림 목록) 튜플
        """
        if not self._config.enabled:
            return ChromeInfo(is_running=False), []

        is_running = self._is_chrome_running()
        if not is_running:
            logger.debug("크롬이 실행 중이지 않음")
            return ChromeInfo(is_running=False), []

        # CDP로 탭 정보 수집 시도
        tabs, debug_mode = self._get_tabs_via_cdp()

        if not debug_mode:
            logger.debug(
                f"크롬 CDP 미활성 (포트 {self._config.debug_port}). "
                "크롬 바로가기에 --remote-debugging-port 및 --user-data-dir 옵션 필요"
            )

        # 차단 URL 검사
        alerts = self._check_blocked_urls(tabs)
        # 메신저 URL 검사
        alerts.extend(self._check_messenger_urls(tabs))

        chrome_info = ChromeInfo(
            is_running=True,
            debug_mode=debug_mode,
            tabs=tabs,
            tab_count=len(tabs),
        )

        self._last_tabs = tabs
        logger.debug(
            f"크롬 수집 완료: {'CDP' if debug_mode else '비CDP'} 모드, "
            f"탭 {len(tabs)}개, 알림 {len(alerts)}개"
        )
        return chrome_info, alerts

    def is_cdp_available(self) -> bool:
        """CDP 디버깅 포트가 열려있는지 확인합니다."""
        try:
            urlopen(self._cdp_url, timeout=1)
            return True
        except Exception:
            return False

    def launch_chrome_with_cdp(self) -> bool:
        """
        CDP가 활성화된 독립 크롬 인스턴스를 실행합니다.
        이미 CDP가 활성화된 경우 실행하지 않습니다.

        Returns:
            True: 실행 성공 또는 이미 활성화, False: 실행 실패
        """
        if self.is_cdp_available():
            logger.debug("CDP가 이미 활성화되어 있음")
            return True

        try:
            args = [
                "chrome.exe",
                f"--remote-debugging-port={self._config.debug_port}",
                f"--user-data-dir={self._config.user_data_dir}",
            ]
            subprocess.Popen(args)
            # 크롬 시작 대기
            for _ in range(10):
                time.sleep(1)
                if self.is_cdp_available():
                    logger.info(f"CDP 크롬 인스턴스 실행 성공 (포트 {self._config.debug_port})")
                    return True
            logger.warning("CDP 크롬 시작 타임아웃")
            return False
        except FileNotFoundError:
            logger.error("chrome.exe를 찾을 수 없습니다. 크롬이 설치되어 있는지 확인하세요.")
            return False
        except Exception as e:
            logger.error(f"CDP 크롬 실행 실패: {e}")
            return False

    # ─── 내부 메서드 ──────────────────────────────────────────────

    def _is_chrome_running(self) -> bool:
        """크롬 프로세스가 실행 중인지 확인합니다."""
        try:
            for proc in psutil.process_iter(["name"]):
                if (proc.info.get("name") or "").lower() in self.CHROME_PROCESS_NAMES:
                    return True
        except Exception:
            pass
        return False

    def _get_tabs_via_cdp(self) -> Tuple[List[ChromeTabInfo], bool]:
        """
        CDP HTTP 엔드포인트에서 탭 목록을 가져옵니다.

        Returns:
            (탭 목록, CDP 활성 여부) 튜플
        """
        try:
            with urlopen(self._cdp_url, timeout=2) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)

            tabs = []
            for item in data:
                # type이 "page"인 항목만 탭으로 처리
                if item.get("type") != "page":
                    continue
                tab = ChromeTabInfo(
                    tab_id=item.get("id", ""),
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    is_active=False,
                    favicon_url=item.get("faviconUrl"),
                )
                tabs.append(tab)

            # 첫 번째 탭을 활성으로 표시 (휴리스틱)
            if tabs:
                tabs[0].is_active = True

            logger.info(f"CDP 탭 수집: {len(tabs)}개")
            for tab in tabs:
                logger.info(f"  [{tab.tab_id[:8]}] {tab.title[:50]} | {tab.url[:80]}")

            return tabs, True

        except URLError as e:
            logger.debug(f"CDP 연결 실패: {e}")
            return [], False
        except json.JSONDecodeError as e:
            logger.warning(f"CDP 응답 파싱 실패: {e}")
            return [], False
        except Exception as e:
            logger.warning(f"CDP 탭 수집 오류: {e}")
            return [], False

    def _check_blocked_urls(self, tabs: List[ChromeTabInfo]) -> List[Alert]:
        """
        탭 URL을 차단 목록과 비교하여 알림을 생성합니다.
        부분 일치 방식으로 검사합니다.
        """
        alerts = []
        blocked_patterns = [p.lower() for p in self._config.blocked_urls]

        for tab in tabs:
            url_lower = tab.url.lower()
            for pattern in blocked_patterns:
                if pattern in url_lower:
                    alert = Alert(
                        level="warning",
                        code="BLOCKED_URL",
                        message=f"차단된 URL 접근: {tab.url}",
                        url=tab.url,
                    )
                    alerts.append(alert)
                    logger.warning(f"차단 URL 감지: [{pattern}] {tab.url}")
                    break  # 같은 탭 중복 알림 방지

        return alerts

    def _check_messenger_urls(self, tabs: List[ChromeTabInfo]) -> List[Alert]:
        """
        탭 URL을 메신저 패턴과 비교하여 알림을 생성합니다.
        부분 일치 방식으로 검사합니다 (차단 URL과 별개 카테고리: MESSENGER_DETECTED).
        """
        alerts = []
        for tab in tabs:
            url_lower = tab.url.lower()
            for pattern in self._messenger_patterns:
                if pattern in url_lower:
                    alerts.append(Alert(
                        level="warning",
                        code="MESSENGER_DETECTED",
                        message=f"메신저 사이트 접근: {tab.url}",
                        url=tab.url,
                    ))
                    logger.warning(f"메신저 URL 감지: [{pattern}] {tab.url}")
                    break  # 같은 탭 중복 알림 방지

        return alerts

    def get_cdp_setup_guide(self) -> str:
        """CDP 설정 가이드 문자열을 반환합니다."""
        port = self._config.debug_port
        udd = self._config.user_data_dir
        return (
            f"[크롬 CDP 설정 방법]\n"
            f"1. 크롬 바로가기 우클릭 → 속성\n"
            f"2. '대상' 끝에 두 옵션 추가:\n"
            f"   --remote-debugging-port={port} --user-data-dir={udd}\n"
            f"   예: \"C:\\...\\chrome.exe\" --remote-debugging-port={port} --user-data-dir={udd}\n"
            f"3. 크롬 재시작 후 http://127.0.0.1:{port}/json 접근 확인\n\n"
            f"[PowerShell 실행 명령]\n"
            f"Start-Process \"chrome.exe\" -ArgumentList "
            f"\"--remote-debugging-port={port}\", \"--user-data-dir={udd}\"\n"
        )
