"""
윈도우(창) 모니터링 모듈

pywin32를 이용해 현재 포그라운드 창 및 각 프로세스의 창 제목을 수집합니다.
- 현재 활성화된(포그라운드) 창 정보
- PID별 창 제목 매핑
"""

from typing import Dict, List, NamedTuple, Optional

import psutil

from agent.utils.logger import get_logger

logger = get_logger("monitors.window")


class VisibleWindow(NamedTuple):
    """보이는 최상위 창 1개의 정보"""
    hwnd: int
    pid: int
    process_name: str  # 소문자 (예: "chrome.exe"), 조회 실패 시 ""
    title: str


class WindowMonitor:
    """
    윈도우(창) 모니터링 클래스

    win32gui를 이용해 실행 중인 창 목록과
    포그라운드 창 정보를 수집합니다.
    """

    def __init__(self):
        self._win32gui = None
        self._win32process = None
        self._init_win32()

    def _init_win32(self):
        """win32 모듈 초기화"""
        try:
            import win32gui
            import win32process
            self._win32gui = win32gui
            self._win32process = win32process
            logger.debug("win32gui/win32process 초기화 성공")
        except ImportError as e:
            logger.warning(f"win32gui 초기화 실패 (창 정보 수집 불가): {e}")

    # ─── 공개 메서드 ──────────────────────────────────────────────

    def get_foreground_window(self) -> Optional[str]:
        """
        현재 포그라운드(활성화된) 창의 제목을 반환합니다.

        Returns:
            창 제목 문자열, 또는 None
        """
        if not self._win32gui:
            return None
        try:
            hwnd = self._win32gui.GetForegroundWindow()
            title = self._win32gui.GetWindowText(hwnd)
            return title if title else None
        except Exception as e:
            logger.debug(f"포그라운드 창 조회 실패: {e}")
            return None

    def get_foreground_pid(self) -> Optional[int]:
        """
        현재 포그라운드 창의 프로세스 PID를 반환합니다.

        Returns:
            PID 정수, 또는 None
        """
        if not self._win32gui or not self._win32process:
            return None
        try:
            hwnd = self._win32gui.GetForegroundWindow()
            _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
            return pid
        except Exception as e:
            logger.debug(f"포그라운드 PID 조회 실패: {e}")
            return None

    def get_window_titles_by_pid(self) -> Dict[int, str]:
        """
        모든 보이는 창의 PID → 창 제목 딕셔너리를 반환합니다.

        Returns:
            {pid: window_title} 딕셔너리
        """
        if not self._win32gui or not self._win32process:
            return {}

        pid_titles: Dict[int, str] = {}

        def enum_callback(hwnd, _):
            try:
                # 보이는 창만 처리
                if not self._win32gui.IsWindowVisible(hwnd):
                    return True
                title = self._win32gui.GetWindowText(hwnd)
                if not title:
                    return True
                _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
                # 같은 PID에 창이 여러 개면 첫 번째(메인) 창 제목 사용
                if pid not in pid_titles:
                    pid_titles[pid] = title
            except Exception:
                pass
            return True  # 계속 열거

        try:
            self._win32gui.EnumWindows(enum_callback, None)
        except Exception as e:
            logger.debug(f"창 목록 열거 실패: {e}")

        logger.debug(f"창 제목 수집 완료: {len(pid_titles)}개")
        return pid_titles

    def get_visible_windows(self) -> List[VisibleWindow]:
        """
        보이는 모든 최상위 창의 (hwnd, pid, 프로세스명, 제목) 목록을 반환합니다.

        get_window_titles_by_pid()와 달리 같은 프로세스의 창이 여러 개여도
        (예: 크롬 창 여러 개) 모두 반환합니다.

        Returns:
            VisibleWindow 목록
        """
        if not self._win32gui or not self._win32process:
            return []

        windows: List[VisibleWindow] = []
        names: Dict[int, str] = {}

        def enum_callback(hwnd, _):
            try:
                if not self._win32gui.IsWindowVisible(hwnd):
                    return True
                title = self._win32gui.GetWindowText(hwnd)
                if not title:
                    return True
                _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
                if pid not in names:
                    names[pid] = self._process_name(pid)
                windows.append(VisibleWindow(hwnd, pid, names[pid], title))
            except Exception:
                pass
            return True  # 계속 열거

        try:
            self._win32gui.EnumWindows(enum_callback, None)
        except Exception as e:
            logger.debug(f"창 목록 열거 실패: {e}")

        return windows

    @staticmethod
    def _process_name(pid: int) -> str:
        """PID의 프로세스 이름(소문자)을 반환합니다. 조회 실패 시 빈 문자열."""
        try:
            return psutil.Process(pid).name().lower()
        except Exception:
            return ""
