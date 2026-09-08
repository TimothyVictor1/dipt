"""DIPT operator dashboard.

Run from the project root with:

    streamlit run dashboard/app.py

Views, chosen from the sidebar:

* Run pipeline   - start fetch / parse / categorise / summarise / score / qa /
                   all / stream as background jobs, with a paper cap, and watch
                   their logs.
* Schedule       - turn the automated full-pipeline run on/off (every N hours,
                   default 24), set a per-cycle paper cap, kick a one-off
                   cycle, and watch the daemon.
* Pipeline status - paper counts per lifecycle stage and recent runs.
* Paper browser  - filter papers and inspect categories, summary, and score.
* QA queue       - review and clear papers the QA agent escalated.
* Settings       - manage the category list and the agent prompts, no code
                   change required.

Rendering deliberately avoids ``st.dataframe`` / ``st.bar_chart`` (they pull in
pyarrow, which is not always loadable on the locked-down production host) in
favour of Markdown tables and text bars.
"""

from __future__ import annotations

import streamlit as st

from dipt import prompt_store
from dipt.exceptions import DIPTError
from dashboard import runner, schedule
from dashboard.data import (
    AGENT_PROMPTS,
    STATUS_ORDER,
    get_repository,
    missing_format_fields,
)

st.set_page_config(page_title="DIPT dashboard", page_icon="📄", layout="wide")


def _repo():
    """Return the shared repository, stopping the app cleanly on failure."""
    try:
        return get_repository()
    except DIPTError as exc:
        st.error(f"Could not connect to the database: {exc}")
        st.stop()


def _cell(value) -> str:
    """Render one table cell as safe single-line Markdown."""
    if value is None:
        return ""
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= 140 else text[:137] + "..."


def _table(rows, columns) -> None:
    """Render a list of dict rows as a Markdown table.

    Args:
        rows: Row dicts.
        columns: Sequence of ``(key, header)`` pairs.
    """
    if not rows:
        st.caption("Nothing to show.")
        return
    header = "| " + " | ".join(h for _, h in columns) + " |"
    rule = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_cell(row.get(k)) for k, _ in columns) + " |"
        for row in rows
    ]
    st.markdown("\n".join([header, rule, *body]))


def _bars(pairs) -> None:
    """Render ``(label, count)`` pairs as a text bar chart."""
    if not pairs:
        st.caption("No data.")
        return
    peak = max(count for _, count in pairs) or 1
    for label, count in pairs:
        filled = "█" * max(1, round(30 * count / peak)) if count else ""
        st.markdown(f"`{label:<12}` {filled} **{count}**")


# ── Run pipeline ──────────────────────────────────────────────────────────
_STAGE_HELP: dict[str, str] = {
    "fetch": "Pull new open-access SE papers from the 7 sources. The cap "
    "limits how many NEW papers are saved this run.",
    "parse": "Download each fetched paper's PDF and extract its text.",
    "categorise": "Assign 1-3 of the 47 SE categories (llama3.1:70b, "
    "~30-60 s/paper).",
    "summarise": "Four-part structured summary (qwen2.5-coder:32b, "
    "~30-90 s/paper).",
    "score": "Industrial-relevance score 1-10 with rationale "
    "(deepseek-r1:70b, ~2-3 min/paper).",
    "qa": "Consistency check: auto-fix, then escalate to the QA queue "
    "(llama3.1:70b, ~20-90 s/paper).",
    "all": "fetch -> parse -> categorise -> summarise -> score -> qa in one "
    "go. The cap applies to every stage.",
    "stream": "Carry each categorised paper through summarise -> score -> qa "
    "one at a time, so approvals appear sooner.",
}


def _fmt_duration(seconds: float) -> str:
    """Render a second count as ``1h 04m`` / ``6m 12s`` / ``42s``."""
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds}s"


_STATUS_ICON = {
    "running": "🟢 running",
    "finished": "✅ finished",
    "failed": "❌ failed",
    "died": "⚠️ stopped",
}


