"""
대시보드 관리자 로그인 라우터

  POST /api/auth/login  – 아이디·비밀번호 확인 후 토큰 발급
  GET  /api/auth/me     – 토큰이 유효한지 확인

대시보드·통계 API는 require_dashboard_user 로 보호한다 (Authorization: Bearer <토큰>).
"""

import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services import auth_service
from app.services.auth_service import login_lockout

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str
    password: str
    device_token: Optional[str] = None  # 전에 로그인했던 브라우저가 가진 기기 토큰


class LoginResponse(BaseModel):
    token: str
    username: str
    expires_at: datetime
    expires_in: int  # 남은 유지 시간(초) — 브라우저는 자기 시계 기준으로 만료를 계산 (PC 시계가 틀려도 동작)
    device_token: str  # 이 브라우저를 신뢰 기기로 기억하는 토큰


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


def _locked_response(seconds: float) -> JSONResponse:
    seconds = math.ceil(seconds)
    minutes, rest = divmod(seconds, 60)
    wait = f"{minutes}분 {rest}초" if minutes and rest else (f"{minutes}분" if minutes else f"{rest}초")
    return JSONResponse(
        status_code=429,
        content={"detail": f"로그인 실패가 많아 잠시 막혔습니다. {wait} 뒤에 다시 시도하세요.", "retry_after": seconds},
        headers={"Retry-After": str(seconds)},
    )


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    """관리자 로그인. 연속 10회 실패하면 잠근다(1분 → 5분 → 30분 → 60분 → 120분).

    실패는 신뢰 기기(전에 로그인했던 브라우저)면 기기별로, 아니면 접속 IP별로 센다.
    운영에서는 접속 IP가 모두 노드 주소 하나라, 처음 쓰는 브라우저들은 사실상 함께 잠긴다.
    """
    if not auth_service.is_configured():
        raise HTTPException(status_code=503, detail="서버에 관리자 비밀번호가 설정되지 않아 로그인할 수 없습니다")

    client = request.client.host if request.client else "unknown"
    key = auth_service.lockout_key(req.device_token, client)
    locked = login_lockout.locked_seconds(key)
    if locked > 0:
        return _locked_response(locked)

    if not auth_service.check_credentials(req.username, req.password):
        lock_seconds = login_lockout.record_failure(key)
        if lock_seconds > 0:
            logger.warning(f"대시보드 로그인 연속 실패로 잠금: {key} ({int(lock_seconds // 60)}분)")
            return _locked_response(lock_seconds)
        remaining = login_lockout.remaining_attempts(key)
        raise _unauthorized(f"아이디 또는 비밀번호가 올바르지 않습니다 (잠기기 전 남은 시도 {remaining}회)")

    login_lockout.record_success(key)
    token, expires_at = auth_service.issue_token(req.username)
    device_token = auth_service.issue_device_token(auth_service.verify_device_token(req.device_token))
    logger.info(f"대시보드 로그인: {req.username} ({key})")
    return LoginResponse(
        token=token,
        username=req.username,
        expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        expires_in=max(0, expires_at - int(time.time())),
        device_token=device_token,
    )


@router.get("/me", response_model=MeResponse)
async def me(username: str = Depends(require_dashboard_user)):
    """저장해 둔 토큰이 아직 유효한지 확인"""
    return MeResponse(username=username)
