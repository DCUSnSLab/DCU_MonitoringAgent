"""
대시보드 관리자 로그인

관리자 계정 1개(아이디·비밀번호는 환경변수)로 로그인하면 서명된 토큰을 발급하고,
대시보드·통계 API와 실시간 연결(WebSocket)은 이 토큰이 있어야 쓸 수 있다.
에이전트 보고·OTA는 기존처럼 API 키로 인증한다(이 모듈과 무관).

- 비밀번호는 코드에 두지 않는다(공개 저장소). DASHBOARD_ADMIN_PASSWORD 가 비어 있으면 로그인할 수 없다.
- 토큰은 서버 비밀값으로 서명(HMAC-SHA256)하고 만료 시각을 담는다. 서버에 저장하지 않는다.
  서명 키에 아이디·비밀번호를 섞어, 비밀번호를 바꾸면 기존 토큰은 모두 무효가 된다.
- DASHBOARD_TOKEN_SECRET 이 비어 있으면 서버가 켜질 때마다 새로 만든다(서버 재시작 시 다시 로그인).
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from app.config import settings

# 실시간 연결에서 토큰을 실어 보내는 하위 프로토콜 이름 (브라우저: new WebSocket(url, ['bearer', token]))
WS_SUBPROTOCOL = "bearer"

# 서버가 켜질 때 만드는 서명 비밀값 (DASHBOARD_TOKEN_SECRET 이 없을 때 사용)
_process_secret = secrets.token_bytes(32)


def is_configured() -> bool:
    """관리자 비밀번호가 설정돼 로그인할 수 있는지."""
    return bool(settings.dashboard_admin_password)


def check_credentials(username: str, password: str) -> bool:
    """아이디·비밀번호 확인 (비교 시간이 내용에 따라 달라지지 않게 비교)."""
    if not is_configured():
        return False
    user_ok = hmac.compare_digest(username.encode("utf-8"), settings.dashboard_admin_username.encode("utf-8"))
    password_ok = hmac.compare_digest(password.encode("utf-8"), settings.dashboard_admin_password.encode("utf-8"))
    return user_ok and password_ok


def _b64encode(data: bytes) -> str:
    # 실시간 연결 하위 프로토콜 값으로도 쓰이므로 '=' 패딩 없는 URL 안전 문자만 쓴다
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signing_key() -> bytes:
    secret = settings.dashboard_token_secret.encode("utf-8") or _process_secret
    account = f"{settings.dashboard_admin_username}\n{settings.dashboard_admin_password}".encode("utf-8")
    return hmac.new(secret, account, hashlib.sha256).digest()


def _sign(payload: str) -> str:
    return _b64encode(hmac.new(_signing_key(), payload.encode("ascii"), hashlib.sha256).digest())


def issue_token(username: str, now: Optional[float] = None) -> tuple[str, int]:
    """토큰과 만료 시각(유닉스 초)을 만든다."""
    now = time.time() if now is None else now
    expires_at = int(now) + settings.dashboard_token_ttl_hours * 3600
    payload = _b64encode(json.dumps({"sub": username, "exp": expires_at}, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(payload)}", expires_at


def verify_token(token: str, now: Optional[float] = None) -> Optional[str]:
    """유효한 토큰이면 아이디, 아니면 None (서명 불일치·만료·형식 오류·로그인 불가 상태)."""
    if not token or not is_configured():
        return None
    payload, _, signature = token.partition(".")
    if not payload or not signature or not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        claims = json.loads(_b64decode(payload))
        username, expires_at = claims["sub"], int(claims["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    now = time.time() if now is None else now
    if expires_at <= now or username != settings.dashboard_admin_username:
        return None
    return username


def token_from_subprotocols(subprotocols: list[str]) -> Optional[str]:
    """실시간 연결 요청의 하위 프로토콜 목록 ['bearer', <토큰>] 에서 토큰을 꺼낸다."""
    if len(subprotocols) >= 2 and subprotocols[0] == WS_SUBPROTOCOL:
        return subprotocols[1]
    return None


@dataclass
class _LoginFailures:
    failures: int = 0        # 마지막 잠금 이후 연속 실패 수
    lockouts: int = 0        # 지금까지 잠긴 횟수 (다음 잠금 시간 단계)
    locked_until: float = 0.0
    last_failure: float = 0.0


class LoginLockout:
    """같은 곳(접속 IP)에서 로그인에 연속으로 실패하면 잠근다 (메모리, 서버 1대 기준).

    max_failures 번 연속 실패할 때마다 잠그고, 잠글 때마다 lock_minutes 의 다음 단계 시간만큼 잠근다
    (마지막 단계는 반복). 잠금이 풀리면 다시 max_failures 번 실패해야 다음 단계로 잠긴다.
    로그인에 성공하거나 reset_seconds 동안 실패가 없으면 처음 단계로 돌아간다.
    """

    MAX_TRACKED = 10_000  # 기록하는 접속 IP 수가 이보다 많으면 오래된 기록을 정리

    def __init__(self, max_failures: int, lock_minutes: tuple[int, ...], reset_seconds: float):
        self.max_failures = max_failures
        self.lock_minutes = lock_minutes
        self.reset_seconds = reset_seconds
        self._states: dict[str, _LoginFailures] = {}

    def _is_stale(self, state: _LoginFailures, now: float) -> bool:
        return now >= state.locked_until and now - state.last_failure >= self.reset_seconds

    def _get(self, key: str, now: float) -> Optional[_LoginFailures]:
        state = self._states.get(key)
        if state is not None and self._is_stale(state, now):
            del self._states[key]
            return None
        return state

    def locked_seconds(self, key: str, now: Optional[float] = None) -> float:
        """잠겨 있으면 남은 시간(초), 아니면 0."""
        now = time.monotonic() if now is None else now
        state = self._get(key, now)
        return max(0.0, state.locked_until - now) if state else 0.0

    def remaining_attempts(self, key: str, now: Optional[float] = None) -> int:
        """잠기기 전까지 남은 실패 허용 횟수."""
        now = time.monotonic() if now is None else now
        state = self._get(key, now)
        return self.max_failures - (state.failures if state else 0)

    def record_failure(self, key: str, now: Optional[float] = None) -> float:
        """실패 1회 기록. 이번 실패로 잠기면 잠금 시간(초), 아니면 0."""
        now = time.monotonic() if now is None else now
        state = self._get(key, now)
        if state is None:
            if len(self._states) >= self.MAX_TRACKED:
                self._prune(now)
            state = self._states[key] = _LoginFailures()
        state.failures += 1
        state.last_failure = now
        if state.failures < self.max_failures:
            return 0.0
        seconds = self.lock_minutes[min(state.lockouts, len(self.lock_minutes) - 1)] * 60
        state.locked_until = now + seconds
        state.lockouts += 1
        state.failures = 0
        return float(seconds)

    def record_success(self, key: str) -> None:
        self._states.pop(key, None)

    def _prune(self, now: float) -> None:
        for key in [k for k, state in self._states.items() if self._is_stale(state, now)]:
            del self._states[key]


# 접속 IP마다 연속 10회 실패 시 잠금: 1분 → 5분 → 30분 → 60분 → 120분(이후 반복). 하루 동안 실패가 없으면 처음부터
login_lockout = LoginLockout(max_failures=10, lock_minutes=(1, 5, 30, 60, 120), reset_seconds=24 * 3600)
