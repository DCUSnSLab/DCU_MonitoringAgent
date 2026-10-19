"""대시보드 전송 관리자 테스트 (GEN-1149).

보고 처리와 대시보드 전송을 분리한 구조를 확인한다:
publish 는 대기열에 넣고 바로 돌아오고, 대시보드별 전송 담당이 순서대로 보내며,
전송에 실패한 대시보드만 정리한다. 같은 PC 갱신·요약은 대기열에서 합친다.
"""
import asyncio
import inspect
import json

import pytest

from app.services import ws_manager as ws_module
from app.services.ws_manager import WSManager, _coalesce_key, _merge


class FakeWebSocket:
    """보낸 메시지를 기록하는 가짜 대시보드 연결. fail=True면 보내기에서 오류."""

    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail
        self.closed_with = None

    async def accept(self):
        pass

    async def send_text(self, text: str):
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000):
        self.closed_with = code


async def settle():
    """전송 담당 태스크가 대기열을 비울 시간을 준다 (메시지마다 이벤트 루프가 몇 바퀴 필요)."""
    await asyncio.sleep(0.05)


@pytest.fixture
async def manager():
    """테스트용 전송 관리자. 끝날 때 대시보드별 전송 태스크를 정리한다."""
    m = WSManager()
    yield m
    tasks = [c.task for c in m._clients.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def types(ws: FakeWebSocket) -> list[str]:
    return [m["type"] for m in ws.sent]


# ─── 합치기 규칙 ─────────────────────────────────────────────────

def test_coalesce_key():
    assert _coalesce_key("agent_update", {"agent_id": "A"}) == "agent:A"
    assert _coalesce_key("agent_offline", {"agent_id": "A"}) == "agent:A"
    assert _coalesce_key("summary_update", {}) == "summary_update"
    assert _coalesce_key("heartbeat", {}) == "heartbeat"
    assert _coalesce_key("connected", {}) is None
    assert _coalesce_key("agent_update", {}) is None  # agent_id 없으면 합치지 않음


def test_merge_keeps_latest_fields_and_appends_alerts():
    first = {"type": "agent_update", "data": {"agent_id": "A", "hostname": "PC-1", "current_state": "warning",
                                              "alerts": [{"message": "a1"}]}}
    offline = {"type": "agent_offline", "data": {"agent_id": "A", "is_online": False}}
    merged = _merge(first, offline)
    assert merged["type"] == "agent_offline"
    assert merged["data"] == {"agent_id": "A", "hostname": "PC-1", "current_state": "warning",
                              "is_online": False, "alerts": [{"message": "a1"}]}

    second = {"type": "agent_update", "data": {"agent_id": "A", "current_state": "danger", "alerts": [{"message": "a2"}]}}
    assert [a["message"] for a in _merge(first, second)["data"]["alerts"]] == ["a1", "a2"]


def test_merged_alerts_capped(monkeypatch):
    monkeypatch.setattr(ws_module, "MAX_MERGED_ALERTS", 3)
    prev = {"type": "agent_update", "data": {"agent_id": "A", "alerts": [{"n": 1}, {"n": 2}]}}
    new = {"type": "agent_update", "data": {"agent_id": "A", "alerts": [{"n": 3}, {"n": 4}]}}
    assert [a["n"] for a in _merge(prev, new)["data"]["alerts"]] == [2, 3, 4]


# ─── 전송 ────────────────────────────────────────────────────────

def test_publish_does_not_wait_for_network():
    """보고 처리에서 부르는 publish 는 일반 함수 (await 하지 않으므로 네트워크를 기다리지 않음)"""
    assert not inspect.iscoroutinefunction(WSManager.publish)


async def test_messages_delivered_in_order_to_each_dashboard(manager):
    a, b = FakeWebSocket(), FakeWebSocket()
    await manager.connect(a)
    await manager.connect(b)
    manager.publish("agent_update", {"agent_id": "A", "current_state": "safe"})
    manager.publish("agent_update", {"agent_id": "B", "current_state": "warning"})
    await settle()

    for ws in (a, b):
        assert types(ws) == ["connected", "agent_update", "agent_update"]
        assert [m["data"]["agent_id"] for m in ws.sent[1:]] == ["A", "B"]


async def test_pending_updates_merged_per_agent(manager):
    """보내기 전에 쌓인 같은 PC 갱신은 1건으로 합치고(새 알림 이어 붙임), 요약은 최신 1건만"""
    ws = FakeWebSocket()
    await manager.connect(ws)
    manager.publish("agent_update", {"agent_id": "A", "current_state": "warning", "alerts": [{"message": "a1"}]})
    manager.publish("summary_update", {"danger_count": 0})
    manager.publish("agent_update", {"agent_id": "A", "current_state": "danger", "alerts": [{"message": "a2"}]})
    manager.publish("summary_update", {"danger_count": 1})
    await settle()

    assert types(ws) == ["connected", "agent_update", "summary_update"]
    agent = ws.sent[1]["data"]
    assert agent["current_state"] == "danger"
    assert [a["message"] for a in agent["alerts"]] == ["a1", "a2"]
    assert ws.sent[2]["data"] == {"danger_count": 1}


async def test_failed_dashboard_dropped_others_unaffected(manager):
    """보내기에 실패한 대시보드만 목록에서 빠지고 종료 신호(1013)를 받음, 다른 대시보드는 계속 받음"""
    good, bad = FakeWebSocket(), FakeWebSocket(fail=True)
    await manager.connect(good)
    await manager.connect(bad)
    await settle()
    assert manager.connection_count == 1
    assert bad.closed_with == 1013

    manager.publish("agent_update", {"agent_id": "A"})
    await settle()
    assert types(good) == ["connected", "agent_update"]


async def test_queue_overflow_drops_dashboard(manager, monkeypatch):
    """대기열이 상한을 넘으면 그 대시보드를 끊음 (재연결해서 전체를 다시 불러오도록)"""
    monkeypatch.setattr(ws_module, "MAX_PENDING", 3)
    ws = FakeWebSocket()
    await manager.connect(ws)  # 'connected' 1건이 대기열에 있음
    for i in range(3):
        manager.publish("notice", {"n": i})  # 합치지 않는 메시지
    assert manager.connection_count == 0
    await settle()
    assert ws.closed_with == 1013


async def test_disconnect_stops_sender(manager):
    ws = FakeWebSocket()
    await manager.connect(ws)
    await settle()
    client_task = next(iter(manager._clients.values())).task
    await manager.disconnect(ws)
    await settle()
    assert manager.connection_count == 0
    assert client_task.cancelled() or client_task.done()

    manager.publish("agent_update", {"agent_id": "A"})  # 끊긴 대시보드로는 보내지 않음
    await settle()
    assert types(ws) == ["connected"]


def test_summary_request_taken_once():
    manager = WSManager()
    assert manager.take_summary_request() is False
    manager.request_summary()
    manager.request_summary()
    assert manager.take_summary_request() is True
    assert manager.take_summary_request() is False
