"""SSO 관리자 전용 사용자 권한 관리 API."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dashboard_models import DashboardUser
from app.services import dashboard_access as access
from app.services import sso_service as sso

router = APIRouter(prefix="/api/admin")


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    if settings.dashboard_auth_mode != "sso":
        raise HTTPException(404, "SSO 모드에서만 사용할 수 있습니다")
    user = await sso.session_user(db, request.cookies.get(sso.COOKIE, ""))
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다")
    if user.role != "admin":
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return user.sub


@router.get("/users")
async def users(actor: str = Depends(require_admin), db: AsyncSession = Depends(get_db),
                q: str = Query(default="", max_length=256), offset: int = Query(default=0, ge=0),
                limit: int = Query(default=50, ge=1, le=100)):
    query = select(DashboardUser)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.where(or_(DashboardUser.username.ilike(term), DashboardUser.name.ilike(term),
                                DashboardUser.email.ilike(term), DashboardUser.sub.ilike(term)))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.scalars(query.order_by(DashboardUser.username, DashboardUser.sub).offset(offset).limit(limit))).all()
    return JSONResponse({"users": [access.serialize(user) for user in rows], "total": total, "current_sub": actor},
                        headers={"Cache-Control": "no-store"})


class AccessChange(BaseModel):
    role: Literal["pending", "viewer", "admin"]
    enabled: bool


@router.patch("/users/{sub}")
async def update(sub: UUID, body: AccessChange, request: Request,
                 actor: str = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if request.headers.get("origin") != settings.dashboard_frontend_url.rstrip("/"):
        raise HTTPException(403, "Invalid origin")
    result = await access.change_access(db, str(sub), body.role, body.enabled, actor_sub=actor)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
