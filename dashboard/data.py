"""Shared data-access helpers for the Streamlit dashboard.

Streamlit re-executes the whole script on every interaction, so the connection
pool and repository are created once and cached with ``st.cache_resource``.
Every database call the dashboard makes goes through :class:`PaperRepository`,
keeping SQL out of the UI code.
"""

from __future__ import annotations

import logging

import streamlit as st

from dipt.agents.categorisation_agent import _PROMPT_TEMPLATE as _CATEGORISE_PROMPT
from dipt.agents.qa_agent import _PROMPT_TEMPLATE as _QA_PROMPT
from dipt.agents.quality_gate import _PROMPT_TEMPLATE as _QUALITY_GATE_PROMPT
from dipt.agents.scoring_agent import _PROMPT_TEMPLATE as _SCORING_PROMPT
from dipt.agents.summarisation_agent import _PROMPT_TEMPLATE as _SUMMARISE_PROMPT
from dipt.config import Settings, get_settings
from dipt.database.connection import ConnectionPool
from dipt.database.repository import PaperRepository

logger = logging.getLogger(__name__)

# Pipeline stages in lifecycle order, for stable ordering of status charts.
STATUS_ORDER: tuple[str, ...] = (
    "fetched",
    "parsed",
    "no_pdf",
    "no_text",
    "categorised",
    "summarised",
    "scored",
    "approved",
    "rejected",
)

# Agent key -> (human label, module-default prompt, required format fields).
AGENT_PROMPTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "quality_gate": ("Quality gate", _QUALITY_GATE_PROMPT, ("content",)),
    "categorisation": (
        "Categorisation",
        _CATEGORISE_PROMPT,
        ("category_list", "content", "max_categories", "min_confidence"),
    ),
    "summarisation": ("Summarisation", _SUMMARISE_PROMPT, ("content",)),
    "scoring": ("Scoring", _SCORING_PROMPT, ("title", "summary")),
    "qa": ("QA review", _QA_PROMPT, ("title", "summary", "score", "rationale")),
}


@st.cache_resource(show_spinner=False)
def _pool(dsn: str) -> ConnectionPool:
    """Return a process-wide connection pool keyed by DSN.

    Args:
        dsn: The libpq DSN; used only as the cache key.

    Returns:
        A shared :class:`ConnectionPool`.
    """
    return ConnectionPool(get_settings())


def get_settings_cached() -> Settings:
    """Return the validated settings object."""
    return get_settings()


def get_repository() -> PaperRepository:
    """Return a repository bound to the shared connection pool."""
    settings = get_settings()
    return PaperRepository(_pool(settings.database_dsn))


def missing_format_fields(template: str, required: tuple[str, ...]) -> list[str]:
    """Return any required ``{field}`` placeholders absent from a template.

    Args:
        template: The candidate prompt template.
        required: Placeholder names the agent will supply to ``str.format``.

    Returns:
        The subset of ``required`` not present as ``{name}`` in the template.
    """
    return [name for name in required if "{" + name + "}" not in template]
