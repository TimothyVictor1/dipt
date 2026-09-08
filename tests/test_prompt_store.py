"""Unit tests for :mod:`dipt.prompt_store`."""

from __future__ import annotations

import pytest

from dipt import prompt_store


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """Point the store at a temporary directory."""
    monkeypatch.setattr(prompt_store, "_STORE_DIR", tmp_path / "agent_prompts")
    return tmp_path


def test_get_returns_none_when_no_override() -> None:
    assert prompt_store.get("summarisation") is None


def test_set_then_get_round_trips() -> None:
    prompt_store.set("summarisation", "Summarise: {content}")
    assert prompt_store.get("summarisation") == "Summarise: {content}"


def test_set_creates_the_directory(_tmp_store) -> None:
    prompt_store.set("qa", "check {title} {summary} {score} {rationale}")
    assert (_tmp_store / "agent_prompts" / "qa.txt").is_file()


def test_blank_override_is_treated_as_no_override() -> None:
    with pytest.raises(ValueError):
        prompt_store.set("scoring", "   \n  ")


def test_delete_removes_override_and_reports_true() -> None:
    prompt_store.set("categorisation", "x {category_list} {content} y")
    assert prompt_store.delete("categorisation") is True
    assert prompt_store.get("categorisation") is None


def test_delete_when_absent_reports_false() -> None:
    assert prompt_store.delete("quality_gate") is False


def test_unknown_agent_key_raises() -> None:
    with pytest.raises(ValueError):
        prompt_store.get("not_an_agent")
    with pytest.raises(ValueError):
        prompt_store.set("not_an_agent", "text")


def test_overridden_lists_only_set_agents() -> None:
    assert prompt_store.overridden() == []
    prompt_store.set("scoring", "score {title} {summary}")
    prompt_store.set("qa", "qa {title} {summary} {score} {rationale}")
    assert prompt_store.overridden() == ["qa", "scoring"]


def test_agent_keys_match_the_dashboard_registry() -> None:
    from dashboard.data import AGENT_PROMPTS

    assert set(AGENT_PROMPTS) == prompt_store.AGENT_KEYS
