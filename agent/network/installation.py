"""GEN-1773: 상태 보고 루프와 분리한 등록/하드웨어 동기화."""
import json
from pathlib import Path
import threading

from agent.identity import atomic_write
from agent.collectors.hardware import collect, configuration


class InstallationSync:
    def __init__(self, identity):
        self.identity = identity
        self.cache = Path(identity.path).with_name("last_synced_hardware.json")
        self.snapshot = None
        self.ready = threading.Event()

    def prepare(self, client):
        state = self.identity.state
        client._session.headers["X-Installation-Token"] = state["token"]
        if not state["registered"]:
            import socket
            ok, reply = client._post_with_response("/api/agents/installations", {
                "agent_id": state["agent_id"], "installation_id": state["installation_id"],
                "hostname": socket.gethostname(), "token": state["token"]})
            if not ok or not reply or reply.get("status") != "active":
                return False
            state["registered"] = True
            self.identity.save()
        self.ready.set()
        if self.snapshot is None:
            self.snapshot = collect()
        try:
            cached = json.loads(self.cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        current = configuration(self.snapshot)
        if current != cached:
            ok, reply = client._post_with_response("/api/agents/hardware", {
                **self.snapshot, "agent_id": state["agent_id"], "installation_id": state["installation_id"]})
            if not ok or not reply or reply.get("status") != "synced":
                return False
            # 조회 실패가 있으면 다음 주기에 다시 수집. NULL로 기존 구성을 지우지 않는다.
            if any(self.snapshot[k] is None for k in ("memory_total_bytes", "cpus", "gpus", "disks", "adapters")):
                self.snapshot = None
                return False
            atomic_write(self.cache, current)
        return True
