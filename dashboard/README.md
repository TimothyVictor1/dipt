# DIPT dashboard

A Streamlit operator console for the pipeline. Read-mostly, with a few write
actions (resolve a QA flag, edit categories and agent prompts).

## Run

From the project root, with `requirements.txt` installed and `.env` configured
for the database:

```
streamlit run dashboard/app.py
```

It opens on http://localhost:8501.

## Views

| View            | What it shows |
|-----------------|---------------|
| Run pipeline    | Start any stage (`fetch` / `parse` / `categorise` / `summarise` / `score` / `qa` / `all` / `stream`) as a background job with a paper cap, then watch its live log. One run at a time. Runs survive a browser refresh or restart because their state lives in `runs/<id>.{json,log}`. `fetch`'s cap limits how many *new* papers are saved. |
| Schedule        | Turn the automated full-pipeline run on/off. Interval in hours (**default 24**), optional per-cycle paper cap, "Run one cycle now", live daemon + cycle logs, next-run ETA. A background daemon (`scripts/scheduler_daemon.py`) runs `dipt.scheduler` every N hours; it does **not** survive a reboot (use the OS scheduler for that - see `docs/SCHEDULING.md`). |
| Pipeline status | Paper counts per lifecycle stage, approved / open-flag totals, and recent scheduled runs (needs the `fetch_log` table). |
| Paper browser   | Filter papers by status, sort by score, and open one to see its categories, four-part summary, and score rationale. |
| QA queue        | Papers the QA agent escalated. Each shows the stored summary and score; "Mark resolved" clears the flag so the next QA run re-checks the paper. |
| Settings        | Add / edit / deactivate the 47 categories (used live by the categorisation agent), and edit each agent's prompt. Prompt edits are saved as files under `config/agent_prompts/` and picked up on the next run - no database table, no migration, no privileges. Placeholder validation blocks a save that drops a required `{field}`. |

## How "Run pipeline" works

Each run is launched with `subprocess.Popen` as a detached
`python -m scripts.run_stage <stage> [cap]` process, writing combined
stdout/stderr to `runs/<run_id>.log`. `scripts.run_stage` appends a
`__DIPT_RUN_DONE__ exit=<code>` line when finished, so the dashboard can tell
running / finished / failed apart by reading the log alone - no long-lived
Streamlit state, no blocking the UI. "Stop this run" issues `taskkill /F /T`.

## How "Schedule" works

`Enable` writes `runs/schedule.json` (`enabled`, `interval_hours`,
`cycle_limit`) and spawns `scripts/scheduler_daemon.py`, tracked via
`runs/_daemon.json` / `runs/_daemon.log`. The daemon loops: start one cycle
(`scripts/run_scheduled.py` -> `dipt.scheduler`, recorded as a `scheduled` run),
wait for it, then sleep `interval_hours` - re-reading the interval each loop so
a change applies without a restart. `Disable` drops `runs/_daemon.stop` (the
daemon checks it between and during waits) and kills the daemon + any live
cycle. If a manual run is in progress the daemon waits and retries.

## Notes

- All database access goes through `dipt.database.repository.PaperRepository`;
  the dashboard adds no SQL of its own.
- Category changes take effect on the next categorisation run with no code
  change or restart.
- Agent-prompt overrides live in `config/agent_prompts/<agent>.txt`. Editing
  one from Settings, or deleting the file, takes effect on that stage's next
  run. No database involvement.
- Only the scheduler's run history / incremental fetch needs
  `migrations/001_support_tables.sql` (the `fetch_log` table). Everything else
  works without it.
- The connection pool is created once per Streamlit process
  (`st.cache_resource`), not per rerun.