def view_run_pipeline() -> None:
    """Start pipeline stages as background jobs and watch their logs."""
    st.header("Run pipeline")
    st.caption(
        "Runs execute as background processes on the server. You can leave "
        "this page or close the browser; the run keeps going. Only one run at "
        "a time (the local models saturate the GPU)."
    )

    active = runner.active_run()
    if active is not None:
        mins = _fmt_duration(active.duration_s)
        st.info(
            f"**{active.stage}** is running (cap "
            f"{active.limit if active.limit is not None else 'none'}, "
            f"started {active.started_at.astimezone():%H:%M}, {mins} elapsed)."
        )

    col1, col2 = st.columns([1, 1])
    stage = col1.selectbox("Stage", runner.STAGES, index=runner.STAGES.index("summarise"))
    cap_label = (
        "Max new papers to save"
        if stage == "fetch"
        else "Cap for every stage"
        if stage == "all"
        else "Max papers to process"
    )
    limit = col2.number_input(
        cap_label, min_value=1, max_value=1000, value=5, step=1
    )
    st.caption(_STAGE_HELP[stage])

    start_col, refresh_col = st.columns([1, 1])
    if start_col.button("Start run", type="primary", disabled=active is not None):
        try:
            started = runner.start_run(stage, int(limit))
        except runner.RunnerBusy as exc:
            st.warning(f"Cannot start: {exc}.")
        except (ValueError, OSError) as exc:
            st.error(f"Could not start run: {exc}")
        else:
            st.success(f"Started {started.stage} (cap {started.limit}).")
            st.rerun()
    if refresh_col.button("Refresh"):
        st.rerun()

    runs = runner.list_runs(limit=15)
    if not runs:
        st.caption("No runs yet.")
        return

    st.subheader("Recent runs")
    _table(
        [
            {
                "started": r.started_at.astimezone().strftime("%m-%d %H:%M"),
                "stage": r.stage,
                "cap": r.limit if r.limit is not None else "-",
                "status": _STATUS_ICON.get(r.status, r.status),
                "took": _fmt_duration(r.duration_s),
            }
            for r in runs
        ],
        [
            ("started", "Started"),
            ("stage", "Stage"),
            ("cap", "Cap"),
            ("status", "Status"),
            ("took", "Duration"),
        ],
    )

    st.subheader("Run log")
    chosen = st.selectbox(
        "Run",
        [r.run_id for r in runs],
        format_func=lambda rid: next(
            f"{r.stage} @ {r.started_at.astimezone():%m-%d %H:%M} "
            f"({_STATUS_ICON.get(r.status, r.status)})"
            for r in runs
            if r.run_id == rid
        ),
    )
    if chosen:
        sel = next(r for r in runs if r.run_id == chosen)
        if sel.status == "running" and st.button("Stop this run"):
            if runner.stop_run(chosen):
                st.warning("Stop signal sent.")
                st.rerun()
            else:
                st.info("Run already finished.")
        st.code(runner.tail_log(chosen, lines=120), language="log")
        if sel.status == "running":
            st.caption("Still running - click Refresh to update the log.")


