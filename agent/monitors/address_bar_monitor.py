"""
주소창 모니터링 모듈 (UI Automation)

크롬 계열 브라우저(Chrome·Edge·Whale) 창의 주소창 글자를 Windows UI Automation으로 읽어,
CDP와 같은 URL 목록(chrome.blocked_urls, messenger.url_patterns)으로 차단·메신저 사이트를 탐지합니다.
CDP(원격 디버깅) 없이 일반 크롬 프로필에서도 동작합니다.

- 창마다 '앞에 보이는 탭' 하나의 주소만 읽힘 (뒤에 숨은 탭은 CDP로만 확인 가능)
- 주소창 글자는 화면 표시 그대로라 https:// 와 www. 가 빠질 수 있음 (예: "chatgpt.com/c/...")
- 사용자가 주소창에 입력 중이면 실제 주소가 아닐 수 있어 읽지 않음 (그 창은 창 제목으로 판정)
- UI Automation을 쓸 수 없는 환경이면 비활성화되고, 창 제목 탐지가 대신 동작
"""

import ctypes
import time
from typing import Dict, List, Optional, Set, Tuple

from agent.config import AddressBarConfig, ChromeConfig, MessengerConfig
from agent.models import Alert
from agent.monitors.window_monitor import VisibleWindow
from agent.utils.logger import get_logger

logger = get_logger("monitors.address_bar")


