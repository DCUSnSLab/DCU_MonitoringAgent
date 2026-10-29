"""브라우저 OIDC 진입·콜백·서버 세션 조회·로그아웃."""
import secrets
from urllib.parse import urlencode

import httpx
from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jose import JWTError
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dashboard_models import DashboardLogin, DashboardSession
from app.services import sso_service as sso

router = APIRouter(prefix="/api/auth")


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(settings.dashboard_frontend_url.rstrip("/") + path, status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def sso_required():
    if settings.dashboard_auth_mode != "sso":
        raise HTTPException(404, "SSO is disabled")


@router.get("/config")
async def config():
    return JSONResponse({"mode": settings.dashboard_auth_mode}, headers={"Cache-Control": "no-store"})


@router.get("/sso/login", dependencies=[Depends(sso_required)])
async def start(db: AsyncSession = Depends(get_db)):
    try:
        sso.check_config()
    except (ValueError, TypeError):
        raise HTTPException(503, "SSO 설정이 완료되지 않았습니다")
    state, browser, nonce, verifier = [secrets.token_urlsafe(48) for _ in range(4)]
    await db.execute(delete(DashboardLogin).where(DashboardLogin.expires_at <= sso.now()))
    db.add(DashboardLogin(state_hash=sso.digest(state), browser_hash=sso.digest(browser),
                          nonce=nonce, verifier=verifier, expires_at=sso.now() + 600))
    async with sso.oauth_client() as client:
        url, _ = client.create_authorization_url(
            settings.sso_issuer.rstrip("/") + "/oauth/authorize",
            state=state, nonce=nonce, code_verifier=verifier,
        )
    await db.commit()
    response = RedirectResponse(url, headers={"Cache-Control": "no-store"})
    response.set_cookie(sso.LOGIN_COOKIE, browser, max_age=600, httponly=True,
                        secure=settings.dashboard_cookie_secure, samesite="lax", path="/api/auth")
    return response


@router.get("/callback", dependencies=[Depends(sso_required)])
async def callback(request: Request, db: AsyncSession = Depends(get_db)):
    p = request.query_params
    state = p.get("state", "")
    browser = request.cookies.get(sso.LOGIN_COOKIE, "")
    # DELETE RETURNING은 SQLite/PG 모두에서 state를 원자적으로 한 번만 소비한다.
    login = await db.scalar(delete(DashboardLogin).where(
        DashboardLogin.state_hash == sso.digest(state),
        DashboardLogin.browser_hash == sso.digest(browser),
        DashboardLogin.expires_at > sso.now(),
    ).returning(DashboardLogin)) if state and browser else None
    if login is None:
        return redirect("/login?error=invalid_callback")
    nonce, verifier = login.nonce, login.verifier
    await db.commit()  # 인가코드 교환 실패도 같은 state로 재시도하지 않는다.
    response = redirect("/login?error=sso_failed")
    try:
        if p.get("error") or not p.get("code"):
            return response
        async with sso.oauth_client() as client:
            token = await client.fetch_token(sso.endpoint("oauth/token"),
                                            code=p["code"], code_verifier=verifier)
        claims = await sso.verify_identity(token["id_token"], nonce)
        user = await sso.update_identity(db, claims)
        if not user.enabled or user.role not in sso.ROLES:
            await db.execute(delete(DashboardSession).where(
                DashboardSession.token_hash == sso.digest(request.cookies.get(sso.COOKIE, ""))
            ))
            await db.commit()  # pending 사용자는 운영자가 목록에서 확인 후 승인한다.
            response = redirect("/login?error=access_denied")
            response.delete_cookie(sso.COOKIE, path="/")
        else:
            cookie = secrets.token_urlsafe(48)
            # 같은 브라우저의 기존 서버 세션도 교체한다.
            old = request.cookies.get(sso.COOKIE)
            if old:
                await db.execute(delete(DashboardSession).where(DashboardSession.token_hash == sso.digest(old)))
            await db.execute(delete(DashboardSession).where(DashboardSession.expires_at <= sso.now()))
            db.add(DashboardSession(
                token_hash=sso.digest(cookie), sub=user.sub,
                expires_at=sso.now() + settings.dashboard_token_ttl_hours * 3600,
                refresh_at=sso.now() + max(1, min(int(token["expires_in"]), 900) - 60),
                refresh_token=sso.cipher().encrypt(token["refresh_token"].encode()).decode(),
            ))
            await db.commit()
            response = redirect("/")
            response.set_cookie(sso.COOKIE, cookie, httponly=True, samesite="lax", path="/",
                                secure=settings.dashboard_cookie_secure,
                                max_age=settings.dashboard_token_ttl_hours * 3600)
    except (OAuthError, httpx.HTTPError, JWTError, ValueError, KeyError):
        await db.rollback()
    finally:
        response.delete_cookie(sso.LOGIN_COOKIE, path="/api/auth")
    return response


@router.get("/session", dependencies=[Depends(sso_required)])
async def session(request: Request, db: AsyncSession = Depends(get_db)):
    user = await sso.session_user(db, request.cookies.get(sso.COOKIE, ""))
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다")
    return JSONResponse({"username": user.name or user.username, "sub": user.sub, "role": user.role,
                         "mode": "sso"}, headers={"Cache-Control": "no-store"})


@router.post("/logout", dependencies=[Depends(sso_required)])
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    # 쿠키 인증을 쓰는 변경 요청에는 정확한 Origin 검사를 적용한다.
    if request.headers.get("origin") != settings.dashboard_frontend_url.rstrip("/"):
        raise HTTPException(403, "Invalid origin")
    cookie = request.cookies.get(sso.COOKIE, "")
    await db.execute(delete(DashboardSession).where(DashboardSession.token_hash == sso.digest(cookie)))
    await db.commit()
    target = settings.sso_issuer.rstrip("/") + "/logout?" + urlencode({
        "post_logout_redirect_uri": settings.dashboard_frontend_url.rstrip("/") + "/login",
    })
    response = JSONResponse({"logout_url": target}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(sso.COOKIE, path="/")
    return response
