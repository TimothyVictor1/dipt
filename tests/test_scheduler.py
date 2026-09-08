"""Unit tests for :mod:`dipt.scheduler`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from dipt import scheduler
from dipt.exceptions import SourceRequestError


# ── window calculation ────────────────────────────────────────────────────
def test_window_defaults_when_no_prior_run() -> None:
    assert scheduler._window_since_last_run(None, default_days=7) == 7


def test_window_is_gap_plus_overlap() -> None:
    last = datetime.now(timezone.utc) - timedelta(days=3, hours=2)
    # ceil(just over 3d2h) = 4, plus one overlap day.
    assert scheduler._window_since_last_run(last, default_days=7) == 5


def test_window_rounds_partial_day_up() -> None:
    last = datetime.now(timezone.utc) - timedelta(hours=20)
    # ceil(just over 20h) = 1, plus one overlap day.
    assert scheduler._window_since_last_run(last, default_days=7) == 2


def test_window_never_below_one() -> None:
    last = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert scheduler._window_since_last_run(last, default_days=7) == 2


def test_window_clamped_to_max() -> None:
    last = datetime.now(timezone.utc) - timedelta(days=5000)
    assert scheduler._window_since_last_run(last, default_days=7) == 365


# ── run_pipeline orchestration ───────────────────────────────────────────
def _patched(pipeline: MagicMock):
    """Patch the scheduler's collaborators around a fake pipeline."""
    return (
        patch.object(scheduler, "get_settings", return_value=MagicMock()),
        patch.object(scheduler, "configure_logging"),
        patch.object(scheduler, "Pipeline", return_value=pipeline),
        patch.object(scheduler, "export_site_data"),
    )


def test_run_pipeline_records_success_and_narrows_window() -> None:
    pipeline = MagicMock()
    pipeline.has_fetch_log = True
    pipeline.fetch.return_value = 4
    repo = pipeline.repository
    # 20 h ago -> ceil(<1 day) = 1, plus one overlap day = 2. Using a partial
    # day keeps the assertion stable against the clock advancing mid-test.
    repo.get_last_successful_fetch.return_value = datetime.now(
        timezone.utc
    ) - timedelta(hours=20)
    repo.start_fetch_run.return_value = 77

    patches = _patched(pipeline)
    for p in patches:
        p.start()
    try:
        rc = scheduler.run_pipeline(limit=3)
    finally:
        for p in patches:
            p.stop()

    assert rc == 0
    # Window derived from the 1-day gap (+1 overlap) is passed to fetch.
    assert pipeline.fetch.call_args.kwargs["days_back"] == 2
    pipeline.parse.assert_called_once_with(3)
    pipeline.qa.assert_called_once_with(3)
    finish = repo.finish_fetch_run.call_args[0]
    assert finish[0] == 77 and finish[2] == 4 and finish[3] == "success"
    pipeline.close.assert_called_once()


def test_run_pipeline_records_failure_on_stage_error() -> None:
    pipeline = MagicMock()
    pipeline.has_fetch_log = True
    pipeline.repository.start_fetch_run.return_value = 5
    pipeline.repository.get_last_successful_fetch.return_value = None
    pipeline.categorise.side_effect = SourceRequestError("openalex", "boom")

    patches = _patched(pipeline)
    for p in patches:
        p.start()
    try:
        rc = scheduler.run_pipeline()
    finally:
        for p in patches:
            p.stop()

    assert rc == 1
    finish = pipeline.repository.finish_fetch_run.call_args[0]
    assert finish[2] == 0 and finish[3] == "failed"
    pipeline.close.assert_called_once()


def test_run_pipeline_runs_untracked_without_support_tables() -> None:
    pipeline = MagicMock()
    pipeline.has_fetch_log = False
    pipeline.fetch.return_value = 0

    patches = _patched(pipeline)
    for p in patches:
        p.start()
    try:
        rc = scheduler.run_pipeline()
    finally:
        for p in patches:
            p.stop()

    assert rc == 0
    pipeline.repository.start_fetch_run.assert_not_called()
    pipeline.repository.finish_fetch_run.assert_not_called()
    # Falls back to the configured default window (no explicit days_back gap).
    pipeline.fetch.assert_called_once()
