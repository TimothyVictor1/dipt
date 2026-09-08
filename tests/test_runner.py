"""Unit tests for :mod:`dashboard.runner` status logic.

The subprocess spawn and ``tasklist`` liveness check are not exercised here
(they need a live OS process); the status computation from the meta + log
files is, since that is what the dashboard depends on.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from dashboard import runner


@pytest.fixture(autouse=True)
def _runs_dir(tmp_path, monkeypatch):
    """Point the runner at a temporary runs directory."""
    monkeypatch.setattr(runner, "_RUNS_DIR", tmp_path)
    return tmp_path


def _write_run(tmp_path, run_id: str, *, started: datetime, log: str) -> None:
    (tmp_path / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "stage": run_id.split("-")[-1],
                "limit": 5,
                "pid": 424242,
                "started_at": started.isoformat(),
                "cmd": ["python"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / f"{run_id}.log").write_text(log, encoding="utf-8")


def test_finished_run_is_detected_from_sentinel(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: False)
    _write_run(
        _runs_dir,
        "20260101-000000-summarise",
        started=datetime.now(timezone.utc) - timedelta(minutes=5),
        log="working...\n__DIPT_RUN_DONE__ exit=0\n",
    )

    runs = runner.list_runs()
    assert runs[0].status == "finished"
    assert runs[0].exit_code == 0


def test_failed_run_is_detected_from_nonzero_exit(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: False)
    _write_run(
        _runs_dir,
        "20260101-000000-score",
        started=datetime.now(timezone.utc) - timedelta(minutes=1),
        log="boom\n__DIPT_RUN_DONE__ exit=1\n",
    )

    assert runner.list_runs()[0].status == "failed"
    assert runner.list_runs()[0].exit_code == 1


def test_running_when_pid_alive_and_no_sentinel(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: True)
    _write_run(
        _runs_dir,
        "20260101-000000-score",
        started=datetime.now(timezone.utc) - timedelta(minutes=2),
        log="[3/25] Scoring paper 930\n",
    )

    run = runner.list_runs()[0]
    assert run.status == "running"
    assert run.exit_code is None


def test_died_when_pid_gone_and_no_sentinel(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: False)
    _write_run(
        _runs_dir,
        "20260101-000000-qa",
        started=datetime.now(timezone.utc) - timedelta(minutes=3),
        log="[1/5] QA-checking paper 926\n",
    )

    assert runner.list_runs()[0].status == "died"


def test_active_run_returns_the_running_one(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: True)
    _write_run(
        _runs_dir,
        "20260101-010000-summarise",
        started=datetime.now(timezone.utc),
        log="starting\n",
    )
    assert runner.active_run().stage == "summarise"


def test_start_run_rejects_when_busy(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: True)
    _write_run(
        _runs_dir,
        "20260101-010000-score",
        started=datetime.now(timezone.utc),
        log="starting\n",
    )
    with pytest.raises(runner.RunnerBusy):
        runner.start_run("summarise", 5)


def test_start_run_rejects_unknown_stage(_runs_dir) -> None:
    with pytest.raises(ValueError):
        runner.start_run("bogus", 5)


def test_tail_log_returns_last_lines(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: False)
    lines = "\n".join(f"line {i}" for i in range(200))
    _write_run(
        _runs_dir,
        "20260101-000000-parse",
        started=datetime.now(timezone.utc),
        log=lines + "\n__DIPT_RUN_DONE__ exit=0\n",
    )
    tail = runner.tail_log("20260101-000000-parse", lines=10)
    assert "line 199" in tail
    assert "line 150" not in tail
