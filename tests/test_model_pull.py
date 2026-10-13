"""Unit tests for :mod:`dipt.model_pull`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import ollama
import pytest

from dipt import model_pull
from dipt.exceptions import ModelPullError

_BASE = "http://x:11434"


def _with_client(inner: MagicMock):
    return patch("dipt.model_pull.ollama.Client", return_value=inner)


# ── list_installed / is_installed ───────────────────────────────────────
def test_list_installed_returns_sorted_names() -> None:
    inner = MagicMock()
    inner.list.return_value = {
        "models": [{"name": "qwen2.5:32b"}, {"name": "llama3.1:8b"}]
    }
    with _with_client(inner):
        assert model_pull.list_installed(_BASE) == ["llama3.1:8b", "qwen2.5:32b"]


def test_list_installed_empty_when_ollama_unreachable() -> None:
    inner = MagicMock()
    inner.list.side_effect = ConnectionError("down")
    with _with_client(inner):
        assert model_pull.list_installed(_BASE) == []


def test_is_installed_exact_and_bare_name() -> None:
    inner = MagicMock()
    inner.list.return_value = {
        "models": [{"name": "llama3.1:70b"}, {"name": "gemma2:latest"}]
    }
    with _with_client(inner):
        assert model_pull.is_installed(_BASE, "llama3.1:70b") is True
        assert model_pull.is_installed(_BASE, "gemma2") is True  # -> gemma2:latest
        assert model_pull.is_installed(_BASE, "llama3.1") is False  # no :latest
        assert model_pull.is_installed(_BASE, "") is False


# ── pull ────────────────────────────────────────────────────────────────
def test_pull_streams_progress_and_completes() -> None:
    inner = MagicMock()
    inner.pull.return_value = iter(
        [
            {"status": "pulling manifest"},
            {"status": "downloading", "completed": 50, "total": 100},
            {"status": "success"},
        ]
    )
    seen: list[tuple[str, int, int]] = []
    with _with_client(inner):
        model_pull.pull(_BASE, "new:1b", on_progress=lambda s, c, t: seen.append((s, c, t)))
    inner.pull.assert_called_once_with("new:1b", stream=True)
    assert ("downloading", 50, 100) in seen


def test_pull_raises_on_registry_error() -> None:
    inner = MagicMock()
    inner.pull.side_effect = ollama.ResponseError("file does not exist")
    with _with_client(inner):
        with pytest.raises(ModelPullError):
            model_pull.pull(_BASE, "does-not-exist:1b")


def test_pull_raises_on_error_line_in_stream() -> None:
    inner = MagicMock()
    inner.pull.return_value = iter([{"error": "pull model manifest: 404"}])
    with _with_client(inner):
        with pytest.raises(ModelPullError):
            model_pull.pull(_BASE, "typo")


def test_pull_rejects_blank_name() -> None:
    with pytest.raises(ModelPullError):
        model_pull.pull(_BASE, "   ")


# ── ensure ──────────────────────────────────────────────────────────────
def test_ensure_skips_when_already_present() -> None:
    inner = MagicMock()
    inner.list.return_value = {"models": [{"name": "here:1b"}]}
    with _with_client(inner):
        assert model_pull.ensure(_BASE, "here:1b") is False
    inner.pull.assert_not_called()


def test_ensure_pulls_when_missing() -> None:
    inner = MagicMock()
    inner.list.return_value = {"models": []}
    inner.pull.return_value = iter([{"status": "success"}])
    with _with_client(inner):
        assert model_pull.ensure(_BASE, "fresh:1b") is True
    inner.pull.assert_called_once()


# ── client response shapes ───────────────────────────────────────────────
class _Model:
    """Typed entry as returned by ollama-python >= 0.4."""

    def __init__(self, model: str) -> None:
        self.model = model


class _ListResponse:
    """Typed response object as returned by ollama-python >= 0.4."""

    def __init__(self, models: list) -> None:
        self.models = models


@pytest.mark.parametrize(
    ("resp", "label"),
    [
        ({"models": [{"name": "a:1"}, {"name": "b:2"}]}, "dict keyed 'name' (<=0.3)"),
        ({"models": [{"model": "a:1"}, {"model": "b:2"}]}, "dict keyed 'model'"),
        (_ListResponse([_Model("a:1"), _Model("b:2")]), "typed ListResponse (>=0.4)"),
    ],
)
def test_list_installed_reads_every_client_shape(monkeypatch, resp, label) -> None:
    """Model detection must survive an ollama-python upgrade.

    Regression test: upgrading the client from 0.3.3 to 0.6.2 changed the
    response from dicts keyed "name" to typed objects exposing ".model".
    Reading only the old shape made list_installed return [] forever, so the
    dashboard reported every installed model as missing.
    """
    monkeypatch.setattr(
        model_pull, "_client", lambda _url: type("C", (), {"list": lambda self: resp})()
    )
    assert model_pull.list_installed("http://x") == ["a:1", "b:2"], label
