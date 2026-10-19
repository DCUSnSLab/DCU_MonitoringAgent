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


class ActiveDetectionSchema(BaseModel):
    """에이전트가 지금 탐지 중인 항목 1건 (대상별 중복 없음)"""
    code: str = ""  # BLOCKED_PROCESS | UNAUTHORIZED_PROCESS | BLOCKED_URL | MESSENGER_DETECTED
    target: str = ""  # 프로세스명 / URL / 창 제목
    source: str = ""  # process | url | title
    is_foreground: bool = False  # 지금 화면에 띄워져 있는지
    # 서버가 채움: 이 대상이 화면 앞에 계속 떠 있기 시작한 시각 (화면 앞일 때만)
    on_screen_since: Optional[datetime] = None


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
    # 지금 탐지 중인 항목. 필드가 없으면(None) 현재 목록을 건드리지 않는다
    # (구버전 에이전트, 오프라인 버퍼에서 재전송되는 과거 보고)
    active_detections: Optional[list[ActiveDetectionSchema]] = None
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
    # DANGER일 때 그 이유 (화면 앞에 띄운 탐지 대상). DANGER 유지 시간 동안 함께 유지된다.
    danger_reason: Optional[str] = None
    # DANGER가 이어진 구간: 시작 시각, 마지막 DANGER 보고 시각, 지금도 화면에 떠 있는지 (유지 중일 때만)
    danger_since: Optional[datetime] = None
    danger_last_at: Optional[datetime] = None
    danger_on_screen: bool = False

    class Config:
        from_attributes = True


class AgentDetail(AgentSummary):
    """에이전트 상세 모달에 표시되는 전체 정보"""
    processes: list[ProcessInfoSchema] = []
    chrome_tabs: list[ChromeTabSchema] = []
    recent_alerts: list[AlertSchema] = []
    active_detections: list[ActiveDetectionSchema] = []
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


class BlockedSiteDetailDay(BaseModel):
    """특정 에이전트×사이트의 일자별 내역"""
    date: str
    active_seconds: int = 0
    background_seconds: int = 0
    access_count: int = 0


class BlockedSiteLogEntry(BaseModel):
    """개별 차단 접근 로그 (alert_logs 한 건)"""
    detected_at: datetime
    level: str
    url: Optional[str] = None
    message: Optional[str] = None


class BlockedSiteDetail(BaseModel):
    """상세 행 클릭 시 보여줄 에이전트×사이트 드릴다운 정보"""
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
    daily: list[BlockedSiteDetailDay] = []
    logs: list[BlockedSiteLogEntry] = []
    log_total: int = 0


# ─── WebSocket 메시지 ────────────────────────────────────────────


class WSMessage(BaseModel):
    type: str  # "agent_update" | "agent_online" | "agent_offline"
    data: dict
