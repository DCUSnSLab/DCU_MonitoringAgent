"""대시보드 관리자 로그인 테스트 (GEN-1149).

관리자 계정 1개로 로그인해 토큰을 받고, 대시보드·통계 API와 실시간 연결은 토큰이 있어야 쓸 수 있다.
로그인에 연속 10회 실패하면 접속 IP를 잠그고, 잠글 때마다 1분 → 5분 → 30분 → 60분 → 120분으로 늘린다.
"""
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.config import settings
from app.database import get_db
from app.services import auth_service
from app.services.auth_service import LoginLockout, login_lockout

PASSWORD = "test-password!"


@pytest.fixture(autouse=True)
def admin_account(monkeypatch):
    """테스트용 관리자 계정 (운영 비밀번호는 코드에 없음), 로그인 잠금 기록 초기화."""
    monkeypatch.setattr(settings, "dashboard_admin_username", "admin")
    monkeypatch.setattr(settings, "dashboard_admin_password", PASSWORD)
    monkeypatch.setattr(settings, "dashboard_token_secret", "test-secret")
    login_lockout._states.clear()
    yield
    login_lockout._states.clear()


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
    for bad in ("", "abc", ".", f"{payload}.", f".{signature}", f"{forged_payload}.{signature}",
                f"{payload}.{signature[:-2]}AA", token + "x"):
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


# ─── 로그인 잠금 ─────────────────────────────────────────────────

def fail(lockout: LoginLockout, times: int, now: float, key: str = "ip") -> float:
    """times 번 실패를 기록하고 마지막 기록의 잠금 시간(초)을 돌려준다."""
    seconds = 0.0
    for _ in range(times):
        seconds = lockout.record_failure(key, now)
    return seconds


def test_lockout_after_ten_failures():
    lockout = LoginLockout(max_failures=10, lock_minutes=(1, 5, 30, 60, 120), reset_seconds=86400)
    assert fail(lockout, 9, 0.0) == 0
    assert lockout.remaining_attempts("ip", 0.0) == 1
    assert lockout.locked_seconds("ip", 0.0) == 0

    assert fail(lockout, 1, 0.0) == 60  # 10번째 실패에서 1분 잠금
    assert lockout.locked_seconds("ip", 30.0) == 30
    assert lockout.locked_seconds("ip", 60.0) == 0  # 풀림
    assert lockout.remaining_attempts("ip", 60.0) == 10  # 풀리면 다시 10번 실패해야 다음 단계
    assert fail(lockout, 9, 60.0) == 0
    assert fail(lockout, 1, 60.0) == 300  # 다음 단계 5분


def test_lockout_steps_escalate_then_repeat_last():
    lockout = LoginLockout(max_failures=10, lock_minutes=(1, 5, 30, 60, 120), reset_seconds=86400)
    now, minutes = 0.0, []
    for _ in range(7):
        seconds = fail(lockout, 10, now)
        minutes.append(seconds / 60)
        now += seconds  # 잠금이 풀린 뒤 다시 10번 실패
    assert minutes == [1, 5, 30, 60, 120, 120, 120]


def test_lockout_is_per_client():
    lockout = LoginLockout(max_failures=10, lock_minutes=(1,), reset_seconds=86400)
    fail(lockout, 10, 0.0, key="a")
    assert lockout.locked_seconds("a", 0.0) == 60
    assert lockout.locked_seconds("b", 0.0) == 0


def test_lockout_reset_by_success_or_quiet_day():
    lockout = LoginLockout(max_failures=10, lock_minutes=(1, 5), reset_seconds=86400)
    fail(lockout, 10, 0.0)  # 1분 잠금
    lockout.record_success("ip")
    assert fail(lockout, 10, 100.0) == 60  # 성공으로 처음 단계부터

    # 하루 동안 실패가 없으면 처음 단계부터
    assert fail(lockout, 10, 100.0 + 60 + 86400) == 60
    # 하루가 안 지났으면 다음 단계
    assert fail(lockout, 10, 100.0 + 60 + 86400 + 60 + 10) == 300


# ─── 로그인 API · 보호 ───────────────────────────────────────────

async def test_login_and_access_protected_api(client):
    r = await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin" and body["expires_at"]
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
    """에이전트·OTA 경로는 로그인 토큰이 아니라 기존 API 키로 인증 (헬스체크는 공개)"""
    assert (await client.get("/api/health")).status_code == 200
    r = await client.post("/api/agents/register", json={"agent_id": "A", "hostname": "PC-1"},
                          headers={"X-API-Key": settings.api_key})
    assert r.status_code == 200


async def test_wrong_password_counts_down_then_locks(client):
    for i in range(9):
        r = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
        assert f"남은 시도 {9 - i}회" in r.json()["detail"]

    r = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60" and r.json()["retry_after"] == 60
    assert "1분" in r.json()["detail"]

    # 잠긴 동안에는 맞는 비밀번호도 거부 (비밀번호 확인 안 함)
    r = await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 429


async def test_successful_login_resets_failures(client):
    for _ in range(5):
        await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert (await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})).status_code == 200
    r = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert "남은 시도 9회" in r.json()["detail"]


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
            if "bearer" in subprotocols:
                assert ws.accepted_subprotocol == "bearer"


def test_websocket_accepts_valid_token():
    token, _ = auth_service.issue_token("admin")
    tc = TestClient(main.app)
    with tc.websocket_connect("/ws/dashboard", subprotocols=["bearer", token]) as ws:
        assert ws.accepted_subprotocol == "bearer"
        assert ws.receive_json()["type"] == "connected"
