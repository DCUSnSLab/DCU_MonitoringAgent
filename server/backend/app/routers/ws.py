"""
WebSocket 라우터

WS /ws/dashboard  – 관리자 브라우저 실시간 연결
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    """관리자 대시보드 실시간 업데이트 WebSocket"""
    await ws_manager.connect(websocket)
    try:
        # 초기 연결 확인 메시지 전송
        await websocket.send_json({"type": "connected", "data": {"message": "대시보드 연결됨"}})
        # 연결 유지 (클라이언트가 끊을 때까지 대기)
        while True:
            # ping/pong 유지 (클라이언트 메시지 수신 대기)
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WS 클라이언트 정상 해제")
    except Exception as e:
        logger.warning(f"WS 오류: {e}")
    finally:
        await ws_manager.disconnect(websocket)
