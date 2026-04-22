"""
WebSocket 연결 매니저

관리자 브라우저와의 WebSocket 연결을 관리하고
에이전트 보고 수신 시 모든 연결된 클라이언트에 브로드캐스트합니다.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSManager:
    """WebSocket 연결 풀 관리자"""

    def __init__(self):
        # 연결된 관리자 브라우저 소켓 목록
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        """신규 클라이언트 연결 수락"""
        await websocket.accept()
        async with self._lock:
            self._connections.append(websocket)
        logger.info(f"WS 클라이언트 연결 (총 {len(self._connections)}개)")

    async def disconnect(self, websocket: WebSocket):
        """클라이언트 연결 해제"""
        async with self._lock:
            if websocket in self._connections:
                self._connections.remove(websocket)
        logger.info(f"WS 클라이언트 해제 (남은 {len(self._connections)}개)")

    async def broadcast(self, message_type: str, data: Any):
        """모든 연결된 클라이언트에 메시지 전송"""
        if not self._connections:
            return

        payload = json.dumps({"type": message_type, "data": data}, default=str)
        disconnected = []

        async with self._lock:
            connections = list(self._connections)

        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(ws)

        # 끊어진 연결 정리
        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    if ws in self._connections:
                        self._connections.remove(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# 앱 전체에서 공유하는 싱글턴 인스턴스
ws_manager = WSManager()
