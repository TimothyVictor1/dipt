"""Automated pipeline runner for scheduled (cron / Task Scheduler) execution.

This wraps :class:`dipt.pipeline.Pipeline` with run bookkeeping:

* Before the run, the last successful run's start time is read from
  ``fetch_log`` and turned into a look-back window, so each run only asks the
  sources for papers newer than the last success (with a one-day safety
  overlap). The very first run falls back to ``FETCH_DAYS_BACK``.
* A ``fetch_log`` row records the run's start, and is updated with the finish
  time, the number of new papers saved, and ``success`` / ``failed``.

If the ``fetch_log`` table is not installed the runner still executes the full
pipeline; it just cannot narrow the fetch window or record history, and says so.

Entry point:

    python -m dipt.scheduler [N]

where the optional ``N`` caps how many papers each per-paper stage processes
(useful for a first smoke run; omit it in production).
"""

from __future__ import annotations

import logging
import math
import sys
from datetime import datetime, timezone

from dipt.config import get_settings
from dipt.exceptions import DIPTError, RepositoryError
from dipt.logging_config import configure_logging
from dipt.pipeline import Pipeline
from dipt.site_export import export_site_data

logger = logging.getLogger(__name__)

# Re-ask each source for a day beyond the gap since the last run, so a paper
# indexed slightly late is never missed at the window boundary.
_OVERLAP_DAYS: int = 1
_MAX_WINDOW_DAYS: int = 365


def _window_since_last_run(last_started: datetime | None, default_days: int) -> int:
    """Return the look-back window in days for this run.

    Args:
        last_started: Start time of the last successful run, or ``None``.
        default_days: Fallback window when there is no prior run.

    Returns:
        A day count in the range ``[1, 365]``.
    """
    if last_started is None:
        return default_days

    now = datetime.now(last_started.tzinfo or timezone.utc)
    gap_days = math.ceil((now - last_started).total_seconds() / 86400)
    return max(1, min(_MAX_WINDOW_DAYS, gap_days + _OVERLAP_DAYS))


def run_pipeline(limit: int | None = None) -> int:
    """Run the full pipeline once, with fetch-window narrowing and logging.

    Args:
        limit: Optional per-stage paper cap.

    Returns:
        Process exit code: ``0`` on success, ``1`` on failure.
    """
    try:
        settings = get_settings()
    except DIPTError:
        logging.basicConfig(level=logging.ERROR)
        logger.exception("Failed to load configuration")
        return 1

    configure_logging(settings.log_level)
    pipeline = Pipeline(settings)
    repo = pipeline.repository
    tracked = pipeline.has_fetch_log

    started_at = datetime.now(timezone.utc)
    run_id: int | None = None
    days_back = settings.fetch_days_back

    try:
        if tracked:
            last = repo.get_last_successful_fetch()
            days_back = _window_since_last_run(last, settings.fetch_days_back)
            run_id = repo.start_fetch_run(started_at)
            logger.info(
                "Scheduled run %s starting; fetch window = %d day(s)",
                run_id,
                days_back,
            )
        else:
            logger.warning(
                "fetch_log table absent: running with the default %d-day "
                "window and no run history. Apply "
                "migrations/001_support_tables.sql to enable incremental fetch.",
                days_back,
            )

        saved = pipeline.fetch(days_back=days_back)
        pipeline.parse(limit)
        pipeline.categorise(limit)
        pipeline.summarise(limit)
        pipeline.score(limit)
        pipeline.qa(limit)
        _export_site(repo, settings)

    except DIPTError:
        logger.exception("Scheduled run failed")
        _finish(repo, run_id, papers_saved=0, status="failed")
        pipeline.close()
        return 1
    except Exception:  # noqa: BLE001 - last-ditch: still record the failure
        logger.exception("Scheduled run crashed unexpectedly")
        _finish(repo, run_id, papers_saved=0, status="failed")
        pipeline.close()
        return 1

    _finish(repo, run_id, papers_saved=saved, status="success")
    pipeline.close()
    logger.info("Scheduled run complete: %d new paper(s) fetched", saved)
    return 0


def _export_site(repo, settings) -> None:
    """Refresh the static-site data files, tolerating any failure.

    A broken export must not fail an otherwise successful pipeline run; the site
    simply keeps its previous data until the next run.

    Args:
        repo: The repository.
        settings: Application settings.
    """
    try:
        export_site_data(repo, settings)
    except (DIPTError, OSError):
        logger.exception("Site export step failed; site data left unchanged")


def _finish(repo, run_id: int | None, papers_saved: int, status: str) -> None:
    """Best-effort update of the ``fetch_log`` row for this run.

    Args:
        repo: The repository.
        run_id: The row id from ``start_fetch_run``, or ``None`` if untracked.
        papers_saved: New papers saved by the fetch stage.
        status: ``"success"`` or ``"failed"``.
    """
    if run_id is None:
        return
    try:
        repo.finish_fetch_run(
            run_id, datetime.now(timezone.utc), papers_saved, status
        )
    except RepositoryError:
        logger.exception("Could not record run %s outcome", run_id)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = sys.argv[1:] if argv is None else argv
    limit: int | None = None
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            logger.error("Usage: python -m dipt.scheduler [N]")
            return 2
    return run_pipeline(limit)


if __name__ == "__main__":
    sys.exit(main())
