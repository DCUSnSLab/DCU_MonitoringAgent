"""
창 제목 기반 탐지기 (Window Title Detector)

브라우저 창 제목(= 그 창에서 현재 보고 있는 탭의 제목)에서 키워드를 찾아
메신저·차단 사이트 사용을 탐지합니다. CDP(원격 디버깅) 없이 일반 브라우저에서도 동작합니다.

- 차단 키워드 일치   → BLOCKED_URL
- 메신저 키워드 일치 → MESSENGER_DETECTED

창 제목은 그 창의 '앞에 보이는 탭' 하나만 반영하므로, 뒤에 숨은 탭은 CDP(URL) 탐지로만 잡힙니다.
"""

from typing import Iterable, List, Optional, Set

from agent.config import WindowTitleConfig
from agent.models import Alert
from agent.monitors.window_monitor import VisibleWindow
from agent.utils.logger import get_logger

logger = get_logger("analyzers.title")


class WindowTitleDetector:
    """브라우저 창 제목을 키워드와 비교해 알림을 생성하는 클래스"""

    def __init__(self, config: WindowTitleConfig):
        self._enabled = config.enabled
        self._browsers = {b.lower() for b in config.browser_processes}
        self._messenger_keywords = [k.lower() for k in config.messenger_keywords if k]
        self._blocked_keywords = [k.lower() for k in config.blocked_keywords if k]

    def check(
        self,
        windows: Iterable[VisibleWindow],
        skip_pids: Optional[Set[int]] = None,
    ) -> List[Alert]:
        """
        브라우저 창 제목을 검사하여 알림 목록을 반환합니다.

        Args:
            windows: 보이는 창 목록
            skip_pids: 검사에서 제외할 프로세스 PID (예: CDP로 이미 URL 검사 중인 브라우저)

        Returns:
            알림 목록 (창마다 차단·메신저 각 카테고리 최대 1건)
        """
        if not self._enabled:
            return []

        skip = skip_pids or set()
        alerts: List[Alert] = []

        for win in windows:
            if win.process_name not in self._browsers or win.pid in skip:
                continue

            title_lower = win.title.lower()

            blocked = self._first_match(title_lower, self._blocked_keywords)
            if blocked:
                alerts.append(Alert(
                    level="warning",
                    code="BLOCKED_URL",
                    message=f"차단 사이트 창 감지: {win.title}",
                    window_title=win.title,
                    window_is_foreground=win.is_foreground,
                ))
                logger.debug(f"차단 사이트 창 감지: [{blocked}] {win.title}")

            messenger = self._first_match(title_lower, self._messenger_keywords)
            if messenger:
                alerts.append(Alert(
                    level="warning",
                    code="MESSENGER_DETECTED",
                    message=f"메신저 사이트 창 감지: {win.title}",
                    window_title=win.title,
                    window_is_foreground=win.is_foreground,
                ))
                logger.debug(f"메신저 사이트 창 감지: [{messenger}] {win.title}")

        return alerts

    @staticmethod
    def _first_match(text: str, keywords: List[str]) -> Optional[str]:
        """text에 포함된 첫 번째 키워드를 반환합니다. 없으면 None."""
        for kw in keywords:
            if kw in text:
                return kw
        return None
