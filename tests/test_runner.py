"""Unit tests for :mod:`dashboard.runner` status logic.

The subprocess spawn and ``tasklist`` liveness check are not exercised here
(they need a live OS process); the status computation from the meta + log
files is, since that is what the dashboard depends on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from dashboard import runner


@pytest.fixture(autouse=True)
def _runs_dir(tmp_path, monkeypatch):
    """Point the runner at a temporary runs directory."""
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)
    return tmp_path


def _write_run(tmp_path, run_id: str, *, started: datetime, log: str) -> None:
    (tmp_path / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "stage": run_id.rsplit("-", maxsplit=1)[-1],
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
    monkeypatch.setattr(runner, "pid_alive", lambda pid: False)
    _write_run(
        _runs_dir,
        "20260101-000000-summarise",
        started=datetime.now(UTC) - timedelta(minutes=5),
        log="working...\n__DIPT_RUN_DONE__ exit=0\n",
    )

    runs = runner.list_runs()
    assert runs[0].status == "finished"
    assert runs[0].exit_code == 0


def test_failed_run_is_detected_from_nonzero_exit(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "pid_alive", lambda pid: False)
    _write_run(
        _runs_dir,
        "20260101-000000-score",
        started=datetime.now(UTC) - timedelta(minutes=1),
        log="boom\n__DIPT_RUN_DONE__ exit=1\n",
    )

    assert runner.list_runs()[0].status == "failed"
    assert runner.list_runs()[0].exit_code == 1


def test_running_when_pid_alive_and_no_sentinel(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "pid_alive", lambda pid: True)
    _write_run(
        _runs_dir,
        "20260101-000000-score",
        started=datetime.now(UTC) - timedelta(minutes=2),
        log="[3/25] Scoring paper 930\n",
    )

    run = runner.list_runs()[0]
    assert run.status == "running"
    assert run.exit_code is None


def test_died_when_pid_gone_and_no_sentinel(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "pid_alive", lambda pid: False)
    _write_run(
        _runs_dir,
        "20260101-000000-qa",
        started=datetime.now(UTC) - timedelta(minutes=3),
        log="[1/5] QA-checking paper 926\n",
    )

    assert runner.list_runs()[0].status == "died"


def test_active_run_returns_the_running_one(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "pid_alive", lambda pid: True)
    _write_run(
        _runs_dir,
        "20260101-010000-summarise",
        started=datetime.now(UTC),
        log="starting\n",
    )
    assert runner.active_run().stage == "summarise"


def test_start_run_rejects_when_busy(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "pid_alive", lambda pid: True)
    _write_run(
        _runs_dir,
        "20260101-010000-score",
        started=datetime.now(UTC),
        log="starting\n",
    )
    with pytest.raises(runner.RunnerBusy):
        runner.start_run("summarise", 5)


def test_start_run_rejects_unknown_stage(_runs_dir) -> None:
    with pytest.raises(ValueError):
        runner.start_run("bogus", 5)


def test_tail_log_returns_last_lines(_runs_dir, monkeypatch) -> None:
    monkeypatch.setattr(runner, "pid_alive", lambda pid: False)
    lines = "\n".join(f"line {i}" for i in range(200))
    _write_run(
        _runs_dir,
        "20260101-000000-parse",
        started=datetime.now(UTC),
        log=lines + "\n__DIPT_RUN_DONE__ exit=0\n",
    )
    tail = runner.tail_log("20260101-000000-parse", lines=10)
    assert "line 199" in tail
    assert "line 150" not in tail


def test_truncated_sentinel_does_not_crash(_runs_dir, monkeypatch) -> None:
    """A half-written sentinel must not raise out of the run listing.

    Regression test: the log can be read while ``scripts.run_stage`` is still
    flushing ``__DIPT_RUN_DONE__ exit=<code>``. Parsing the code off an empty
    remainder used to raise ``IndexError`` and blank the dashboard.
    """
    monkeypatch.setattr(runner, "pid_alive", lambda pid: True)
    _write_run(
        _runs_dir,
        "20260101-000000-qa",
        started=datetime.now(UTC) - timedelta(minutes=2),
        log="working...\n__DIPT_RUN_DONE__ exit=",
    )

    runs = runner.list_runs()
    assert runs[0].status == "running"
    assert runs[0].exit_code is None


def test_incomplete_meta_file_is_skipped(_runs_dir, monkeypatch) -> None:
    """A torn metadata file is ignored rather than raising ``KeyError``."""
    monkeypatch.setattr(runner, "pid_alive", lambda pid: False)
    (_runs_dir / "20260101-000000-parse.json").write_text(
        json.dumps({"run_id": "20260101-000000-parse"}), encoding="utf-8"
    )
    _write_run(
        _runs_dir,
        "20260101-000001-score",
        started=datetime.now(UTC),
        log="__DIPT_RUN_DONE__ exit=0\n",
    )

    runs = runner.list_runs()
    assert [r.run_id for r in runs] == ["20260101-000001-score"]


def test_meta_with_unparseable_timestamp_is_skipped(_runs_dir) -> None:
    """A corrupt ``started_at`` is rejected instead of raising ``ValueError``."""
    (_runs_dir / "20260101-000000-fetch.json").write_text(
        json.dumps(
            {
                "run_id": "20260101-000000-fetch",
                "stage": "fetch",
                "pid": 1,
                "started_at": "not-a-timestamp",
            }
        ),
        encoding="utf-8",
    )
    assert runner.list_runs() == []


def test_kill_process_tree_uses_the_platform_command(monkeypatch) -> None:
    """The stop path must issue a kill that works on the host OS.

    Regression test: ``stop_run`` always shelled out to Windows ``taskkill``,
    so the dashboard's Stop button silently did nothing on POSIX.
    """
    seen: dict = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(runner.subprocess, "run", _fake_run)

    monkeypatch.setattr(runner.sys, "platform", "win32")
    assert runner.kill_process_tree(4321) is True
    assert seen["cmd"][0] == "taskkill" and "4321" in seen["cmd"]

    monkeypatch.setattr(runner.sys, "platform", "linux")
    assert runner.kill_process_tree(4321) is True
    assert seen["cmd"] == ["kill", "-TERM", "-4321"]


def test_detach_kwargs_are_platform_appropriate(monkeypatch) -> None:
    """Children must lead their own process group so the tree can be killed."""
    monkeypatch.setattr(runner.sys, "platform", "linux")
    assert runner.detach_kwargs() == {"start_new_session": True}

    monkeypatch.setattr(runner.sys, "platform", "win32")
    assert "creationflags" in runner.detach_kwargs()
