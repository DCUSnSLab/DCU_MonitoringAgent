"""실제 RSA 서명과 Authlib HTTP 교환을 사용하는 로컬 OIDC 통합 테스트."""
import base64
import asyncio
import hashlib
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import jwt, jwk, JWTError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import main
from app.config import settings
from app.database import get_db
from app.dashboard_models import DashboardAccessLog, DashboardLogin, DashboardSession, DashboardUser
from app.services import dashboard_access as access
from app.services import sso_service as sso
from app.routers import ws as ws_router
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

SUB = str(uuid4())


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_auth_mode", "sso")
    monkeypatch.setattr(settings, "dashboard_frontend_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "sso_redirect_uri", "http://localhost:8000/api/auth/callback")
    monkeypatch.setattr(settings, "sso_client_secret", "test-only-secret")
    monkeypatch.setattr(settings, "sso_token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "dashboard_cookie_secure", False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    data = {"nonce": "", "refresh": "refresh-1", "calls": [], "error": None, "claims": {}}

    async def jwks():
        return {"keys": [{**jwk.construct(pub, "RS256").to_dict(), "kid": "test", "use": "sig"}]}

    monkeypatch.setattr(sso, "fetch_jwks", jwks)

    def encoded(**overrides):
        claims = dict(iss=settings.sso_issuer, aud=settings.sso_client_id, sub=SUB,
                      iat=sso.now(), exp=sso.now() + 900, nonce=data["nonce"],
                      username="professor", name="테스트 교수", email="test@example.com")
        claims.update(data["claims"])
        claims.update(overrides)
        return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "test"})

    def handler(request):
        p = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        data["calls"].append(p)
        assert p["client_id"] == settings.sso_client_id
        assert p["client_secret"] == "test-only-secret"
        if data["error"] == "network":
            raise httpx.ConnectError("offline", request=request)
        if data["error"]:
            return httpx.Response(400, json={"error": data["error"]})
        if p["grant_type"] == "authorization_code":
            assert p["redirect_uri"] == settings.sso_redirect_uri
            challenge = base64.urlsafe_b64encode(hashlib.sha256(p["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            assert challenge == data["challenge"]
        else:
            assert p["refresh_token"] == data["refresh"]
            data["refresh"] += "-rotated"
        return httpx.Response(200, json={"access_token": "unused", "token_type": "Bearer",
                              "id_token": encoded(), "refresh_token": data["refresh"], "expires_in": 900})

    original = sso.oauth_client
    def oauth_client():
        client = original()
        client._transport = httpx.MockTransport(handler)
        return client

    monkeypatch.setattr(sso, "oauth_client", oauth_client)
    data["encode"] = encoded
    return data


@pytest.fixture
async def client(db, provider):
    async def dependency():
        yield db
    main.app.dependency_overrides[get_db] = dependency
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://localhost:8000") as c:
        yield c
    main.app.dependency_overrides.pop(get_db, None)


async def begin(client, provider):
    response = await client.get("/api/auth/sso/login")
    assert response.status_code == 307
    p = parse_qs(urlparse(response.headers["location"]).query)
    assert p["code_challenge_method"] == ["S256"]
    assert set(p["scope"][0].split()) == {"openid", "profile", "email", "offline_access"}
    provider["nonce"], provider["challenge"] = p["nonce"][0], p["code_challenge"][0]
    assert "HttpOnly" in response.headers["set-cookie"]
    return p["state"][0]


async def authorize(db, client, provider):
    db.add(DashboardUser(sub=SUB, username="professor", name="", email="", role="admin", enabled=True))
    await db.commit()
    state = await begin(client, provider)
    result = await client.get("/api/auth/callback", params={"state": state, "code": "once"})
    assert result.headers["location"] == "http://localhost:3000/"
    return state


async def test_pending_user_never_gets_session(client, db, provider):
    state = await begin(client, provider)
    result = await client.get("/api/auth/callback", params={"state": state, "code": "once"})
    assert result.headers["location"].endswith("error=access_denied")
    assert (await db.get(DashboardUser, SUB)).role == "pending"
    assert await db.scalar(select(DashboardSession)) is None
    assert (await client.get("/api/dashboard/summary")).status_code == 401


async def test_login_protected_apis_and_logout(client, db, provider):
    state = await authorize(db, client, provider)
    assert client.cookies.get(sso.COOKIE)
    row = await db.scalar(select(DashboardSession))
    assert row.token_hash != client.cookies.get(sso.COOKIE)
    assert row.refresh_token != provider["refresh"]
    assert (await client.get("/api/auth/session")).json()["role"] == "admin"
    for path in ["/api/dashboard/summary", "/api/statistics/labs"]:
        assert (await client.get(path)).status_code == 200
    # state 재사용과 기존 관리자 로그인 우회를 모두 거부한다.
    assert "invalid_callback" in (await client.get("/api/auth/callback", params={"state": state, "code": "again"})).headers["location"]
    assert (await client.post("/api/auth/login", json={"username": "admin", "password": "x"})).status_code == 404
    assert (await client.post("/api/auth/logout", headers={"origin": "https://evil.example"})).status_code == 403
    result = await client.post("/api/auth/logout", headers={"origin": "http://localhost:3000"})
    assert result.status_code == 200 and "/sso/logout?" in result.json()["logout_url"]
    assert not client.cookies.get(sso.COOKIE)
    assert (await client.get("/api/auth/session")).status_code == 401


async def test_callback_bound_to_browser_and_expiry(client, db, provider):
    state = await begin(client, provider)
    cookie = client.cookies.get(sso.LOGIN_COOKIE)
    client.cookies.clear()
    response = await client.get("/api/auth/callback", params={"state": state, "code": "x"})
    assert "invalid_callback" in response.headers["location"]
    assert not provider["calls"]
    login = await db.scalar(select(DashboardLogin))
    login.expires_at = 0
    await db.commit()
    client.cookies.set(sso.LOGIN_COOKIE, cookie)
    assert "invalid_callback" in (await client.get("/api/auth/callback", params={"state": state, "code": "x"})).headers["location"]


@pytest.mark.parametrize("claims", [
    {"nonce": "forged"}, {"aud": "oj"}, {"iss": "https://evil.example"},
    {"exp": 1}, {"sub": "invalid"}, {"azp": "oj"},
])
async def test_invalid_id_token_rejected(client, db, provider, claims):
    state = await begin(client, provider)
    provider["claims"] = claims
    result = await client.get("/api/auth/callback", params={"state": state, "code": "x"})
    assert "sso_failed" in result.headers["location"]
    assert await db.scalar(select(DashboardSession)) is None


async def test_bad_signature_rejected(provider):
    token = provider["encode"]()
    a, b, c = token.split(".")
    c = ("A" if c[0] != "A" else "B") + c[1:]
    with pytest.raises(JWTError):
        await sso.verify_identity(f"{a}.{b}.{c}")


async def test_refresh_rotation_and_invalid_grant(client, db, provider):
    await authorize(db, client, provider)
    row = await db.scalar(select(DashboardSession))
    row.refresh_at = 0
    await db.commit()
    assert (await client.get("/api/auth/session")).status_code == 200
    await db.refresh(row)
    assert sso.cipher().decrypt(row.refresh_token.encode()).decode() == "refresh-1-rotated"
    assert provider["calls"][-1]["grant_type"] == "refresh_token"
    row.refresh_at = 0
    await db.commit()
    provider["error"] = "invalid_grant"
    assert (await client.get("/api/auth/session")).status_code == 401
    assert await db.scalar(select(DashboardSession)) is None


async def test_network_error_preserves_bounded_session_and_revocation(client, db, provider):
    await authorize(db, client, provider)
    row = await db.scalar(select(DashboardSession))
    row.refresh_at = 0
    expiry = row.expires_at
    await db.commit()
    provider["error"] = "network"
    assert (await client.get("/api/auth/session")).status_code == 200
    assert row.expires_at == expiry
    user = await db.get(DashboardUser, SUB)
    user.enabled = False
    await db.commit()
    assert (await client.get("/api/auth/session")).status_code == 401


async def test_session_expiry_and_missing_cookie(client, db, provider):
    await authorize(db, client, provider)
    row = await db.scalar(select(DashboardSession))
    row.expires_at = 0
    await db.commit()
    assert (await client.get("/api/dashboard/summary")).status_code == 401


async def test_parallel_tabs_refresh_only_once(client, db, provider):
    await authorize(db, client, provider)
    row = await db.scalar(select(DashboardSession))
    row.refresh_at = 0
    await db.commit()
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    cookie = client.cookies.get(sso.COOKIE)
    async def request():
        async with factory() as session:
            return await sso.session_user(session, cookie)
    users = await asyncio.gather(request(), request(), request())
    assert all(user.sub == SUB for user in users)
    assert sum(p["grant_type"] == "refresh_token" for p in provider["calls"]) == 1


async def test_changed_encryption_key_requires_login(client, db, provider, monkeypatch):
    await authorize(db, client, provider)
    row = await db.scalar(select(DashboardSession))
    row.refresh_at = 0
    await db.commit()
    monkeypatch.setattr(settings, "sso_token_encryption_key", Fernet.generate_key().decode())
    assert (await client.get("/api/auth/session")).status_code == 401


async def test_management_grant_revoke(client, db, provider, monkeypatch, capsys):
    from argparse import Namespace
    from app import manage_dashboard as manage
    class Engine:
        def begin(self): return db.bind.begin()
        async def dispose(self): pass
    monkeypatch.setattr(manage, "engine", Engine())
    monkeypatch.setattr(manage, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    await manage.run(Namespace(command="grant", sub=SUB, role="viewer"))
    state = await begin(client, provider)
    await client.get("/api/auth/callback", params={"state": state, "code": "x"})
    assert (await client.get("/api/auth/session")).json()["role"] == "viewer"
    await manage.run(Namespace(command="list"))
    assert SUB in capsys.readouterr().out
    await manage.run(Namespace(command="revoke", sub=SUB))
    assert (await client.get("/api/auth/session")).status_code == 401


def test_websocket_rejects_wrong_origin(provider):
    with TestClient(main.app).websocket_connect("/ws/dashboard", headers={"origin": "https://evil.example"}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4403


def test_websocket_cookie_auth(provider, monkeypatch):
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    async def user(db, cookie):
        return object() if cookie == "valid" else None
    monkeypatch.setattr(ws_router, "AsyncSessionLocal", DB)
    monkeypatch.setattr(sso, "session_user", user)
    tc = TestClient(main.app)
    with tc.websocket_connect("/ws/dashboard", headers={"origin": "http://localhost:3000", "cookie": f"{sso.COOKIE}=valid"}) as ws:
        assert ws.receive_json()["type"] == "connected"
    with tc.websocket_connect("/ws/dashboard", headers={"origin": "http://localhost:3000"}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4401


async def test_first_admin_is_promoted_in_database(client, db, provider):
    state = await begin(client, provider)
    result = await client.get("/api/auth/callback", params={"state": state, "code": "first"})
    assert result.headers["location"].endswith("error=access_denied")
    assert (await db.get(DashboardUser, SUB)).role == "pending"
    await db.execute(update(DashboardUser).where(DashboardUser.sub == SUB).values(role="admin", enabled=True))
    await db.commit()
    state = await begin(client, provider)
    result = await client.get("/api/auth/callback", params={"state": state, "code": "second"})
    assert result.headers["location"] == "http://localhost:3000/"
    assert (await client.get("/api/auth/session")).json()["role"] == "admin"


async def test_admin_list_search_and_permissions(client, db, provider):
    assert (await client.get("/api/admin/users")).status_code == 401
    await authorize(db, client, provider)
    target = str(uuid4())
    db.add(DashboardUser(sub=target, username="waiting-user", name="대기 사용자", email="wait@example.com",
                         role="pending", enabled=True))
    await db.commit()
    result = await client.get("/api/admin/users", params={"q": "waiting"})
    assert result.status_code == 200
    body = result.json()
    assert body["total"] == 1 and body["current_sub"] == SUB
    assert body["users"][0]["sub"] == target
    assert (await client.get("/api/admin/users", params={"offset": 1, "limit": 1})).json()["users"]
    assert (await client.get("/api/admin/users", params={"offset": -1})).status_code == 422


async def test_viewer_cannot_manage_users(client, db, provider):
    await authorize(db, client, provider)
    user = await db.get(DashboardUser, SUB)
    user.role = "viewer"
    await db.commit()
    assert (await client.get("/api/dashboard/summary")).status_code == 200
    assert (await client.get("/api/admin/users")).status_code == 403
    result = await client.patch(f"/api/admin/users/{SUB}", headers={"origin": "http://localhost:3000"},
                                json={"role": "admin", "enabled": True})
    assert result.status_code == 403
    assert (await db.get(DashboardUser, SUB)).role == "viewer"


async def test_admin_grant_revoke_and_audit(client, db, provider):
    await authorize(db, client, provider)
    target = str(uuid4())
    db.add(DashboardUser(sub=target, username="new-user", name="", email="", role="pending", enabled=True))
    await db.commit()
    path = f"/api/admin/users/{target}"
    body = {"role": "viewer", "enabled": True}
    assert (await client.patch(path, json=body)).status_code == 403  # Origin 필수
    assert (await client.patch(path, headers={"origin": "https://evil.example"}, json=body)).status_code == 403
    headers = {"origin": "http://localhost:3000"}
    assert (await client.patch(path, headers=headers, json=body)).json()["role"] == "viewer"
    assert (await client.patch(path, headers=headers, json={**body, "role": "admin"})).json()["role"] == "admin"
    # 대상 사용자의 열린 세션은 접근 회수 시 폐기한다.
    token = "target-session"
    db.add(DashboardSession(token_hash=sso.digest(token), sub=target, expires_at=sso.now() + 300,
                            refresh_at=sso.now() + 300, refresh_token="not-used"))
    await db.commit()
    assert (await client.patch(path, headers=headers, json={"role": "pending", "enabled": False})).status_code == 200
    assert await db.get(DashboardSession, sso.digest(token), populate_existing=True) is None
    logs = (await db.scalars(select(DashboardAccessLog).order_by(DashboardAccessLog.id))).all()
    assert [log.role for log in logs] == ["viewer", "admin", "pending"]
    assert all(log.actor == SUB and log.target_sub == target for log in logs)
    assert (await client.patch(path, headers=headers, json={"role": "owner", "enabled": True})).status_code == 422
    assert (await client.patch(f"/api/admin/users/{uuid4()}", headers=headers, json=body)).status_code == 404


async def test_last_admin_protection_and_self_demotion(client, db, provider):
    await authorize(db, client, provider)
    path = f"/api/admin/users/{SUB}"
    headers = {"origin": "http://localhost:3000"}
    for body in ({"role": "viewer", "enabled": True}, {"role": "admin", "enabled": False}):
        result = await client.patch(path, headers=headers, json=body)
        assert result.status_code == 409
    assert (await db.get(DashboardUser, SUB, populate_existing=True)).role == "admin"
    target = str(uuid4())
    await access.change_access(db, target, "admin", True, create=True)
    assert (await client.patch(path, headers=headers, json={"role": "viewer", "enabled": True})).status_code == 200
    assert (await client.get("/api/auth/session")).json()["role"] == "viewer"
    assert (await client.get("/api/admin/users")).status_code == 403


async def test_simultaneous_admin_demotion_keeps_one_admin(db, provider):
    other = str(uuid4())
    await access.change_access(db, SUB, "admin", True, create=True)
    await access.change_access(db, other, "admin", True, create=True)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    async def demote(sub):
        async with factory() as session:
            return await access.change_access(session, sub, "viewer", True, actor_sub=sub)
    results = await asyncio.gather(demote(SUB), demote(other), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(getattr(result, "status_code", None) == 409 for result in results) == 1
    await db.rollback()
    users = (await db.scalars(select(DashboardUser).execution_options(populate_existing=True))).all()
    assert sum(user.role == "admin" and user.enabled for user in users) == 1
