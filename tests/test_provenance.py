"""Unit tests for :mod:`dipt.provenance`."""

from __future__ import annotations

import pytest

from dipt import provenance


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    """Point the provenance store at a temporary directory."""
    monkeypatch.setattr(provenance, "_STORE_DIR", tmp_path)
    return tmp_path


def test_record_then_read_back() -> None:
    """A recorded run comes back with every field needed to reproduce it."""
    provenance.record(
        "scoring",
        42,
        model="nemotron:latest",
        prompt_template="rate this {title}",
        temperature=0.0,
        seed=7,
    )

    entries = provenance.history("scoring", paper_id=42)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["model"] == "nemotron:latest"
    assert entry["temperature"] == 0.0
    assert entry["seed"] == 7
    assert entry["prompt_sha"] == provenance.prompt_fingerprint("rate this {title}")


def test_history_filters_by_paper() -> None:
    """Records for other papers are not returned."""
    for pid in (1, 2, 1):
        provenance.record(
            "summarisation", pid, model="m", prompt_template="t", temperature=0.0
        )
    assert len(provenance.history("summarisation", paper_id=1)) == 2
    assert len(provenance.history("summarisation")) == 3


def test_records_append_rather_than_replace() -> None:
    """Re-running a stage keeps the earlier record, so history is auditable."""
    provenance.record("qa", 9, model="a", prompt_template="p", temperature=0.0)
    provenance.record("qa", 9, model="b", prompt_template="p", temperature=0.0)
    assert [e["model"] for e in provenance.history("qa", 9)] == ["a", "b"]


def test_prompt_fingerprint_changes_with_the_prompt() -> None:
    """Editing a prompt must produce a different fingerprint."""
    a = provenance.prompt_fingerprint("original prompt")
    b = provenance.prompt_fingerprint("original prompt ")
    assert a != b
    assert a == provenance.prompt_fingerprint("original prompt")


def test_unknown_stage_is_rejected() -> None:
    """A typo in the stage name must not silently create a stray file."""
    with pytest.raises(ValueError, match="Unknown provenance stage"):
        provenance.history("summarise")


def test_recording_failure_does_not_raise(monkeypatch) -> None:
    """Losing an audit line must never fail the run that produced the artefact."""
    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(provenance.Path, "mkdir", _boom)
    provenance.record("scoring", 1, model="m", prompt_template="p", temperature=0.0)


def test_torn_line_costs_one_record_not_the_file(_store) -> None:
    """A partially written final line is skipped, earlier records survive."""
    provenance.record("promote", 5, model="m", prompt_template="p", temperature=0.0)
    with (_store / "promote.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"paper_id": 6, "model": "tru')
    assert len(provenance.history("promote")) == 1
