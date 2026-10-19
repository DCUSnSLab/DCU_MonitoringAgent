"""
서버 설정 (환경변수 기반)
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./dcu_monitoring.db"
    api_key: str = "dcu-secret-key-change-in-production"
    cors_origins: list[str] = ["http://localhost:3000", "http://frontend:3000"]

    # 원시 데이터(status_reports/alert_logs) 및 통계 누적 데이터 보관 기간(일). 기본 4개월.
    retention_days: int = 120

    # 서버가 보고서의 크롬 탭 URL을 직접 매칭해 차단 사이트 통계를 누적할 때 사용.
    # 에이전트 config(agent_config.yaml)의 blocked_urls와 동일하게 유지한다.
    blocked_url_patterns: list[str] = [
        "chat.openai.com",
        "chatgpt.com",
        "bard.google.com",
        "gemini.google.com",
        "claude.ai",
    ]

    # 에이전트가 창 제목으로 탐지한 차단 알림(URL 없음)을 통계에 넣을 때 쓰는 사이트 키.
    # 알림 메시지(창 제목)에 키워드가 있으면 해당 사이트로 집계한다.
    # 사이트 키는 위 blocked_url_patterns 와 같게 맞춰, URL 기반 알림과 한 행으로 합쳐지게 한다.
    blocked_title_sites: dict[str, str] = {
        "chatgpt": "chatgpt.com",
        "gemini": "gemini.google.com",
        "claude": "claude.ai",
    }

    # DANGER(차단 대상을 화면에 띄움)는 수집 주기 하나(약 3초)만 잡혔다 사라지기 쉬우므로,
    # 대시보드에서 놓치지 않도록 마지막 DANGER 보고 후 이 시간(초) 동안 DANGER 상태와 이유를 유지한다.
    danger_hold_seconds: int = 60
    # 같은 대상을 화면에 다시 띄워도 '화면 표시' 알림은 이 간격(초)에 한 번만 남긴다.
    on_screen_alert_cooldown_seconds: int = 60

    class Config:
        env_file = ".env"


settings = Settings()
