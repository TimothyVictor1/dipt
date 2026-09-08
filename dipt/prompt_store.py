"""File-backed store for admin-edited agent prompts.

Each agent (quality gate, categorisation, summarisation, scoring, QA) ships with
a default prompt as a module constant. An administrator can override any of them
from the dashboard Settings page; the override is written here as a plain text
file under ``config/agent_prompts/<agent>.txt``.

A file store is used rather than a database table so prompt editing needs no
schema migration and no special database privileges: the pipeline reads the
file at start-up, and deleting the file reverts the agent to its built-in
default.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

#: Agent keys that may be overridden, matching the ``prompt_template`` argument
#: each agent's constructor accepts.
AGENT_KEYS: Final[frozenset[str]] = frozenset(
    {"quality_gate", "categorisation", "summarisation", "scoring", "qa"}
)

_STORE_DIR: Final[Path] = (
    Path(__file__).resolve().parents[1] / "config" / "agent_prompts"
)


def _path(agent: str) -> Path:
    """Return the override file path for an agent, validating the key.

    Args:
        agent: Agent key.

    Returns:
        The path to that agent's override file.

    Raises:
        ValueError: If ``agent`` is not a known key.
    """
    if agent not in AGENT_KEYS:
        raise ValueError(f"Unknown agent key: {agent!r}")
    return _STORE_DIR / f"{agent}.txt"


def get(agent: str) -> str | None:
    """Return the admin-edited prompt for an agent, if one is stored.

    Args:
        agent: Agent key.

    Returns:
        The stored template, or ``None`` if the agent uses its default.
    """
    path = _path(agent)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("Could not read prompt override for '%s'", agent)
        return None
    return text if text.strip() else None


def set(agent: str, template: str) -> None:  # noqa: A001 - mirrors get/delete
    """Store (or replace) the admin-edited prompt for an agent.

    Args:
        agent: Agent key.
        template: The full prompt template text.

    Raises:
        ValueError: If ``agent`` is not a known key, or ``template`` is blank.
        OSError: If the file cannot be written.
    """
    if not template.strip():
        raise ValueError("Prompt template must not be blank")
    path = _path(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    logger.info("Stored prompt override for '%s' (%d chars)", agent, len(template))


def delete(agent: str) -> bool:
    """Remove an agent's override so it reverts to the built-in default.

    Args:
        agent: Agent key.

    Returns:
        ``True`` if an override file was removed, ``False`` if there was none.
    """
    path = _path(agent)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Could not delete prompt override for '%s'", agent)
        return False
    logger.info("Removed prompt override for '%s'", agent)
    return True


def overridden() -> list[str]:
    """Return the agent keys that currently have a stored override."""
    return sorted(key for key in AGENT_KEYS if get(key) is not None)
