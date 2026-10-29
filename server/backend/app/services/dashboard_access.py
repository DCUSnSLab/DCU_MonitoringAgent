"""권한 변경. 웹/CLI 모두 동일한 마지막 관리자 보호를 적용한다."""
import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard_models import DashboardAccessLog, DashboardSession, DashboardUser
from app.services.sso_service import now

_access_lock = asyncio.Lock()


@asynccontextmanager
async def access_transaction(db: AsyncSession):
    async with _access_lock:
        # 인증 조회 때 시작한 트랜잭션을 정리하고 권한 변경 전용 잠금을 잡는다.
        await db.rollback()
        if db.get_bind().dialect.name == "postgresql":
            # 모든 worker/CLI가 같은 잠금을 써서 마지막 관리자 동시 회수를 막는다.
            await db.execute(text("SELECT pg_advisory_xact_lock(1731001)"))
        elif db.get_bind().dialect.name == "sqlite":
            await db.execute(text("BEGIN IMMEDIATE"))
        try:
            yield
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def change_access(db: AsyncSession, sub: str, role: str, enabled: bool,
                        actor_sub: str | None = None, create: bool = False) -> dict:
    sub = str(UUID(sub))
    if role not in {"pending", "viewer", "admin"}:
        raise ValueError("Invalid role")
    async with access_transaction(db):
        if actor_sub:
            actor = await db.get(DashboardUser, actor_sub, populate_existing=True)
            if actor is None or not actor.enabled or actor.role != "admin":
                raise HTTPException(403, "관리자 권한이 필요합니다")
        user = await db.get(DashboardUser, sub, populate_existing=True)
        if user is None:
            if not create:
                raise HTTPException(404, "사용자를 찾을 수 없습니다")
            user = DashboardUser(sub=sub, username=sub, name="", email="", role="pending")
            db.add(user)
        if user.role == "admin" and user.enabled and (role != "admin" or not enabled):
            other_admin = await db.scalar(select(DashboardUser.sub).where(
                DashboardUser.role == "admin", DashboardUser.enabled.is_(True), DashboardUser.sub != sub
            ).limit(1))
            if not other_admin:
                raise HTTPException(409, "마지막 관리자의 권한은 회수할 수 없습니다. 다른 관리자를 먼저 등록하세요.")
        previous_role = user.role
        if (user.role, user.enabled) != (role, enabled):
            user.role, user.enabled = role, enabled
            # 다음 요청부터 새 권한을 검사한다. 열린 WebSocket도 30초 이내 재검사한다.
            # viewer/admin 전환은 세션을 유지하고, 접근 회수만 세션을 폐기한다.
            if not enabled or role == "pending":
                await db.execute(delete(DashboardSession).where(DashboardSession.sub == sub))
            db.add(DashboardAccessLog(actor=actor_sub or "cli", target_sub=sub,
                                      previous_role=previous_role, role=role, enabled=enabled, changed_at=now()))
        result = serialize(user)
    return result


def serialize(user: DashboardUser) -> dict:
    return {"sub": user.sub, "username": user.username, "name": user.name,
            "email": user.email, "role": user.role, "enabled": user.enabled}
