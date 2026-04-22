"""
SQLAlchemy DB 모델 정의

테이블:
  - agents         : 등록된 에이전트 (실습실 PC) 정보
  - status_reports : 에이전트 상태 보고 이력
  - alert_logs     : 개별 알림 이력
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func,
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
