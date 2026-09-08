"""Run one full scheduled pipeline cycle, then mark the log as finished.

A "scheduled cycle" is exactly what ``python -m dipt.scheduler`` does: the whole
fetch -> parse -> categorise -> summarise -> score -> qa chain, with the fetch
window narrowed to "since the last successful run", plus a site-data export.

The dashboard's schedule daemon launches this once per interval so it can track
each cycle the same way it tracks a manual stage run (via the
``__DIPT_RUN_DONE__ exit=<code>`` sentinel line).

Usage (normally invoked by the daemon, not by hand):

    python -m scripts.run_scheduled
"""

from __future__ import annotations

import sys

from dipt.scheduler import main

_SENTINEL = "__DIPT_RUN_DONE__ exit="


if __name__ == "__main__":
    code = 1
    try:
        code = main(sys.argv[1:])
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
    except BaseException:  # noqa: BLE001 - always leave the sentinel behind
        import traceback

        traceback.print_exc()
        code = 1
    finally:
        print(f"{_SENTINEL}{code}", flush=True)
    sys.exit(code)
