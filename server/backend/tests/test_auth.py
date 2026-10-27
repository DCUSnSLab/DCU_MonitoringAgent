"""대시보드 관리자 로그인 테스트 (GEN-1682).

관리자 계정 1개로 로그인해 토큰을 받고, 대시보드·통계 API와 실시간 연결은 토큰이 있어야 쓸 수 있다.
"""
import re
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.config import settings
from app.database import get_db
from app.services import auth_service

PASSWORD = "test-password!"


@pytest.fixture(autouse=True)
def admin_account(monkeypatch):
    """테스트용 관리자 계정 (운영 비밀번호는 코드에 없음)."""
    monkeypatch.setattr(settings, "dashboard_auth_mode", "local")
    monkeypatch.setattr(settings, "dashboard_admin_username", "admin")
    monkeypatch.setattr(settings, "dashboard_admin_password", PASSWORD)
    monkeypatch.setattr(settings, "dashboard_token_secret", "test-secret")


@pytest.fixture
async def client(db):
    """테스트 DB를 쓰는 HTTP 클라이언트 (같은 이벤트 루프에서 앱 호출)."""
    async def test_db():
        yield db

    main.app.dependency_overrides[get_db] = test_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.pop(get_db, None)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── 토큰 ────────────────────────────────────────────────────────

def test_token_roundtrip_and_expiry():
    token, expires_at = auth_service.issue_token("admin", now=1_000_000)
    assert expires_at == 1_000_000 + settings.dashboard_token_ttl_hours * 3600
    assert auth_service.verify_token(token, now=1_000_000) == "admin"
    assert auth_service.verify_token(token, now=expires_at - 1) == "admin"
    assert auth_service.verify_token(token, now=expires_at) is None  # 만료


def test_token_rejects_tampering_and_garbage():
    token, _ = auth_service.issue_token("admin")
    payload, signature = token.split(".")
    forged_payload = auth_service._b64encode(b'{"sub":"admin","exp":9999999999}')
    other_char = "B" if signature[-1] != "B" else "C"  # 항상 원래와 다른 서명
    for bad in ("", "abc", ".", f"{payload}.", f".{signature}", f"{forged_payload}.{signature}",
                f"{payload}.{signature[:-1]}{other_char}", token + "x", "abc.\u00e9", "\u00e9.abc", token + "\u00e9"):
        assert auth_service.verify_token(bad) is None, bad


def test_token_usable_as_websocket_subprotocol():
    """하위 프로토콜 값으로 쓸 수 있는 문자만 사용 ('=' 패딩·공백 없음)"""
    token, _ = auth_service.issue_token("admin")
    assert re.fullmatch(r"[A-Za-z0-9._-]+", token)


def test_password_change_or_unset_invalidates_tokens(monkeypatch):
    token, _ = auth_service.issue_token("admin")
    monkeypatch.setattr(settings, "dashboard_admin_password", "changed")
    assert auth_service.verify_token(token) is None
    monkeypatch.setattr(settings, "dashboard_admin_password", PASSWORD)
    assert auth_service.verify_token(token) == "admin"
    monkeypatch.setattr(settings, "dashboard_admin_password", "")
    assert auth_service.verify_token(token) is None


def test_other_secret_invalidates_tokens(monkeypatch):
    token, _ = auth_service.issue_token("admin")
    monkeypatch.setattr(settings, "dashboard_token_secret", "other-secret")
    assert auth_service.verify_token(token) is None


def test_credentials():
    assert auth_service.check_credentials("admin", PASSWORD)
    assert not auth_service.check_credentials("admin", PASSWORD + "x")
    assert not auth_service.check_credentials("Admin", PASSWORD)
    assert not auth_service.check_credentials("", "")


