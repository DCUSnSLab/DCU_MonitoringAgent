"""
설정 관리 모듈

YAML 기반 설정 파일을 로드하고 관리합니다.
Pydantic v1 모델로 설정 값의 유효성을 검증합니다.
"""

import os
import sys
import uuid
import yaml
from typing import List, Optional
from pydantic import BaseModel, Field


# ─── 설정 모델 정의 ──────────────────────────────────────────────

class AgentConfig(BaseModel):
    """에이전트 기본 설정"""
    id: str = ""
    lab_name: str = "실습실"
    version: str = "1.0.10"
    # OTA 배포 채널: stable(실습실 전체) / develop(개발·검증용 PC). 빌드에 구워진 값을 따른다
    channel: str = "stable"


class ProcessConfig(BaseModel):
    """프로세스 모니터링 설정"""
    mode: str = "all"  # all | whitelist | blacklist
    whitelist: List[str] = Field(default_factory=list)
    blacklist: List[str] = Field(default_factory=list)
    # 프로세스 모드와 무관하게 항상 차단 프로그램으로 탐지할 목록 (AI 데스크톱 앱 등)
    # 설정 키가 없는 기존 PC(OTA 갱신)에서도 동작하도록 기본값을 둔다.
    always_blocked: List[str] = Field(
        default_factory=lambda: ["claude.exe", "chatgpt.exe", "chatgpt classic.exe"]
    )


class ChromeConfig(BaseModel):
    """크롬 모니터링 설정"""
    enabled: bool = True
    debug_port: int = 9222
    poll_interval_seconds: int = 5
    blocked_urls: List[str] = Field(default_factory=list)
    # CDP 독립 프로파일 경로 (기존 크롬과 충돌 방지)
    user_data_dir: str = "C:\\chrome-debug"


class MessengerConfig(BaseModel):
    """메신저 탐지 설정 (프로세스 whitelist/blacklist/all 모드와 무관하게 항상 탐지)

    배포 PC는 OTA 갱신 시 기존 설정 파일(APPDATA)을 그대로 쓰므로,
    messenger 섹션이 없는 PC에서도 탐지되도록 기본값을 둔다.
    """
    enabled: bool = True
    # 탐지할 메신저 프로세스명
    process_names: List[str] = Field(
        default_factory=lambda: ["kakaotalk.exe", "telegram.exe", "discord.exe"]
    )
    # 탐지할 메신저 URL 부분일치 패턴
    url_patterns: List[str] = Field(
        default_factory=lambda: [
            "instagram.com", "facebook.com", "messenger.com",
            "web.whatsapp.com", "web.telegram.org", "discord.com",
        ]
    )


class AddressBarConfig(BaseModel):
    """주소창 URL 읽기 설정

    CDP 없이도 크롬 계열 브라우저 창의 주소창 글자를 UI Automation으로 읽어,
    CDP와 같은 URL 목록(chrome.blocked_urls, messenger.url_patterns)으로 차단·메신저 사이트를 탐지한다.
    주소창을 읽지 못한 창은 창 제목 탐지로 판정한다.
    설정 파일에 섹션이 없어도(OTA로 갱신된 기존 PC) 동작하도록 기본값을 둔다.
    """
    enabled: bool = True
    # 주소창을 읽을 브라우저 프로세스 (크로미움 계열만 지원)
    browser_processes: List[str] = Field(
        default_factory=lambda: ["chrome.exe", "msedge.exe", "whale.exe"]
    )


class WindowTitleConfig(BaseModel):
    """창 제목 기반 탐지 설정

    CDP 없이도 브라우저 창 제목(= 그 창에서 보고 있는 탭의 제목)으로 메신저·차단 사이트를 탐지한다.
    오탐을 줄이기 위해 browser_processes 에 해당하는 브라우저 창만 검사한다.
    설정 파일에 섹션이 없어도(OTA로 갱신된 기존 PC) 동작하도록 기본값을 둔다.
    """
    enabled: bool = True
    # 검사 대상 브라우저 프로세스
    browser_processes: List[str] = Field(
        default_factory=lambda: ["chrome.exe", "msedge.exe", "whale.exe", "firefox.exe"]
    )
    # 창 제목에 포함되면 메신저로 판단할 키워드 (대소문자 무시, 부분일치)
    messenger_keywords: List[str] = Field(
        default_factory=lambda: ["Instagram", "Facebook", "Messenger", "WhatsApp", "Telegram", "Discord"]
    )
    # 창 제목에 포함되면 차단 사이트로 판단할 키워드 (대소문자 무시, 부분일치)
    blocked_keywords: List[str] = Field(
        default_factory=lambda: ["ChatGPT", "Gemini", "Claude"]
    )


