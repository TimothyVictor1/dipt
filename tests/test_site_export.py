"""Unit tests for :mod:`dipt.site_export`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from dipt import site_export
from dipt.models.schemas import PaperSummary


def test_slugify_basic() -> None:
    assert site_export._slugify("AI for Software Engineering (AI4SE)") == (
        "ai-for-software-engineering-ai4se"
    )


def test_slugify_empty_falls_back() -> None:
    assert site_export._slugify("   ") == "uncategorised"


def test_split_summary_round_trips_to_text() -> None:
    original = PaperSummary(
        research_problem="The gap.",
        methodology="The method.",
        key_findings="The findings.",
        industrial_implications="The point.",
    )
    parsed = site_export._split_summary(original.to_text())
    assert parsed["research_problem"] == "The gap."
    assert parsed["methodology"] == "The method."
    assert parsed["key_findings"] == "The findings."
    assert parsed["industrial_implications"] == "The point."


def test_split_summary_handles_empty() -> None:
    assert site_export._split_summary("") == {
        "research_problem": "",
        "methodology": "",
        "key_findings": "",
        "industrial_implications": "",
    }


def _row(**overrides) -> dict:
    row = {
        "id": 1,
        "title": "A Paper",
        "abstract": "Abs.",
        "authors": ["Jane Roe"],
        "doi": "10.1/a",
        "source": "arxiv",
        "published_date": "2026-06-01",
        "relevance_score": 8.0,
        "score_rationale": "Useful.",
        "summary": PaperSummary(
            research_problem="rp",
            methodology="me",
            key_findings="kf",
            industrial_implications="ii",
        ).to_text(),
        "categories": ["Software Testing", "AI4SE"],
    }
    row.update(overrides)
    return row


def test_paper_payload_shapes_record() -> None:
    payload = site_export._paper_payload(_row())
    assert payload["category_slugs"] == ["software-testing", "ai4se"]
    assert payload["summary"]["key_findings"] == "kf"
    assert payload["score"] == 8.0
    assert payload["share_post"] == ""  # none drafted for this row


def test_paper_payload_includes_share_post_when_present(monkeypatch) -> None:
    monkeypatch.setattr(
        site_export.share_store, "get",
        lambda pid: "I found this one interesting." if pid == 1 else None,
    )
    assert site_export._paper_payload(_row(id=1))["share_post"] == (
        "I found this one interesting."
    )
    assert site_export._paper_payload(_row(id=2))["share_post"] == ""


def test_build_categories_counts_and_sorts() -> None:
    papers = [
        site_export._paper_payload(_row(id=1, categories=["A", "B"])),
        site_export._paper_payload(_row(id=2, categories=["A"])),
    ]
    cats = site_export._build_categories(papers)
    assert cats[0]["name"] == "A" and cats[0]["count"] == 2
    assert cats[1]["name"] == "B" and cats[1]["count"] == 1


def test_render_rss_is_wellformed_xml() -> None:
    import xml.dom.minidom as minidom

    settings = MagicMock()
    settings.site_base_url = "https://example.test/"
    settings.site_title = "DIPT Test"
    papers = [site_export._paper_payload(_row(title="Title & <fun>"))]
    from datetime import datetime, timezone

    xml = site_export._render_rss(papers, settings, datetime(2026, 9, 7, tzinfo=timezone.utc))
    # Parses without error and escapes the title.
    minidom.parseString(xml)
    assert "Title &amp; &lt;fun&gt;" in xml
    assert "<link>https://example.test/papers/1</link>" in xml


def test_export_site_data_writes_all_three_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(site_export, "_SITE_DIR", tmp_path)
    repo = MagicMock()
    repo.list_approved_papers.return_value = [_row(id=1), _row(id=2, categories=["AI4SE"])]
    settings = MagicMock()
    settings.site_base_url = "https://example.test"
    settings.site_title = "DIPT"

    count = site_export.export_site_data(repo, settings)

    assert count == 2
    papers_json = json.loads((tmp_path / "data" / "papers.json").read_text("utf-8"))
    assert papers_json["count"] == 2
    assert papers_json["papers"][0]["title"] == "A Paper"
    cats_json = json.loads(
        (tmp_path / "data" / "categories.json").read_text("utf-8")
    )
    assert any(c["slug"] == "ai4se" for c in cats_json)
    assert (tmp_path / "public" / "rss.xml").read_text("utf-8").startswith(
        "<?xml"
    )
