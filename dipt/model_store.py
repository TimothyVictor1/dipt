"""File-backed store for per-stage model overrides.

Each pipeline stage that calls a model has a default model tag in ``.env``
(``MODEL_QUALITY_GATE``, ``MODEL_SUMMARISATION`` and so on). When a better
model is released, an administrator can point a stage at it from the dashboard
without editing ``.env`` or the code: the override is written here to
``config/model_overrides.json`` and used on that stage's next run. Removing a
stage's entry reverts it to the ``.env`` default.

A file store (rather than a database table) is used for the same reason as
:mod:`dipt.prompt_store`: it needs no migration and no special privileges.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

#: Stage keys that have a model, and the ``Settings`` attribute holding the
#: ``.env`` default for each.
STAGE_SETTING: Final[dict[str, str]] = {
    "quality_gate": "model_quality_gate",
    "categorisation": "model_categorisation",
    "summarisation": "model_summarisation",
    "scoring": "model_scoring",
    "qa": "model_qa",
    "promote": "model_promote",
}
STAGES: Final[tuple[str, ...]] = tuple(STAGE_SETTING)

_STORE_FILE: Final[Path] = (
    Path(__file__).resolve().parents[1] / "config" / "model_overrides.json"
)
#: An Ollama model tag: family/name plus optional ``:tag``, e.g.
#: ``llama3.1:70b``, ``qwen2.5-coder:32b``, ``hf.co/user/repo:Q4_K_M``.
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][\w.\-/]*(:[\w.\-]+)?$")


def _read() -> dict[str, str]:
    try:
        data = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Could not read model overrides; ignoring them")
        return {}
    return {
        k: str(v).strip()
        for k, v in data.items()
        if k in STAGE_SETTING and str(v).strip()
    }


def _write(data: dict[str, str]) -> None:
    _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STORE_FILE.write_text(
        json.dumps(dict(sorted(data.items())), indent=2), encoding="utf-8"
    )


def _check_stage(stage: str) -> None:
    if stage not in STAGE_SETTING:
        raise ValueError(f"Unknown model stage: {stage!r}")


def get(stage: str) -> str | None:
    """Return the override model tag for a stage, if one is set.

    Args:
        stage: One of :data:`STAGES`.

    Returns:
        The override tag, or ``None`` when the stage uses its ``.env`` default.
    """
    _check_stage(stage)
    return _read().get(stage) or None


def set(stage: str, tag: str) -> None:  # noqa: A001 - mirrors get/delete
    """Point a stage at a different model.

    Args:
        stage: One of :data:`STAGES`.
        tag: An Ollama model tag, e.g. ``"llama3.2:90b"``.

    Raises:
        ValueError: If ``stage`` is unknown or ``tag`` is not a plausible tag.
        OSError: If the store file cannot be written.
    """
    _check_stage(stage)
    tag = (tag or "").strip()
    if not _TAG_RE.match(tag):
        raise ValueError(f"Not a valid model tag: {tag!r}")
    data = _read()
    data[stage] = tag
    _write(data)
    logger.info("Model override set: %s -> %s", stage, tag)


def delete(stage: str) -> bool:
    """Remove a stage's override so it reverts to the ``.env`` default.

    Args:
        stage: One of :data:`STAGES`.

    Returns:
        ``True`` if an override was removed, ``False`` if there was none.
    """
    _check_stage(stage)
    data = _read()
    if stage not in data:
        return False
    del data[stage]
    _write(data)
    logger.info("Model override cleared: %s", stage)
    return True


def all_overrides() -> dict[str, str]:
    """Return every stage that currently has an override, ``{stage: tag}``."""
    return _read()
