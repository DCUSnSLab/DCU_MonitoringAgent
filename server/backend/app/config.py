"""
서버 설정 (환경변수 기반)
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./dcu_monitoring.db"
    api_key: str = "dcu-secret-key-change-in-production"
    cors_origins: list[str] = ["http://localhost:3000", "http://frontend:3000"]
    report_retention_days: int = 7

    class Config:
        env_file = ".env"


settings = Settings()
