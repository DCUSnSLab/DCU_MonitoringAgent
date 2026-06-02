"""
SQLAlchemy DB 모델 정의

테이블:
  - agents         : 등록된 에이전트 (실습실 PC) 정보
  - status_reports : 에이전트 상태 보고 이력
  - alert_logs     : 개별 알림 이력
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Agent(Base):
    """등록된 에이전트 (실습실 PC 1대)"""
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(256), nullable=False)
    lab_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    mac_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    os_version: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    agent_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # 현재 상태 (마지막 보고 기준 캐시)
    current_state: Mapped[str] = mapped_column(String(16), default="unknown")
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 관계
    reports: Mapped[list["StatusReportDB"]] = relationship(
        "StatusReportDB", back_populates="agent", cascade="all, delete-orphan"
    )


class StatusReportDB(Base):
    """주기적 상태 보고 이력"""
    __tablename__ = "status_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    agent_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("agents.agent_id", ondelete="CASCADE"), index=True
    )
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, server_default=func.now()
    )

    # 상태
    user_state: Mapped[str] = mapped_column(String(16), default="safe")
    alert_count: Mapped[int] = mapped_column(Integer, default=0)

    # 시스템 리소스
    cpu_percent: Mapped[float] = mapped_column(Float, default=0.0)
    memory_percent: Mapped[float] = mapped_column(Float, default=0.0)

    # 프로세스/탭 요약
    process_count: Mapped[int] = mapped_column(Integer, default=0)
    chrome_tab_count: Mapped[int] = mapped_column(Integer, default=0)
    foreground_window: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # 관계
    agent: Mapped["Agent"] = relationship("Agent", back_populates="reports")
    alerts: Mapped[list["AlertLogDB"]] = relationship(
        "AlertLogDB", back_populates="report", cascade="all, delete-orphan"
    )


class AlertLogDB(Base):
    """개별 알림 이력"""
    __tablename__ = "alert_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("status_reports.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(String(128), index=True)

    level: Mapped[str] = mapped_column(String(16), default="warning")
    code: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    process_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 관계
    report: Mapped["StatusReportDB"] = relationship("StatusReportDB", back_populates="alerts")


class BlockedSiteDailyStat(Base):
    """차단 사이트 접근 일자별 누적 통계 (에이전트 × 차단패턴 × 날짜당 1행)

    개별 탭은 DB에 저장하지 않으므로, 매 상태 보고 시 열려 있던 차단 탭의
    활성/백그라운드 여부에 따라 직전 보고로부터 경과한 시간을 누적한다.
    """
    __tablename__ = "blocked_site_daily_stats"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "url_pattern", "stat_date",
            name="uq_blocked_site_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    agent_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("agents.agent_id", ondelete="CASCADE"), index=True
    )
    url_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    stat_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)

    # 누적 시간(초)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0)
    background_seconds: Mapped[int] = mapped_column(Integer, default=0)

    first_access_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_access_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OTARelease(Base):
    """OTA 업데이트 릴리즈 정보"""
    __tablename__ = "ota_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)  # sha256
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    release_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
