"""Background pipeline-run manager for the dashboard.

Streamlit runs synchronously, but a pipeline stage can take hours, so runs are
launched as detached child processes. Each run owns two files under
``<project root>/runs/``:

* ``<run_id>.json`` - metadata (stage, limit, pid, start time, command)
* ``<run_id>.log``  - combined stdout/stderr, ending in a
  ``__DIPT_RUN_DONE__ exit=<code>`` line written by :mod:`scripts.run_stage`

Because the state lives entirely in those files, the dashboard can show run
status and logs after a browser refresh, a Streamlit rerun, or even a restart.
Only one run is allowed at a time: the local models already saturate the GPU.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_RUNS_DIR = _PROJECT_ROOT / "runs"
_SENTINEL = "__DIPT_RUN_DONE__ exit="
_STALL_SECONDS = 30 * 60  # no log growth for this long => assume the run died

STAGES: tuple[str, ...] = (
    "fetch",
    "parse",
    "categorise",
    "summarise",
    "score",
    "qa",
    "all",
    "stream",
)


class RunnerBusy(RuntimeError):
    """Raised when a new run is requested while one is still in progress."""


@dataclass
class Run:
    """A single background pipeline run and its computed state.

    Attributes:
        run_id: Sortable identifier, ``<UTC timestamp>-<stage>``.
        stage: Pipeline stage name.
        limit: Paper cap passed to the stage, or ``None``.
        pid: Child process id.
        started_at: UTC start time.
        status: One of ``running``, ``finished``, ``failed``, ``died``.
        exit_code: Process exit code once known, else ``None``.
        duration_s: Seconds from start to finish (or to now, if running).
        log_path: Path to the run's log file.
    """

    run_id: str
    stage: str
    limit: int | None
    pid: int
    started_at: datetime
    status: str
    exit_code: int | None
    duration_s: float
    log_path: Path


def _ensure_dir() -> None:
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _pid_alive(pid: int) -> bool:
    """Return whether ``pid`` is a live process."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return f'"{pid}"' in out.stdout


def _parse_meta(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _status_of(meta: dict, log_path: Path) -> tuple[str, int | None, float]:
    """Compute ``(status, exit_code, duration_s)`` for a run."""
    started = datetime.fromisoformat(meta["started_at"])
    now = datetime.now(timezone.utc)

    exit_code: int | None = None
    tail = ""
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        tail = ""

    idx = tail.rfind(_SENTINEL)
    if idx != -1:
        rest = tail[idx + len(_SENTINEL):].split()[0] if tail[idx:].split() else "1"
        try:
            exit_code = int(rest)
        except ValueError:
            exit_code = 1
        end = _mtime(log_path) or now
        return (
            "finished" if exit_code == 0 else "failed",
            exit_code,
            (end - started).total_seconds(),
        )

    if _pid_alive(meta["pid"]):
        return "running", None, (now - started).total_seconds()

    # No sentinel and the process is gone: crashed, or log went silent.
    last_change = _mtime(log_path) or started
    if (now - last_change).total_seconds() > _STALL_SECONDS:
        return "died", None, (last_change - started).total_seconds()
    return "died", None, (now - started).total_seconds()


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def active_run() -> Run | None:
    """Return the currently running run, if any."""
    for run in list_runs(limit=10):
        if run.status == "running":
            return run
    return None


def list_runs(limit: int = 25) -> list[Run]:
    """Return recent runs, newest first.

    Args:
        limit: Maximum number of runs to return.

    Returns:
        A list of :class:`Run` objects.
    """
    _ensure_dir()
    runs: list[Run] = []
    for meta_path in sorted(_RUNS_DIR.glob("*.json"), reverse=True):
        if meta_path.name.startswith("_") or meta_path.name == "schedule.json":
            continue  # daemon/schedule state, not a run
        meta = _parse_meta(meta_path)
        if meta is None or "run_id" not in meta:
            continue
        if len(runs) >= limit:
            break
        log_path = _RUNS_DIR / f"{meta['run_id']}.log"
        status, code, dur = _status_of(meta, log_path)
        runs.append(
            Run(
                run_id=meta["run_id"],
                stage=meta["stage"],
                limit=meta.get("limit"),
                pid=meta["pid"],
                started_at=datetime.fromisoformat(meta["started_at"]),
                status=status,
                exit_code=code,
                duration_s=dur,
                log_path=log_path,
            )
        )
    return runs


def _spawn(stage_label: str, cmd: list[str], limit: int | None) -> Run:
    """Launch ``cmd`` as a detached background run and record its metadata.

    Args:
        stage_label: Label to store as the run's stage (a pipeline stage name,
            or ``"scheduled"`` for a full scheduled cycle).
        cmd: The full command vector to execute.
        limit: Paper cap to record, or ``None``.

    Returns:
        The :class:`Run` that was started.

    Raises:
        RunnerBusy: If another run is still in progress.
    """
    busy = active_run()
    if busy is not None:
        raise RunnerBusy(
            f"'{busy.stage}' (started {busy.started_at:%H:%M}) is still running"
        )

    _ensure_dir()
    run_id = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{stage_label}"
    log_path = _RUNS_DIR / f"{run_id}.log"
    meta_path = _RUNS_DIR / f"{run_id}.json"

    creationflags = 0
    if sys.platform == "win32":
        # New process group so the whole tree can be killed on stop, and no
        # console window pops up.
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )

    log_file = log_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed command, our own module
            cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        log_file.close()

    meta = {
        "run_id": run_id,
        "stage": stage_label,
        "limit": limit,
        "pid": proc.pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cmd": cmd,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return Run(
        run_id=run_id,
        stage=stage_label,
        limit=limit,
        pid=proc.pid,
        started_at=datetime.fromisoformat(meta["started_at"]),
        status="running",
        exit_code=None,
        duration_s=0.0,
        log_path=log_path,
    )


