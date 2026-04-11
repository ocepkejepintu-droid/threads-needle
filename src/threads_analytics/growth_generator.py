"""Content generator for Growth OS.

Uses Claude to generate post ideas based on:
- Winning patterns from top-performing posts
- User's stylistic signatures (YouProfile)
- Topic coverage
- Predicted performance scoring
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .llm_client import create_llm_client
from .models import ContentPattern, GeneratedIdea, Topic, YouProfile

log = logging.getLogger(__name__)


# =============================================================================
# System Prompt
# =============================================================================

GENERATION_SYSTEM = (
    "You are a creative content strategist who generates post ideas for Threads. "
    "You analyze what works for an account and create fresh ideas that leverage "
    "proven patterns while maintaining the creator's unique voice. You return "
    "structured data with clear, actionable post ideas that sound authentic."
)


# =============================================================================
# Generation Prompt Template
# =============================================================================

GENERATION_PROMPT_TEMPLATE = """Generate {count} post ideas for a Threads account.

ACCOUNT PROFILE:
{core_identity}

TOPICS:
{topics}

WINNING PATTERNS (use these):
{patterns}

STYLISTIC SIGNATURES:
{stylistic_signatures}

Generate {count} distinct post ideas that:
1. Use at least one winning pattern
2. Match the stylistic signatures
3. Cover different topics
4. Sound like the account owner

For each idea, provide:
- Title/name
- Full post text (under 500 chars)
- Which patterns it uses
- Why it should work

