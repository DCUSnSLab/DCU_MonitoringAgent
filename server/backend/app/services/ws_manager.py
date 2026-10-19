"""
WebSocket 연결 매니저

관리자 대시보드(브라우저)와의 WebSocket 연결을 관리하고, 에이전트 보고 등 변경을 실시간으로 전달합니다.

보고 처리와 대시보드 전송을 분리한다:
- publish()는 대시보드마다 둔 전송 대기열에 넣고 바로 돌아온다. 보고 처리(DB 작업)는 네트워크를 기다리지 않는다.
  (전에는 보고 처리 중 대시보드마다 차례로 보내며 기다려서, 응답 없는 대시보드 하나가 DB 연결을 모두 묶었다)
- 대시보드마다 전송 담당 태스크 1개가 대기열을 순서대로 보낸다. 한 번 보내는 데 SEND_TIMEOUT_SECONDS를 넘기거나
  대기열이 MAX_PENDING을 넘으면 그 대시보드 연결만 끊는다. 대시보드는 재연결한 뒤 전체를 다시 불러온다.
- 같은 PC의 갱신은 대기열에서 최신 1건으로 합치고(새 알림은 이어 붙임), 요약·연결 확인 신호도 최신 1건만 남긴다.
"""

import asyncio
import json
import logging
from collections import OrderedDict
from typing import Any, Hashable, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 대시보드 1곳에 메시지 1건을 보내는 데 이보다 오래 걸리면 응답 없는 연결로 보고 끊는다
SEND_TIMEOUT_SECONDS = 5.0
# 대시보드 1곳의 전송 대기열 상한 (합치기 후 기준: PC 수 + 요약 등). 넘으면 끊고 재연결·재조회로 회복
MAX_PENDING = 3000
# 끊을 때 종료 신호를 보내 보는 시간 (응답 없는 연결은 기다리지 않음)
CLOSE_TIMEOUT_SECONDS = 1.0
# 같은 PC 갱신을 합칠 때 이어 붙이는 새 알림 최대 건수
MAX_MERGED_ALERTS = 50

# 같은 대상의 이전 메시지를 최신 메시지로 합치는 종류
_AGENT_MESSAGES = ("agent_update", "agent_online", "agent_offline")
_LATEST_ONLY_MESSAGES = ("summary_update", "heartbeat")


def _coalesce_key(message_type: str, data: Any) -> Optional[str]:
    """대기열에서 합칠 메시지의 키 (PC 갱신은 PC별, 요약·신호는 종류별). 합치지 않는 메시지는 None."""
    if message_type in _AGENT_MESSAGES and isinstance(data, dict) and data.get("agent_id"):
        return f"agent:{data['agent_id']}"
    if message_type in _LATEST_ONLY_MESSAGES:
        return message_type
    return None


def _merge(previous: dict, latest: dict) -> dict:
    """같은 PC의 갱신 합치기: 필드는 최신 값이 우선(빠진 필드는 이전 값 유지), 새 알림은 이어 붙인다."""
    prev_data, new_data = previous["data"], latest["data"]
    if not (isinstance(prev_data, dict) and isinstance(new_data, dict)):
        return latest
    data = {**prev_data, **new_data}
    alerts = [*(prev_data.get("alerts") or []), *(new_data.get("alerts") or [])]
    if alerts:
        data["alerts"] = alerts[-MAX_MERGED_ALERTS:]
    return {"type": latest["type"], "data": data}


class _DashboardClient:
    """대시보드 1곳: 전송 대기열과 전송 담당 태스크."""

    def __init__(self, websocket: WebSocket, manager: "WSManager"):
        self.websocket = websocket
        self._manager = manager
        self._pending: "OrderedDict[Hashable, dict]" = OrderedDict()
        self._ready = asyncio.Event()
        self._seq = 0
        self.task = asyncio.create_task(self._run())

    def enqueue(self, message_type: str, data: Any) -> bool:
        """전송 대기열에 넣는다 (기다리지 않음). 대기열이 상한을 넘으면 False."""
        message = {"type": message_type, "data": data}
        key: Optional[Hashable] = _coalesce_key(message_type, data)
        if key is None:
            self._seq += 1
            key = ("seq", self._seq)
        elif key in self._pending:
            message = _merge(self._pending.pop(key), message)
        self._pending[key] = message
        self._ready.set()
        return len(self._pending) <= MAX_PENDING

    async def _run(self):
        try:
            while True:
                await self._ready.wait()
                self._ready.clear()
                while self._pending:
                    _, message = self._pending.popitem(last=False)
                    payload = json.dumps(message, default=str)
                    await asyncio.wait_for(self.websocket.send_text(payload), SEND_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 시간 초과·연결 끊김 → 이 대시보드만 정리 (다른 대시보드와 보고 처리에는 영향 없음)
            logger.warning(f"WS 대시보드 전송 실패로 연결 해제: {type(e).__name__}")
            self._manager.drop(self)


class WSManager:
    """대시보드 WebSocket 연결과 전송 대기열 관리자"""

    def __init__(self):
        self._clients: dict[WebSocket, _DashboardClient] = {}
        self._summary_requested = False

    async def connect(self, websocket: WebSocket):
        """신규 대시보드 연결 수락 (연결 확인 메시지도 대기열로 보낸다)."""
        await websocket.accept()
        client = _DashboardClient(websocket, self)
        self._clients[websocket] = client
        client.enqueue("connected", {"message": "대시보드 연결됨"})
        logger.info(f"WS 클라이언트 연결 (총 {len(self._clients)}개)")

    async def disconnect(self, websocket: WebSocket):
        """대시보드가 연결을 끊었을 때 정리."""
        client = self._clients.pop(websocket, None)
        if client is not None:
            client.task.cancel()
        logger.info(f"WS 클라이언트 해제 (남은 {len(self._clients)}개)")

    def publish(self, message_type: str, data: Any) -> None:
        """모든 대시보드의 전송 대기열에 넣고 바로 돌아온다 (네트워크를 기다리지 않음)."""
        for client in list(self._clients.values()):
            if not client.enqueue(message_type, data):
                logger.warning("WS 대시보드 전송 대기열 초과로 연결 해제")
                self.drop(client)

    def drop(self, client: _DashboardClient) -> None:
        """대시보드 연결을 목록에서 빼고 전송을 멈춘 뒤, 종료 신호는 기다리지 않고 보내 본다."""
        if self._clients.get(client.websocket) is client:
            del self._clients[client.websocket]
        if client.task is not asyncio.current_task():
            client.task.cancel()
        asyncio.create_task(self._close_quietly(client.websocket))

    @staticmethod
    async def _close_quietly(websocket: WebSocket):
        try:
            # 1013: 잠시 후 다시 시도 → 대시보드가 재연결해 전체를 다시 불러온다
            await asyncio.wait_for(websocket.close(code=1013), CLOSE_TIMEOUT_SECONDS)
        except Exception:
            pass

    def request_summary(self) -> None:
        """요약 통계 갱신 요청. 실제 계산·전송은 주기 태스크가 1초에 한 번만 한다 (보고마다 계산하지 않음)."""
        self._summary_requested = True

    def take_summary_request(self) -> bool:
        """요약 갱신 요청이 있었는지 확인하고 비운다."""
        requested, self._summary_requested = self._summary_requested, False
        return requested

    @property
    def connection_count(self) -> int:
        return len(self._clients)


# 앱 전체에서 공유하는 싱글턴 인스턴스
ws_manager = WSManager()
