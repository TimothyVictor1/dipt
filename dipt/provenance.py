"""Record how each generated artefact was produced.

``summaries`` and ``scores`` store the model tag and a timestamp, which is not
enough to reproduce a result: the prompt is editable from the dashboard, and
sampling settings live in code that changes. Without the rest, "which prompt
produced this summary?" has no answer, and a reviewer cannot check a stored
judgement by regenerating it.

The application database role has no ``CREATE`` privilege, so this is a file
store under ``config/provenance/``, following the same pattern as
:mod:`dipt.prompt_store`, :mod:`dipt.share_store` and :mod:`dipt.model_store`.
Records are append-only JSON Lines, one line per generated artefact, so a run
never rewrites history and a partial write costs at most the last line.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_STORE_DIR: Final[Path] = (
    Path(__file__).resolve().parents[1] / "config" / "provenance"
)
#: Stages that record provenance, each written to its own file.
STAGES: Final[frozenset[str]] = frozenset(
    {"categorisation", "summarisation", "scoring", "qa", "promote"}
)


def prompt_fingerprint(template: str) -> str:
    """Return a short stable fingerprint for a prompt template.

    Storing the hash rather than the text keeps the record small while still
    proving which prompt was in force; the full text lives in
    ``config/agent_prompts/`` when it has been overridden.

    Args:
        template: The prompt template as sent to the model.

    Returns:
        The first 12 hex characters of the SHA-256 digest.
    """
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]


def _path(stage: str) -> Path:
    """Return the record file for a stage, validating the stage name.

    Args:
        stage: One of :data:`STAGES`.

    Returns:
        Path to that stage's JSON Lines file.

    Raises:
        ValueError: If ``stage`` is not a known stage.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown provenance stage: {stage!r}")
    return _STORE_DIR / f"{stage}.jsonl"


def record(
    stage: str,
    paper_id: int,
    *,
    model: str,
    prompt_template: str,
    temperature: float,
    seed: int | None = None,
    num_ctx: int | None = None,
    excerpt_chars: int | None = None,
) -> None:
    """Append one provenance record.

    Failure to record never fails the pipeline: losing an audit line is worse
    than losing nothing, but it is much better than losing the summary that
    line describes.

    Args:
        stage: One of :data:`STAGES`.
        paper_id: The paper the artefact belongs to.
        model: Model tag that generated it.
        prompt_template: The prompt template used, hashed before storage.
        temperature: Sampling temperature used.
        seed: Sampling seed, when one was set.
        num_ctx: Context window requested, when one was set.
        excerpt_chars: Characters of paper text sent, when applicable.
    """
    entry = {
        "paper_id": paper_id,
        "at": datetime.now(UTC).isoformat(),
        "model": model,
        "prompt_sha": prompt_fingerprint(prompt_template),
        "temperature": temperature,
        "seed": seed,
        "num_ctx": num_ctx,
        "excerpt_chars": excerpt_chars,
    }
    try:
        path = _path(stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        logger.warning(
            "Could not record provenance for %s of paper %s", stage, paper_id
        )


def history(stage: str, paper_id: int | None = None) -> list[dict]:
    """Return recorded provenance for a stage, newest last.

    Args:
        stage: One of :data:`STAGES`.
        paper_id: When given, only records for that paper.

    Returns:
        The matching records; an empty list when nothing is recorded yet.

    Raises:
        ValueError: If ``stage`` is not a known stage.
    """
    path = _path(stage)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        logger.warning("Could not read provenance for '%s'", stage)
        return []

    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a torn final line costs one record, not the file
        if paper_id is None or entry.get("paper_id") == paper_id:
            out.append(entry)
    return out
