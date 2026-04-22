"""
서버 통신 클라이언트 모듈

에이전트 상태 보고서를 모니터링 서버로 전송합니다.

통신 방식:
  - REST API (주기적 상태 보고)
  - WebSocket (선택적 실시간 스트리밍, 추후 확장)

오프라인 대응:
  - 서버 연결 실패 시 로컬 파일에 버퍼링
  - 재연결 성공 후 버퍼된 데이터 일괄 전송
"""

import json
import os
import sys
import threading
import time
from datetime import datetime
from typing import Optional

import requests

from agent.config import ServerConfig
from agent.models import AgentRegistration, StatusReport
from agent.utils.logger import get_logger

logger = get_logger("network.api_client")


class ApiClient:
    """
    모니터링 서버와 통신하는 REST API 클라이언트

    Usage:
        client = ApiClient(config.server)
        client.start()                          # 백그라운드 전송 스레드 시작
        client.enqueue(report)                  # 전송 큐에 추가
        client.stop()                           # 종료
    """

    def __init__(self, config: ServerConfig, agent_id: str):
        self._config = config
        self._agent_id = agent_id
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-Agent-ID": agent_id,
            "X-API-Key": config.api_key,
        })

        self._running = False
        self._send_thread: Optional[threading.Thread] = None
        self._registered = False

        # OTA 업데이트 콜백 (MonitoringAgent에서 설정)
        self._on_update_available = None

        # 오프라인 버퍼 디렉토리
        if getattr(sys, 'frozen', False):
            base_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.environ.get('APPDATA', '')), "DCU_MonitoringAgent")
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            
        self._buffer_dir = os.path.join(base_dir, "data", "offline_buffer")

    # ─── 공개 메서드 ──────────────────────────────────────────────

    def start(self):
        """백그라운드 전송 스레드를 시작합니다."""
        if not self._config.enabled:
            logger.info("서버 전송 비활성화 (config.server.enabled = false)")
            return

        self._running = True
        self._send_thread = threading.Thread(
            target=self._sender_loop,
            name="ApiSenderThread",
            daemon=True,
        )
        self._send_thread.start()
        logger.info(f"API 클라이언트 시작: {self._config.base_url}")

    def stop(self):
        """전송 스레드를 종료합니다."""
        self._running = False
        if self._send_thread:
            self._send_thread.join(timeout=5)
        logger.info("API 클라이언트 종료")

    def send_registration(self, registration: AgentRegistration) -> bool:
        """에이전트 등록 정보를 서버에 전송합니다."""
        return self._post("/api/agents/register", registration.dict())

    def send_report(self, report: StatusReport) -> bool:
        """상태 보고서를 서버에 전송합니다. 응답의 OTA 정보도 처리합니다."""
        import json
        data = json.loads(report.json())

        success, resp_data = self._post_with_response("/api/agents/report", data)

        # OTA 업데이트 응답 처리
        if success and resp_data and resp_data.get("update_available"):
            latest_version = resp_data.get("latest_version", "")
            latest_checksum = resp_data.get("latest_checksum", "")
            logger.info(f"서버로부터 OTA 업데이트 감지: v{latest_version}")
            if self._on_update_available:
                self._on_update_available(latest_version, latest_checksum)

        return success

    def set_update_callback(self, callback):
        """OTA 업데이트 감지 시 호출될 콜백을 설정합니다."""
        self._on_update_available = callback

    def send_offline_status(self) -> bool:
        """에이전트 종료 시 서버에 오프라인 상태임을 알립니다."""
        return self._post("/api/agents/offline", {"agent_id": self._agent_id})

    def check_connection(self) -> bool:
        """서버 연결 가능 여부를 확인합니다."""
        try:
            resp = self._session.get(
                f"{self._config.base_url}/api/health",
                timeout=3
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ─── 백그라운드 전송 루프 ─────────────────────────────────────

    def _sender_loop(self):
        """
        보고서 큐를 주기적으로 서버에 전송합니다.
        서버 미연결 시 로컬 파일에 버퍼링하고,
        재연결 성공 시 버퍼 데이터를 일괄 전송합니다.
        """
        retry_interval = self._config.retry_interval_seconds

        while self._running:
            try:
                # 서버 연결 확인 및 등록
                if not self._registered:
                    if self.check_connection():
                        logger.info("서버 연결 성공")
                        self._registered = True
                        # 오프라인 버퍼 전송
                        self._flush_offline_buffer()
                    else:
                        logger.debug(f"서버 미연결. {retry_interval}초 후 재시도...")
                        time.sleep(retry_interval)
                        continue

                time.sleep(retry_interval)

            except Exception as e:
                logger.error(f"전송 루프 오류: {e}")
                self._registered = False
                time.sleep(retry_interval)

    def _flush_offline_buffer(self):
        """오프라인 버퍼 파일들을 서버에 일괄 전송합니다."""
        if not os.path.exists(self._buffer_dir):
            return

        files = sorted(f for f in os.listdir(self._buffer_dir) if f.endswith(".json"))
        if not files:
            return

        logger.info(f"오프라인 버퍼 전송 시작: {len(files)}개 파일")
        sent = 0
        for filename in files:
            filepath = os.path.join(self._buffer_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if self._post("/api/agents/report", data):
                    os.remove(filepath)
                    sent += 1
                else:
                    break  # 전송 실패 시 중단

            except Exception as e:
                logger.error(f"버퍼 파일 처리 오류 ({filename}): {e}")

        logger.info(f"오프라인 버퍼 전송 완료: {sent}/{len(files)}개")

    def buffer_report_offline(self, report: StatusReport) -> bool:
        """
        서버 전송 실패 시 보고서를 로컬 파일로 저장합니다.
        """
        try:
            os.makedirs(self._buffer_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filepath = os.path.join(self._buffer_dir, f"report_{timestamp}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report.dict(), f, ensure_ascii=False, default=str)

            # 버퍼 크기 제한 (오래된 파일 삭제)
            self._trim_buffer()
            return True
        except Exception as e:
            logger.error(f"오프라인 버퍼 저장 실패: {e}")
            return False

    def _trim_buffer(self):
        """버퍼 디렉토리의 파일 수를 설정 한도로 제한합니다."""
        max_size = self._config.offline_buffer_size
        if not os.path.exists(self._buffer_dir):
            return
        files = sorted(
            (f for f in os.listdir(self._buffer_dir) if f.endswith(".json")),
        )
        while len(files) > max_size:
            oldest = os.path.join(self._buffer_dir, files.pop(0))
            try:
                os.remove(oldest)
                logger.debug(f"버퍼 초과로 삭제: {oldest}")
            except Exception:
                pass

    # ─── HTTP 통신 ────────────────────────────────────────────────

    def _post(self, endpoint: str, data: dict) -> bool:
        """
        JSON 데이터를 POST 요청으로 서버에 전송합니다.

        Returns:
            True: 전송 성공, False: 실패
        """
        success, _ = self._post_with_response(endpoint, data)
        return success

    def _post_with_response(self, endpoint: str, data: dict) -> tuple:
        """
        JSON 데이터를 POST 요청으로 전송하고, 응답 JSON도 함께 반환합니다.

        Returns:
            (success: bool, response_data: dict or None)
        """
        url = f"{self._config.base_url}{endpoint}"
        try:
            resp = self._session.post(url, json=data, timeout=5)
            if resp.status_code in (200, 201):
                logger.debug(f"POST 성공: {endpoint} ({resp.status_code})")
                try:
                    return True, resp.json()
                except Exception:
                    return True, None
            else:
                logger.warning(f"POST 실패: {endpoint} → HTTP {resp.status_code}")
                return False, None
        except requests.ConnectionError:
            logger.debug(f"서버 연결 불가: {url}")
            self._registered = False
            return False, None
        except requests.Timeout:
            logger.warning(f"서버 응답 타임아웃: {url}")
            return False, None
        except Exception as e:
            logger.error(f"POST 오류 ({endpoint}): {e}")
            return False, None
