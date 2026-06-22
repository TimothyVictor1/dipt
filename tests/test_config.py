"""Unit tests for :mod:`dipt.config`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dipt.config import Settings


def test_database_dsn_is_well_formed() -> None:
    """The DSN should include every connection field."""
    settings = Settings(db_password="secret")  # type: ignore[call-arg]
    dsn = settings.database_dsn
    assert "dbname=dipt" in dsn
    assert "user=dipt_user" in dsn
    assert "password=secret" in dsn
    assert "port=5432" in dsn


def test_pool_max_below_min_is_rejected() -> None:
    """A maximum pool size below the minimum must fail validation."""
    with pytest.raises(ValidationError):
        Settings(db_password="x", db_pool_min=5, db_pool_max=2)  # type: ignore[call-arg]


def test_invalid_log_level_is_rejected() -> None:
    """An unrecognised log level must fail validation."""
    with pytest.raises(ValidationError):
        Settings(db_password="x", log_level="VERBOSE")  # type: ignore[call-arg]


def test_log_level_is_upper_cased() -> None:
    """A valid lower-case log level should be normalised to upper-case."""
    settings = Settings(db_password="x", log_level="debug")  # type: ignore[call-arg]
    assert settings.log_level == "DEBUG"


def test_fetch_days_back_out_of_range_is_rejected() -> None:
    """A fetch window outside the allowed range must fail validation."""
    with pytest.raises(ValidationError):
        Settings(db_password="x", fetch_days_back=0)  # type: ignore[call-arg]
