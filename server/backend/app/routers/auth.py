"""
대시보드 관리자 로그인 라우터

  POST /api/auth/login  – 아이디·비밀번호 확인 후 토큰 발급
  GET  /api/auth/me     – 토큰이 유효한지 확인

대시보드·통계 API는 require_dashboard_user 로 보호한다 (Authorization: Bearer <토큰>).
"""

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    expires_at: datetime
    expires_in: int  # 남은 유지 시간(초) — 브라우저는 자기 시계 기준으로 만료를 계산 (PC 시계가 틀려도 동작)


class MeResponse(BaseModel):
    username: str


def _unauthorized(detail: str = "로그인이 필요합니다") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


async def require_dashboard_user(authorization: str = Header(default="")) -> str:
    """대시보드·통계 API 보호: 유효한 로그인 토큰이 있어야 통과. 아이디를 돌려준다."""
    scheme, _, token = authorization.partition(" ")
    username = auth_service.verify_token(token.strip()) if scheme.lower() == "bearer" else None
    if username is None:
        raise _unauthorized()
    return username


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    """관리자 로그인"""
    if not auth_service.is_configured():
        raise HTTPException(status_code=503, detail="서버에 관리자 비밀번호가 설정되지 않아 로그인할 수 없습니다")

    client = request.client.host if request.client else "unknown"
    if not auth_service.check_credentials(req.username, req.password):
        logger.warning(f"대시보드 로그인 실패 ({client})")
        raise _unauthorized("아이디 또는 비밀번호가 올바르지 않습니다")

    token, expires_at = auth_service.issue_token(req.username)
    logger.info(f"대시보드 로그인: {req.username} ({client})")
    return LoginResponse(
        token=token,
        username=req.username,
        expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        expires_in=max(0, expires_at - int(time.time())),
    )


@router.get("/me", response_model=MeResponse)
async def me(username: str = Depends(require_dashboard_user)):
    """저장해 둔 토큰이 아직 유효한지 확인"""
    return MeResponse(username=username)
