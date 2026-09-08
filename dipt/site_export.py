"""Export approved papers to the static-site data files.

The public site (``website/``) is a static Next.js + Fuse.js build with no
server and no database access. This module is the bridge: it reads every
``approved`` paper from PostgreSQL and writes three files the site consumes at
build time:

* ``website/data/papers.json``     - the full record set, including the parsed
                                     four-part summary, score, and categories.
* ``website/data/categories.json`` - categories that have at least one approved
                                     paper, with counts, for the browse view.
* ``website/public/rss.xml``       - an RSS 2.0 feed of the most recent
                                     approved papers.

Run it after any stage that can approve papers (the scheduler does this
automatically). A change to ``papers.json`` is what a Cloudflare Pages / CI
build watches to rebuild and redeploy the site.

Entry point:

    python -m dipt.site_export
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Final
from xml.sax.saxutils import escape

from dipt import share_store
from dipt.config import Settings, get_settings
from dipt.database.connection import ConnectionPool
from dipt.database.repository import PaperRepository
from dipt.exceptions import DIPTError
from dipt.logging_config import configure_logging

logger = logging.getLogger(__name__)

_SITE_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "website"
_RSS_ITEM_LIMIT: Final[int] = 50

# Headers written by :meth:`PaperSummary.to_text`, in order.
_SUMMARY_HEADERS: Final[tuple[tuple[str, str], ...]] = (
    ("research_problem", "Research Problem:"),
    ("methodology", "Methodology:"),
    ("key_findings", "Key Findings:"),
    ("industrial_implications", "Industrial Implications:"),
)


def _slugify(name: str) -> str:
    """Return a URL-safe slug for a category name.

    Args:
        name: The category name.

    Returns:
        A lower-case, hyphen-separated slug.
    """
    out = [ch.lower() if ch.isalnum() else "-" for ch in name.strip()]
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "uncategorised"


def _split_summary(summary_text: str) -> dict[str, str]:
    """Parse the stored plain-text summary back into its four sections.

    Args:
        summary_text: The headed text produced by ``PaperSummary.to_text``.

    Returns:
        A dict keyed by section name. Missing sections map to an empty string.
    """
    sections = {key: "" for key, _ in _SUMMARY_HEADERS}
    if not summary_text:
        return sections

    remainder = summary_text
    positions: list[tuple[str, int]] = []
    for key, header in _SUMMARY_HEADERS:
        idx = remainder.find(header)
        if idx != -1:
            positions.append((key, idx))

    positions.sort(key=lambda pair: pair[1])
    for order, (key, start) in enumerate(positions):
        header = dict(_SUMMARY_HEADERS)[key]
        text_start = start + len(header)
        end = (
            positions[order + 1][1]
            if order + 1 < len(positions)
            else len(remainder)
        )
        sections[key] = remainder[text_start:end].strip()
    return sections


def _paper_payload(row: dict) -> dict:
    """Shape one repository row into the site's paper record.

    Args:
        row: A row from :meth:`PaperRepository.list_approved_papers`.

    Returns:
        The JSON-serialisable paper record.
    """
    return {
        "id": row["id"],
        "title": row["title"],
        "abstract": row["abstract"],
        "authors": row["authors"],
        "doi": row["doi"],
        "source": row["source"],
        "published_date": row["published_date"],
        "score": row["relevance_score"],
        "score_rationale": row["score_rationale"],
        "summary_text": row["summary"],
        "summary": _split_summary(row["summary"]),
        "categories": row["categories"],
        "category_slugs": [_slugify(c) for c in row["categories"]],
        "share_post": share_store.get(row["id"]) or "",
    }


def _build_categories(papers: list[dict]) -> list[dict]:
    """Aggregate the category facet from the exported papers.

    Args:
        papers: The shaped paper records.

    Returns:
        A list of ``{"name", "slug", "count"}`` dicts, most populous first.
    """
    counts: dict[str, int] = {}
    for paper in papers:
        for name in paper["categories"]:
            counts[name] = counts.get(name, 0) + 1
    return [
        {"name": name, "slug": _slugify(name), "count": count}
        for name, count in sorted(
            counts.items(), key=lambda kv: (-kv[1], kv[0])
        )
    ]


def _render_rss(papers: list[dict], settings: Settings, generated: datetime) -> str:
    """Render an RSS 2.0 feed for the most recent approved papers.

    Args:
        papers: The shaped paper records (already score-ordered).
        settings: Application settings supplying the site URL and title.
        generated: The feed build time.

    Returns:
        The XML document as a string.
    """
    base = settings.site_base_url.rstrip("/")
    items: list[str] = []
    for paper in papers[:_RSS_ITEM_LIMIT]:
        link = f"{base}/papers/{paper['id']}"
        summary = paper["summary"].get("industrial_implications") or paper[
            "abstract"
        ]
        pub = ""
        if paper["published_date"]:
            try:
                pub_dt = datetime.fromisoformat(paper["published_date"]).replace(
                    tzinfo=timezone.utc
                )
                pub = f"<pubDate>{format_datetime(pub_dt)}</pubDate>"
            except ValueError:
                pub = ""
        items.append(
            "    <item>\n"
            f"      <title>{escape(paper['title'])}</title>\n"
            f"      <link>{escape(link)}</link>\n"
            f"      <guid isPermaLink=\"true\">{escape(link)}</guid>\n"
            f"      <description>{escape(summary)}</description>\n"
            + (f"      {pub}\n" if pub else "")
            + "    </item>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>{escape(settings.site_title)}</title>\n"
        f"    <link>{escape(base)}</link>\n"
        "    <description>Newly approved Software Engineering research, "
        "summarised and scored for industrial relevance.</description>\n"
        f"    <lastBuildDate>{format_datetime(generated)}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n  </channel>\n</rss>\n"
    )


def export_site_data(repo: PaperRepository, settings: Settings) -> int:
    """Write ``papers.json``, ``categories.json`` and ``rss.xml``.

    Args:
        repo: The repository to read approved papers from.
        settings: Application settings.

    Returns:
        The number of approved papers exported.

    Raises:
        DIPTError: If the database read fails.
        OSError: If a file cannot be written.
    """
    rows = repo.list_approved_papers()
    papers = [_paper_payload(row) for row in rows]
    categories = _build_categories(papers)
    generated = datetime.now(timezone.utc)

    data_dir = _SITE_DIR / "data"
    public_dir = _SITE_DIR / "public"
    data_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": generated.isoformat(),
        "count": len(papers),
        "papers": papers,
    }
    (data_dir / "papers.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (data_dir / "categories.json").write_text(
        json.dumps(categories, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (public_dir / "rss.xml").write_text(
        _render_rss(papers, settings, generated), encoding="utf-8"
    )

    logger.info(
        "Site export: %d approved paper(s), %d categor(y|ies) -> %s",
        len(papers),
        len(categories),
        _SITE_DIR,
    )
    return len(papers)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Unused; present for symmetry with the other entry points.

    Returns:
        Process exit code.
    """
    try:
        settings = get_settings()
    except DIPTError:
        logging.basicConfig(level=logging.ERROR)
        logger.exception("Failed to load configuration")
        return 1

    configure_logging(settings.log_level)
    pool = ConnectionPool(settings)
    try:
        export_site_data(PaperRepository(pool), settings)
    except (DIPTError, OSError):
        logger.exception("Site export failed")
        return 1
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
