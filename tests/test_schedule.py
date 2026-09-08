"""Unit tests for :mod:`dashboard.schedule` and the daemon loop.

No real pipeline is ever launched here: ``subprocess`` and the pid-liveness
check are mocked, so these exercise the config, status, and control logic only.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dashboard import runner, schedule


@pytest.fixture(autouse=True)
def _tmp_runs(tmp_path, monkeypatch):
    """Redirect both modules' runs directory to a temp folder."""
    monkeypatch.setattr(runner, "_RUNS_DIR", tmp_path)
    monkeypatch.setattr(schedule, "_RUNS_DIR", tmp_path)
    monkeypatch.setattr(schedule, "_CONFIG", tmp_path / "schedule.json")
    monkeypatch.setattr(schedule, "_DAEMON_META", tmp_path / "_daemon.json")
    monkeypatch.setattr(schedule, "_DAEMON_LOG", tmp_path / "_daemon.log")
    monkeypatch.setattr(schedule, "STOP_FLAG", tmp_path / "_daemon.stop")
    return tmp_path


# ── config ───────────────────────────────────────────────────────────────
def test_default_config_is_off_and_24h() -> None:
    cfg = schedule.get_config()
    assert cfg == {
        "enabled": False,
        "interval_hours": 24,
        "cycle_limit": None,
    }


def test_interval_is_clamped_on_write() -> None:
    schedule._write_config(interval_hours=9999)
    assert schedule.interval_hours() == schedule.MAX_INTERVAL_HOURS
    schedule._write_config(interval_hours=0)
    assert schedule.interval_hours() == schedule.MIN_INTERVAL_HOURS


def test_config_round_trips() -> None:
    schedule._write_config(enabled=True, interval_hours=12)
    cfg = schedule.get_config()
    assert cfg["enabled"] is True and cfg["interval_hours"] == 12


# ── enable / disable ─────────────────────────────────────────────────────
def test_enable_writes_config_clears_stop_and_spawns_daemon(
    _tmp_runs, monkeypatch
) -> None:
    schedule.STOP_FLAG.write_text("stop", encoding="utf-8")
    monkeypatch.setattr(schedule, "daemon_running", lambda: False)
    popen = MagicMock(return_value=SimpleNamespace(pid=4321))
    monkeypatch.setattr(schedule.subprocess, "Popen", popen)

    schedule.enable(6, limit=25)

    assert schedule.get_config() == {
        "enabled": True,
        "interval_hours": 6,
        "cycle_limit": 25,
    }
    assert not schedule.STOP_FLAG.exists()
    popen.assert_called_once()
    assert json.loads(schedule._DAEMON_META.read_text())["pid"] == 4321


def test_enable_does_not_spawn_a_second_daemon(_tmp_runs, monkeypatch) -> None:
    monkeypatch.setattr(schedule, "daemon_running", lambda: True)
    popen = MagicMock()
    monkeypatch.setattr(schedule.subprocess, "Popen", popen)

    schedule.enable(8)

    popen.assert_not_called()
    assert schedule.get_config()["interval_hours"] == 8


def test_disable_writes_stop_flag_and_kills_daemon(_tmp_runs, monkeypatch) -> None:
    schedule._DAEMON_META.write_text(json.dumps({"pid": 777}), encoding="utf-8")
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: pid == 777)
    killed = MagicMock()
    monkeypatch.setattr(schedule.subprocess, "run", killed)
    monkeypatch.setattr(schedule, "scheduled_cycles", lambda limit=3: [])

    schedule.disable()

    assert schedule.get_config()["enabled"] is False
    assert schedule.STOP_FLAG.exists()
    killed.assert_called_once()
    assert not schedule._DAEMON_META.exists()


def test_stop_requested_reflects_flag_file(_tmp_runs) -> None:
    assert schedule.stop_requested() is False
    schedule.STOP_FLAG.write_text("stop", encoding="utf-8")
    assert schedule.stop_requested() is True


# ── status ───────────────────────────────────────────────────────────────
def _fake_cycle(status: str, minutes_ago: int, limit: int | None = None) -> runner.Run:
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return runner.Run(
        run_id=f"{started:%Y%m%d-%H%M%S}-scheduled",
        stage="scheduled",
        limit=limit,
        pid=123,
        started_at=started,
        status=status,
        exit_code=0 if status == "finished" else None,
        duration_s=600.0,
        log_path=schedule._RUNS_DIR / "x.log",
    )


def test_status_reports_next_eta_after_a_finished_cycle(monkeypatch) -> None:
    schedule._write_config(enabled=True, interval_hours=24)
    monkeypatch.setattr(schedule, "daemon_running", lambda: True)
    cyc = _fake_cycle("finished", minutes_ago=60)
    monkeypatch.setattr(schedule, "scheduled_cycles", lambda limit=1: [cyc])

    stat = schedule.status()

    assert stat.enabled and stat.daemon_running
    assert stat.last_cycle is cyc
    assert stat.next_eta == cyc.started_at + timedelta(hours=24)


def test_status_has_no_eta_while_a_cycle_runs(monkeypatch) -> None:
    schedule._write_config(enabled=True, interval_hours=12)
    monkeypatch.setattr(schedule, "daemon_running", lambda: True)
    monkeypatch.setattr(
        schedule, "scheduled_cycles", lambda limit=1: [_fake_cycle("running", 5)]
    )
    assert schedule.status().next_eta is None


def test_scheduled_cycles_filters_to_scheduled_stage(monkeypatch) -> None:
    runs = [
        _fake_cycle("finished", 10),
        runner.Run(
            run_id="20260101-000000-summarise",
            stage="summarise",
            limit=5,
            pid=1,
            started_at=datetime.now(timezone.utc),
            status="finished",
            exit_code=0,
            duration_s=1.0,
            log_path=schedule._RUNS_DIR / "y.log",
        ),
    ]
    monkeypatch.setattr(runner, "list_runs", lambda limit=60: runs)
    got = schedule.scheduled_cycles()
    assert [r.stage for r in got] == ["scheduled"]


# ── daemon loop ──────────────────────────────────────────────────────────
def test_daemon_main_exits_immediately_when_stop_requested(monkeypatch) -> None:
    import scripts.scheduler_daemon as daemon

    monkeypatch.setattr(daemon.schedule, "stop_requested", lambda: True)
    monkeypatch.setattr(daemon.schedule, "interval_hours", lambda: 24)
    start = MagicMock()
    monkeypatch.setattr(daemon.runner, "start_scheduled_cycle", start)

    assert daemon.main() == 0
    start.assert_not_called()