class AddressBarMonitor:
    """
    브라우저 주소창 모니터링 클래스

    창별 주소창 요소를 한 번 찾아 캐시해 두고, 매 주기에는 값만 읽습니다.
    (주소창 탐색은 창당 약 10ms, 값 읽기는 1ms 미만)
    """

    # 크로미움 계열 브라우저 주소창의 UI Automation 클래스명
    OMNIBOX_CLASS = "OmniboxViewViews"
    # 주소창을 찾지 못한 창(앱 창·팝업 등)을 다시 찾아보기까지의 대기 시간(초)
    RETRY_SECONDS = 30
    # 응답 없는 브라우저 때문에 수집 주기가 멈추지 않도록 줄인 UI Automation 호출 제한시간(ms)
    UIA_TIMEOUT_MS = 1000

    def __init__(
        self,
        config: AddressBarConfig,
        chrome: ChromeConfig,
        messenger: Optional[MessengerConfig] = None,
    ):
        self._enabled = config.enabled
        self._browsers = {b.lower() for b in config.browser_processes}
        self._blocked_patterns = [p.lower() for p in chrome.blocked_urls if p]
        self._messenger_patterns = (
            [p.lower() for p in messenger.url_patterns if p]
            if messenger and messenger.enabled else []
        )

        # UI Automation 객체는 수집 스레드에서 처음 쓸 때 만든다 (COM 객체는 만든 스레드에서 사용)
        self._uia = None
        self._uia_mod = None
        self._omnibox_condition = None
        self._uia_failed = False

        self._omnibox_cache: Dict[int, object] = {}  # hwnd → 주소창 요소
        self._missing_since: Dict[int, float] = {}  # hwnd → 주소창을 못 찾은 시각

    # ─── 공개 메서드 ──────────────────────────────────────────────

    def collect(
        self,
        windows: List[VisibleWindow],
        skip_pids: Optional[Set[int]] = None,
    ) -> Tuple[Dict[int, str], List[Alert]]:
        """
        브라우저 창의 주소창을 읽어 차단·메신저 사이트를 검사합니다.

        Args:
            windows: 보이는 창 목록
            skip_pids: 제외할 프로세스 PID (예: CDP로 이미 URL 검사 중인 브라우저)

        Returns:
            ({hwnd: 주소창 URL}, 알림 목록) 튜플.
            주소를 읽지 못한 창은 dict에 없으므로 호출 측에서 창 제목으로 판정한다.
        """
        urls = self.read_urls(windows, skip_pids)
        alerts: List[Alert] = []
        for win in windows:
            url = urls.get(win.hwnd)
            if url:
                alerts.extend(self._check_url(win, url))
        return urls, alerts

    def read_urls(
        self,
        windows: List[VisibleWindow],
        skip_pids: Optional[Set[int]] = None,
    ) -> Dict[int, str]:
        """브라우저 창별 주소창 글자 {hwnd: url}을 반환합니다. 읽지 못한 창은 제외."""
        if not self._enabled:
            return {}

        skip = skip_pids or set()
        targets = [w for w in windows if w.process_name in self._browsers and w.pid not in skip]
        self._forget_closed({w.hwnd for w in targets})
        if not targets or not self._ensure_uia():
            return {}

        urls: Dict[int, str] = {}
        for win in targets:
            url = self._read_window(win.hwnd)
            if url:
                urls[win.hwnd] = url
        return urls

    # ─── 내부 메서드 ──────────────────────────────────────────────

    def _read_window(self, hwnd: int) -> Optional[str]:
        """창 하나의 주소창 글자를 읽습니다. 읽을 수 없으면 None."""
        # 응답 없는 창은 건드리지 않음 (UI Automation 호출이 제한시간까지 대기하므로)
        if self._is_hung(hwnd):
            return None

        element = self._omnibox_cache.get(hwnd) or self._find_omnibox(hwnd)
        if element is None:
            return None

        try:
            # 입력 중인 주소창의 글자는 실제 주소가 아닐 수 있음
            if element.GetCurrentPropertyValue(self._uia_mod.UIA_HasKeyboardFocusPropertyId):
                return None
            value = element.GetCurrentPropertyValue(self._uia_mod.UIA_ValueValuePropertyId)
        except Exception as e:
            # 창 구조가 바뀌어 요소가 무효해진 경우 → 다음 주기에 다시 찾음
            logger.debug(f"주소창 읽기 실패 (hwnd={hwnd}): {e}")
            self._omnibox_cache.pop(hwnd, None)
            return None

        if not isinstance(value, str):
            return None
        return value.strip() or None

    def _find_omnibox(self, hwnd: int):
        """창에서 주소창 요소를 찾아 캐시합니다. 못 찾은 창은 RETRY_SECONDS 동안 다시 찾지 않음."""
        missed_at = self._missing_since.get(hwnd)
        if missed_at is not None and time.monotonic() - missed_at < self.RETRY_SECONDS:
            return None

        try:
            root = self._uia.ElementFromHandle(hwnd)
            element = root.FindFirst(self._uia_mod.TreeScope_Descendants, self._omnibox_condition)
        except Exception as e:
            logger.debug(f"주소창 탐색 실패 (hwnd={hwnd}): {e}")
            element = None

        # comtypes는 찾지 못하면 NULL 포인터(거짓)를 돌려준다
        if not element:
            self._missing_since[hwnd] = time.monotonic()
            return None

        self._missing_since.pop(hwnd, None)
        self._omnibox_cache[hwnd] = element
        return element

    def _forget_closed(self, alive: Set[int]):
        """닫힌(이번 주기에 없는) 창의 캐시를 지웁니다."""
        for cache in (self._omnibox_cache, self._missing_since):
            for hwnd in [h for h in cache if h not in alive]:
                del cache[hwnd]

    def _check_url(self, win: VisibleWindow, url: str) -> List[Alert]:
        """주소창 URL을 차단·메신저 패턴과 비교합니다 (CDP와 같은 부분일치, 카테고리별 최대 1건)."""
        url_lower = url.lower()
        alerts: List[Alert] = []

        if any(p in url_lower for p in self._blocked_patterns):
            alerts.append(Alert(
                level="warning",
                code="BLOCKED_URL",
                message=f"차단된 URL 접근: {url}",
                url=url,
                window_title=win.title,
                window_is_foreground=win.is_foreground,
            ))
            logger.debug(f"주소창 차단 URL 감지: {url} ({win.process_name})")

        if any(p in url_lower for p in self._messenger_patterns):
            alerts.append(Alert(
                level="warning",
                code="MESSENGER_DETECTED",
                message=f"메신저 사이트 접근: {url}",
                url=url,
                window_title=win.title,
                window_is_foreground=win.is_foreground,
            ))
            logger.debug(f"주소창 메신저 URL 감지: {url} ({win.process_name})")

        return alerts

    def _ensure_uia(self) -> bool:
        """UI Automation을 준비합니다 (처음 호출한 스레드에서 1회). 실패하면 이후 계속 비활성."""
        if self._uia is not None:
            return True
        if self._uia_failed:
            return False
        try:
            self._uia_mod, self._uia = self._create_uia()
            self._omnibox_condition = self._uia.CreatePropertyCondition(
                self._uia_mod.UIA_ClassNamePropertyId, self.OMNIBOX_CLASS
            )
            logger.info("주소창 URL 읽기 활성 (UI Automation)")
            return True
        except Exception as e:
            self._uia = None
            self._uia_failed = True
            logger.warning(f"주소창 URL 읽기 비활성 (UI Automation 초기화 실패, 창 제목 탐지로 대체): {e}")
            return False

    @classmethod
    def _create_uia(cls):
        """UI Automation 타입 모듈과 IUIAutomation 객체를 만듭니다."""
        import comtypes
        import comtypes.client

        # comtypes는 처음 import한 스레드만 COM을 초기화하므로, 다른 스레드에서 먼저 import된 경우를 대비해
        # 이 스레드도 같은 방식(STA)으로 초기화한다. 이미 초기화돼 있으면 그대로 쓴다.
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except OSError:
            pass

        mod = comtypes.client.GetModule("UIAutomationCore.dll")
        try:
            # Windows 8+: 호출 제한시간을 줄여 응답 없는 창에서 오래 기다리지 않게 한다 (기본 2초/20초)
            uia = comtypes.client.CreateObject(mod.CUIAutomation8, interface=mod.IUIAutomation2)
            uia.ConnectionTimeout = cls.UIA_TIMEOUT_MS
            uia.TransactionTimeout = cls.UIA_TIMEOUT_MS
        except Exception:
            uia = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
        return mod, uia

    @staticmethod
    def _is_hung(hwnd: int) -> bool:
        """창이 응답 없음 상태인지 확인합니다."""
        try:
            return bool(ctypes.windll.user32.IsHungAppWindow(hwnd))
        except Exception:
            return False
