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


def _secret() -> bytes:
    return settings.dashboard_token_secret.encode("utf-8") or _process_secret


def _derive_key(purpose: str, account: str) -> bytes:
    return hmac.new(_secret(), f"{purpose}\n{account}".encode("utf-8"), hashlib.sha256).digest()


def _signed(key: bytes, claims: dict) -> str:
    payload = _b64encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_b64encode(hmac.new(key, payload.encode('ascii'), hashlib.sha256).digest())}"


def _open_signed(key: bytes, token: str) -> Optional[dict]:
    """서명이 맞으면 내용(dict), 아니면 None. 서명을 확인한 뒤에만 내용을 해석한다."""
    if not token or not token.isascii():
        return None
    payload, _, signature = token.partition(".")
    if not payload or not signature:
        return None
    expected = _b64encode(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        claims = json.loads(_b64decode(payload))
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None


# ─── 로그인 토큰 ─────────────────────────────────────────────────

def _token_key() -> bytes:
    # 아이디·비밀번호를 섞어 비밀번호를 바꾸면 기존 토큰이 모두 무효가 되게 한다
    return _derive_key("token", f"{settings.dashboard_admin_username}\n{settings.dashboard_admin_password}")


def issue_token(username: str, now: Optional[float] = None) -> tuple[str, int]:
    """토큰과 만료 시각(유닉스 초)을 만든다."""
    now = time.time() if now is None else now
    expires_at = int(now) + settings.dashboard_token_ttl_hours * 3600
    return _signed(_token_key(), {"sub": username, "exp": expires_at}), expires_at


def verify_token_claims(token: str, now: Optional[float] = None) -> Optional[tuple[str, int]]:
    """유효한 토큰이면 (아이디, 만료 시각), 아니면 None (서명 불일치·만료·형식 오류·로그인 불가 상태)."""
    if not is_configured():
        return None
    claims = _open_signed(_token_key(), token)
    if claims is None:
        return None
    try:
        username, expires_at = claims["sub"], int(claims["exp"])
    except (KeyError, TypeError, ValueError):
        return None
    now = time.time() if now is None else now
    if expires_at <= now or username != settings.dashboard_admin_username:
        return None
    return username, expires_at


def verify_token(token: str, now: Optional[float] = None) -> Optional[str]:
    """유효한 토큰이면 아이디, 아니면 None."""
    claims = verify_token_claims(token, now)
    return claims[0] if claims else None


def token_from_subprotocols(subprotocols: list[str]) -> Optional[str]:
    """실시간 연결 요청의 하위 프로토콜 목록 ['bearer', <토큰>] 에서 토큰을 꺼낸다."""
    if len(subprotocols) >= 2 and subprotocols[0] == WS_SUBPROTOCOL:
        return subprotocols[1]
    return None
