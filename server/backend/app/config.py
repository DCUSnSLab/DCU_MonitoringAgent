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
    ]

    class Config:
        env_file = ".env"


settings = Settings()