# ── Schedule ──────────────────────────────────────────────────────────────
def view_schedule() -> None:
    """Turn the automated full-pipeline schedule on/off and watch it."""
    st.header("Schedule")
    st.caption(
        "When enabled, a background daemon runs the whole pipeline "
        "(fetch -> parse -> categorise -> summarise -> score -> qa -> site "
        "export) every N hours, fetching only papers newer than the last "
        "successful run. It does NOT survive a machine reboot - for that, use "
        "the OS scheduler (see docs/SCHEDULING.md)."
    )

    stat = schedule.status()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Automated schedule", "ON" if stat.enabled else "OFF")
    c2.metric("Interval", f"{stat.interval_hours} h")
    c3.metric(
        "Papers / cycle",
        "all" if stat.cycle_limit is None else str(stat.cycle_limit),
    )
    c4.metric("Daemon", "running" if stat.daemon_running else "stopped")

    if stat.enabled and not stat.daemon_running:
        st.warning(
            "Schedule is ON but the daemon is not running (it may have been "
            "killed or the machine rebooted). Click **Enable / restart** below."
        )

    hcol, lcol = st.columns(2)
    hours = hcol.number_input(
        "Interval (hours)",
        min_value=schedule.MIN_INTERVAL_HOURS,
        max_value=schedule.MAX_INTERVAL_HOURS,
        value=int(stat.interval_hours),
        step=1,
        help="Time between the start of one full run and the next.",
    )
    cap = lcol.number_input(
        "Papers per cycle (0 = whole backlog)",
        min_value=0,
        max_value=1000,
        value=int(stat.cycle_limit or 0),
        step=1,
        help="Per-stage cap for each cycle. 0 runs everything that is due.",
    )
    limit = int(cap) or None

    b1, b2, b3 = st.columns(3)
    if b1.button(
        "Enable / restart" if not stat.daemon_running else "Apply changes",
        type="primary",
    ):
        schedule.enable(int(hours), limit)
        st.success(
            f"Schedule on: every {int(hours)} h, "
            f"{'whole backlog' if limit is None else f'{limit} papers/stage'}. "
            "First cycle starts now."
            if not stat.daemon_running
            else "Settings saved (apply after the current wait)."
        )
        st.rerun()
    if b2.button("Disable", disabled=not (stat.enabled or stat.daemon_running)):
        schedule.disable()
        st.warning("Schedule off. Daemon and any running cycle are being stopped.")
        st.rerun()
    if b3.button("Run one cycle now"):
        try:
            cyc = schedule.run_once(limit)
        except runner.RunnerBusy as exc:
            st.warning(f"Cannot start: {exc}.")
        else:
            st.success(f"Started a one-off cycle ({cyc.run_id}).")
            st.rerun()

    if stat.last_cycle is not None:
        lc = stat.last_cycle
        line = (
            f"**Last cycle:** {lc.started_at.astimezone():%Y-%m-%d %H:%M} - "
            f"{_STATUS_ICON.get(lc.status, lc.status)}"
        )
        if lc.status != "running":
            line += f" after {_fmt_duration(lc.duration_s)}"
        st.markdown(line)
    if stat.next_eta is not None:
        st.markdown(
            f"**Next cycle:** ~{stat.next_eta.astimezone():%Y-%m-%d %H:%M}"
        )

    st.subheader("Recent cycles")
    cycles = schedule.scheduled_cycles(limit=12)
    if cycles:
        _table(
            [
                {
                    "started": r.started_at.astimezone().strftime("%m-%d %H:%M"),
                    "status": _STATUS_ICON.get(r.status, r.status),
                    "took": _fmt_duration(r.duration_s),
                }
                for r in cycles
            ],
            [("started", "Started"), ("status", "Status"), ("took", "Duration")],
        )
        newest = cycles[0]
        with st.expander(f"Log of the latest cycle ({newest.run_id})"):
            st.code(runner.tail_log(newest.run_id, lines=120), language="log")
    else:
        st.caption("No scheduled cycles have run yet.")

    with st.expander("Daemon log"):
        st.code(schedule.daemon_log_tail(lines=80), language="log")
    if st.button("Refresh", key="schedule_refresh"):
        st.rerun()


# ── Pipeline status ────────────────────────────────────────────────────────
def view_pipeline_status() -> None:
    """Show paper counts per lifecycle stage and recent pipeline runs."""
    st.header("Pipeline status")
    repo = _repo()

    try:
        counts = repo.count_by_status()
    except DIPTError as exc:
        st.error(f"Failed to load status counts: {exc}")
        return

    ordered = [(s, counts.get(s, 0)) for s in STATUS_ORDER if s in counts]
    for status, count in sorted(counts.items()):
        if status not in STATUS_ORDER:
            ordered.append((status, count))

    approved = counts.get("approved", 0)
    flagged = 0
    try:
        flagged = len(repo.list_open_qa_flags())
    except DIPTError:
        pass

    col1, col2, col3 = st.columns(3)
    col1.metric("Papers total", sum(counts.values()))
    col2.metric("Approved", approved)
    col3.metric("Open QA flags", flagged)

    if ordered:
        _bars(ordered)
    else:
        st.info("No papers in the database yet.")

    st.subheader("Recent pipeline runs")
    if not repo.support_tables_present():
        st.caption(
            "The fetch-log table is not installed. Apply "
            "migrations/001_support_tables.sql as the database owner to record "
            "scheduled runs here."
        )
        return
    try:
        runs = repo.recent_fetch_runs(limit=15)
    except DIPTError as exc:
        st.warning(f"Could not load run history: {exc}")
        return
    _table(
        runs,
        [
            ("id", "Run"),
            ("started_at", "Started"),
            ("finished_at", "Finished"),
            ("papers_saved", "Saved"),
            ("status", "Status"),
        ],
    )


