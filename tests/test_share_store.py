"""Unit tests for :mod:`dipt.share_store`."""

from __future__ import annotations

import pytest

from dipt import share_store


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(share_store, "_STORE_DIR", tmp_path / "share_posts")
    return tmp_path


def test_get_returns_none_when_no_post() -> None:
    assert share_store.get(942) is None
    assert share_store.has(942) is False


def test_set_then_get_round_trips_and_creates_dir(_tmp_store) -> None:
    share_store.set(942, "I came across a neat paper on kernel isolation.")
    assert share_store.has(942) is True
    assert share_store.get(942).startswith("I came across")
    assert (_tmp_store / "share_posts" / "942.txt").is_file()


def test_blank_text_is_rejected() -> None:
    with pytest.raises(ValueError):
        share_store.set(1, "   \n  ")


def test_tidy_strips_label_and_wrapping_quotes() -> None:
    share_store.set(7, 'Post: "This paper looks at flaky tests and why they hurt."')
    assert share_store.get(7) == "This paper looks at flaky tests and why they hurt."


def test_tidy_caps_length() -> None:
    share_store.set(8, "word " * 400)
    assert len(share_store.get(8)) <= 600


def test_delete_removes_and_reports() -> None:
    share_store.set(9, "something")
    assert share_store.delete(9) is True
    assert share_store.get(9) is None
    assert share_store.delete(9) is False
