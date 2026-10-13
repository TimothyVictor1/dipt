"""Unit tests for :mod:`dipt.config`."""

from __future__ import annotations

import pytest
from psycopg2.extensions import parse_dsn
from pydantic import ValidationError

from dipt.config import Settings


def test_database_dsn_is_well_formed() -> None:
    """libpq should parse the DSN back to exactly the configured fields."""
    settings = Settings(db_password="secret")  # type: ignore[call-arg]
    parsed = parse_dsn(settings.database_dsn)
    assert parsed["dbname"] == "dipt"
    assert parsed["user"] == "dipt_user"
    assert parsed["password"] == "secret"
    assert parsed["port"] == "5432"


@pytest.mark.parametrize(
    "password",
    [
        "simple",
        "with space",          # unquoted, libpq rejects the whole DSN
        "with'quote",
        "with\\backslash",     # unescaped, libpq silently drops the backslash
        "ev'il\\ mix",
    ],
)
def test_database_dsn_round_trips_awkward_passwords(password: str) -> None:
    """Passwords with spaces, quotes or backslashes must survive the DSN.

    Regression test: an unquoted DSN made libpq reject a password containing a
    space outright, and silently mis-read one containing a backslash.
    """
    settings = Settings(db_password=password)  # type: ignore[call-arg]
    assert parse_dsn(settings.database_dsn)["password"] == password


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