# ── Paper browser ─────────────────────────────────────────────────────────
def view_paper_browser() -> None:
    """Filter papers and inspect one paper's categories, summary, and score."""
    st.header("Paper browser")
    repo = _repo()

    try:
        counts = repo.count_by_status()
    except DIPTError as exc:
        st.error(f"Failed to load papers: {exc}")
        return

    statuses = ["(any)"] + [s for s in STATUS_ORDER if s in counts]
    left, right = st.columns([1, 1])
    status = left.selectbox("Status", statuses, index=0)
    limit = right.slider("Rows", min_value=10, max_value=200, value=50, step=10)

    try:
        rows = repo.list_papers_overview(
            status=None if status == "(any)" else status, limit=limit
        )
    except DIPTError as exc:
        st.error(f"Failed to list papers: {exc}")
        return

    if not rows:
        st.info("No papers match this filter.")
        return

    _table(
        rows,
        [
            ("id", "ID"),
            ("title", "Title"),
            ("status", "Status"),
            ("relevance_score", "Score"),
            ("categories", "Categories"),
            ("source", "Source"),
        ],
    )

    ids = [r["id"] for r in rows]
    paper_id = st.selectbox("Inspect paper", ids)
    if paper_id is None:
        return

    try:
        detail = repo.get_paper_detail(int(paper_id))
    except DIPTError as exc:
        st.error(f"Failed to load paper {paper_id}: {exc}")
        return
    if detail is None:
        st.warning("That paper no longer exists.")
        return

    st.subheader(detail["title"])
    meta = f"**Status:** {detail['status']}  |  **Source:** {detail['source']}"
    if detail["published_date"]:
        meta += f"  |  **Published:** {detail['published_date']}"
    if detail["doi"]:
        meta += f"  |  **DOI:** {detail['doi']}"
    st.markdown(meta)
    if detail["authors"]:
        st.caption(", ".join(detail["authors"]))

    if detail["categories"]:
        st.markdown("**Categories**")
        st.markdown(
            "\n".join(
                f"- {name} ({conf:.2f})" for name, conf in detail["categories"]
            )
        )
    else:
        st.caption("No categories assigned.")

    score_col, _ = st.columns([1, 3])
    if detail["score"] is not None:
        score_col.metric("Industrial relevance", f"{detail['score']:.1f} / 10")
    else:
        score_col.caption("Not scored yet.")
    if detail["rationale"]:
        st.markdown(f"**Score rationale**\n\n{detail['rationale']}")

    st.markdown("**Summary**")
    st.text(detail["summary"] or "Not summarised yet.")

    with st.expander("Abstract"):
        st.write(detail["abstract"] or "(no abstract)")


# ── QA queue ──────────────────────────────────────────────────────────────
def view_qa_queue() -> None:
    """List escalated papers and let the operator clear a flag after review."""
    st.header("QA queue")
    repo = _repo()

    try:
        flags = repo.list_open_qa_flags()
    except DIPTError as exc:
        st.error(f"Failed to load QA flags: {exc}")
        return

    if not flags:
        st.success("No open QA flags. Every scored paper passed review.")
        return

    st.caption(
        f"{len(flags)} paper(s) awaiting human review. The QA agent already "
        "tried to auto-fix each one."
    )

    for flag in flags:
        with st.container(border=True):
            st.markdown(f"**Paper {flag['paper_id']}** - {flag['title']}")
            st.markdown(
                f"`{flag['flag_type']}`  ·  flagged {flag['flagged_at']}"
            )
            st.write(flag["description"])

            try:
                detail = repo.get_paper_detail(flag["paper_id"])
            except DIPTError:
                detail = None
            if detail is not None:
                with st.expander("Stored summary and score"):
                    if detail["score"] is not None:
                        st.write(f"Score: {detail['score']:.1f} / 10")
                    if detail["rationale"]:
                        st.write(detail["rationale"])
                    st.text(detail["summary"] or "(no summary)")

            if st.button("Mark resolved", key=f"resolve_{flag['id']}"):
                try:
                    repo.resolve_qa_flag(flag["id"])
                except DIPTError as exc:
                    st.error(f"Could not resolve flag: {exc}")
                else:
                    st.success("Flag resolved. It will clear on the next run.")
                    st.rerun()


