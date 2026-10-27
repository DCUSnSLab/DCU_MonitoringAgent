import csv
import threading
import time
from datetime import datetime
from unittest.mock import Mock

import pytest

from agent.collectors.input_collector import CSV_FIELDS, KST, InputCollector
from agent.config import AppConfig


class FakeListener:
    def __init__(self):
        self.ident = None
        self.alive = False

    def start(self):
        self.ident = 1
        self.alive = True

    def stop(self):
        self.alive = False

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return self.alive


def factory(**callbacks):
    return FakeListener(), FakeListener()


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_aggregation_privacy_and_shutdown_drain(tmp_path):
    collector = InputCollector(tmp_path, "TEST-PC", factory)
    collector.start()
    collector._on_press("PRIVATE_TEXT")
    collector._on_press("PRIVATE_TEXT")
    collector._on_move(-100, 200)
    collector._on_move(-80, 210)
    collector._on_click(-80, 210, "left", True)
    collector._on_click(-80, 210, "left", False)
    collector._on_scroll(-80, 210, 0, -2)
    collector.stop()
    collector.stop()
    path = next(tmp_path.glob("*.csv"))
    rows = read_rows(path)
    assert len(rows) == 1
    assert path.name == "TEST-PC-" + datetime.now(KST).strftime("%Y%m%d") + ".csv"
    assert tuple(rows[0]) == (
        "session_id", "interval_start", "interval_end", "elapsed_seconds",
        "key_presses", "mouse_clicks", "mouse_moves", "scroll_dx", "scroll_dy",
        "cursor_x", "cursor_y",
    )
    assert rows[0]["key_presses"] == "2"
    assert rows[0]["mouse_clicks"] == "1"
    assert rows[0]["mouse_moves"] == "2"
    assert rows[0]["scroll_dy"] == "-2"
    assert rows[0]["cursor_x"] == "-80"
    assert "PRIVATE_TEXT" not in repr(vars(collector))
    assert "PRIVATE_TEXT" not in path.read_text()
    assert not collector._writer.is_alive()
    assert not collector._sampler.is_alive()


def test_sampling_includes_idle_intervals(tmp_path):
    collector = InputCollector(tmp_path, "TEST-PC", factory)
    collector.INTERVAL_SECONDS = 0.02
    sampled = threading.Event()
    original = collector._enqueue

    def enqueue():
        original()
        sampled.set()

    collector._enqueue = enqueue
    collector.start()
    try:
        assert sampled.wait(2)
    finally:
        collector.stop()
    rows = read_rows(next(tmp_path.glob("*.csv")))
    assert len(rows) >= 2
    assert all(row["key_presses"] == "0" for row in rows)
    assert rows[0]["cursor_x"] == ""


def test_rotation_and_append_without_duplicate_header(tmp_path):
    collector = InputCollector(tmp_path, "TEST-PC", factory)
    for date in ("2026-10-26", "2026-10-27", "2026-10-26"):
        row = dict.fromkeys(CSV_FIELDS, 0)
        row["interval_start"] = date + "T23:59:59+09:00"
        collector._queue.put(row)
    path = tmp_path / "TEST-PC-20261026.csv"
    collector._writer_stop.set()
    collector._write_loop()
    assert collector.error is None
    assert len(read_rows(path)) == 2
    assert len(read_rows(tmp_path / "TEST-PC-20261027.csv")) == 1
    assert path.read_text().count("session_id") == 1


def test_writer_failure_stops_listeners_and_sampler(tmp_path):
    (tmp_path / ("TEST-PC-" + datetime.now(KST).strftime("%Y%m%d") + ".csv")).mkdir()
    collector = InputCollector(tmp_path, "TEST-PC", factory)
    collector.INTERVAL_SECONDS = 0.01
    collector.start()
    try:
        assert collector._stop.wait(2)
    finally:
        collector.stop()
    assert isinstance(collector.error, OSError)
    assert all(not listener.is_alive() for listener in collector._listeners)


def test_queue_overflow_stops_collection_and_drains_previous_rows(tmp_path):
    collector = InputCollector(tmp_path, "TEST-PC", factory)
    collector._session = "session"
    collector._start_wall = datetime.now(KST)
    collector._start_tick = time.monotonic()
    collector._listeners = factory()
    for _ in range(collector._queue.maxsize):
        collector._enqueue()
    collector._stop.wait = lambda timeout: False
    for listener in collector._listeners:
        listener.start()
    collector._sample_loop()
    assert isinstance(collector.error, RuntimeError)
    assert "queue full" in str(collector.error)
    assert collector._writer_stop.is_set()
    collector._write_loop()
    assert len(read_rows(next(tmp_path.glob("*.csv")))) == 300


def test_start_failure_cleans_up_started_listener(tmp_path):
    listeners = factory()
    listeners[1].start = Mock(side_effect=RuntimeError("listener startup failed"))
    collector = InputCollector(tmp_path, "TEST-PC", lambda **kw: listeners)
    with pytest.raises(RuntimeError, match="startup failed"):
        collector.start()
    assert not listeners[0].is_alive()


def test_listener_death_is_logged_and_stops_workers(tmp_path):
    collector = InputCollector(tmp_path, "TEST-PC", factory)
    collector.INTERVAL_SECONDS = 0.01
    collector.start()
    collector._listeners[0].stop()
    try:
        assert collector._stop.wait(2)
    finally:
        collector.stop()
    assert "listener stopped unexpectedly" in str(collector.error)
    assert not collector._writer.is_alive()


def test_new_instance_appends_without_duplicate_header(tmp_path):
    for _ in range(2):
        collector = InputCollector(tmp_path, "TEST-PC", factory)
        collector.start()
        collector.stop()
    path = next(tmp_path.glob("*.csv"))
    rows = read_rows(path)
    assert len(rows) == 2
    assert rows[0]["session_id"] != rows[1]["session_id"]
    assert path.read_text().count("session_id") == 1


def test_old_configuration_keeps_feature_disabled():
    assert AppConfig().monitoring.input_activity.enabled is False
