"""Tests for the intake layer."""

from __future__ import annotations

from datetime import datetime, timezone

from threads_analytics.intake.dedupe import dedupe_items
from threads_analytics.intake.fetchers import RawIntakeItem
from threads_analytics.intake.filter import _clean_mechanics, _clean_relevance, _clamp
from threads_analytics.models import GeneratedIdea, IntakeItem


def test_dedupe_by_exact_url():
    items = [
        RawIntakeItem("hn", "https://example.com/a", "Anthropic drops Claude 4", {}, datetime.now(timezone.utc)),
        RawIntakeItem("hn", "https://example.com/a", "Anthropic drops Claude 4 dup", {}, datetime.now(timezone.utc)),
        RawIntakeItem("hn", "https://example.com/b", "OpenAI raises prices 30%", {}, datetime.now(timezone.utc)),
    ]
    result = dedupe_items(items)
    assert len(result) == 2
    assert {r.source_url for r in result} == {"https://example.com/a", "https://example.com/b"}


def test_dedupe_by_similar_title():
    items = [
        RawIntakeItem("hn", "https://a.com", "Claude 3.7 Released", {}, datetime.now(timezone.utc)),
        RawIntakeItem("anthropic", "https://b.com", "Claude 3.7 Released", {}, datetime.now(timezone.utc)),
        RawIntakeItem("openai", "https://c.com", "GPT-5 announcement", {}, datetime.now(timezone.utc)),
    ]
    result = dedupe_items(items)
    assert len(result) == 2


def test_clamp():
    assert _clamp(50, 0, 100) == 50
    assert _clamp(150, 0, 100) == 100
    assert _clamp(-10, 0, 100) == 0
    assert _clamp(None, 0, 100) == 50


def test_clean_mechanics():
    assert _clean_mechanics(["binary_verdict", "invalid", "token_receipt"]) == [
        "binary_verdict",
        "token_receipt",
    ]
    assert _clean_mechanics([]) == []
    assert _clean_mechanics(None) == []


def test_clean_relevance():
    assert _clean_relevance("high") == "high"
    assert _clean_relevance("skip") == "skip"
    assert _clean_relevance("unknown") == "medium"


def test_intake_item_model_defaults(tmp_path, monkeypatch):
    """IntakeItem can be created and persisted."""
    from threads_analytics.db import init_db, session_scope
    from threads_analytics.config import get_settings

    db_path = tmp_path / "test_intake.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    with session_scope() as session:
        item = IntakeItem(
            source="hn",
            source_url="https://example.com/post",
            source_title="Test Post",
            summary="A summary",
            operator_standing_score=75,
            candidate_mechanics=["binary_verdict"],
            relevance="high",
        )
        session.add(item)
        session.flush()
        assert item.id is not None
        assert item.status == "new"
        assert item.expires_at is not None


def test_generated_idea_total_score():
    idea = GeneratedIdea(title="Test", concept="Test")
    # All None -> total_score is None
    assert idea.total_score is None

    idea.rubric_hook_test = 15
    idea.rubric_mechanic_fit = 18
    idea.rubric_operator_standing = 20
    idea.rubric_trend_freshness = 12
    idea.rubric_reply_invitation = 10
    idea.rubric_voice_signature = 8
    assert idea.total_score == 83