def test_no_password_configured_means_no_login(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_admin_password", "")
    assert not auth_service.is_configured()
    assert not auth_service.check_credentials("admin", "")


def test_token_from_subprotocols():
    assert auth_service.token_from_subprotocols(["bearer", "t0k"]) == "t0k"
    assert auth_service.token_from_subprotocols(["bearer"]) is None
    assert auth_service.token_from_subprotocols(["other", "t0k"]) is None
    assert auth_service.token_from_subprotocols([]) is None


# ─── 로그인 API · 보호 ───────────────────────────────────────────

async def test_login_and_access_protected_api(client):
    r = await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin" and body["expires_at"]
    assert abs(body["expires_in"] - settings.dashboard_token_ttl_hours * 3600) <= 2
    token = body["token"]

    assert (await client.get("/api/auth/me", headers=bearer(token))).json() == {"username": "admin"}
    for path in ("/api/dashboard/summary", "/api/dashboard/agents", "/api/dashboard/alerts",
                 "/api/statistics/labs", "/api/statistics/blocked-sites", "/api/statistics/timeline"):
        assert (await client.get(path, headers=bearer(token))).status_code == 200, path


async def test_protected_api_requires_valid_token(client):
    token, _ = auth_service.issue_token("admin")
    for path in ("/api/auth/me", "/api/dashboard/summary", "/api/dashboard/agents", "/api/dashboard/agents/A",
                 "/api/dashboard/alerts", "/api/statistics/labs", "/api/statistics/blocked-sites",
                 "/api/statistics/timeline", "/api/statistics/blocked-detail?agent_id=A&site=x"):
        for headers in ({}, bearer("garbage"), {"Authorization": token}, bearer(token + "x")):
            r = await client.get(path, headers=headers)
            assert r.status_code == 401, (path, headers)
            assert r.headers["www-authenticate"] == "Bearer"


async def test_agent_and_ota_endpoints_unaffected(client):
    """에이전트·OTA 경로는 로그인 토큰과 무관 (에이전트는 기존 API 키, 헬스체크·OTA 조회는 그대로)"""
    assert (await client.get("/api/health")).status_code == 200
    assert (await client.get("/api/ota/latest")).status_code != 401
    r = await client.post("/api/agents/register", json={"agent_id": "A", "hostname": "PC-1"},
                          headers={"X-API-Key": settings.api_key})
    assert r.status_code == 200


async def test_wrong_password_rejected_without_lockout(client):
    """틀린 비밀번호는 401, 여러 번 틀려도 잠그지 않음 (맞는 비밀번호로 바로 로그인 가능)"""
    for _ in range(15):
        r = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["detail"] == "아이디 또는 비밀번호가 올바르지 않습니다"
    r = await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 200


async def test_login_unavailable_without_configured_password(client, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_admin_password", "")
    r = await client.post("/api/auth/login", json={"username": "admin", "password": ""})
    assert r.status_code == 503


# ─── 실시간 연결 ─────────────────────────────────────────────────

def test_websocket_requires_token():
    """토큰 없거나 틀리면 연결을 받은 뒤 4401로 닫음 (브라우저가 이유를 알고 로그인 화면으로 가도록)"""
    tc = TestClient(main.app)
    for subprotocols in ([], ["bearer"], ["bearer", "garbage"], ["other", "x"]):
        with tc.websocket_connect("/ws/dashboard", subprotocols=subprotocols) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
            assert closed.value.code == 4401, subprotocols
            assert ws.accepted_subprotocol == ("bearer" if "bearer" in subprotocols else None)


def test_websocket_accepts_valid_token():
    token, _ = auth_service.issue_token("admin")
    tc = TestClient(main.app)
    with tc.websocket_connect("/ws/dashboard", subprotocols=["bearer", token]) as ws:
        assert ws.accepted_subprotocol == "bearer"
        assert ws.receive_json()["type"] == "connected"


def test_websocket_closed_when_login_expires():
    """연결 중에 로그인이 만료되면 4401로 닫음 (대시보드가 로그인 화면으로 이동)"""
    ttl = settings.dashboard_token_ttl_hours * 3600
    token, expires_at = auth_service.issue_token("admin", now=time.time() - ttl + 2)
    tc = TestClient(main.app)
    with tc.websocket_connect("/ws/dashboard", subprotocols=["bearer", token]) as ws:
        assert ws.receive_json()["type"] == "connected"
        closed = {}

        def wait_for_close():
            try:
                ws.receive_text()
            except WebSocketDisconnect as e:
                closed["code"] = e.code
                closed["at"] = time.time()

        # 닫히지 않으면 테스트가 멈추지 않고 실패하도록 시간 제한을 두고 기다린다
        waiter = threading.Thread(target=wait_for_close, daemon=True)
        waiter.start()
        waiter.join(10)
        assert closed.get("code") == 4401
        assert closed["at"] >= expires_at
