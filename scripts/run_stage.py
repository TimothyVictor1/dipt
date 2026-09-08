"""Run one pipeline stage as a child process, then mark the log as finished.

The dashboard's "Run pipeline" view launches this so it can tell, by reading
the log file alone, whether a background run is still going, finished cleanly,
or failed. The only thing this adds over ``python -m dipt.pipeline`` is the
final ``__DIPT_RUN_DONE__ exit=<code>`` sentinel line.

Usage (normally invoked by the dashboard, not by hand):

    python -m scripts.run_stage <stage> [limit]
"""

from __future__ import annotations

import sys

from dipt.pipeline import main

_SENTINEL = "__DIPT_RUN_DONE__ exit="


if __name__ == "__main__":
    code = 1
    try:
        code = main(sys.argv[1:])
    except SystemExit as exc:  # argparse errors, etc.
        code = int(exc.code) if isinstance(exc.code, int) else 2
    except BaseException:  # noqa: BLE001 - always leave the sentinel behind
        import traceback

        traceback.print_exc()
        code = 1
    finally:
        print(f"{_SENTINEL}{code}", flush=True)
    sys.exit(code)
