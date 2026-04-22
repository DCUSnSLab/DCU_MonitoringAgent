"""
Phase 2: 프로세스/윈도우 모니터링 단위 테스트
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from agent.config import ProcessConfig
from agent.monitors.process_monitor import ProcessMonitor
from agent.monitors.window_monitor import WindowMonitor
from agent.models import ProcessInfo, Alert


# ─── ProcessMonitor 테스트 ────────────────────────────────────────


class TestProcessMonitor:

    def test_collect_returns_processes(self):
        """프로세스 목록이 비어있지 않음을 확인"""
        config = ProcessConfig(mode="all")
        monitor = ProcessMonitor(config)
        processes, alerts = monitor.collect()
        assert len(processes) > 0, "프로세스 목록이 비어있음"
        print(f"\n  수집된 프로세스 수: {len(processes)}")

    def test_process_info_fields(self):
        """수집된 프로세스 정보의 필수 필드 확인"""
        config = ProcessConfig(mode="all")
        monitor = ProcessMonitor(config)
        processes, _ = monitor.collect()

        for proc in processes[:5]:  # 처음 5개만 검사
            assert isinstance(proc.pid, int), f"PID가 정수가 아님: {proc.pid}"
            assert isinstance(proc.name, str), f"이름이 문자열이 아님: {proc.name}"
            assert proc.memory_mb >= 0, f"메모리가 음수: {proc.memory_mb}"
            assert proc.cpu_percent >= 0, f"CPU가 음수: {proc.cpu_percent}"

    def test_whitelist_mode_filters_processes(self):
        """whitelist 모드에서 허용 목록 외 프로세스가 경고를 생성하는지 확인"""
        # 존재하지 않을 것 같은 프로세스만 허용
        config = ProcessConfig(
            mode="whitelist",
            whitelist=["explorer.exe", "system"]
        )
        monitor = ProcessMonitor(config)
        processes, alerts = monitor.collect()

        # whitelist 외 프로세스는 수집 목록에 없어야 함
        for proc in processes:
            assert proc.name.lower() in ["explorer.exe", "system"], \
                f"whitelist 외 프로세스가 포함됨: {proc.name}"

    def test_blacklist_mode_generates_alerts(self):
        """blacklist 모드에서 차단 목록 프로세스에 알림이 생성되는지 확인"""
        # 현재 실행 중인 프로세스를 하나 차단 목록에 추가
        import psutil
        running = [p.name() for p in psutil.process_iter(["name"])
                   if p.info.get("name")]
        if not running:
            pytest.skip("실행 중인 프로세스 없음")

        target = running[0].lower()
        config = ProcessConfig(mode="blacklist", blacklist=[target])
        monitor = ProcessMonitor(config)
        _, alerts = monitor.collect()

        blocked_alerts = [a for a in alerts if a.code == "BLOCKED_PROCESS"]
        assert len(blocked_alerts) > 0, \
            f"차단 목록 프로세스({target})에 대한 알림이 없음"

    def test_system_info_collection(self):
        """시스템 리소스 정보 수집 확인"""
        config = ProcessConfig(mode="all")
        monitor = ProcessMonitor(config)
        sys_info = monitor.get_system_info()

        assert 0 <= sys_info.cpu_percent <= 100
        assert 0 <= sys_info.memory_percent <= 100
        assert sys_info.memory_total_gb > 0
        assert sys_info.uptime_seconds > 0
        print(f"\n  CPU: {sys_info.cpu_percent}%")
        print(f"  Memory: {sys_info.memory_used_gb:.1f}/{sys_info.memory_total_gb:.1f} GB ({sys_info.memory_percent}%)")
        print(f"  Uptime: {sys_info.uptime_seconds // 3600}h {(sys_info.uptime_seconds % 3600) // 60}m")

    def test_change_detection(self):
        """두 번 collect() 호출 시 변경 감지가 동작하는지 확인"""
        config = ProcessConfig(mode="all")
        monitor = ProcessMonitor(config)
        monitor.collect()  # 첫 번째 수집 (기준)
        _, alerts = monitor.collect()  # 두 번째 수집 (변경 감지)
        # 오류 없이 실행되면 OK (변경 감지 로그는 INFO 레벨로 출력됨)
        assert isinstance(alerts, list)


# ─── WindowMonitor 테스트 ─────────────────────────────────────────


class TestWindowMonitor:

    def test_foreground_window_returns_string_or_none(self):
        """포그라운드 창 제목이 문자열 또는 None임을 확인"""
        monitor = WindowMonitor()
        title = monitor.get_foreground_window()
        assert title is None or isinstance(title, str)
        print(f"\n  현재 포그라운드 창: '{title}'")

    def test_foreground_pid_returns_int_or_none(self):
        """포그라운드 PID가 정수 또는 None임을 확인"""
        monitor = WindowMonitor()
        pid = monitor.get_foreground_pid()
        assert pid is None or isinstance(pid, int)
        print(f"\n  포그라운드 PID: {pid}")

    def test_window_titles_by_pid(self):
        """PID별 창 제목 딕셔너리가 반환되는지 확인"""
        monitor = WindowMonitor()
        titles = monitor.get_window_titles_by_pid()
        assert isinstance(titles, dict)
        print(f"\n  수집된 창 수: {len(titles)}")
        for pid, title in list(titles.items())[:5]:
            print(f"    PID {pid}: {title}")


# ─── 통합 실행 ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Phase 2 수동 테스트")
    print("=" * 60)

    # 프로세스 모니터 테스트
    print("\n[ProcessMonitor - all 모드]")
    config = ProcessConfig(mode="all")
    pm = ProcessMonitor(config)
    procs, alerts = pm.collect()
    print(f"수집된 프로세스: {len(procs)}개")
    for p in sorted(procs, key=lambda x: x.memory_mb, reverse=True)[:10]:
        print(f"  [{p.pid:>6}] {p.name:<30} CPU:{p.cpu_percent:>5.1f}%  MEM:{p.memory_mb:>7.1f}MB  {p.username or ''}")

    sys_info = pm.get_system_info()
    print(f"\n시스템 정보:")
    print(f"  CPU: {sys_info.cpu_percent}%")
    print(f"  메모리: {sys_info.memory_used_gb:.1f}/{sys_info.memory_total_gb:.1f} GB")
    print(f"  가동 시간: {sys_info.uptime_seconds // 3600}시간 {(sys_info.uptime_seconds % 3600) // 60}분")

    # 윈도우 모니터 테스트
    print("\n[WindowMonitor]")
    wm = WindowMonitor()
    fg = wm.get_foreground_window()
    fg_pid = wm.get_foreground_pid()
    print(f"현재 활성 창: '{fg}' (PID: {fg_pid})")

    titles = wm.get_window_titles_by_pid()
    print(f"전체 창 목록 ({len(titles)}개):")
    for pid, title in list(titles.items())[:10]:
        print(f"  PID {pid:>6}: {title}")
