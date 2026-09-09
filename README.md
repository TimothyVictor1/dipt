# DIPT

Automated Software Engineering research-paper monitoring pipeline for SERL
Sweden, BTH. Runs entirely on local hardware with local LLMs served by Ollama.

A paper enters as a title + abstract and comes out fully read, categorised,
summarised, and scored. Each stage is a separate agent; every paper carries a
`status` in PostgreSQL so any agent can stop and resume without losing work.

```
Fetch -> Parse -> Categorise -> Summarise -> Score -> QA -> Approved
```

## Setup

```
pip install -r requirements.txt
cp .env.example .env          # then edit DB password etc.
psql -U postgres -d dipt -f migrations/001_support_tables.sql   # optional, once, as owner
python -m pytest tests/ -v
```

`migrations/001_support_tables.sql` adds the `fetch_log` table, which only the
scheduler needs (incremental fetch + run history). Everything else - including
editing agent prompts from the dashboard - works without it. Prompt overrides
are files in `config/agent_prompts/`, not a database table.

## Pipeline CLI

```
python -m dipt.pipeline fetch [N]            # pull new open-access SE papers
                                             #   (N caps how many are saved)
python -m dipt.pipeline parse [N]            # download + extract PDF text
python -m dipt.pipeline categorise [N]       # assign 1-3 of 47 SE categories
python -m dipt.pipeline summarise [N]        # four-part structured summary
python -m dipt.pipeline score [N]            # industrial-relevance score 1-10
python -m dipt.pipeline qa [N]               # consistency check: auto-fix then flag
python -m dipt.pipeline promote [N]          # draft a short LinkedIn / X post per
                                             #   approved paper (shown on the site)
python -m dipt.pipeline all [N]              # every stage in order (batch)
python -m dipt.pipeline stream [N]           # one paper through summarise->score->qa
                                             # at a time, so approvals appear sooner
```

`N` caps how many papers a per-paper stage processes. The 30B-70B models take
tens of seconds per paper, so always test with a small `N` first.

## Scheduler

```
python -m dipt.scheduler [N]
```

Runs the full chain, narrows the fetch window to "since the last successful
run", records the run in `fetch_log`, and refreshes the public-site data.

Two ways to make it recurring:

- **Dashboard → Schedule** view: a background daemon fires a full cycle every N
  hours (default 24), with an optional per-cycle paper cap. Turn it on/off from
  the UI. Does not survive a reboot.
- **OS scheduler** for a reboot-proof headless setup - see
  [docs/SCHEDULING.md](docs/SCHEDULING.md) (Windows Task Scheduler, cron,
  launchd).

## Dashboard

```
streamlit run dashboard/app.py
```

A **Run pipeline** view (start any stage as a background job, with a paper
cap, and watch its log), pipeline status, a paper browser, the QA queue, and a
settings page for categories and agent prompts. Everything the pipeline does
can be driven from here. See [dashboard/README.md](dashboard/README.md).

## Public site

Static Next.js + Fuse.js site of approved papers. Data is exported from the
database by `python -m dipt.site_export` (also run automatically by the
scheduler). See [website/README.md](website/README.md).

Each paper page carries a **Share this paper** panel: the `promote` stage
drafts a short first-person post (LinkedIn / X, no hype, at most two hashtags),
which the panel shows in an editable box with one-click *Share on X* / *Share
on LinkedIn* buttons and a *Copy post + link* button. Drafts are cached as
files in `config/share_posts/` — no database change needed.

## Models

Configured in `.env` (defaults below), and overridable per stage at runtime
from the dashboard's **Settings → Agent models** — no `.env` edit, no restart.
Overrides live in `config/model_overrides.json`; "Reset to .env default" clears
one. **You do not need to `ollama pull` first**: a model that isn't downloaded
yet is fetched automatically — the dashboard downloads it when you save it (with
a progress bar), and the pipeline downloads it on that stage's first run.
Re-saving / "Update model" re-pulls the latest version of a tag.

| Stage | Model | Notes |
|-------|-------|-------|
| Quality gate  | `llama3.1:8b`       | fast SE-relevance gate |
| Categorisation| `llama3.1:70b`      | 47-category assignment |
| Summarisation | `qwen2.5-coder:32b` | JSON mode; see note below |
| Scoring       | `deepseek-r1:70b`   | reasoning model, "virtual CTO" |
| QA            | `llama3.1:70b`      | summary/score consistency check |
| Promote       | `qwen2.5-coder:32b` | drafts the shareable social post |

The brief specifies `command-r-plus` for summarisation. It does not start on
the current HP Z2 Mini iGPU allocation (the 56 GB of weights load, then
llama-server times out allocating the KV cache), and `llama3.1:70b` collapses
into repetition loops on the long summarisation prompt. `qwen2.5-coder:32b`
with Ollama JSON mode produces clean structured summaries and leaves memory
headroom. Revert `MODEL_SUMMARISATION` once the iGPU memory ceiling is raised.

## Layout

```
dipt/            pipeline, agents, sources, models, database, config
  agents/        fetch, parser, categorisation, summarisation, scoring, qa,
                 share (promote), quality_gate, llm_client
  scheduler.py   scheduled full-chain runner with fetch-window narrowing
  site_export.py database -> website/data/*.json + rss.xml
  prompt_store.py  file-backed admin overrides for agent prompts
  model_store.py   file-backed per-stage model overrides
  model_pull.py    download / update Ollama models on demand
  share_store.py   file-backed cache of the per-paper share post
dashboard/       Streamlit operator console (Run pipeline, Schedule, ...)
  runner.py      background pipeline-run manager
  schedule.py    automated-schedule control (daemon on/off, interval, status)
website/         static Next.js + Fuse.js public site
scripts/         run_stage / run_scheduled (job wrappers), scheduler_daemon,
                 qa_demo, rescore
runs/            per-run + daemon state and logs (created at runtime)
config/agent_prompts/   admin prompt overrides, one .txt per agent (created on save)
config/share_posts/     drafted share posts, one <paper_id>.txt (created by promote)
config/model_overrides.json  per-stage model overrides set from the dashboard
migrations/      SQL for the scheduler's fetch_log table (optional)
docs/            scheduling setup notes
tests/           pytest suite, all LLM and DB calls mocked
```
