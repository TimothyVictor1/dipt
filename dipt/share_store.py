"""File-backed store for the short "share" post drafted for each approved paper.

Each approved paper gets a one-paragraph post, written in the professor's
first-person voice, that he can send to LinkedIn / X from the public site with
one click. The draft is cached here as ``config/share_posts/<paper_id>.txt``.

A file store (rather than a database table) is used for the same reason as
:mod:`dipt.prompt_store`: the application database role cannot create tables,
and this needs no migration and no special privileges. Deleting a file makes
the ``promote`` stage regenerate that paper's post on its next run.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_STORE_DIR: Final[Path] = (
    Path(__file__).resolve().parents[1] / "config" / "share_posts"
)


def _path(paper_id: int) -> Path:
    """Return the cache-file path for a paper's share post.

    Args:
        paper_id: The paper's primary key.

    Returns:
        The path to that paper's post file.
    """
    return _STORE_DIR / f"{int(paper_id)}.txt"


def get(paper_id: int) -> str | None:
    """Return the stored share post for a paper, if one has been generated.

    Args:
        paper_id: The paper's primary key.

    Returns:
        The post text, or ``None`` if there is no draft yet.
    """
    try:
        text = _path(paper_id).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("Could not read share post for paper %s", paper_id)
        return None
    return text.strip() or None


def has(paper_id: int) -> bool:
    """Return whether a share post has already been drafted for a paper."""
    return get(paper_id) is not None


def set(paper_id: int, text: str) -> None:  # noqa: A001 - mirrors get/delete
    """Store (or replace) the share post for a paper.

    Args:
        paper_id: The paper's primary key.
        text: The post text.

    Raises:
        ValueError: If ``text`` is blank.
        OSError: If the file cannot be written.
    """
    cleaned = _tidy(text)
    if not cleaned:
        raise ValueError("Share post text must not be blank")
    path = _path(paper_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cleaned, encoding="utf-8")
    logger.info("Stored share post for paper %s (%d chars)", paper_id, len(cleaned))


def delete(paper_id: int) -> bool:
    """Remove a paper's share post so ``promote`` regenerates it.

    Args:
        paper_id: The paper's primary key.

    Returns:
        ``True`` if a file was removed, ``False`` if there was none.
    """
    try:
        _path(paper_id).unlink()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Could not delete share post for paper %s", paper_id)
        return False
    return True


def _tidy(text: str) -> str:
    """Normalise a model-produced post: strip wrapping quotes, labels, runs.

    Args:
        text: Raw model output.

    Returns:
        A cleaned single- or multi-paragraph post, capped in length.
    """
    out = (text or "").strip()
    out = re.sub(r"^(post|draft|linkedin|tweet)\s*[:\-]\s*", "", out, flags=re.I)
    if len(out) >= 2 and out[0] in "\"'“‘" and out[-1] in "\"'”’":
        out = out[1:-1].strip()
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()[:600]
