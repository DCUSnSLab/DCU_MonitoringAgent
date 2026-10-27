"""Authlib OAuth2 + RS256 OIDC 검증, 서버 세션·회전 refresh 관리."""
import hashlib
import hmac
import time
import asyncio
from weakref import WeakValueDictionary
from uuid import UUID

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.integrations.base_client.errors import OAuthError
from cryptography.fernet import Fernet, InvalidToken
from jose import jwt, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dashboard_models import DashboardSession, DashboardUser

COOKIE = "dcu_dashboard_session"
LOGIN_COOKIE = "dcu_dashboard_login"
ROLES = {"viewer", "admin"}
_session_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def now() -> int:
    return int(time.time())


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cipher() -> Fernet:
    return Fernet(settings.sso_token_encryption_key.encode())


def check_config() -> None:
    if not settings.sso_client_secret:
        raise ValueError("SSO_CLIENT_SECRET is required")
    cipher()


def endpoint(path: str) -> str:
    return f"{(settings.sso_internal_base or settings.sso_issuer).rstrip('/')}/{path}"


def oauth_client() -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        settings.sso_client_id, settings.sso_client_secret,
        redirect_uri=settings.sso_redirect_uri,
        scope="openid profile email offline_access",
        code_challenge_method="S256", token_endpoint_auth_method="client_secret_post",
        timeout=10.0,
    )


async def fetch_jwks() -> dict:
    # 키 회전 직후에도 새 kid를 검증할 수 있도록 JWKS를 조회한다.
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(endpoint("jwks.json"))
        response.raise_for_status()
        return response.json()


async def verify_identity(encoded: str, nonce: str | None = None) -> dict:
    keys = await fetch_jwks()
    claims = jwt.decode(
        encoded, keys, algorithms=["RS256"], audience=settings.sso_client_id,
        issuer=settings.sso_issuer,
        options={"require_exp": True, "require_iat": True, "require_sub": True,
                 "require_aud": True, "require_iss": True},
    )
    if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise JWTError("nonce mismatch")
    if claims.get("azp", settings.sso_client_id) != settings.sso_client_id:
        raise JWTError("azp mismatch")
    if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and "azp" not in claims:
        raise JWTError("missing azp")
    claims["sub"] = str(UUID(claims["sub"]))
    return claims


async def update_identity(db: AsyncSession, claims: dict) -> DashboardUser:
    user = await db.get(DashboardUser, claims["sub"])
    if user is None:
        user = DashboardUser(sub=claims["sub"], role="pending", enabled=True)
        db.add(user)
    user.username = str(claims.get("username") or claims.get("preferred_username") or claims["sub"])
    user.name = str(claims.get("name", ""))
    user.email = str(claims.get("email", ""))
    await db.flush()
    return user


async def session_user(db: AsyncSession, cookie: str) -> DashboardUser | None:
    if not cookie:
        return None
    key = digest(cookie)
    # SQLite는 FOR UPDATE를 지원하지 않는다. 로컬에서도 같은 세션의 갱신을 직렬화.
    lock = _session_locks.setdefault(key, asyncio.Lock())
    async with lock:
        return await _session_user(db, key)


async def _session_user(db: AsyncSession, key: str) -> DashboardUser | None:
    # PG 행 잠금은 여러 탭/여러 서버의 refresh를 직렬화한다. 커밋 후 잠금 해제.
    session = await db.scalar(select(DashboardSession).where(
        DashboardSession.token_hash == key
    ).with_for_update().execution_options(populate_existing=True))
    if session is None:
        return None
    user = await db.get(DashboardUser, session.sub, populate_existing=True)
    if session.expires_at <= now() or user is None or not user.enabled or user.role not in ROLES:
        await db.delete(session)
        await db.commit()
        return None
    if session.refresh_at <= now():
        try:
            async with oauth_client() as client:
                token = await client.refresh_token(
                    endpoint("oauth/token"), refresh_token=cipher().decrypt(session.refresh_token.encode()).decode()
                )
            # 회전된 refresh는 즉시 영속화. 이후 JWKS 장애가 나도 이전 토큰을 재사용하지 않는다.
            session.refresh_token = cipher().encrypt(token["refresh_token"].encode()).decode()
            session.refresh_at = now() + 30  # 신원 검증 실패 시 짧은 간격으로 재확인
            await db.commit()
            claims = await verify_identity(token["id_token"])
            if claims["sub"] != session.sub:
                raise JWTError("subject changed")
            user = await update_identity(db, claims)
            session.refresh_at = now() + max(1, min(int(token["expires_in"]), 900) - 60)
        except OAuthError as exc:
            if exc.error == "invalid_grant":
                await db.delete(session)
                await db.commit()
                return None
            # SSO의 일시적인 장애 동안 기존 세션의 절대 만료는 연장하지 않는다.
            session.refresh_at = now() + 30
        except httpx.HTTPError:
            session.refresh_at = now() + 30
        except (JWTError, ValueError, KeyError, InvalidToken):
            await db.delete(session)
            await db.commit()
            return None
    await db.commit()
    return user