def start_run(stage: str, limit: int | None) -> Run:
    """Launch a pipeline stage as a detached background process.

    Args:
        stage: One of :data:`STAGES`.
        limit: Paper cap for the stage (new-papers cap for ``fetch``), or
            ``None`` for no cap.

    Returns:
        The :class:`Run` that was started.

    Raises:
        ValueError: If ``stage`` is not recognised.
        RunnerBusy: If another run is still in progress.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    cmd = [sys.executable, "-u", "-m", "scripts.run_stage", stage]
    if limit is not None:
        cmd.append(str(limit))
    return _spawn(stage, cmd, limit)


def start_scheduled_cycle(limit: int | None = None) -> Run:
    """Launch one full scheduled pipeline cycle (as ``dipt.scheduler`` does).

    Recorded with stage ``"scheduled"`` so it shows up in :func:`list_runs`
    alongside manual runs and blocks a concurrent manual run.

    Args:
        limit: Optional per-stage paper cap for the cycle. ``None`` runs the
            full backlog.

    Returns:
        The :class:`Run` that was started.

    Raises:
        RunnerBusy: If another run is still in progress.
    """
    cmd = [sys.executable, "-u", "-m", "scripts.run_scheduled"]
    if limit is not None:
        cmd.append(str(limit))
    return _spawn("scheduled", cmd, limit)


def stop_run(run_id: str) -> bool:
    """Terminate a running run's process tree.

    Args:
        run_id: The run to stop.

    Returns:
        ``True`` if a kill command was issued, ``False`` if the run was not
        found or already finished.
    """
    meta = _parse_meta(_RUNS_DIR / f"{run_id}.json")
    if meta is None:
        return False
    pid = meta["pid"]
    if not _pid_alive(pid):
        return False
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        with (_RUNS_DIR / f"{run_id}.log").open("a", encoding="utf-8") as fh:
            fh.write("\n[stopped from the dashboard]\n")
    except OSError:
        pass
    return True


def tail_log(run_id: str, lines: int = 80) -> str:
    """Return the last ``lines`` lines of a run's log.

    Args:
        run_id: The run whose log to read.
        lines: How many trailing lines to return.

    Returns:
        The log tail, or a short placeholder if the log is missing/empty.
    """
    path = _RUNS_DIR / f"{run_id}.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no log yet)"
    if not text.strip():
        return "(waiting for output...)"
    return "\n".join(text.splitlines()[-lines:])