class MonitoringConfig(BaseModel):
    """모니터링 통합 설정"""
    interval_seconds: int = 5
    track_foreground: bool = True
    process: ProcessConfig = Field(default_factory=ProcessConfig)
    chrome: ChromeConfig = Field(default_factory=ChromeConfig)
    messenger: MessengerConfig = Field(default_factory=MessengerConfig)
    address_bar: AddressBarConfig = Field(default_factory=AddressBarConfig)
    window_title: WindowTitleConfig = Field(default_factory=WindowTitleConfig)


class ServerConfig(BaseModel):
    """서버 통신 설정"""
    enabled: bool = False
    base_url: str = "http://localhost:8080"
    api_key: str = ""
    use_websocket: bool = False
    ws_url: str = "ws://localhost:8080/ws"
    retry_interval_seconds: int = 30
    offline_buffer_size: int = 1000


class SecurityConfig(BaseModel):
    """보안 설정"""
    prevent_exit: bool = True
    exit_password: str = "admin1234"
    hide_from_taskbar: bool = False


class LoggingConfig(BaseModel):
    """로깅 설정"""
    level: str = "INFO"
    file: str = "logs/agent.log"
    max_size_mb: int = 10
    backup_count: int = 5
    console_output: bool = True


class AppConfig(BaseModel):
    """애플리케이션 전체 설정"""
    agent: AgentConfig = Field(default_factory=AgentConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# ─── 설정 관리 클래스 ────────────────────────────────────────────

# OTA 갱신 시 번들 설정 값을 기준으로 맞출 탐지 정책 목록 (설정 경로)
POLICY_LIST_PATHS = (
    ("monitoring", "chrome", "blocked_urls"),
    ("monitoring", "process", "always_blocked"),
    ("monitoring", "messenger", "process_names"),
    ("monitoring", "messenger", "url_patterns"),
    ("monitoring", "address_bar", "browser_processes"),
    ("monitoring", "window_title", "browser_processes"),
    ("monitoring", "window_title", "messenger_keywords"),
    ("monitoring", "window_title", "blocked_keywords"),
)


class ConfigManager:
    """
    설정 파일을 로드하고 관리하는 클래스
    """

    @staticmethod
    def get_default_config_path() -> str:
        if getattr(sys, 'frozen', False):
            # PyInstaller로 패키징된 실행 파일인 경우
            appdata = os.environ.get('LOCALAPPDATA', os.environ.get('APPDATA', ''))
            return os.path.join(appdata, "DCU_MonitoringAgent", "agent_config.yaml")
        else:
            # 개발 환경
            return os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config",
                "agent_config.yaml",
            )

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path or self.get_default_config_path()
        self._config: Optional[AppConfig] = None
        self.load()

    @property
    def config(self) -> AppConfig:
        """현재 설정을 반환합니다."""
        if self._config is None:
            self.load()
        return self._config

    def load(self) -> AppConfig:
        """설정 파일을 로드합니다."""
        # 패키징된 경우, LOCALAPPDATA에 파일이 없으면 내장된 config를 복사
        if getattr(sys, 'frozen', False) and not os.path.exists(self._config_path):
            builtin_config = os.path.join(sys._MEIPASS, "config", "agent_config.yaml")
            if os.path.exists(builtin_config):
                try:
                    os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
                    with open(builtin_config, "r", encoding="utf-8") as f_in:
                        with open(self._config_path, "w", encoding="utf-8") as f_out:
                            f_out.write(f_in.read())
                except Exception:
                    pass

        if os.path.exists(self._config_path):
            with open(self._config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            self._config = AppConfig(**raw)
        else:
            # 설정 파일이 없으면 기본값 사용
            self._config = AppConfig()

        # frozen 환경: 번들된 config의 주요 정보(버전, 서버주소)를 덮어씀
        if getattr(sys, 'frozen', False):
            builtin_config = os.path.join(sys._MEIPASS, "config", "agent_config.yaml")
            if os.path.exists(builtin_config):
                try:
                    with open(builtin_config, "r", encoding="utf-8") as f:
                        builtin_raw = yaml.safe_load(f) or {}
                    
                    need_save = False
                    
                    # 1. 버전·배포 채널 갱신 (채널도 빌드에 구워진 값이 기준: develop 빌드를 설치한 PC 는 develop 채널)
                    builtin_version = builtin_raw.get("agent", {}).get("version", "")
                    if builtin_version and self._config.agent.version != builtin_version:
                        self._config.agent.version = builtin_version
                        need_save = True
                    builtin_channel = builtin_raw.get("agent", {}).get("channel", "")
                    if builtin_channel and self._config.agent.channel != builtin_channel:
                        self._config.agent.channel = builtin_channel
                        need_save = True

                    # 2. 서버 통신 주소 갱신
                    server_raw = builtin_raw.get("server", {})
                    builtin_base_url = server_raw.get("base_url", "")
                    builtin_ws_url = server_raw.get("ws_url", "")
                    
                    if builtin_base_url and self._config.server.base_url != builtin_base_url:
                        self._config.server.base_url = builtin_base_url
                        need_save = True
                        
                    if builtin_ws_url and self._config.server.ws_url != builtin_ws_url:
                        self._config.server.ws_url = builtin_ws_url
                        need_save = True

                    # 3. 탐지 정책 목록 갱신 (OTA로 추가·변경된 탐지 대상이 기존 PC에도 반영되도록)
                    if self._sync_policy_lists(builtin_raw):
                        need_save = True

                    # 덮어쓴 최신 설정을 로컬 %APPDATA% 파일에도 바로 반영 (업데이트 동기화)
                    if need_save and getattr(self, "_config_path", None):
                        # agent_id 생성 전에 save()를 호출하면 id가 빈 값으로 저장될 수 있으므로
                        # 이후 하단에서 id 생성 검사 후에 save()를 진행하도록 플래그만 설정해도 되지만, 
                        # 하단 로직을 수정하지 않기 위해 여기서 바로 id를 생성해줍니다.
                        if not self._config.agent.id:
                            self._config.agent.id = self._generate_agent_id()
                        self.save()
                        
                except Exception as e:
                    print(f"[Config] Error syncing config from update: {e}")
                    pass

        # 에이전트 ID: 물리 랜카드 MAC 으로 고정 계산한 값을 쓴다 (설정 파일이 지워져도 같은 PC 면 같은 ID).
        # 못 구하면 저장된 ID 를 쓰고, 그것도 없으면 예비 ID 를 만든다.
        derived_id = self._derive_agent_id()
        if derived_id and derived_id != self._config.agent.id:
            if self._config.agent.id:
                print(f"[Config] 에이전트 ID 를 물리 랜카드 기준으로 변경: {self._config.agent.id} → {derived_id}")
            self._config.agent.id = derived_id
            self._save_quietly()
        elif not self._config.agent.id:
            self._config.agent.id = self._generate_agent_id()
            self._save_quietly()

        return self._config

    def _save_quietly(self) -> None:
        if getattr(self, "_config_path", None):
            try:
                self.save()
            except Exception as e:
                print(f"[Config] 설정 저장 실패: {e}")

    def _sync_policy_lists(self, builtin_raw: dict) -> bool:
        """번들 설정에 있는 탐지 정책 목록으로 현재 설정을 덮어씁니다. 바뀐 항목이 있으면 True.

        배포 PC는 OTA 갱신 후에도 기존 APPDATA 설정 파일을 계속 쓰므로,
        버전·서버 주소와 마찬가지로 탐지 대상 목록도 번들 값을 기준으로 맞춘다.
        """
        changed = False
        for path in POLICY_LIST_PATHS:
            value = builtin_raw
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if not isinstance(value, list):
                continue
            target = self._config
            for key in path[:-1]:
                target = getattr(target, key)
            if getattr(target, path[-1]) != value:
                setattr(target, path[-1], list(value))
                changed = True
        return changed

    def save(self) -> None:
        """현재 설정을 파일에 저장합니다."""
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.dump(
                self._config.dict(),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def reload(self) -> AppConfig:
        """설정 파일을 다시 로드합니다."""
        return self.load()

    @staticmethod
    def _derive_agent_id() -> str:
        """물리 랜카드 MAC 기준 고정 ID (agent.utils.machine_id). 못 구하면 빈 문자열."""
        try:
            from agent.utils.machine_id import derive_agent_id
            return derive_agent_id()
        except Exception as e:
            print(f"[Config] 물리 랜카드 MAC 조회 실패: {e}")
            return ""

    @staticmethod
    def _generate_agent_id() -> str:
        """예비 에이전트 ID (물리 랜카드를 못 찾았을 때만). uuid.getnode 는 아무 어댑터나 고르므로 고정값이 아니다."""
        try:
            mac = uuid.getnode()
            hostname = os.environ.get("COMPUTERNAME", "UNKNOWN")
            return f"{hostname}-{mac & 0xFFFFFF:06X}"
        except Exception:
            return f"AGENT-{uuid.uuid4().hex[:8].upper()}"
