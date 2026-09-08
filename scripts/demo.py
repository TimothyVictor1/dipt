"""Small helper for the demo runbook so the operator never types a long
one-liner into PowerShell.

    python -m scripts.demo check      pre-flight: DB, data, Ollama, models, env
    python -m scripts.demo ids        the paper ids used in the runbook
    python -m scripts.demo newest     the most recently approved paper + score
    python -m scripts.demo site       what the public website currently shows
    python -m scripts.demo restore    put the two demo papers back to normal
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from dipt.config import get_settings
from dipt.database.connection import ConnectionPool

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_NEED_MODELS = ("llama3.1:8b", "llama3.1:70b", "qwen2.5-coder:32b", "deepseek-r1:70b")
_ENV_KEYS = (
    "OLLAMA_LOAD_TIMEOUT",
    "OLLAMA_KEEP_ALIVE",
    "OLLAMA_CONTEXT_LENGTH",
    "OLLAMA_KV_CACHE_TYPE",
    "OLLAMA_FLASH_ATTENTION",
)
_RESTORE_PAPER = 929


def _pool() -> ConnectionPool:
    return ConnectionPool(get_settings())


def check() -> int:
    """Pre-flight for the demo. Returns 0 if everything looks ready."""
    ok = True
    settings = get_settings()

    print("=== PostgreSQL ===")
    try:
        pool = _pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select status, count(*) from papers "
                "group by status order by 2 desc"
            )
            counts = dict(cur.fetchall())
            cur.execute("select count(*) from papers where status = 'approved'")
            approved = cur.fetchone()[0]
            cur.execute("select count(*) from qa_flags where resolved = false")
            flags = cur.fetchone()[0]
        pool.close()
        print("  papers by status :", counts)
        print(f"  approved papers  : {approved}   (want about 28)")
        print(f"  open QA flags    : {flags}   (want 0 before the demo)")
        if approved < 1:
            print("  -> no approved papers; run the pipeline first")
            ok = False
    except Exception as exc:  # noqa: BLE001 - surface any failure plainly
        print(f"  NOT REACHABLE: {exc}")
        print("  -> is PostgreSQL running? is the password in .env correct?")
        ok = False

    print("\n=== Ollama ===")
    try:
        url = f"{settings.ollama_base_url}/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:
            have = sorted(m["name"] for m in json.load(resp)["models"])
        print("  UP. Models installed:")
        for name in have:
            print("   ", name)
        missing = [
            m for m in _NEED_MODELS if not any(h.startswith(m) for h in have)
        ]
        if missing:
            print("  MISSING required models:", missing)
            ok = False
        else:
            print("  all required models present")
    except Exception as exc:  # noqa: BLE001
        print(f"  DOWN or unreachable: {exc}")
        print("  -> launch the Ollama app, or see step 0.3 in the runbook")
        ok = False

    print("\n=== Tuned Ollama settings (Windows user environment) ===")
    values = _user_env_ollama()
    for key in _ENV_KEYS:
        val = values.get(key) or os.environ.get(key) or "(not set)"
        print(f"  {key} = {val}")
    if any((values.get(k) or os.environ.get(k)) is None for k in _ENV_KEYS):
        print("  -> some are unset; scoring long runs may fail. See step 0.3.")
        ok = False

    print("\n" + ("READY." if ok else "NOT READY - fix the items marked above."))
    return 0 if ok else 1


def _user_env_ollama() -> dict[str, str]:
    """Read persisted OLLAMA_* user environment variables (Windows)."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg  # noqa: PLC0415 - platform-specific

        out: dict[str, str] = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                if name.startswith("OLLAMA_"):
                    out[name] = str(value)
                i += 1
        return out
    except OSError:
        return {}


def ids() -> int:
    """List the paper ids the runbook refers to."""
    pool = _pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select p.id, s.relevance_score, p.title "
            "from papers p join scores s on s.paper_id = p.id "
            "where p.status = 'approved' "
            "order by s.relevance_score desc, p.id limit 8"
        )
        print("Approved papers (id / score / title) - for the Paper browser:")
        for pid, score, title in cur.fetchall():
            print(f"  {pid:>4}  {score}  {title[:64]}")
        cur.execute(
            "select id from papers where status = 'categorised' "
            "order by id limit 3"
        )
        cats = [r[0] for r in cur.fetchall()]
    pool.close()
    print("\nCategorised papers available for the Part 3 live run:", cats)
    print("\nRunbook uses paper 946 (QA auto-fix) and paper 929 (QA escalate).")
    print("If those are not approved above, pick two that are.")
    return 0


def newest() -> int:
    """Show the most recently approved paper and its score."""
    pool = _pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select p.id, s.relevance_score, s.score_rationale, p.title "
            "from papers p join scores s on s.paper_id = p.id "
            "where p.status = 'approved' order by s.scored_at desc limit 1"
        )
        row = cur.fetchone()
    pool.close()
    if row is None:
        print("No approved papers yet.")
        return 1
    pid, score, why, title = row
    print(f"Newest approved: paper {pid}   score {score}/10")
    print(f"Title    : {title}")
    print(f"Rationale: {(why or '')[:260]}...")
    return 0


def site() -> int:
    """Show what the public website currently publishes."""
    data_dir = _PROJECT_ROOT / "website" / "data"
    papers_file = data_dir / "papers.json"
    if not papers_file.exists():
        print("website/data/papers.json not found - run: python -m dipt.site_export")
        return 1
    data = json.loads(papers_file.read_text(encoding="utf-8"))
    print(f"{data['count']} approved papers published "
          f"(generated {data['generated_at'][:19]}). Top 5 by score:\n")
    for paper in sorted(
        data["papers"], key=lambda p: -(p.get("score") or 0)
    )[:5]:
        cats = ", ".join(paper["categories"][:3])
        print(f"  {paper['score']}/10  {paper['title'][:62]}")
        print(f"          {cats}")
    rss = (_PROJECT_ROOT / "website" / "public" / "rss.xml")
    if rss.exists():
        print("\nRSS feed head:\n")
        print(rss.read_text(encoding="utf-8")[:600])
    return 0


def restore() -> int:
    """Undo what the QA demos did: paper 929 back to 'categorised', flags cleared."""
    pool = _pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("update qa_flags set resolved = true where resolved = false")
        cur.execute("delete from summaries where paper_id = %s", (_RESTORE_PAPER,))
        cur.execute("delete from scores where paper_id = %s", (_RESTORE_PAPER,))
        cur.execute(
            "update papers set status = 'categorised' where id = %s",
            (_RESTORE_PAPER,),
        )
    pool.close()
    print(f"Restored. Paper {_RESTORE_PAPER} is back to 'categorised'; "
          "all QA flags cleared.")
    return 0


_COMMANDS = {
    "check": check,
    "ids": ids,
    "newest": newest,
    "site": site,
    "restore": restore,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in _COMMANDS:
        print(__doc__)
        return 2
    return _COMMANDS[args[0]]()


if __name__ == "__main__":
    sys.exit(main())
