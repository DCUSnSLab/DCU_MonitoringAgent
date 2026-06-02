"""
Pydantic 요청/응답 스키마

에이전트 → 서버 요청과 대시보드 API 응답에 사용됩니다.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ─── 에이전트 → 서버 요청 ─────────────────────────────────────────


class ChromeTabSchema(BaseModel):
    tab_id: str = ""
    title: str = ""
    url: str = ""
    is_active: bool = False


class ChromeInfoSchema(BaseModel):
    is_running: bool = False
    debug_mode: bool = False
    tabs: list[ChromeTabSchema] = []
    tab_count: int = 0


class BrowserDetailsSchema(BaseModel):
    chrome: ChromeInfoSchema = ChromeInfoSchema()


class SystemInfoSchema(BaseModel):
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_total_gb: float = 0.0
    memory_used_gb: float = 0.0
    uptime_seconds: int = 0


class ProcessInfoSchema(BaseModel):
    pid: int
    name: str
    exe_path: Optional[str] = None
    status: str = "running"
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    window_title: Optional[str] = None
    is_foreground: bool = False


class AlertSchema(BaseModel):
    level: str = "warning"
    code: str = ""
    message: str = ""
    process_name: Optional[str] = None
    url: Optional[str] = None
    detected_at: Optional[datetime] = None


class AgentRegisterRequest(BaseModel):
    agent_id: str
    hostname: str
    ip_address: str = ""
    mac_address: str = ""
    os_version: str = ""
    agent_version: str = ""


class StatusReportRequest(BaseModel):
    agent_id: str
    hostname: str = ""
    ip_address: str = ""
    timestamp: Optional[datetime] = None
    agent_version: str = ""
    user_state: str = "safe"
    system_info: SystemInfoSchema = SystemInfoSchema()
    processes: list[ProcessInfoSchema] = []
    browser_details: BrowserDetailsSchema = BrowserDetailsSchema()
    alerts: list[AlertSchema] = []
    foreground_window: Optional[str] = None


class AgentOfflineRequest(BaseModel):
    """에이전트 종료 시 서버에 전송하는 명시적 오프라인 보고"""
    agent_id: str


# ─── 서버 → 대시보드 응답 ─────────────────────────────────────────


class AgentSummary(BaseModel):
    """에이전트 카드에 표시되는 요약 정보"""
    agent_id: str
    hostname: str
    lab_name: Optional[str]
    ip_address: Optional[str]
    current_state: str
    is_online: bool
    last_seen_at: Optional[datetime]
    process_count: int = 0
    chrome_tab_count: int = 0
    alert_count: int = 0
    foreground_window: Optional[str] = None
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    agent_version: Optional[str] = None
    os_version: Optional[str] = None

    class Config:
        from_attributes = True


class AgentDetail(AgentSummary):
    """에이전트 상세 모달에 표시되는 전체 정보"""
    processes: list[ProcessInfoSchema] = []
    chrome_tabs: list[ChromeTabSchema] = []
    recent_alerts: list[AlertSchema] = []
    registered_at: Optional[datetime] = None


class AlertLogResponse(BaseModel):
    id: int
    agent_id: str
    hostname: Optional[str] = None
    level: str
    code: str
    message: str
    process_name: Optional[str] = None
    url: Optional[str] = None
    detected_at: datetime

    class Config:
        from_attributes = True


class DashboardSummary(BaseModel):
    """대시보드 상단 요약 카드"""
    total_agents: int
    online_agents: int
    safe_count: int
    warning_count: int
    danger_count: int
    recent_alert_count: int


# ─── 통계 응답 ───────────────────────────────────────────────────


class BlockedSiteStatRow(BaseModel):
    """에이전트 × 차단 사이트별 통계 한 행"""
    agent_id: str
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    lab_name: Optional[str] = None
    url_pattern: str
    active_seconds: int = 0
    background_seconds: int = 0
    access_count: int = 0
    first_access_at: Optional[datetime] = None
    last_access_at: Optional[datetime] = None


class TimelineHourPoint(BaseModel):
    hour: int
    count: int


class TimelineDayPoint(BaseModel):
    date: str
    count: int


class BlockedAccessTimeline(BaseModel):
    """차단 사이트 접근의 시간대별/일자별 분포"""
    hourly: list[TimelineHourPoint] = []
    daily: list[TimelineDayPoint] = []
    total: int = 0


# ─── WebSocket 메시지 ────────────────────────────────────────────


class WSMessage(BaseModel):
    type: str  # "agent_update" | "agent_online" | "agent_offline"
    data: dict
