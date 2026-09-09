"""Domain-specific exception hierarchy for the DIPT system.

All custom exceptions inherit from :class:`DIPTError`, allowing callers to
catch the entire family with a single ``except DIPTError`` while still being
able to handle specific failure modes granularly.
"""

from __future__ import annotations


class DIPTError(Exception):
    """Base exception for all DIPT-specific errors."""


# ── Configuration ────────────────────────────────────────────────────────
class ConfigurationError(DIPTError):
    """Raised when required configuration is missing or invalid."""


# ── Database ─────────────────────────────────────────────────────────────
class DatabaseError(DIPTError):
    """Base class for database-related failures."""


class ConnectionPoolError(DatabaseError):
    """Raised when the connection pool cannot be created or exhausted."""


class RepositoryError(DatabaseError):
    """Raised when a repository operation fails."""


# ── Sources / Networking ─────────────────────────────────────────────────
class SourceError(DIPTError):
    """Base class for paper-source (API) failures."""


class SourceRequestError(SourceError):
    """Raised when an HTTP request to an external source fails."""

    def __init__(self, source: str, message: str) -> None:
        self.source = source
        super().__init__(f"[{source}] {message}")


class SourceRateLimitError(SourceError):
    """Raised when an external source signals a rate limit (HTTP 429)."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"[{source}] rate limit exceeded")


class SourceParseError(SourceError):
    """Raised when an external source returns an unparseable payload."""

    def __init__(self, source: str, message: str) -> None:
        self.source = source
        super().__init__(f"[{source}] parse error: {message}")


# ── LLM ──────────────────────────────────────────────────────────────────
class LLMError(DIPTError):
    """Base class for local LLM (Ollama) failures."""


class LLMRequestError(LLMError):
    """Raised when an LLM inference call fails after retries."""


class LLMResponseError(LLMError):
    """Raised when the LLM returns an unusable or unparseable response."""


class ModelPullError(LLMError):
    """Raised when a model cannot be downloaded from the Ollama registry."""


# ── Parsing ──────────────────────────────────────────────────────────────
class ParserError(DIPTError):
    """Base class for PDF download / text-extraction failures."""


class PDFDownloadError(ParserError):
    """Raised when a PDF cannot be downloaded or is not a valid PDF."""


class TextExtractionError(ParserError):
    """Raised when text cannot be extracted from a downloaded PDF."""
