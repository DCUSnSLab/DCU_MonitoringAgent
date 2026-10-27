"""독립적인 입력 활동 집계 및 큐 기반 일별 CSV 저장.

문자나 키 코드는 보관하지 않습니다. 저장 폴더별로 한 인스턴스만 실행하세요.
"""

import csv
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))
CSV_FIELDS = (
    "session_id", "interval_start", "interval_end", "elapsed_seconds",
    "key_presses", "mouse_clicks", "mouse_moves", "scroll_dx", "scroll_dy",
    "cursor_x", "cursor_y",
)
logger = logging.getLogger(__name__)


def _create_listeners(**callbacks):
    # 비활성화 시에는 pynput/데스크톱 환경이 필요하지 않습니다.
    from pynput import keyboard, mouse

    return (
        keyboard.Listener(on_press=callbacks["on_press"]),
        mouse.Listener(on_move=callbacks["on_move"],
                       on_click=callbacks["on_click"],
                       on_scroll=callbacks["on_scroll"]),
    )


class InputCollector:
    """pynput 콜백 → 메모리 집계 → 1초마다 큐 → CSV 저장 스레드.

    start()/stop()으로 제어하며 실행마다 새 인스턴스를 생성합니다.
    파일과 타임스탬프는 한국 시간 기준이며 구간 시작 날짜에 기록합니다.
    키 자동 반복은 누름 횟수에 포함하고 클릭은 버튼 누름만 집계합니다.
    """

    INTERVAL_SECONDS = 1.0

    def __init__(self, output_dir, agent_id, listener_factory=None):
        if not agent_id or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in agent_id):
            raise ValueError("agent_id must be a valid filename component")
        self.output_dir = Path(output_dir)
        self.agent_id = agent_id
        self._factory = listener_factory or _create_listeners
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._writer_stop = threading.Event()
        self._queue = queue.Queue(maxsize=300)
        self._listeners = ()
        self._sampler = None
        self._writer = None
        self._started = False
        self.error = None
        self._counts = [0, 0, 0, 0, 0]
        self._position = (None, None)

    def start(self):
        if self._started:
            raise RuntimeError("Create a new InputCollector for each run")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._started = True
        self._session = uuid.uuid4().hex
        self._start_wall = datetime.now(KST)
        self._start_tick = time.monotonic()
        try:
            self._listeners = self._factory(
                on_press=self._on_press, on_move=self._on_move,
                on_click=self._on_click, on_scroll=self._on_scroll,
            )
            for listener in self._listeners:
                listener.start()
            self._writer = threading.Thread(target=self._write_loop,
                                            name="InputCsvWriter", daemon=True)
            self._sampler = threading.Thread(target=self._sample_loop,
                                             name="InputSampler", daemon=True)
            self._writer.start()
            self._sampler.start()
        except Exception:
            self.stop()
            raise
        logger.info("Input activity collection started: %s", self.output_dir)

    def stop(self):
        """콜백 중지 후 마지막 부분 구간과 큐의 모든 행을 저장합니다."""
        if not self._started:
            return
        self._stop.set()
        for listener in self._listeners:
            listener.stop()
        for listener in self._listeners:
            if listener.ident is not None:
                # pynput은 콜백 예외를 join()에서 다시 발생시킬 수 있습니다.
                try:
                    listener.join(timeout=2)
                except Exception:
                    logger.exception("Input listener failed during shutdown")
        if self._sampler and self._sampler.ident is not None:
            self._sampler.join()
        self._writer_stop.set()
        if self._writer and self._writer.ident is not None:
            self._writer.join()

    def _on_press(self, _key):
        with self._lock:
            if not self._stop.is_set():
                self._counts[0] += 1

    def _on_move(self, x, y):
        with self._lock:
            if not self._stop.is_set():
                self._counts[2] += 1
                self._position = (x, y)

    def _on_click(self, x, y, _button, pressed):
        with self._lock:
            if not self._stop.is_set():
                self._counts[1] += int(pressed)
                self._position = (x, y)

    def _on_scroll(self, x, y, dx, dy):
        with self._lock:
            if not self._stop.is_set():
                self._counts[3] += dx
                self._counts[4] += dy
                self._position = (x, y)

    def _snapshot(self):
        with self._lock:
            end_wall = datetime.now(KST)
            end_tick = time.monotonic()
            row = dict(zip(CSV_FIELDS, (
                self._session, self._start_wall.isoformat(), end_wall.isoformat(),
                round(end_tick - self._start_tick, 6),
                *self._counts, *self._position,
            )))
            self._counts = [0, 0, 0, 0, 0]
            self._start_wall, self._start_tick = end_wall, end_tick
        return row

    def _enqueue(self):
        try:
            self._queue.put_nowait(self._snapshot())
        except queue.Full:
            raise RuntimeError("Input CSV queue full; collection stopped to bound memory")

    def _fail(self, error):
        self.error = error
        self._stop.set()
        logger.error("Input collection stopped: %s", error, exc_info=True)
        for listener in self._listeners:
            listener.stop()

    def _sample_loop(self):
        try:
            while not self._stop.wait(self.INTERVAL_SECONDS):
                if not all(listener.is_alive() for listener in self._listeners):
                    raise RuntimeError("An input listener stopped unexpectedly")
                self._enqueue()
            if self.error is None:
                self._enqueue()
        except Exception as error:
            self._fail(error)
        finally:
            self._writer_stop.set()

    def _write_loop(self):
        stream = None
        current_date = None
        try:
            while not self._writer_stop.is_set() or not self._queue.empty():
                try:
                    row = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                date = row["interval_start"][:10]
                if current_date != date:
                    if stream:
                        stream.close()
                    path = self.output_dir / (self.agent_id + "-" + date.replace("-", "") + ".csv")
                    needs_header = not path.exists() or path.stat().st_size == 0
                    stream = path.open("a", newline="", encoding="utf-8")
                    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
                    if needs_header:
                        writer.writeheader()
                    current_date = date
                writer.writerow(row)
                stream.flush()
        except Exception as error:
            self._fail(error)
        finally:
            if stream:
                stream.close()
