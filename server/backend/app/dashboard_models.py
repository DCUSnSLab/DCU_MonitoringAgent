"""대시보드 전용 사용자·서버 세션. OIDC sub만 계정 연결 키로 사용한다."""
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DashboardUser(Base):
    __tablename__ = "dashboard_users"
    sub: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(256), default="")
    email: Mapped[str] = mapped_column(String(512), default="")
    role: Mapped[str] = mapped_column(String(16), default="pending")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DashboardSession(Base):
    __tablename__ = "dashboard_sessions"
    # 브라우저에는 난수 원문, DB에는 그 해시만 저장한다.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    sub: Mapped[str] = mapped_column(ForeignKey("dashboard_users.sub"), index=True)
    expires_at: Mapped[int] = mapped_column(Integer)
    refresh_at: Mapped[int] = mapped_column(Integer)
    refresh_token: Mapped[str] = mapped_column(Text)


class DashboardLogin(Base):
    __tablename__ = "dashboard_logins"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(128))
    verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class DashboardAccessLog(Base):
    __tablename__ = "dashboard_access_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    target_sub: Mapped[str] = mapped_column(String(36), index=True)
    previous_role: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean)
    changed_at: Mapped[int] = mapped_column(Integer)