Return as JSON array with this structure:
[
  {
    "title": "Short descriptive title",
    "post_text": "The full post content",
    "patterns_used": ["pattern_name_1", "pattern_name_2"],
    "rationale": "Why this should work (1-2 sentences)"
  }
]
"""


# =============================================================================
# Helper Functions
# =============================================================================


def _safe_json(text: str) -> list | dict | None:
    """Extract JSON from LLM response, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
        elif text.startswith("python"):
            text = text[6:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array or object
        start_arr = text.find("[")
        start_obj = text.find("{")

        if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
            end = text.rfind("]")
            if end > start_arr:
                try:
                    return json.loads(text[start_arr : end + 1])
                except json.JSONDecodeError:
                    pass

        if start_obj != -1:
            end = text.rfind("}")
            if end > start_obj:
                try:
                    return json.loads(text[start_obj : end + 1])
                except json.JSONDecodeError:
                    pass

        return None


def _format_patterns(patterns: list[ContentPattern]) -> str:
    """Format patterns for the prompt."""
    lines = []
    for p in patterns:
        line = f"- {p.pattern_name} ({p.pattern_type}): {p.description}"
        lines.append(line)
    return "\n".join(lines) if lines else "No patterns available yet."


def _format_topics(topics: list[Topic]) -> str:
    """Format topics for the prompt."""
    lines = []
    for t in topics:
        line = f"- {t.label}: {t.description}" if t.description else f"- {t.label}"
        lines.append(line)
    return "\n".join(lines) if lines else "No specific topics defined."


def _format_stylistic_signatures(signatures: list[dict[str, Any]] | None) -> str:
    """Format stylistic signatures for the prompt."""
    if not signatures:
        return "No stylistic signatures recorded."
    lines = []
    for sig in signatures:
        signature = sig.get("signature", "Unknown")
        evidence = sig.get("evidence", "")
        line = f"- {signature}"
        if evidence:
            line += f" ({evidence})"
        lines.append(line)
    return "\n".join(lines)


def _get_patterns_by_name(
    session: Session, pattern_names: list[str]
) -> list[ContentPattern]:
    """Get ContentPattern objects by their names."""
    patterns = []
    for name in pattern_names:
        pattern = session.scalar(
            select(ContentPattern).where(ContentPattern.pattern_name == name)
        )
        if pattern:
            patterns.append(pattern)
    return patterns


def _get_pattern_ids_by_name(
    session: Session, pattern_names: list[str]
) -> list[int]:
    """Get ContentPattern IDs by their names."""
    ids = []
    for name in pattern_names:
        pattern = session.scalar(
            select(ContentPattern).where(ContentPattern.pattern_name == name)
        )
        if pattern:
            ids.append(pattern.id)
    return ids


# =============================================================================
# Core Functions
# =============================================================================


def generate_content_ideas(
    session: Session,
    count: int = 5,
    you_profile: YouProfile | None = None,
    patterns: list[ContentPattern] | None = None,
    topics: list[Topic] | None = None,
) -> list[GeneratedIdea]:
    """Generate post ideas using winning patterns.

    Uses Claude to generate ideas that:
    1. Use at least one winning pattern
    2. Match the stylistic signatures
    3. Cover different topics

    Args:
        session: SQLAlchemy session
        count: Number of ideas to generate
        you_profile: User's YouProfile for stylistic guidance
        patterns: List of ContentPattern to use
        topics: List of Topic to cover

    Returns:
        List of GeneratedIdea objects (saved to database)
    """
    settings = get_settings()

    # Fetch defaults if not provided
    if you_profile is None:
        you_profile = session.scalar(select(YouProfile).order_by(YouProfile.run_id.desc()))

    if patterns is None:
        patterns = list(
            session.scalars(
                select(ContentPattern)
                .where(ContentPattern.is_active == True)  # noqa: E712
                .order_by(ContentPattern.confidence_score.desc())
            ).all()
        )

    if topics is None:
        topics = list(session.scalars(select(Topic)).all())

    # Validate we have enough data
    if not you_profile:
        log.warning("No YouProfile found for content generation")
        return []

    if not patterns:
        log.warning("No content patterns found for generation")
        return []

    # Build the prompt
    core_identity = you_profile.core_identity or "No profile available."
    stylistic_signatures = you_profile.stylistic_signatures or []

    prompt = GENERATION_PROMPT_TEMPLATE.format(
        count=count,
        core_identity=core_identity,
        topics=_format_topics(topics),
        patterns=_format_patterns(patterns),
        stylistic_signatures=_format_stylistic_signatures(stylistic_signatures),
    )

    # Call LLM
    try:
        client = create_llm_client()
    except ValueError as e:
        log.warning("LLM client not configured: %s", e)
        return []

    # Select model based on provider
    if settings.llm_provider == "anthropic":
        model = settings.claude_recommender_model
    elif settings.llm_provider == "openrouter":
        model = settings.openrouter_model
    else:
        model = settings.zai_model

    try:
        resp = client.create_message(
            model=model,
            max_tokens=4000,
            system=GENERATION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        data = _safe_json(resp.text)
    except Exception as e:
        log.warning("Content generation LLM call failed: %s", e)
        return []

    if not data or not isinstance(data, list):
        log.warning("Content generation produced no parseable ideas")
        return []

    # Convert generation results to GeneratedIdea objects
    ideas: list[GeneratedIdea] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        # Get pattern names from the generation
        pattern_names = item.get("patterns_used", [])
        if isinstance(pattern_names, str):
            pattern_names = [pattern_names]

        # Map pattern names to IDs
        pattern_ids = _get_pattern_ids_by_name(session, pattern_names)

        # Create the idea
        idea = create_idea_from_generation(
            session=session,
            generation_result=item,
            patterns_used=pattern_ids,
        )
        ideas.append(idea)

    log.info("Generated %d content ideas", len(ideas))
    return ideas


def predict_performance(idea: GeneratedIdea) -> tuple[int, str]:
    """Predict 0-100 score and views range.

    Score based on:
    - Patterns used (each strong pattern = +15 points)
    - Multiple patterns bonus (+10 for 2+)
    - Baseline 50

    Args:
        idea: GeneratedIdea to predict performance for

    Returns:
        Tuple of (score 0-100, views range string)
    """
    score = 50  # Baseline
    patterns_used = idea.patterns_used or []

    # +15 points per pattern
    score += len(patterns_used) * 15

    # +10 bonus for 2+ patterns
    if len(patterns_used) >= 2:
        score += 10

    # Cap at 100
    score = min(100, score)

    # Determine views range based on score
    if score >= 85:
        views_range = "20k+"
    elif score >= 70:
        views_range = "5k-20k"
    elif score >= 55:
        views_range = "1k-5k"
    else:
        views_range = "500-1k"

    return score, views_range


def create_idea_from_generation(
    session: Session,
    generation_result: dict,
    patterns_used: list[int],
) -> GeneratedIdea:
    """Save generated idea to database.

    Args:
        session: SQLAlchemy session
        generation_result: Dict from LLM with title, post_text, patterns_used, rationale
        patterns_used: List of ContentPattern IDs that were used

    Returns:
        Persisted GeneratedIdea object
    """
    title = generation_result.get("title", "Untitled Idea")
    post_text = generation_result.get("post_text", "")
    rationale = generation_result.get("rationale", "")

    # Build concept from post text and rationale
    concept_parts = []
    if post_text:
        concept_parts.append(post_text)
    if rationale:
        concept_parts.append(f"\n\nWhy it works: {rationale}")
    concept = "\n".join(concept_parts)

    # Create the idea
    idea = GeneratedIdea(
        title=title,
        concept=concept,
        patterns_used=patterns_used,
        created_at=datetime.now(timezone.utc),
        generated_by="ai",
    )

    # Predict performance
    # Note: We need to set patterns_used before calling predict_performance
    score, views_range = predict_performance(idea)
    idea.predicted_score = score
    idea.predicted_views_range = views_range

    session.add(idea)

    return idea
