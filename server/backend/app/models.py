"""
SQLAlchemy DB 모델 정의

테이블:
  - agents               : 등록된 에이전트 (실습실 PC) 정보 + 최신 보고 요약
  - agent_status_minutes : 에이전트 상태 1분 집계 이력 (보고 이력은 여기에 쌓는다)
  - status_reports       : (사용 중단) 예전 1초 단위 보고 이력. 새 보고는 쓰지 않는다
  - alert_logs           : 개별 알림 이력
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Agent(Base):
    """등록된 에이전트 (실습실 PC 1대)"""
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(256), nullable=False)
    # 기존 MAC 기반 ID를 자리 코드로 추측해 확정하지 않는다. 등록/이관 단계에서 명시적으로 변경한다.
    seat_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    lab_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    mac_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    os_version: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    agent_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # 현재 상태 (마지막 보고 기준 캐시)
    current_state: Mapped[str] = mapped_column(String(16), default="unknown")
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)

    # 마지막 보고서 요약 캐시 (비정규화)
    # 대시보드 목록 조회가 status_reports 대량 스캔 없이 agents 한 줄만 읽도록,
    # 매 보고 시 save_status_report에서 아래 값을 함께 갱신한다.
    last_report_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    process_count: Mapped[int] = mapped_column(Integer, default=0)
    chrome_tab_count: Mapped[int] = mapped_column(Integer, default=0)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    cpu_percent: Mapped[float] = mapped_column(Float, default=0.0)
    memory_percent: Mapped[float] = mapped_column(Float, default=0.0)
    foreground_window: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 관계
    reports: Mapped[list["StatusReportDB"]] = relationship(
        "StatusReportDB", back_populates="agent", cascade="all, delete-orphan",
        foreign_keys="StatusReportDB.agent_id",
    )


class StatusReportDB(Base):
    """(사용 중단) 예전 1초 단위 상태 보고 이력.

    보고마다 1행씩 쌓였지만 읽는 곳이 없어 쓰기 부하만 컸다 (GEN-1683).
    새 보고는 agent_status_minutes 에 1분 단위로 집계해 저장하고, 이 테이블에는 쓰지 않는다.
    기존 데이터 정리 방법은 migrations/2026-10-20_report_minutely.sql 참고.
    """
    __tablename__ = "status_reports"

    # GEN-1773: 고정 내부 키. 문자열 ID는 구형 조회/호환을 위해 유지한다.
    agent_pk: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)

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
    agent: Mapped["Agent"] = relationship("Agent", back_populates="reports", foreign_keys=[agent_id])


class AlertLogDB(Base):
    """개별 알림 이력"""
    __tablename__ = "alert_logs"

    # GEN-1773: 고정 내부 키. 문자열 ID는 구형 조회/호환을 위해 유지한다.
    agent_pk: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # 예전 status_reports 행 번호. 새 알림은 비워 둔다 (보고 이력을 1분 집계로 바꾸면서 연결을 끊음).
    # 외래키를 두지 않는다: status_reports 를 비울 때 ON DELETE CASCADE 로 알림 이력까지 지워지지 않도록.
    report_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(128), index=True)

    level: Mapped[str] = mapped_column(String(16), default="warning")
    code: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    process_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentStatusMinute(Base):
    """에이전트 상태 1분 집계 (에이전트 × 1분당 1행)

    보고(약 1초 간격)를 서버 메모리에서 1분씩 모아 한 번에 저장한다 (services/report_aggregator.py).
    평균은 합계(*_sum)와 샘플 수(samples)로 저장한다: 오프라인 버퍼 재전송처럼 이미 저장한 분에
    늦게 들어온 샘플을 더할 때 단순 덧셈으로 합칠 수 있도록. 평균 = *_sum / samples.
    """
    __tablename__ = "agent_status_minutes"
    __table_args__ = (
        UniqueConstraint("agent_id", "minute", name="uq_agent_status_minute"),
        Index("ix_agent_status_minutes_minute", "minute"),
    )

    # GEN-1773: 고정 내부 키. 문자열 ID는 구형 조회/호환을 위해 유지한다.
    agent_pk: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False
    )
    # 집계 구간의 시작 시각 (UTC, 분 단위로 내림)
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    samples: Mapped[int] = mapped_column(Integer, default=0)  # 이 분에 받은 보고 수

    cpu_sum: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_max: Mapped[float] = mapped_column(Float, default=0.0)
    memory_sum: Mapped[float] = mapped_column(Float, default=0.0)
    memory_max: Mapped[float] = mapped_column(Float, default=0.0)
    process_count_sum: Mapped[int] = mapped_column(Integer, default=0)
    process_count_max: Mapped[int] = mapped_column(Integer, default=0)
    chrome_tab_count_sum: Mapped[int] = mapped_column(Integer, default=0)
    chrome_tab_count_max: Mapped[int] = mapped_column(Integer, default=0)

    alert_count: Mapped[int] = mapped_column(Integer, default=0)  # 이 분에 새로 발생한 알림 합계

    # 상태별 샘플 수 (보고 간격이 약 1초라 대략 그 상태로 있던 초)
    safe_samples: Mapped[int] = mapped_column(Integer, default=0)
    warning_samples: Mapped[int] = mapped_column(Integer, default=0)
    danger_samples: Mapped[int] = mapped_column(Integer, default=0)

    # 이 분의 마지막 보고 기준 값 (평균을 낼 수 없는 값)
    last_state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    foreground_window: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    last_sample_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


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

    # GEN-1773: 고정 내부 키. 문자열 ID는 구형 조회/호환을 위해 유지한다.
    agent_pk: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)

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
    # 배포 채널: stable(실습실 전체) / develop(개발용 PC). 에이전트는 자기 채널의 최신 버전만 받는다
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="stable", server_default="stable", index=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)  # sha256
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    release_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentHardware(Base):
    """자리별 최신 사양 요약. 사용률은 기존 agents/agent_status_minutes에서 관리한다."""
    __tablename__ = "agent_hardware"
    __table_args__ = (
        CheckConstraint("memory_total_bytes >= 0", name="ck_hardware_memory_nonnegative"),
    )

    agent_pk: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    memory_total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class _HardwareDevice:
    """모델명이 같은 장치도 구분한다. 조회하지 못한 사양은 0 대신 NULL로 저장한다."""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_pk: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    device_key: Mapped[str] = mapped_column(String(512), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


class AgentCPU(_HardwareDevice, Base):
    __tablename__ = "agent_cpus"
    __table_args__ = (
        UniqueConstraint("agent_pk", "device_key", name="uq_agent_cpu_device"),
        CheckConstraint("physical_cores > 0", name="ck_cpu_cores_positive"),
        CheckConstraint("logical_processors > 0", name="ck_cpu_threads_positive"),
    )
    physical_cores: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    logical_processors: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class AgentGPU(_HardwareDevice, Base):
    __tablename__ = "agent_gpus"
    __table_args__ = (
        UniqueConstraint("agent_pk", "device_key", name="uq_agent_gpu_device"),
        CheckConstraint("dedicated_memory_bytes >= 0", name="ck_gpu_memory_nonnegative"),
        CheckConstraint("gpu_type IN ('integrated', 'discrete')", name="ck_gpu_type"),
    )
    vendor: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    dedicated_memory_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    gpu_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)


class AgentDisk(_HardwareDevice, Base):
    __tablename__ = "agent_disks"
    __table_args__ = (
        UniqueConstraint("agent_pk", "device_key", name="uq_agent_disk_device"),
        CheckConstraint("capacity_bytes >= 0", name="ck_disk_capacity_nonnegative"),
        CheckConstraint("media_type IN ('ssd', 'hdd')", name="ck_disk_media_type"),
    )
    capacity_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)


class AgentNetworkAdapter(Base):
    __tablename__ = "agent_network_adapters"
    __table_args__ = (
        UniqueConstraint("agent_pk", "device_key", name="uq_agent_network_device"),
        CheckConstraint("adapter_type IN ('ethernet', 'wifi', 'other')", name="ck_network_adapter_type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_pk: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    device_key: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    mac_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    adapter_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    is_physical: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)


class AgentInstallation(Base):
    """설치 이력. UUID는 식별값이며 인증은 별도 토큰으로 한다. API는 다음 단계에서 추가한다."""
    __tablename__ = "agent_installations"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'active', 'revoked')", name="ck_installation_status"),
        CheckConstraint("status != 'active' OR (token_hash IS NOT NULL AND approved_at IS NOT NULL)",
                        name="ck_active_installation_credentials"),
        Index("uq_agent_active_installation", "agent_pk", unique=True,
              postgresql_where=text("status = 'active'"), sqlite_where=text("status = 'active'")),
    )
    installation_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    agent_pk: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    token_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)


class AgentIDAlias(Base):
    """구형 ID 매핑. 현재 ID와의 충돌 검사 및 실제 병합은 후속 이관 처리에서 수행한다."""
    __tablename__ = "agent_id_aliases"
    legacy_agent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_pk: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
