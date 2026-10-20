"""
에이전트 상태 1분 집계 (GEN-1683)

예전에는 보고(약 1초 간격)마다 status_reports 에 1행씩 INSERT 했다. 읽는 곳이 없는 테이블에
PC당 초당 1행이 쌓여 인덱스 갱신·vacuum·보관 정리 부담만 컸다.
지금은 보고를 서버 메모리에서 에이전트 × 1분 단위로 모았다가, 주기 태스크(main._minute_flusher)가
한 번의 다중 행 INSERT 로 agent_status_minutes 에 저장한다.

시각 기준
- 실시간 보고: 서버 수신 시각(UTC)으로 분을 정한다.
- 오프라인 버퍼 재전송(현재 탐지 목록이 없는 보고): 보고에 담긴 에이전트 시각을 쓴다.
  에이전트는 시간대 없는 PC 로컬 시각을 보내므로, 마지막 실시간 보고에서 잰 '서버 UTC - 에이전트 시각' 차이를
  더해 UTC 로 맞춘다 (시간대 차이와 PC 시계 오차를 함께 보정). 차이를 아직 모르면 수신 시각을 쓴다.

저장 시점 (서버 수신 시각 기준, PC 시계와 무관)
- 마지막 샘플을 받은 지 IDLE_SECONDS 가 지났거나,
- 그 에이전트가 더 나중 분으로 넘어갔고 마지막 샘플 후 SETTLE_SECONDS 가 지났을 때.
같은 분 행이 이미 저장돼 있으면(재전송이 늦게 도착) 합계·샘플 수를 더하고 최대값은 큰 쪽을 남긴다.

메모리 보관이라 서버가 비정상 종료되면 저장 전 최대 1분치가 사라진다 (정상 종료 시에는 모두 저장).
DANGER 유지·현재 탐지 목록과 같은 단일 레플리카 전제이며, 다중 워커 전환 시 Redis 로 옮긴다.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentStatusMinute
from app.schemas import StatusReportRequest

logger = logging.getLogger(__name__)

# 마지막 샘플 후 이 시간(초) 동안 새 샘플이 없으면 그 분을 저장
IDLE_SECONDS = 10.0
# 에이전트가 다음 분으로 넘어간 뒤, 이전 분을 저장하기 전에 기다리는 시간(초)
SETTLE_SECONDS = 2.0
# DB 저장이 계속 실패할 때 메모리에 붙잡아 두는 최대 버킷 수 (넘으면 오래된 것부터 버림)
MAX_PENDING_BUCKETS = 50_000
# foreground_window 컬럼 길이 (넘는 값 하나 때문에 한 번에 저장하는 전체 행이 실패하지 않도록 자른다)
WINDOW_TITLE_MAX = 512

_STATES = ("safe", "warning", "danger")


def floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


@dataclass
class MinuteBucket:
    """에이전트 1명 × 1분 누적값"""
    agent_id: str
    minute: datetime
    samples: int = 0
    cpu_sum: float = 0.0
    cpu_max: float = 0.0
    memory_sum: float = 0.0
    memory_max: float = 0.0
    process_count_sum: int = 0
    process_count_max: int = 0
    chrome_tab_count_sum: int = 0
    chrome_tab_count_max: int = 0
    alert_count: int = 0
    state_samples: dict[str, int] = field(default_factory=lambda: {s: 0 for s in _STATES})
    last_state: Optional[str] = None
    foreground_window: Optional[str] = None
    last_sample_at: Optional[datetime] = None
    last_received: float = 0.0  # 서버 단조 시계 (저장 시점 판단용)

    def add(self, req: StatusReportRequest, sample_at: datetime, received: float) -> None:
        cpu = float(req.system_info.cpu_percent)
        memory = float(req.system_info.memory_percent)
        processes = len(req.processes)
        tabs = int(req.browser_details.chrome.tab_count)

        self.samples += 1
        self.cpu_sum += cpu
        self.cpu_max = max(self.cpu_max, cpu)
        self.memory_sum += memory
        self.memory_max = max(self.memory_max, memory)
        self.process_count_sum += processes
        self.process_count_max = max(self.process_count_max, processes)
        self.chrome_tab_count_sum += tabs
        self.chrome_tab_count_max = max(self.chrome_tab_count_max, tabs)
        self.alert_count += len(req.alerts)
        if req.user_state in self.state_samples:
            self.state_samples[req.user_state] += 1
        # 재전송 샘플이 순서 없이 섞여도 '마지막 값'은 가장 늦은 시각의 샘플로 정한다
        if self.last_sample_at is None or sample_at >= self.last_sample_at:
            self.last_sample_at = sample_at
            self.last_state = req.user_state
            window = req.foreground_window
            self.foreground_window = window[:WINDOW_TITLE_MAX] if window else None
        self.last_received = max(self.last_received, received)

    def merge(self, other: "MinuteBucket") -> None:
        """같은 에이전트·같은 분의 다른 누적값을 더한다 (저장 실패 후 되돌릴 때)."""
        self.samples += other.samples
        self.cpu_sum += other.cpu_sum
        self.cpu_max = max(self.cpu_max, other.cpu_max)
        self.memory_sum += other.memory_sum
        self.memory_max = max(self.memory_max, other.memory_max)
        self.process_count_sum += other.process_count_sum
        self.process_count_max = max(self.process_count_max, other.process_count_max)
        self.chrome_tab_count_sum += other.chrome_tab_count_sum
        self.chrome_tab_count_max = max(self.chrome_tab_count_max, other.chrome_tab_count_max)
        self.alert_count += other.alert_count
        for s in _STATES:
            self.state_samples[s] += other.state_samples[s]
        if other.last_sample_at is not None and (
            self.last_sample_at is None or other.last_sample_at >= self.last_sample_at
        ):
            self.last_sample_at = other.last_sample_at
            self.last_state = other.last_state
            self.foreground_window = other.foreground_window
        self.last_received = max(self.last_received, other.last_received)

    def to_row(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "minute": self.minute,
            "samples": self.samples,
            "cpu_sum": self.cpu_sum,
            "cpu_max": self.cpu_max,
            "memory_sum": self.memory_sum,
            "memory_max": self.memory_max,
            "process_count_sum": self.process_count_sum,
            "process_count_max": self.process_count_max,
            "chrome_tab_count_sum": self.chrome_tab_count_sum,
            "chrome_tab_count_max": self.chrome_tab_count_max,
            "alert_count": self.alert_count,
            "safe_samples": self.state_samples["safe"],
            "warning_samples": self.state_samples["warning"],
            "danger_samples": self.state_samples["danger"],
            "last_state": self.last_state,
            "foreground_window": self.foreground_window,
            "last_sample_at": self.last_sample_at,
        }


class ReportStats:
    """주기 요약 로그용 보고 처리 통계 (보고마다 로그를 남기는 대신 1분에 1줄)"""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.reports = 0
        self.alerts = 0
        self.agents: set[str] = set()
        self.elapsed_ms_total = 0.0
        self.elapsed_ms_max = 0.0

    def record(self, agent_id: str, alerts: int, elapsed_ms: float) -> None:
        self.reports += 1
        self.alerts += alerts
        self.agents.add(agent_id)
        self.elapsed_ms_total += elapsed_ms
        self.elapsed_ms_max = max(self.elapsed_ms_max, elapsed_ms)

    def take_summary(self) -> Optional[str]:
        """지난 요약 이후 통계 한 줄 (보고가 없었으면 None) 을 돌려주고 비운다."""
        if not self.reports:
            return None
        avg = self.elapsed_ms_total / self.reports
        line = (
            f"보고 {self.reports}건 · PC {len(self.agents)}대 · 새 알림 {self.alerts}건 · "
            f"처리 평균 {avg:.1f} ms / 최대 {self.elapsed_ms_max:.1f} ms"
        )
        self.reset()
        return line


class MinuteAggregator:
    """에이전트 × 1분 버킷 관리자 (메모리 보관)"""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._buckets: dict[tuple[str, datetime], MinuteBucket] = {}
        self._latest_minute: dict[str, datetime] = {}  # 에이전트별 가장 늦은 분 (다음 분으로 넘어갔는지 판단)
        self._clock_offset: dict[str, timedelta] = {}  # 에이전트별 '서버 UTC - 에이전트 시각'

    # ─── 수집 ─────────────────────────────────────────────────────

    def sample_time(self, req: StatusReportRequest, now: datetime) -> datetime:
        """이 보고를 넣을 시각 (UTC). 실시간 보고는 수신 시각, 재전송 보고는 보정한 에이전트 시각."""
        agent_ts = req.timestamp
        if agent_ts is not None and agent_ts.tzinfo is not None:
            agent_ts = agent_ts.astimezone(timezone.utc).replace(tzinfo=None)
        live = req.active_detections is not None
        if live:
            if agent_ts is not None:
                self._clock_offset[req.agent_id] = now.replace(tzinfo=None) - agent_ts
            return now
        offset = self._clock_offset.get(req.agent_id)
        if agent_ts is None or offset is None:
            return now
        return (agent_ts + offset).replace(tzinfo=timezone.utc)

    def add(self, req: StatusReportRequest, now: datetime) -> None:
        """보고 1건을 누적한다 (저장이 확정된 뒤 호출)."""
        sample_at = self.sample_time(req, now)
        minute = floor_minute(sample_at)
        key = (req.agent_id, minute)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = MinuteBucket(agent_id=req.agent_id, minute=minute)
        bucket.add(req, sample_at, self._clock())
        latest = self._latest_minute.get(req.agent_id)
        if latest is None or minute > latest:
            self._latest_minute[req.agent_id] = minute

    # ─── 저장 대상 꺼내기 ─────────────────────────────────────────

    def take_ready(self) -> list[MinuteBucket]:
        """저장할 때가 된 버킷을 꺼낸다."""
        now = self._clock()
        ready = []
        for key, bucket in list(self._buckets.items()):
            idle = now - bucket.last_received
            moved_on = bucket.minute < self._latest_minute.get(bucket.agent_id, bucket.minute)
            if idle >= IDLE_SECONDS or (moved_on and idle >= SETTLE_SECONDS):
                ready.append(self._buckets.pop(key))
        self._prune_latest()
        return ready

    def take_all(self) -> list[MinuteBucket]:
        """남은 버킷을 모두 꺼낸다 (서버 종료 시)."""
        ready = list(self._buckets.values())
        self._buckets.clear()
        self._prune_latest()
        return ready

    def restore(self, buckets: list[MinuteBucket]) -> None:
        """저장에 실패한 버킷을 되돌린다 (다음 주기에 다시 저장). 상한을 넘으면 오래된 분부터 버린다."""
        for bucket in buckets:
            key = (bucket.agent_id, bucket.minute)
            current = self._buckets.get(key)
            if current is None:
                self._buckets[key] = bucket
            else:
                current.merge(bucket)
        overflow = len(self._buckets) - MAX_PENDING_BUCKETS
        if overflow > 0:
            for key in sorted(self._buckets, key=lambda k: k[1])[:overflow]:
                del self._buckets[key]
            logger.error(f"1분 집계 저장 지연으로 오래된 집계 {overflow}건을 버림")

    def _prune_latest(self) -> None:
        # 남은 버킷이 없는 에이전트의 '가장 늦은 분' 기록은 지운다 (계속 쌓이지 않도록)
        active = {agent_id for agent_id, _ in self._buckets}
        for agent_id in [a for a in self._latest_minute if a not in active]:
            del self._latest_minute[agent_id]

    @property
    def pending_count(self) -> int:
        return len(self._buckets)


async def write_buckets(db: AsyncSession, buckets: list[MinuteBucket]) -> int:
    """버킷을 한 번의 다중 행 INSERT 로 저장한다. 같은 에이전트·같은 분 행이 있으면 더해서 합친다.

    커밋은 호출하는 쪽에서 한다. 저장한 행 수를 돌려준다.
    """
    if not buckets:
        return 0
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - 운영(PostgreSQL)·테스트(SQLite) 외 DB 는 쓰지 않음
        raise RuntimeError(f"지원하지 않는 DB: {dialect}")

    table = AgentStatusMinute.__table__
    stmt = insert(table).values([b.to_row() for b in buckets])
    ex = stmt.excluded
    t = table.c

    def add(col: str):
        return t[col] + ex[col]

    def bigger(col: str):
        return case((ex[col] > t[col], ex[col]), else_=t[col])

    def later(col: str):
        # 새로 들어온 쪽의 마지막 샘플이 더 늦으면 그 값을 쓴다
        return case((ex.last_sample_at >= t.last_sample_at, ex[col]), else_=t[col])

    stmt = stmt.on_conflict_do_update(
        index_elements=[t.agent_id, t.minute],
        set_={
            **{c: add(c) for c in (
                "samples", "cpu_sum", "memory_sum", "process_count_sum", "chrome_tab_count_sum",
                "alert_count", "safe_samples", "warning_samples", "danger_samples",
            )},
            **{c: bigger(c) for c in (
                "cpu_max", "memory_max", "process_count_max", "chrome_tab_count_max",
            )},
            "last_state": later("last_state"),
            "foreground_window": later("foreground_window"),
            "last_sample_at": bigger("last_sample_at"),
        },
    )
    await db.execute(stmt)
    return len(buckets)


# 앱 전체에서 공유하는 인스턴스
report_aggregator = MinuteAggregator()
report_stats = ReportStats()
