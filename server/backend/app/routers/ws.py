"""
WebSocket 라우터

WS /ws/dashboard  – 관리자 브라우저 실시간 연결

로그인 토큰은 하위 프로토콜로 받는다: new WebSocket(url, ['bearer', <토큰>]).
(브라우저 WebSocket 은 헤더를 붙일 수 없고, 주소에 넣으면 서버 접속 로그에 토큰이 남는다)
토큰이 없거나 틀리면 연결을 받은 뒤 4401 로 닫는다 — 받기 전에 거부하면 브라우저는 이유를 알 수 없어
재연결만 반복하므로, 4401 을 받은 대시보드는 재연결하지 않고 로그인 화면으로 간다.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services import auth_service
from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# 로그인이 필요함을 알리는 종료 코드 (4000~4999 는 애플리케이션 정의 코드)
WS_CLOSE_UNAUTHORIZED = 4401


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    """관리자 대시보드 실시간 업데이트 WebSocket"""
    subprotocols = websocket.scope.get("subprotocols") or []
    # 브라우저는 요청한 하위 프로토콜 중 하나를 서버가 돌려줘야 연결을 받아들인다
    subprotocol = auth_service.WS_SUBPROTOCOL if auth_service.WS_SUBPROTOCOL in subprotocols else None
    claims = auth_service.verify_token_claims(auth_service.token_from_subprotocols(subprotocols) or "")
    if claims is None:
        await websocket.accept(subprotocol=subprotocol)
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    _, expires_at = claims
    # 연결 확인 메시지는 이 대시보드의 전송 대기열로 보낸다 (ws_manager.connect)
    await ws_manager.connect(websocket, subprotocol=subprotocol)
    try:
        # 연결 유지 (클라이언트가 끊을 때까지 대기). 로그인이 만료되면 4401로 닫아 로그인 화면으로 보낸다
        await asyncio.wait_for(_receive_until_closed(websocket), timeout=max(0.0, expires_at - time.time()))
    except asyncio.TimeoutError:
        logger.info("WS 로그인 만료로 연결 종료")
        try:
            await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        except Exception:
            pass
    except WebSocketDisconnect:
        logger.info("WS 클라이언트 정상 해제")
    except Exception as e:
        logger.warning(f"WS 오류: {e}")
    finally:
        await ws_manager.disconnect(websocket)


async def _receive_until_closed(websocket: WebSocket):
    """클라이언트가 끊을 때까지 수신 대기 (ping/pong 유지)"""
    while True:
        await websocket.receive_text()