# ── Settings ──────────────────────────────────────────────────────────────
def _settings_categories(repo) -> None:
    """Render the category-management section."""
    st.subheader("Categories")
    try:
        categories = repo.list_all_categories()
    except DIPTError as exc:
        st.error(f"Failed to load categories: {exc}")
        return

    active = sum(1 for c in categories if c["is_active"])
    st.caption(f"{len(categories)} categories, {active} active.")
    _table(
        categories,
        [
            ("id", "ID"),
            ("name", "Name"),
            ("is_active", "Active"),
            ("paper_count", "Papers"),
            ("description", "Description"),
        ],
    )

    with st.expander("Add a category"):
        new_name = st.text_input("Name", key="new_cat_name")
        new_desc = st.text_area("Description", key="new_cat_desc")
        if st.button("Add category"):
            if not new_name.strip():
                st.warning("A name is required.")
            else:
                try:
                    repo.add_category(new_name, new_desc)
                except DIPTError as exc:
                    st.error(f"Could not add category: {exc}")
                else:
                    st.success(f"Added '{new_name.strip()}'.")
                    st.rerun()

    with st.expander("Edit or deactivate a category"):
        by_id = {c["id"]: c for c in categories}
        chosen = st.selectbox(
            "Category",
            list(by_id),
            format_func=lambda cid: f"{cid} - {by_id[cid]['name']}",
        )
        if chosen is not None:
            current = by_id[chosen]
            name = st.text_input(
                "Name", value=current["name"], key="edit_cat_name"
            )
            desc = st.text_area(
                "Description",
                value=current["description"],
                key="edit_cat_desc",
            )
            is_active = st.checkbox(
                "Active", value=current["is_active"], key="edit_cat_active"
            )
            if st.button("Save category"):
                try:
                    repo.update_category(chosen, name, desc)
                    if is_active != current["is_active"]:
                        repo.set_category_active(chosen, is_active)
                except DIPTError as exc:
                    st.error(f"Could not save category: {exc}")
                else:
                    st.success("Category saved.")
                    st.rerun()


def _settings_prompts() -> None:
    """Render the agent-prompt editing section.

    Overrides are stored as files under ``config/agent_prompts/`` - no database
    table and no special privileges needed. The pipeline picks up a saved
    prompt on its next run.
    """
    st.subheader("Agent prompts")
    st.caption(
        "Edit the prompt an agent sends to its model. Saving writes an "
        "override file; the next run of that stage uses it. Reset removes the "
        "override and the agent falls back to its built-in default."
    )

    labels = {key: label for key, (label, _d, _f) in AGENT_PROMPTS.items()}
    key = st.selectbox(
        "Agent", list(AGENT_PROMPTS), format_func=lambda k: labels[k]
    )
    label, default, required = AGENT_PROMPTS[key]

    override = prompt_store.get(key)
    if override is not None:
        st.caption("This agent currently uses a **custom** prompt.")
    else:
        st.caption("This agent currently uses its **built-in** prompt.")

    st.caption(
        "Must keep these placeholders: "
        + ", ".join("`{" + f + "}`" for f in required)
    )
    text = st.text_area(
        "Prompt template",
        value=override if override is not None else default,
        height=360,
        key=f"prompt_{key}",
    )

    missing = missing_format_fields(text, required)
    if missing:
        st.warning(
            "Missing placeholder(s): "
            + ", ".join("{" + m + "}" for m in missing)
        )

    save_col, reset_col = st.columns(2)
    if save_col.button("Save prompt", disabled=bool(missing)):
        try:
            prompt_store.set(key, text)
        except (ValueError, OSError) as exc:
            st.error(f"Could not save: {exc}")
        else:
            st.success(f"Saved the {label} prompt. It applies on the next run.")
            st.rerun()
    if reset_col.button("Reset to default", disabled=override is None):
        if prompt_store.delete(key):
            st.success(f"{label} prompt reset to the built-in default.")
            st.rerun()
        else:
            st.info("Already using the default.")


def view_settings() -> None:
    """Manage the category list and the agent prompts without code changes."""
    st.header("Settings")
    repo = _repo()
    _settings_categories(repo)
    st.divider()
    _settings_prompts()


VIEWS = {
    "Run pipeline": view_run_pipeline,
    "Schedule": view_schedule,
    "Pipeline status": view_pipeline_status,
    "Paper browser": view_paper_browser,
    "QA queue": view_qa_queue,
    "Settings": view_settings,
}


def main() -> None:
    """Render the sidebar and dispatch to the selected view."""
    st.sidebar.title("DIPT")
    choice = st.sidebar.radio("View", list(VIEWS))
    st.sidebar.caption(
        "Automated SE research-paper monitoring pipeline.\n\n"
        "SERL Sweden, BTH."
    )
    VIEWS[choice]()


main()
