"""Automated full-pipeline schedule, controlled from the dashboard.

A small daemon process (``scripts.scheduler_daemon``) runs the whole pipeline
chain on a fixed interval (24 hours by default). This module is the control
surface the dashboard uses to enable / disable it, change the interval, kick a
one-off cycle, and report status.

State lives in files next to the run logs so it survives a browser refresh or a
Streamlit restart:

* ``runs/schedule.json``  - ``{"enabled": bool, "interval_hours": int}``
* ``runs/_daemon.json``   - the running daemon's pid + start time
* ``runs/_daemon.log``    - the daemon's own output
* ``runs/_daemon.stop``   - a sentinel file; its presence tells the daemon to exit

The daemon does not survive a machine reboot. For reboot-proof scheduling use
the OS scheduler as described in ``docs/SCHEDULING.md``; the two are
independent, so pick one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard import runner

DEFAULT_INTERVAL_HOURS: int = 24
MIN_INTERVAL_HOURS: int = 1
MAX_INTERVAL_HOURS: int = 24 * 14

_RUNS_DIR: Path = runner._RUNS_DIR
_CONFIG = _RUNS_DIR / "schedule.json"
_DAEMON_META = _RUNS_DIR / "_daemon.json"
_DAEMON_LOG = _RUNS_DIR / "_daemon.log"
STOP_FLAG = _RUNS_DIR / "_daemon.stop"


@dataclass
class ScheduleStatus:
    """Everything the dashboard needs to render the schedule panel.

    Attributes:
        enabled: Whether the automated schedule is switched on.
        interval_hours: Hours between cycles.
        daemon_running: Whether the daemon process is currently alive.
        last_cycle: The most recent scheduled cycle, or ``None``.
        next_eta: When the next cycle is due, or ``None`` if one is running now
            or the schedule is off.
    """

    enabled: bool
    interval_hours: int
    cycle_limit: int | None
    daemon_running: bool
    last_cycle: runner.Run | None
    next_eta: datetime | None


def _read_config() -> dict:
    try:
        data = json.loads(_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    raw_limit = data.get("cycle_limit")
    cycle_limit = int(raw_limit) if raw_limit not in (None, "", 0) else None
    return {
        "enabled": bool(data.get("enabled", False)),
        "interval_hours": int(data.get("interval_hours", DEFAULT_INTERVAL_HOURS)),
        "cycle_limit": cycle_limit,
    }


def _write_config(**changes) -> dict:
    runner._ensure_dir()
    cfg = _read_config()
    cfg.update(changes)
    cfg["interval_hours"] = max(
        MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, int(cfg["interval_hours"]))
    )
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    _CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def get_config() -> dict:
    """Return ``{"enabled", "interval_hours"}`` for the schedule."""
    return _read_config()


def interval_hours() -> int:
    """Return the configured interval in hours (daemon reads this each loop)."""
    return _read_config()["interval_hours"]


def cycle_limit() -> int | None:
    """Return the per-stage paper cap for a cycle, or ``None`` for the full run."""
    return _read_config()["cycle_limit"]


def stop_requested() -> bool:
    """Return whether the stop sentinel is present (checked by the daemon)."""
    return STOP_FLAG.exists()


def _daemon_pid() -> int | None:
    try:
        return int(json.loads(_DAEMON_META.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError):
        return None


def daemon_running() -> bool:
    """Return whether the schedule daemon process is alive."""
    pid = _daemon_pid()
    return pid is not None and runner._pid_alive(pid)


def scheduled_cycles(limit: int = 15) -> list[runner.Run]:
    """Return recent full scheduled cycles, newest first."""
    return [r for r in runner.list_runs(limit=60) if r.stage == "scheduled"][:limit]


def enable(hours: int, limit: int | None = None) -> None:
    """Switch the schedule on and start the daemon if it is not running.

    Args:
        hours: Interval between cycles.
        limit: Optional per-stage paper cap for each cycle (``None`` = full
            backlog).
    """
    _write_config(enabled=True, interval_hours=hours, cycle_limit=limit)
    try:
        STOP_FLAG.unlink()
    except FileNotFoundError:
        pass

    if daemon_running():
        return

    runner._ensure_dir()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )
    log = _DAEMON_LOG.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed command, our own module
            [sys.executable, "-u", "-m", "scripts.scheduler_daemon"],
            cwd=str(runner._PROJECT_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        log.close()
    _DAEMON_META.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def disable() -> None:
    """Switch the schedule off and stop the daemon (and any running cycle)."""
    _write_config(enabled=False)
    STOP_FLAG.write_text("stop", encoding="utf-8")

    pid = _daemon_pid()
    if pid is not None and runner._pid_alive(pid):
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)]
                if sys.platform == "win32"
                else ["kill", "-TERM", str(pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        _DAEMON_META.unlink()
    except FileNotFoundError:
        pass

    # Also stop a cycle the daemon may have spawned.
    for cyc in scheduled_cycles(limit=3):
        if cyc.status == "running":
            runner.stop_run(cyc.run_id)


def run_once(limit: int | None = None) -> runner.Run:
    """Start a single scheduled cycle now, without changing the schedule.

    Args:
        limit: Optional per-stage paper cap for this cycle.

    Returns:
        The :class:`runner.Run` for the cycle.

    Raises:
        runner.RunnerBusy: If a run is already in progress.
    """
    return runner.start_scheduled_cycle(limit)


def status() -> ScheduleStatus:
    """Return the current :class:`ScheduleStatus`."""
    cfg = _read_config()
    cycles = scheduled_cycles(limit=1)
    last = cycles[0] if cycles else None
    running = daemon_running()

    next_eta: datetime | None = None
    if running and last is not None and last.status != "running":
        next_eta = last.started_at + timedelta(hours=cfg["interval_hours"])

    return ScheduleStatus(
        enabled=cfg["enabled"],
        interval_hours=cfg["interval_hours"],
        cycle_limit=cfg["cycle_limit"],
        daemon_running=running,
        last_cycle=last,
        next_eta=next_eta,
    )


def daemon_log_tail(lines: int = 60) -> str:
    """Return the last ``lines`` lines of the daemon's own log."""
    try:
        text = _DAEMON_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(daemon has not run yet)"
    return "\n".join(text.splitlines()[-lines:]) or "(no output yet)"
