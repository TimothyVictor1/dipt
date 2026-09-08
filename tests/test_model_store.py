"""Unit tests for :mod:`dipt.model_store`."""

from __future__ import annotations

import json

import pytest

from dipt import model_store


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "_STORE_FILE", tmp_path / "model_overrides.json")
    return tmp_path


def test_get_returns_none_when_no_override() -> None:
    assert model_store.get("summarisation") is None


def test_set_then_get_round_trips(_tmp_store) -> None:
    model_store.set("summarisation", "llama3.2:90b")
    assert model_store.get("summarisation") == "llama3.2:90b"
    saved = json.loads((_tmp_store / "model_overrides.json").read_text("utf-8"))
    assert saved == {"summarisation": "llama3.2:90b"}


def test_set_rejects_unknown_stage() -> None:
    with pytest.raises(ValueError):
        model_store.set("translate", "llama3.2:90b")


@pytest.mark.parametrize("bad", ["", "  ", "has space", "bad!tag", ":onlytag"])
def test_set_rejects_bad_tag(bad) -> None:
    with pytest.raises(ValueError):
        model_store.set("scoring", bad)


@pytest.mark.parametrize(
    "good",
    ["llama3.1:70b", "qwen2.5-coder:32b", "gemma2", "hf.co/user/repo:Q4_K_M"],
)
def test_set_accepts_valid_tags(good) -> None:
    model_store.set("scoring", good)
    assert model_store.get("scoring") == good


def test_delete_removes_and_reports() -> None:
    model_store.set("qa", "llama3.3:70b")
    assert model_store.delete("qa") is True
    assert model_store.get("qa") is None
    assert model_store.delete("qa") is False


def test_all_overrides_lists_only_set_stages() -> None:
    assert model_store.all_overrides() == {}
    model_store.set("scoring", "deepseek-r2:70b")
    model_store.set("promote", "qwen3:14b")
    assert model_store.all_overrides() == {
        "promote": "qwen3:14b",
        "scoring": "deepseek-r2:70b",
    }


def test_corrupt_file_is_ignored(_tmp_store) -> None:
    (_tmp_store / "model_overrides.json").write_text("{ not json", encoding="utf-8")
    assert model_store.get("scoring") is None


def test_stage_setting_maps_every_stage() -> None:
    from dipt.config import Settings

    for stage, attr in model_store.STAGE_SETTING.items():
        assert hasattr(Settings, "model_fields")
        assert attr in Settings.model_fields, (stage, attr)
