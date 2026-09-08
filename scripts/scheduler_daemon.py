"""Long-running daemon that runs the full pipeline on a fixed interval.

Started and stopped from the dashboard's **Schedule** view (see
:mod:`dashboard.schedule`). Each cycle is one ``dipt.scheduler`` run - the
whole fetch -> ... -> qa chain plus a site export - launched through
:func:`dashboard.runner.start_scheduled_cycle` so it is tracked and logged like
a manual run.

The loop:

1. Exit if the stop sentinel file is present.
2. Start one cycle; wait for it, killing it early if a stop is requested.
3. Sleep for the configured interval, re-reading the interval periodically so a
   change made in the dashboard takes effect without a restart, and breaking
   out early on a stop request.

All output goes to stdout, which the dashboard captures to ``runs/_daemon.log``.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

from dashboard import runner, schedule

_POLL_SECONDS = 15
_BUSY_RETRY_SECONDS = 300


def _log(message: str) -> None:
    print(
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} | {message}",
        flush=True,
    )


def _interruptible_sleep(total_seconds: float) -> bool:
    """Sleep up to ``total_seconds``, returning early on a stop request.

    Returns:
        ``True`` if a stop was requested during the sleep, ``False`` if the
        full duration elapsed.
    """
    waited = 0.0
    while waited < total_seconds:
        if schedule.stop_requested():
            return True
        time.sleep(min(_POLL_SECONDS, total_seconds - waited))
        waited += _POLL_SECONDS
    return schedule.stop_requested()


def _await_cycle(run_id: str) -> None:
    """Block until the given cycle finishes, or stop is requested."""
    while True:
        current = next(
            (r for r in runner.list_runs(limit=20) if r.run_id == run_id), None
        )
        if current is None or current.status != "running":
            outcome = current.status if current else "unknown"
            _log(f"cycle {run_id} ended: {outcome}")
            return
        if schedule.stop_requested():
            _log(f"stop requested; terminating cycle {run_id}")
            runner.stop_run(run_id)
            return
        time.sleep(_POLL_SECONDS)


def main() -> int:
    """Run the schedule loop until a stop is requested."""
    _log(
        f"scheduler daemon started (pid {os.getpid()}); "
        f"interval {schedule.interval_hours()} h"
    )
    try:
        while not schedule.stop_requested():
            try:
                cycle = runner.start_scheduled_cycle(schedule.cycle_limit())
            except runner.RunnerBusy as exc:
                _log(f"a run is in progress ({exc}); retrying in 5 min")
                if _interruptible_sleep(_BUSY_RETRY_SECONDS):
                    break
                continue
            except Exception as exc:  # noqa: BLE001 - keep the daemon alive
                _log(f"could not start cycle: {exc!r}; retrying in 5 min")
                if _interruptible_sleep(_BUSY_RETRY_SECONDS):
                    break
                continue

            _log(f"cycle {cycle.run_id} started")
            _await_cycle(cycle.run_id)

            if schedule.stop_requested():
                break

            hours = schedule.interval_hours()
            _log(f"sleeping {hours} h until the next cycle")
            if _interruptible_sleep(hours * 3600):
                break
    finally:
        _log("scheduler daemon stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
