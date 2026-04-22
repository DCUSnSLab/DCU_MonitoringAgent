"""
설정 관리 모듈

YAML 기반 설정 파일을 로드하고 관리합니다.
Pydantic v1 모델로 설정 값의 유효성을 검증합니다.
"""

import os
import uuid
import yaml
from typing import List, Optional
from pydantic import BaseModel, Field


# ─── 설정 모델 정의 ──────────────────────────────────────────────


class AgentConfig(BaseModel):
    """에이전트 기본 설정"""
    id: str = ""
    lab_name: str = "실습실"
    version: str = "1.0.0"


class ProcessConfig(BaseModel):
    """프로세스 모니터링 설정"""
    mode: str = "all"  # all | whitelist | blacklist
    whitelist: List[str] = Field(default_factory=list)
    blacklist: List[str] = Field(default_factory=list)


class ChromeConfig(BaseModel):
    """크롬 모니터링 설정"""
    enabled: bool = True
    debug_port: int = 9222
    poll_interval_seconds: int = 5
    blocked_urls: List[str] = Field(default_factory=list)
    # CDP 독립 프로파일 경로 (기존 크롬과 충돌 방지)
    user_data_dir: str = "C:\\chrome-debug"


class MonitoringConfig(BaseModel):
    """모니터링 통합 설정"""
    interval_seconds: int = 5
    track_foreground: bool = True
    process: ProcessConfig = Field(default_factory=ProcessConfig)
    chrome: ChromeConfig = Field(default_factory=ChromeConfig)


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


class ConfigManager:
    """
    설정 파일을 로드하고 관리하는 클래스

    Usage:
        config_mgr = ConfigManager("config/agent_config.yaml")
        config = config_mgr.config
        print(config.monitoring.interval_seconds)
    """

    DEFAULT_CONFIG_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "agent_config.yaml",
    )

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path or self.DEFAULT_CONFIG_PATH
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
        if os.path.exists(self._config_path):
            with open(self._config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            self._config = AppConfig(**raw)
        else:
            # 설정 파일이 없으면 기본값 사용
            self._config = AppConfig()

        # 에이전트 ID가 비어있으면 자동 생성
        if not self._config.agent.id:
            self._config.agent.id = self._generate_agent_id()

        return self._config

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
    def _generate_agent_id() -> str:
        """MAC 주소 기반 에이전트 ID를 생성합니다."""
        try:
            mac = uuid.getnode()
            mac_str = ":".join(
                f"{(mac >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6))
            )
            hostname = os.environ.get("COMPUTERNAME", "UNKNOWN")
            mac_short = mac_str.replace(":", "")[-6:]
            return f"{hostname}-{mac_short}"
        except Exception:
            return f"AGENT-{uuid.uuid4().hex[:8].upper()}"
