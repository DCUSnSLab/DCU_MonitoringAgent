"""
프로세스 모니터링 모듈

psutil과 WMI를 이용해 실행 중인 프로세스 목록을 수집하고 분석합니다.
- 전체 프로세스 목록 수집
- 허용/차단 목록 기반 필터링
- 프로세스 변경(신규 실행/종료) 감지
- CPU/메모리 사용량 추적
"""

import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import psutil

from agent.config import ProcessConfig
from agent.models import Alert, ProcessInfo
from agent.utils.logger import get_logger

logger = get_logger("monitors.process")


class ProcessMonitor:
    """
    프로세스 모니터링 클래스

    주기적으로 실행 중인 프로세스를 수집하고
    허용/차단 목록에 따라 알림을 생성합니다.
    """

    # 모니터링에서 항상 제외할 시스템 프로세스
    SYSTEM_PROCESS_EXCLUSIONS: Set[str] = {
        "system", "registry", "smss.exe", "csrss.exe", "wininit.exe",
        "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
        "conhost.exe", "fontdrvhost.exe", "winlogon.exe",
        "spoolsv.exe", "taskhostw.exe", "sihost.exe", "ctfmon.exe",
    }

    def __init__(self, config: ProcessConfig):
        self._config = config
        # 이전 수집 결과 (PID → 프로세스 이름): 변경 감지용
        self._prev_pids: Dict[int, str] = {}
        # WMI 인스턴스 (상세 정보 수집용, 선택적)
        self._wmi = None
        self._init_wmi()

    def _init_wmi(self):
        """WMI 인터페이스 초기화 (현재는 성능 문제로 사용 안함)"""
        self._wmi = None


    # ─── 공개 메서드 ──────────────────────────────────────────────

    def collect(self) -> Tuple[List[ProcessInfo], List[Alert]]:
        """
        현재 실행 중인 프로세스 목록을 수집합니다.

        Returns:
            (프로세스 목록, 알림 목록) 튜플
        """
        processes: List[ProcessInfo] = []
        alerts: List[Alert] = []
        current_pids: Dict[int, str] = {}

        # WMI를 매 주기마다 호출하면 속도가 매우 느림(수 초 소요). psutil만 사용하도록 빈 딕셔너리 처리.
        wmi_cmdlines: Dict[int, str] = {}


        # status 속성이 Windows 시스템 전역 순회 시 ~2.5초 지연을 유발합니다.
        # cpu_percent 도 ~1초를 소모하므로 프로세스 감지 목적상 생략합니다.
        for proc in psutil.process_iter([
            "pid", "name", "exe", "memory_info", 
            "create_time", "username", "cmdline"
        ]):
            try:
                info = proc.info
                name = (info.get("name") or "").lower()
                pid = info.get("pid", 0)

                # 시스템 프로세스 제외
                if name in self.SYSTEM_PROCESS_EXCLUSIONS:
                    continue

                # PID 기록 (변경 감지용)
                current_pids[pid] = name

                # 메모리 계산
                mem_info = info.get("memory_info")
                memory_mb = (mem_info.rss / (1024 * 1024)) if mem_info else 0.0

                # 생성 시간 변환
                create_ts = info.get("create_time")
                create_time = (
                    datetime.fromtimestamp(create_ts) if create_ts else None
                )

                # 커맨드라인 (WMI 우선, psutil 폴백)
                cmdline = wmi_cmdlines.get(pid)
                if not cmdline:
                    raw_cmd = info.get("cmdline") or []
                    cmdline = " ".join(raw_cmd) if raw_cmd else None

                proc_info = ProcessInfo(
                    pid=pid,
                    name=info.get("name") or "",
                    exe_path=info.get("exe"),
                    status="running",  # status 조회 생략으로 기본값
                    cpu_percent=0.0,   # 성능 향상을 위해 조회 생략
                    memory_mb=round(memory_mb, 2),
                    create_time=create_time,
                    username=self._safe_username(info.get("username")),
                    cmdline=cmdline,
                )

                # 필터링 모드에 따른 처리
                alert = self._check_process_alert(proc_info)
                if alert:
                    alerts.append(alert)

                # 모드별 수집 여부 결정
                if self._should_include(name):
                    processes.append(proc_info)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as e:
                logger.debug(f"프로세스 정보 수집 오류 (pid={proc.pid}): {e}")
                continue

        # 변경 감지 알림 생성
        change_alerts = self._detect_changes(current_pids)
        alerts.extend(change_alerts)

        # 상태 업데이트
        self._prev_pids = current_pids

        logger.debug(f"프로세스 수집 완료: {len(processes)}개, 알림: {len(alerts)}개")
        return processes, alerts

    def get_system_info(self):
        """시스템 리소스 정보를 수집합니다."""
        from agent.models import SystemInfo

        mem = psutil.virtual_memory()
        boot_time = psutil.boot_time()
        uptime = int(time.time() - boot_time)

        return SystemInfo(
            cpu_percent=round(psutil.cpu_percent(interval=None), 2),
            memory_percent=round(mem.percent, 2),
            memory_total_gb=round(mem.total / (1024 ** 3), 2),
            memory_used_gb=round(mem.used / (1024 ** 3), 2),
            uptime_seconds=uptime,
        )

    # ─── 내부 메서드 ──────────────────────────────────────────────

    def _should_include(self, proc_name: str) -> bool:
        """
        설정 모드에 따라 프로세스를 수집 목록에 포함할지 결정합니다.

        - all: 모든 프로세스 포함
        - whitelist: 허용 목록에 있는 것만 포함
        - blacklist: 모든 프로세스 포함 (차단 목록은 알림만)
        """
        mode = self._config.mode.lower()
        if mode == "whitelist":
            return proc_name in [p.lower() for p in self._config.whitelist]
        return True  # "all" 또는 "blacklist" 모드

    def _check_process_alert(self, proc: ProcessInfo) -> Optional[Alert]:
        """
        프로세스 실행 여부를 검사하고 알림을 생성합니다.

        - whitelist 모드: 허용 목록에 없는 프로세스 경고
        - blacklist 모드: 차단 목록에 있는 프로세스 경고
        - all 모드: 알림 없음
        """
        mode = self._config.mode.lower()
        name_lower = proc.name.lower()

        if mode == "whitelist":
            whitelist_lower = [p.lower() for p in self._config.whitelist]
            if name_lower not in whitelist_lower:
                return Alert(
                    level="warning",
                    code="UNAUTHORIZED_PROCESS",
                    message=f"허용되지 않은 프로그램 실행: {proc.name}",
                    process_name=proc.name,
                    detected_at=datetime.now(),
                )

        elif mode == "blacklist":
            blacklist_lower = [p.lower() for p in self._config.blacklist]
            if name_lower in blacklist_lower:
                return Alert(
                    level="warning",
                    code="BLOCKED_PROCESS",
                    message=f"차단된 프로그램 실행: {proc.name}",
                    process_name=proc.name,
                    detected_at=datetime.now(),
                )

        return None

    def _detect_changes(self, current_pids: Dict[int, str]) -> List[Alert]:
        """이전 수집과 비교하여 새로 실행되거나 종료된 프로세스를 감지합니다."""
        alerts: List[Alert] = []

        # 새로 실행된 프로세스
        new_pids = set(current_pids.keys()) - set(self._prev_pids.keys())
        for pid in new_pids:
            name = current_pids[pid]
            logger.info(f"[신규 프로세스] {name} (PID: {pid})")

        # 종료된 프로세스
        terminated_pids = set(self._prev_pids.keys()) - set(current_pids.keys())
        for pid in terminated_pids:
            name = self._prev_pids[pid]
            logger.info(f"[종료 프로세스] {name} (PID: {pid})")

        return alerts

    def _get_wmi_cmdlines(self) -> Dict[int, str]:
        """WMI로 프로세스 커맨드라인 정보를 수집합니다."""
        if not self._wmi:
            return {}
        try:
            result = {}
            for proc in self._wmi.Win32_Process(["ProcessId", "CommandLine"]):
                if proc.CommandLine:
                    result[proc.ProcessId] = proc.CommandLine
            return result
        except Exception as e:
            logger.debug(f"WMI 커맨드라인 수집 실패: {e}")
            return {}

    @staticmethod
    def _safe_username(raw: Optional[str]) -> Optional[str]:
        """도메인 접두사를 제거하여 사용자 이름을 정리합니다."""
        if not raw:
            return None
        # "DOMAIN\\username" → "username"
        return raw.split("\\")[-1] if "\\" in raw else raw
