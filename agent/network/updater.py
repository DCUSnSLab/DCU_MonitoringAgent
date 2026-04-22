"""
OTA 업데이트 클라이언트 모듈

서버에서 새 버전 exe를 다운로드하고,
현재 실행 중인 exe를 교체한 뒤 재시작합니다.

Windows에서 실행 중인 exe는 덮어쓸 수 없으므로
rename 전략을 사용합니다:
  1. 새 exe를 .update 파일로 다운로드
  2. sha256 checksum 검증
  3. 현재 exe → .old 로 rename  (실행 중이어도 가능)
  4. .update → 원래 exe 이름으로 rename
  5. 새 exe를 subprocess로 실행 후 현재 프로세스 종료
"""

import hashlib
import os
import subprocess
import sys
import time

import requests

from agent.utils.logger import get_logger

logger = get_logger("network.updater")


class OTAUpdater:
    """OTA 자동 업데이트 관리자"""

    def __init__(self, server_base_url: str, api_key: str, current_version: str):
        self._base_url = server_base_url
        self._api_key = api_key
        self._current_version = current_version
        self._updating = False

        # 경로 설정
        if getattr(sys, 'frozen', False):
            self._exe_path = sys.executable
        else:
            self._exe_path = None  # 개발 환경에서는 업데이트 불가

        self._session = requests.Session()
        self._session.headers.update({
            "X-API-Key": api_key,
            "X-Agent-ID": "ota-updater",
        })

    @property
    def is_updating(self) -> bool:
        return self._updating

    def check_and_update(self, latest_version: str, latest_checksum: str) -> bool:
        """
        서버 응답에서 업데이트가 필요하다고 판단되면 업데이트를 실행합니다.

        Args:
            latest_version: 서버의 최신 버전 문자열
            latest_checksum: 서버의 최신 파일 sha256 checksum

        Returns:
            True: 업데이트 시작됨 (곧 프로세스 재시작)
            False: 업데이트 불필요 또는 실패
        """
        if not self._exe_path:
            logger.debug("개발 환경에서는 OTA 업데이트를 수행하지 않습니다")
            return False

        if self._updating:
            logger.debug("이미 업데이트 진행 중")
            return False

        if latest_version == self._current_version:
            return False

        logger.info(f"OTA 업데이트 감지: {self._current_version} → {latest_version}")
        self._updating = True

        try:
            # 1. 다운로드
            update_path = self._exe_path + ".update"
            if not self._download(latest_version, update_path):
                return False

            # 2. checksum 검증
            if not self._verify_checksum(update_path, latest_checksum):
                self._cleanup(update_path)
                return False

            # 3. exe swap & 재시작
            self._swap_and_restart(update_path)
            return True  # 여기에 도달하면 안 됨 (재시작됨)

        except Exception as e:
            logger.error(f"OTA 업데이트 실패: {e}")
            self._updating = False
            return False

    def _download(self, version: str, dest_path: str) -> bool:
        """서버에서 새 버전 exe를 다운로드합니다."""
        url = f"{self._base_url}/api/ota/download/{version}"
        logger.info(f"새 버전 다운로드 중: {url}")

        try:
            resp = self._session.get(url, stream=True, timeout=120)
            if resp.status_code != 200:
                logger.error(f"다운로드 실패: HTTP {resp.status_code}")
                return False

            total = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)

            logger.info(f"다운로드 완료: {total} bytes → {dest_path}")
            return True

        except Exception as e:
            logger.error(f"다운로드 오류: {e}")
            self._cleanup(dest_path)
            return False

    def _verify_checksum(self, filepath: str, expected: str) -> bool:
        """다운로드된 파일의 sha256 checksum을 검증합니다."""
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)

        actual = hasher.hexdigest()
        if actual != expected:
            logger.error(f"Checksum 불일치! expected={expected[:16]}... actual={actual[:16]}...")
            return False

        logger.info(f"Checksum 검증 성공: {actual[:16]}...")
        return True

    def _swap_and_restart(self, update_path: str):
        """현재 exe를 새 파일로 교체하고 재시작합니다."""
        exe_path = self._exe_path
        old_path = exe_path + ".old"

        logger.info("exe 교체 시작...")

        # 이전 .old 파일이 남아있으면 삭제
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

        # 현재 exe → .old (실행 중이어도 rename은 가능)
        try:
            os.rename(exe_path, old_path)
            logger.info(f"현재 exe → .old rename 완료")
        except OSError as e:
            logger.error(f"현재 exe rename 실패: {e}")
            return

        # .update → 원래 exe 이름
        try:
            os.rename(update_path, exe_path)
            logger.info(f".update → exe rename 완료")
        except OSError as e:
            # 롤백: .old를 다시 원래 이름으로
            logger.error(f".update rename 실패, 롤백 중: {e}")
            os.rename(old_path, exe_path)
            return

        # 새 exe로 재시작
        logger.info(f"새 프로세스 시작: {exe_path}")
        try:
            subprocess.Popen(
                [exe_path],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as e:
            logger.error(f"새 프로세스 시작 실패: {e}")
            # 롤백
            os.rename(exe_path, update_path)
            os.rename(old_path, exe_path)
            return

        logger.info("업데이트 완료. 현재 프로세스를 종료합니다.")
        # 현재 프로세스 종료 - os._exit()으로 즉시 종료
        os._exit(0)

    @staticmethod
    def _cleanup(filepath: str):
        """임시 파일을 삭제합니다."""
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass

    @staticmethod
    def cleanup_old_version():
        """
        시작 시 이전 버전(.old) 파일을 정리합니다.
        에이전트 시작 직후 호출되어야 합니다.
        """
        if not getattr(sys, 'frozen', False):
            return

        old_path = sys.executable + ".old"
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
                logger.info(f"이전 버전 파일 정리 완료: {old_path}")
            except PermissionError:
                # 아직 이전 프로세스가 사용 중일 수 있으므로 잠시 대기 후 재시도
                time.sleep(2)
                try:
                    os.remove(old_path)
                    logger.info(f"이전 버전 파일 정리 완료 (재시도): {old_path}")
                except Exception as e:
                    logger.warning(f"이전 버전 파일 삭제 실패 (다음 시작 시 재시도): {e}")
            except Exception as e:
                logger.warning(f"이전 버전 파일 삭제 실패: {e}")
