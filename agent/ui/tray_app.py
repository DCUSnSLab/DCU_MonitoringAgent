"""
시스템 트레이 UI 모듈

pystray 기반 시스템 트레이 아이콘과 메뉴를 제공합니다.

기능:
- 시스템 트레이 아이콘 (상태에 따른 색상 변화)
- 우클릭 메뉴 (상태 보기, 로그 열기, 종료)
- 종료 방지: 비밀번호 입력 대화상자
- Windows 레지스트리 자동 시작 등록/해제
"""

import os
import sys
import threading
import winreg
from typing import Callable, Optional

from PIL import Image, ImageDraw

try:
    import pystray
    from pystray import MenuItem, Menu
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

from agent.utils.logger import get_logger

logger = get_logger("ui.tray")

# 자동 시작 레지스트리 키
AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_APP_NAME = "DCU_MonitoringAgent"


class TrayApp:
    """
    시스템 트레이 애플리케이션 클래스

    MonitoringAgent의 UI 레이어로,
    트레이 아이콘과 메뉴를 통해 에이전트를 제어합니다.
    """

    def __init__(
        self,
        agent,
        on_stop_callback: Optional[Callable] = None,
    ):
        """
        Args:
            agent: MonitoringAgent 인스턴스
            on_stop_callback: 에이전트 종료 시 호출할 콜백
        """
        self._agent = agent
        self._on_stop = on_stop_callback
        self._icon: Optional[pystray.Icon] = None if PYSTRAY_AVAILABLE else None
        self._status = "running"  # running | stopped | error

    # ─── 공개 메서드 ──────────────────────────────────────────────

    def run(self):
        """트레이 앱을 시작합니다 (메인 스레드에서 실행)."""
        if not PYSTRAY_AVAILABLE:
            logger.error("pystray가 설치되지 않았습니다. 트레이 UI를 사용할 수 없습니다.")
            return

        logger.info("시스템 트레이 앱 시작")
        icon_image = self._create_icon_image("green")
        menu = self._build_menu()

        self._icon = pystray.Icon(
            name=AUTOSTART_APP_NAME,
            icon=icon_image,
            title="DCU 모니터링 에이전트",
            menu=menu,
        )
        self._icon.run()

    def update_status(self, status: str):
        """
        트레이 아이콘 색상을 상태에 따라 변경합니다.

        Args:
            status: 'running' (녹색), 'stopped' (회색), 'alert' (빨간색)
        """
        self._status = status
        if not self._icon:
            return

        color_map = {
            "running": "green",
            "safe": "green",
            "stopped": "gray",
            "warning": "orange",
            "alert": "red",
            "danger": "red",
        }
        color = color_map.get(status, "gray")
        self._icon.icon = self._create_icon_image(color)
        self._icon.title = f"DCU 모니터링 에이전트 [{status}]"

    def show_notification(self, title: str, message: str):
        """Windows 토스트 알림을 표시합니다."""
        if self._icon:
            try:
                self._icon.notify(message, title)
            except Exception as e:
                logger.debug(f"알림 표시 실패: {e}")

    def stop(self):
        """트레이 앱을 종료합니다."""
        if self._icon:
            self._icon.stop()

    # ─── 메뉴 구성 ────────────────────────────────────────────────

    def _build_menu(self) -> "Menu":
        """트레이 우클릭 메뉴를 구성합니다."""
        config = self._agent.config

        return Menu(
            MenuItem("DCU 모니터링 에이전트", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("📊 현재 상태 보기", self._on_show_status),
            MenuItem("📄 로그 파일 열기", self._on_open_log),
            Menu.SEPARATOR,
            MenuItem("🌐 서버 주소 변경", self._on_change_server),
            Menu.SEPARATOR,
            MenuItem(
                "🚀 시작 프로그램에 등록",
                self._on_register_autostart,
                checked=lambda item: self._is_autostart_registered(),
            ),
            Menu.SEPARATOR,
            MenuItem("❌ 종료", self._on_exit_click),
        )

    # ─── 메뉴 이벤트 핸들러 ───────────────────────────────────────

    def _on_change_server(self, icon, item):
        """서버 주소 변경 팝업"""
        import tkinter as tk
        from tkinter import simpledialog, messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        current_url = self._agent.config.server.base_url
        new_url = simpledialog.askstring(
            "서버 주소 변경",
            "모니터링 서버의 기본 주소를 입력하세요\n(예: http://203.250.34.174:8000):",
            initialvalue=current_url,
            parent=root,
        )
        
        if new_url and new_url.strip() != current_url:
            new_url = new_url.strip()
            if not new_url.startswith("http"):
                new_url = "http://" + new_url
                
            self._agent.config.server.base_url = new_url
            
            # 서버 통신이 활성화되어 있지 않다면 활성화
            if not self._agent.config.server.enabled:
                self._agent.config.server.enabled = True
                if self._agent._api_client:
                    self._agent._api_client.start()
                    
            # 설정 저장
            if hasattr(self._agent, 'config_mgr'):
                self._agent.config_mgr.save()
            
            # 네트워크 재등록을 위해 상태 변경 강제 플래그 
            if getattr(self._agent, '_api_client', None):
                self._agent._api_client._registered = False
            
            logger.info(f"서버 주소가 변경되었습니다: {new_url}")
            messagebox.showinfo("변경 완료", f"서버 주소가 '{new_url}'로 변경되었습니다.", parent=root)
            
        root.destroy()

    def _on_show_status(self, icon, item):
        """현재 에이전트 상태를 팝업으로 표시합니다."""
        cfg = self._agent.config
        collector = self._agent._data_collector

        lines = [
            f"에이전트 ID: {cfg.agent.id}",
            f"실습실: {cfg.agent.lab_name}",
            f"상태: {'실행 중' if self._agent.is_running else '정지'}",
            f"모니터링 주기: {cfg.monitoring.interval_seconds}초",
            f"프로세스 모드: {cfg.monitoring.process.mode}",
            f"크롬 CDP: {'활성' if cfg.monitoring.chrome.enabled else '비활성'}",
        ]
        if collector:
            lines.append(f"대기 중인 보고서: {collector.queue_size()}개")

        message = "\n".join(lines)
        self.show_notification("에이전트 상태", message)
        logger.info(f"상태 조회:\n{message}")

    def _on_open_log(self, icon, item):
        """로그 파일을 메모장으로 엽니다."""
        log_file = self._agent.config.logging.file
        # 절대 경로로 변환
        if not os.path.isabs(log_file):
            project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            log_file = os.path.join(project_root, log_file)

        if os.path.exists(log_file):
            os.startfile(log_file)
        else:
            self.show_notification("로그 파일", f"로그 파일이 없습니다:\n{log_file}")

    def _on_register_autostart(self, icon, item):
        """시작 프로그램 등록/해제를 토글합니다."""
        if self._is_autostart_registered():
            self._unregister_autostart()
            self.show_notification("자동 시작", "시작 프로그램에서 제거되었습니다.")
            logger.info("자동 시작 해제")
        else:
            self._register_autostart()
            self.show_notification("자동 시작", "시작 프로그램에 등록되었습니다.")
            logger.info("자동 시작 등록")

    def _on_exit_click(self, icon, item):
        """종료 클릭 핸들러 - 종료 방지 설정 시 비밀번호 확인."""
        cfg = self._agent.config

        if cfg.security.prevent_exit:
            # 별도 스레드에서 입력 대화상자 표시 (트레이 스레드 블로킹 방지)
            threading.Thread(
                target=self._prompt_exit_password,
                daemon=True
            ).start()
        else:
            self._do_exit()

    def _prompt_exit_password(self):
        """비밀번호 입력 대화상자를 표시하고 검증합니다."""
        try:
            import tkinter as tk
            from tkinter import simpledialog, messagebox

            root = tk.Tk()
            root.withdraw()  # 메인 창 숨김
            root.attributes('-topmost', True)

            password = simpledialog.askstring(
                "종료 확인",
                "에이전트를 종료하려면 관리자 비밀번호를 입력하세요:",
                show="*",
                parent=root,
            )
            root.destroy()

            if password is None:
                # 취소 클릭
                logger.info("종료 취소됨")
                return

            correct = self._agent.config.security.exit_password
            if password == correct:
                logger.info("올바른 비밀번호로 종료 요청")
                self._do_exit()
            else:
                logger.warning(f"잘못된 비밀번호로 종료 시도 (입력: {'*' * len(password)})")
                # 실패 알림
                root2 = tk.Tk()
                root2.withdraw()
                root2.attributes('-topmost', True)
                messagebox.showerror(
                    "종료 실패",
                    "비밀번호가 올바르지 않습니다.",
                    parent=root2,
                )
                root2.destroy()

        except Exception as e:
            logger.error(f"비밀번호 대화상자 오류: {e}")

    def _do_exit(self):
        """실제 종료를 수행합니다."""
        logger.info("에이전트 종료 시작")
        self._agent.stop(password=self._agent.config.security.exit_password)
        if self._icon:
            self._icon.stop()
        if self._on_stop:
            self._on_stop()

    # ─── 아이콘 생성 ──────────────────────────────────────────────

    @staticmethod
    def _create_icon_image(color: str = "green") -> Image.Image:
        """
        32x32 트레이 아이콘 이미지를 생성합니다.

        색상:
          green  → 정상 실행 중
          gray   → 정지
          red    → 오류/위험 알림
          orange → 경고
        """
        size = 32
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        color_map = {
            "green":  "#2ecc71",
            "gray":   "#95a5a6",
            "red":    "#e74c3c",
            "orange": "#e67e22",
        }
        fill = color_map.get(color, "#2ecc71")
        outline = "#ffffff"

        # 원형 아이콘
        margin = 3
        draw.ellipse(
            [margin, margin, size - margin, size - margin],
            fill=fill,
            outline=outline,
            width=2,
        )

        # 중앙에 작은 흰색 D 글자 (DCU 상징)
        draw.text((10, 8), "D", fill="white")

        return img

    # ─── 자동 시작 레지스트리 ─────────────────────────────────────

    @staticmethod
    def _get_exe_path() -> str:
        """현재 실행 파일 경로를 반환합니다."""
        if getattr(sys, 'frozen', False):
            # PyInstaller로 패키징된 경우
            return sys.executable
        else:
            # 개발 환경
            python_exe = sys.executable
            main_script = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "agent", "main.py"
            )
            return f'"{python_exe}" "{main_script}"'

    def _is_autostart_registered(self) -> bool:
        """자동 시작이 레지스트리에 등록되어 있는지 확인합니다."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
                winreg.QueryValueEx(key, AUTOSTART_APP_NAME)
                return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _register_autostart(self):
        """Windows 레지스트리에 자동 시작을 등록합니다."""
        try:
            exe_path = self._get_exe_path()
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                AUTOSTART_REG_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, AUTOSTART_APP_NAME, 0, winreg.REG_SZ, exe_path)
            logger.info(f"자동 시작 등록: {exe_path}")
        except Exception as e:
            logger.error(f"자동 시작 등록 실패: {e}")

    def _unregister_autostart(self):
        """Windows 레지스트리에서 자동 시작을 제거합니다."""
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                AUTOSTART_REG_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, AUTOSTART_APP_NAME)
            logger.info("자동 시작 해제 완료")
        except FileNotFoundError:
            logger.debug("자동 시작 레지스트리 키가 없음 (이미 해제됨)")
        except Exception as e:
            logger.error(f"자동 시작 해제 실패: {e}")
