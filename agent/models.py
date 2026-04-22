"""
데이터 모델 정의

에이전트가 수집하고 전송하는 모든 데이터의 구조를 정의합니다.
Pydantic v1 모델로 직렬화/역직렬화 및 유효성 검증을 수행합니다.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

# ─── 열거형 정의 ───────────────────────────────────────────────

class UserState(str, Enum):
    """사용자의 실시간 행동 양식 정의"""
    SAFE = "safe"           # 문제 없음 (위반 사항 없음)
    WARNING = "warning"     # 주의 (백그라운드에서 위반 프로그램/탭 실행)
    DANGER = "danger"       # 경고 (위반 프로그램/탭이 현재 화면에 활성화됨)

# ─── 프로세스 정보 모델 ──────────────────────────────────────────


class ProcessInfo(BaseModel):
    """실행 중인 프로세스 정보"""
    pid: int
    name: str
    exe_path: Optional[str] = None
    status: str = "running"
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    create_time: Optional[datetime] = None
    username: Optional[str] = None
    window_title: Optional[str] = None
    is_foreground: bool = False
    cmdline: Optional[str] = None


# ─── 브라우저 정보 모델 ──────────────────────────────────────────


class ChromeTabInfo(BaseModel):
    """크롬 개별 탭 정보"""
    tab_id: str = ""
    title: str = ""
    url: str = ""
    is_active: bool = False
    favicon_url: Optional[str] = None


class ChromeInfo(BaseModel):
    """크롬 브라우저 전체 정보"""
    is_running: bool = False
    debug_mode: bool = False
    tabs: List[ChromeTabInfo] = Field(default_factory=list)
    tab_count: int = 0


class BrowserDetails(BaseModel):
    """브라우저 상세 정보"""
    chrome: ChromeInfo = Field(default_factory=ChromeInfo)


# ─── 시스템 정보 모델 ────────────────────────────────────────────


class SystemInfo(BaseModel):
    """시스템 리소스 정보"""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_total_gb: float = 0.0
    memory_used_gb: float = 0.0
    uptime_seconds: int = 0


# ─── 알림 모델 ───────────────────────────────────────────────────


class Alert(BaseModel):
    """모니터링 알림"""
    level: str = "info"  # info | warning | critical
    code: str = ""
    message: str = ""
    process_name: Optional[str] = None
    url: Optional[str] = None
    detected_at: datetime = Field(default_factory=datetime.now)


# ─── 상태 보고 모델 ──────────────────────────────────────────────


class AgentRegistration(BaseModel):
    """에이전트 등록 데이터"""
    message_type: str = "agent_register"
    agent_id: str
    hostname: str
    ip_address: str = ""
    mac_address: str = ""
    os_version: str = ""
    agent_version: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


class StatusReport(BaseModel):
    """주기적 상태 보고 데이터"""
    message_type: str = "status_report"
    agent_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    user_state: UserState = UserState.SAFE
    system_info: SystemInfo = Field(default_factory=SystemInfo)
    processes: List[ProcessInfo] = Field(default_factory=list)
    browser_details: BrowserDetails = Field(default_factory=BrowserDetails)
    alerts: List[Alert] = Field(default_factory=list)
    foreground_window: Optional[str] = None


# ─── 서버 명령 모델 ──────────────────────────────────────────────


class ServerCommand(BaseModel):
    """서버에서 에이전트로 보내는 명령"""
    message_type: str = "command"
    command_type: str = ""
    payload: dict = Field(default_factory=dict)
