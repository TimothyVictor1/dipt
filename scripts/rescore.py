"""Reset approved papers back to 'summarised' so scoring + QA can re-run.

Use this after tuning the scoring prompt or rubric: it rewinds papers that
already reached 'approved' (or 'scored') to 'summarised', keeping their stored
summary, and clears their score and any resolved QA flags. The next
``python -m dipt.pipeline score N`` / ``qa N`` then re-evaluates them.

Usage, from the project root:

    python -m scripts.rescore --limit 25          # rewind up to 25 papers
    python -m scripts.rescore --ids 926,927,930   # rewind specific papers

It never touches the summary text, so no LLM work is redone for summarisation.
"""

from __future__ import annotations

import argparse
import logging
import sys

from dipt.config import get_settings
from dipt.database.connection import ConnectionPool
from dipt.exceptions import DIPTError
from dipt.logging_config import configure_logging

logger = logging.getLogger(__name__)


def rewind(pool: ConnectionPool, ids: list[int] | None, limit: int | None) -> int:
    """Rewind matching papers to 'summarised'. Returns the number rewound."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            if ids:
                cur.execute(
                    """
                    SELECT id FROM papers
                    WHERE id = ANY(%s)
                      AND status IN ('scored', 'approved')
                      AND EXISTS (SELECT 1 FROM summaries s WHERE s.paper_id = papers.id)
                    """,
                    (ids,),
                )
            else:
                cur.execute(
                    """
                    SELECT id FROM papers
                    WHERE status IN ('scored', 'approved')
                      AND EXISTS (SELECT 1 FROM summaries s WHERE s.paper_id = papers.id)
                    ORDER BY id
                    LIMIT %s
                    """,
                    (limit if limit is not None else 1000,),
                )
            target = [row[0] for row in cur.fetchall()]
            if not target:
                logger.info("No papers to rewind.")
                return 0

            cur.execute("DELETE FROM scores WHERE paper_id = ANY(%s)", (target,))
            cur.execute(
                "DELETE FROM qa_flags WHERE paper_id = ANY(%s)", (target,)
            )
            cur.execute(
                "UPDATE papers SET status = 'summarised' WHERE id = ANY(%s)",
                (target,),
            )
    logger.info(
        "Rewound %d paper(s) to 'summarised': %s",
        len(target),
        ", ".join(str(i) for i in target),
    )
    return len(target)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Rewind papers for re-scoring")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--limit", type=int)
    group.add_argument("--ids", type=str, help="comma-separated paper ids")
    args = parser.parse_args(argv)

    ids = (
        [int(x) for x in args.ids.split(",") if x.strip()]
        if args.ids
        else None
    )

    try:
        settings = get_settings()
    except DIPTError:
        logging.basicConfig(level=logging.ERROR)
        logger.exception("Failed to load configuration")
        return 1

    configure_logging(settings.log_level)
    pool = ConnectionPool(settings)
    try:
        rewind(pool, ids, args.limit)
    except DIPTError:
        logger.exception("Rewind failed")
        return 1
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
