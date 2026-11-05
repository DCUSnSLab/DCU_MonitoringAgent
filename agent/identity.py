"""GEN-1773: 정책 YAML과 분리한 설치 식별 상태. Windows에서는 DPAPI 사용자 범위 보호."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
from uuid import uuid4

SEAT_PATTERN = re.compile(r"^[A-Za-z0-9]+-\d+-\d+$")


def atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


class Identity:
    def __init__(self, path):
        self.path = Path(path)
        self.state = None
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("protected"):
                import win32crypt
                payload = json.loads(win32crypt.CryptUnprotectData(
                    base64.b64decode(payload["protected"]), None, None, None, 0)[1])
            self.state = payload

    def initialize(self, legacy_id, fallback, hostname=None):
        if self.state:
            return self.state["agent_id"]
        hostname = hostname or socket.gethostname()
        self.state = {
            "version": 1,
            "agent_id": hostname if SEAT_PATTERN.fullmatch(hostname) else (legacy_id or fallback()),
            "legacy_agent_id": legacy_id or None,
            "installation_id": str(uuid4()),
            "token": secrets.token_urlsafe(48),
            "registered": False,
        }
        self.save()  # 요청 전 저장: 응답 유실/재시작 후에도 같은 UUID와 토큰 사용
        return self.state["agent_id"]

    def save(self):
        value = self.state
        if sys.platform == "win32":
            import win32crypt
            blob = win32crypt.CryptProtectData(json.dumps(value).encode(), "DCU installation", None, None, None, 0)
            value = {"protected": base64.b64encode(blob).decode()}
        atomic_write(self.path, value)
