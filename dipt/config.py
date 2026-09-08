"""Centralised, validated configuration for the DIPT system.

Configuration is loaded from environment variables (typically via a ``.env``
file) and validated with Pydantic. Importing :data:`settings` anywhere in the
codebase yields a single, validated configuration object.

Raises:
    ConfigurationError: If required settings are missing or fail validation.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from dipt.exceptions import ConfigurationError


class Settings(BaseSettings):
    """Validated application settings.

    Attributes:
        db_host: PostgreSQL host.
        db_port: PostgreSQL port.
        db_name: PostgreSQL database name.
        db_user: PostgreSQL user.
        db_password: PostgreSQL password.
        db_pool_min: Minimum connections held by the pool.
        db_pool_max: Maximum connections held by the pool.
        ollama_base_url: Base URL of the local Ollama server.
        model_quality_gate: Model used for the SE-relevance gate.
        model_categorisation: Model used for categorisation.
        model_summarisation: Model used for summarisation.
        model_scoring: Model used for industrial-relevance scoring.
        model_qa: Model used for the QA consistency check.
        model_promote: Model used to draft the shareable social post.
        contact_email: Email used in API polite-pool headers.
        core_api_key: Optional API key for the CORE source.
        site_base_url: Public base URL of the static site (for RSS links).
        site_title: Title shown on the public site and in the RSS feed.
        pdf_dir: Directory where downloaded PDFs are stored.
        text_dir: Directory where extracted text files are stored.
        fetch_days_back: How many days back each source should look.
        http_timeout_seconds: Default per-request network timeout.
        log_level: Root logging level.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # The ``model_*`` settings name Ollama models, not Pydantic internals.
        protected_namespaces=(),
    )

    # Database
    db_host: str = Field(default="localhost")
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = Field(default="dipt")
    db_user: str = Field(default="dipt_user")
    db_password: str = Field(...)
    db_pool_min: int = Field(default=1, ge=1)
    db_pool_max: int = Field(default=5, ge=1)

    # Ollama / models
    ollama_base_url: str = Field(default="http://localhost:11434")
    model_quality_gate: str = Field(default="llama3.1:8b")
    model_categorisation: str = Field(default="llama3.1:70b")
    model_summarisation: str = Field(default="qwen2.5-coder:32b")
    model_scoring: str = Field(default="deepseek-r1:70b")
    model_qa: str = Field(default="llama3.1:70b")
    model_promote: str = Field(default="qwen2.5-coder:32b")

    # Sources
    contact_email: str = Field(default="tira25@student.bth.se")
    core_api_key: str = Field(default="")

    # Public site
    site_base_url: str = Field(default="https://dipt.pages.dev")
    site_title: str = Field(default="DIPT - SE Research Monitor")

    # Storage
    pdf_dir: Path = Field(default=Path("pdfs"))
    text_dir: Path = Field(default=Path("pdfs/text"))

    # Behaviour
    fetch_days_back: int = Field(default=7, ge=1, le=365)
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    log_level: str = Field(default="INFO")

    @field_validator("db_pool_max")
    @classmethod
    def _validate_pool_bounds(cls, pool_max: int, info) -> int:
        """Ensure the maximum pool size is not smaller than the minimum."""
        pool_min = info.data.get("db_pool_min", 1)
        if pool_max < pool_min:
            raise ValueError(
                f"db_pool_max ({pool_max}) must be >= db_pool_min ({pool_min})"
            )
        return pool_max

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, level: str) -> str:
        """Validate that the log level is a recognised name."""
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = level.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {sorted(valid)}")
        return upper

    @property
    def database_dsn(self) -> str:
        """Return a libpq-style DSN string for psycopg2."""
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password}"
        )

    def ensure_directories(self) -> None:
        """Create the PDF and text output directories if they do not exist."""
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.text_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache the validated application settings.

    Returns:
        The singleton :class:`Settings` instance.

    Raises:
        ConfigurationError: If settings fail to load or validate.
    """
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigurationError(
            f"Invalid or missing configuration: {exc}"
        ) from exc
