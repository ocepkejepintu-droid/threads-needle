"""Canonical idea generation pipeline.

Combines pattern-driven generation, YouProfile context, anti-slop validation,
performance scoring, and experiment-linked generation in one module.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .content_rules import validate_content
from .db import session_scope
from .llm_client import create_llm_client
from .models import ContentPattern, GeneratedIdea, Topic, YouProfile
from .voice_extractor import extract_voice_profile

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "idea_gen.txt"

# Fallback pattern library for remix
FALLBACK_HOOKS = [
    "Susah juga ya {topic}",
    "Gue baru aja {topic}, and it's frustrating",
    "Akhirnya! Setelah {time}, {topic}",
    "Bingung nih, kenapa ya {topic}?",
    "Real talk: {topic} is harder than it looks",
    "Gue kira {topic} gampang, ternyata...",
]

FALLBACK_DETAILS = [
    "Udah 3 minggu gue coba",
    "Baru aja keluar Rp 5 juta untuk ini",
    "Dari 10 kandidat, cuma 1 yang qualified",
    "Conversion rate naik 25% tapi masih ada masalah",
    "Tim gue udah kerja ekstra 10 jam/minggu",
    "Sejak 2023, kita ngalamin ini terus",
]

FALLBACK_CLOSERS = [
    "Ada yang relate? Drop komen",
    "DM gue kalo punya solusi",
    "Gimana pengalaman kalian?",
    "Still figuring this out",
    "Threads, do your magic!",
]


def _load_prompt_template() -> str:
    """Load the prompt template."""
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text()
    # Fallback template
    return """You are a content creator. Generate 3 ideas about {topic}.
Voice: casual Indonesian-English, specific details, relatable struggle.
Format: JSON with title, concept, hook_type."""


def should_generate_ideas(session: Session, threshold: int = 10, account_id: int | None = None) -> bool:
    """Check if the draft idea inventory needs replenishing."""
    stmt = select(func.count(GeneratedIdea.id)).where(GeneratedIdea.status == "draft")
    if account_id is not None:
        stmt = stmt.where(GeneratedIdea.account_id == account_id)
    draft_count = session.scalar(stmt)
    return (draft_count or 0) < threshold


def _format_patterns(patterns: list[ContentPattern] | None) -> str:
    if not patterns:
        return "Hook: Relatable struggle opener\nHook: Curiosity gap tease\n"

    lines = []
    for pattern in patterns[:8]:
        pattern_type = (pattern.pattern_type or "pattern").upper()
        lines.append(
            f"{pattern_type}: {pattern.pattern_name} "
            f"({pattern.example_count} posts, {pattern.avg_views} avg views)"
        )
        if pattern.description:
            lines.append(f"  How: {pattern.description[:160]}")
    return "\n".join(lines)


def _format_topics(topics: list[Topic] | None) -> str:
    if not topics:
        return "- No specific topics defined yet."
    lines = []
    for topic in topics[:8]:
        line = f"- {topic.label}"
        if topic.description:
            line += f": {topic.description}"
        lines.append(line)
    return "\n".join(lines)


def _format_stylistic_signatures(signatures: Any) -> str:
    if not isinstance(signatures, list) or not signatures:
        return "- No stylistic signatures recorded."

    lines = []
    for sig in signatures[:8]:
        if not isinstance(sig, dict):
            continue
        signature = sig.get("signature") or sig.get("trait") or "Unknown"
        evidence = sig.get("evidence") or sig.get("example") or ""
        line = f"- {signature}"
        if evidence:
            line += f" ({evidence})"
        lines.append(line)
    return "\n".join(lines) or "- No stylistic signatures recorded."


def _select_patterns(
    session: Session, patterns: list[ContentPattern] | None, account_id: int | None = None
) -> list[ContentPattern]:
    if patterns is not None:
        return patterns
    stmt = (
        select(ContentPattern)
        .where(ContentPattern.is_active == True)  # noqa: E712
        .order_by(ContentPattern.confidence_score.desc(), ContentPattern.avg_views.desc())
        .limit(8)
    )
    if account_id is not None:
        stmt = stmt.where(ContentPattern.account_id == account_id)
    return list(session.scalars(stmt).all())


def _select_topics(session: Session, topics: list[Topic] | None, account_id: int | None = None) -> list[Topic]:
    if topics is not None:
        return topics
    stmt = select(Topic).order_by(Topic.extracted_at.desc()).limit(8)
    if account_id is not None:
        stmt = stmt.where(Topic.account_id == account_id)
    return list(session.scalars(stmt).all())


def _select_you_profile(session: Session, you_profile: YouProfile | None, account_id: int | None = None) -> YouProfile | None:
    if you_profile is not None:
        return you_profile
    stmt = select(YouProfile).order_by(YouProfile.run_id.desc())
    if account_id is not None:
        stmt = stmt.where(YouProfile.account_id == account_id)
    return session.scalar(stmt)


def _derive_topic(topic: str | None, topics: list[Topic], you_profile: YouProfile | None) -> str:
    if topic:
        return topic
    if topics:
        return ", ".join(t.label for t in topics[:3])
    if you_profile and you_profile.core_identity:
        return you_profile.core_identity[:120]
    return "hiring and remote work"


def _format_prompt(
    topic: str,
    *,
    count: int,
    voice_profile: dict[str, Any] | None = None,
    experiment_instruction: str = "",
    you_profile: YouProfile | None = None,
    patterns: list[ContentPattern] | None = None,
    topics: list[Topic] | None = None,
) -> str:
    """Format prompt with voice profile, YouProfile context, and winning patterns."""
    template = _load_prompt_template()

    if voice_profile is None:
        voice_profile = extract_voice_profile().to_dict()

    examples = voice_profile.get("example_posts", [])
    example_text = examples[0]["text"] if examples else "Susah juga ya cari akuntan..."

    if not experiment_instruction:
        experiment_instruction = (
            f"Generate {count} different thread ideas about this topic: {topic}"
        )

    base_prompt = template.format(
        opening_patterns=", ".join(voice_profile.get("opening_patterns", ["Susah juga ya"])),
        structure=" → ".join(voice_profile.get("structure", ["complaint", "context", "cta"])),
        emoji_frequency=voice_profile.get("emoji_frequency", 0.3),
        indonesian_ratio=voice_profile.get("indonesian_ratio", 0.6),
        exclamation_ratio=voice_profile.get("exclamation_ratio", 0.4),
        example_post=example_text[:200],
        working_patterns=_format_patterns(patterns),
        experiment_instruction=experiment_instruction,
    )

    you_profile_block = "No YouProfile available."
    if you_profile:
        you_profile_block = (
            f"Core identity: {you_profile.core_identity or 'No profile available.'}\n"
            f"Stylistic signatures:\n{_format_stylistic_signatures(you_profile.stylistic_signatures)}"
        )

    structured_block = f"""

ACCOUNT PROFILE (YouProfile):
{you_profile_block}

TOPICS TO COVER:
{_format_topics(topics)}

Return ONLY valid JSON array with exactly {count} objects.
Each object must include:
- "title": short descriptive title
- "concept": the final post draft under 280 characters
- "hook_type": one of complaint, curiosity, excitement, story, or direct
- "patterns_used": array of winning pattern names you intentionally used
- "rationale": 1-2 sentences on why this should work

Avoid markdown fences. Keep each idea distinct and grounded in the creator's real voice.
"""

    return base_prompt + structured_block


def _parse_llm_response(text: str) -> list[dict[str, Any]]:
    """Parse LLM JSON response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as e:
        log.warning("Failed to parse LLM response directly: %s", e)

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    return []


def _generate_with_patterns(topic: str) -> list[dict[str, str]]:
    """Fallback: Generate ideas using pattern remix."""
    ideas = []

    for i in range(3):
        hook = random.choice(FALLBACK_HOOKS).format(
            topic=topic, time=f"{random.randint(1, 4)} minggu"
        )
        detail = random.choice(FALLBACK_DETAILS)
        closer = random.choice(FALLBACK_CLOSERS)

        concept = f"{hook}. {detail}. {closer}"

        ideas.append(
            {
                "title": f"{topic.title()} Struggle",
                "concept": concept,
                "hook_type": random.choice(["complaint", "curiosity", "excitement"]),
            }
        )

    return ideas


def _get_pattern_ids_by_name(session: Session, pattern_names: list[str], account_id: int | None = None) -> list[int]:
    ids: list[int] = []
    for name in pattern_names:
        stmt = select(ContentPattern).where(ContentPattern.pattern_name == name)
        if account_id is not None:
            stmt = stmt.where(ContentPattern.account_id == account_id)
        pattern = session.scalar(stmt)
        if pattern:
            ids.append(pattern.id)
    return ids


def _normalize_idea_payload(
    session: Session,
    payload: dict[str, Any],
    fallback_topic: str,
    account_id: int | None = None,
) -> dict[str, Any]:
    concept = (payload.get("concept") or payload.get("post_text") or "").strip()
    rationale = (payload.get("rationale") or "").strip()
    if rationale and "Why it works:" not in concept:
        concept = f"{concept}\n\nWhy it works: {rationale}" if concept else rationale

    pattern_names = payload.get("patterns_used") or []
    if isinstance(pattern_names, str):
        pattern_names = [pattern_names]
    if not isinstance(pattern_names, list):
        pattern_names = []

    title = (payload.get("title") or f"Idea about {fallback_topic}").strip()

    return {
        "title": title,
        "concept": concept,
        "hook_type": payload.get("hook_type", ""),
        "patterns_used": _get_pattern_ids_by_name(session, [str(name) for name in pattern_names], account_id=account_id),
    }


def predict_performance(
    idea: GeneratedIdea | None = None,
    *,
    patterns_used: list[int] | None = None,
    validation_score: int | None = None,
    text: str | None = None,
) -> tuple[int, str]:
    """Predict score and views range from anti-slop quality + pattern leverage."""
    if idea is not None:
        patterns_used = idea.patterns_used or []
        text = idea.concept

    patterns_used = patterns_used or []

    if validation_score is None and text is not None:
        validation_score = validate_content(text).score

    pattern_score = min(100, 50 + len(patterns_used) * 15 + (10 if len(patterns_used) >= 2 else 0))
    if validation_score is None:
        score = pattern_score
    else:
        score = min(100, round(validation_score * 0.7 + pattern_score * 0.3))

    if score >= 85:
        views_range = "20k+"
    elif score >= 70:
        views_range = "5k-20k"
    elif score >= 55:
        views_range = "1k-5k"
    else:
        views_range = "500-1k"

    return score, views_range


def _persist_generated_ideas(
    session: Session,
    *,
    raw_ideas: list[dict[str, Any]],
    topic: str,
    count: int,
    validate: bool,
    experiment_id: int | None,
    account_id: int | None = None,
) -> list[GeneratedIdea]:
    validated_ideas: list[GeneratedIdea] = []

    for raw_idea in raw_ideas[:count]:
        idea = _normalize_idea_payload(session, raw_idea, topic, account_id=account_id)
        concept = idea["concept"]

        if validate:
            validation = validate_content(concept)
            if not validation.passed and validation.score < 50:
                fallback = _generate_with_patterns(topic)[0]
                concept = fallback["concept"]
                validation = validate_content(concept)
        else:
            validation = validate_content(concept)

        title = idea["title"]
        if experiment_id:
            from .models import Experiment

            exp = session.get(Experiment, experiment_id)
            category = exp.category if exp else "EXP"
            title = f"[{category}] {title}"

        predicted_score, predicted_views_range = predict_performance(
            patterns_used=idea["patterns_used"],
            validation_score=validation.score,
            text=concept,
        )

        generated = GeneratedIdea(
            account_id=account_id,
            title=title,
            concept=concept,
            patterns_used=idea["patterns_used"],
            predicted_score=predicted_score,
            predicted_views_range=predicted_views_range,
            status="draft",
            generated_by="ai",
            experiment_id=experiment_id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(generated)
        session.flush()
        session.refresh(generated)
        validated_ideas.append(generated)

    return validated_ideas


def _generate_ideas_in_session(
    session: Session,
    *,
    topic: str | None,
    count: int,
    validate: bool,
    use_patterns: bool,
    experiment_id: int | None,
    experiment_instruction: str,
    you_profile: YouProfile | None,
    patterns: list[ContentPattern] | None,
    topics: list[Topic] | None,
    account_id: int | None = None,
) -> list[GeneratedIdea]:
    settings = get_settings()
    selected_patterns = _select_patterns(session, patterns, account_id=account_id)
    selected_topics = _select_topics(session, topics, account_id=account_id)
    selected_you_profile = _select_you_profile(session, you_profile, account_id=account_id)
    resolved_topic = _derive_topic(topic, selected_topics, selected_you_profile)

    voice_profile = extract_voice_profile().to_dict() if use_patterns else None

    try:
        client = create_llm_client()
        prompt = _format_prompt(
            resolved_topic,
            count=count,
            voice_profile=voice_profile,
            experiment_instruction=experiment_instruction,
            you_profile=selected_you_profile,
            patterns=selected_patterns,
            topics=selected_topics,
        )

        if settings.llm_provider == "anthropic":
            model = settings.claude_recommender_model
        elif settings.llm_provider == "openrouter":
            model = settings.openrouter_model
        else:
            model = settings.zai_model

        resp = client.create_message(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        raw_ideas = _parse_llm_response(resp.text)
        if not raw_ideas:
            log.warning("LLM returned no parseable ideas, using patterns")
            raw_ideas = _generate_with_patterns(resolved_topic)
    except Exception as e:
        log.warning("LLM generation failed: %s, using patterns", e)
        raw_ideas = _generate_with_patterns(resolved_topic)

    return _persist_generated_ideas(
        session,
        raw_ideas=raw_ideas,
        topic=resolved_topic,
        count=count,
        validate=validate,
        experiment_id=experiment_id,
        account_id=account_id,
    )


def generate_ideas(
    topic: str | None = None,
    count: int = 3,
    validate: bool = True,
    use_patterns: bool = True,
    experiment_id: int | None = None,
    experiment_instruction: str = "",
    session: Session | None = None,
    you_profile: YouProfile | None = None,
    patterns: list[ContentPattern] | None = None,
    topics: list[Topic] | None = None,
    account_id: int | None = None,
) -> list[GeneratedIdea]:
    """Generate content ideas using the canonical idea engine."""
    if session is not None:
        return _generate_ideas_in_session(
            session,
            topic=topic,
            count=count,
            validate=validate,
            use_patterns=use_patterns,
            experiment_id=experiment_id,
            experiment_instruction=experiment_instruction,
            you_profile=you_profile,
            patterns=patterns,
            topics=topics,
            account_id=account_id,
        )

    with session_scope() as managed_session:
        return _generate_ideas_in_session(
            managed_session,
            topic=topic,
            count=count,
            validate=validate,
            use_patterns=use_patterns,
            experiment_id=experiment_id,
            experiment_instruction=experiment_instruction,
            you_profile=you_profile,
            patterns=patterns,
            topics=topics,
            account_id=account_id,
        )


def get_pending_ideas(limit: int = 20) -> list[GeneratedIdea]:
    """Get draft ideas waiting for approval."""
    with session_scope() as session:
        return list(
            session.scalars(
                select(GeneratedIdea)
                .where(GeneratedIdea.status == "draft")
                .order_by(desc(GeneratedIdea.predicted_score))
                .limit(limit)
            ).all()
        )


def schedule_idea(idea_id: int, scheduled_at: datetime) -> bool:
    """Schedule an idea for publishing."""
    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        if not idea:
            return False

        idea.status = "scheduled"
        idea.scheduled_at = scheduled_at
        return True


def dismiss_idea(idea_id: int) -> bool:
    """Reject an idea."""
    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        if not idea:
            return False

        idea.status = "rejected"
        return True


def generate_ideas_from_experiment(experiment_id: int, count: int = 3, account_id: int | None = None) -> list[GeneratedIdea]:
    """Generate content ideas based on an experiment's hypothesis.

    The experiment defines the FORMAT/CONSTRAINT being tested.
    The generated content should follow that constraint while being about
    the user's actual topics (hiring, remote work, etc.) in their natural voice.

    Args:
        experiment_id: The experiment ID to base ideas on
        count: Number of ideas to generate
        account_id: Optional account to scope generated ideas to

    Returns:
        List of GeneratedIdea objects linked to the experiment
    """
    from .experiment_content_mapper import build_experiment_prompt
    from .models import Experiment

    with session_scope() as session:
        experiment = session.get(Experiment, experiment_id)
        if not experiment:
            log.error("Experiment %s not found", experiment_id)
            return []
        if account_id is None:
            account_id = experiment.account_id

    # Build experiment-aware prompt
    instruction, constraints = build_experiment_prompt(experiment)

    # Generate ideas linked to experiment
    ideas = generate_ideas(
        topic=instruction,
        count=count,
        experiment_id=experiment_id,
        experiment_instruction=instruction,
        account_id=account_id,
    )

    log.info(
        "Generated %d ideas for experiment %s (%s)",
        len(ideas),
        experiment_id,
        constraints["category"],
    )
    return ideas


if __name__ == "__main__":
    # Test generation
    ideas = generate_ideas("hiring remote developers", count=3)
    for idea in ideas:
        print(f"\n--- {idea.title} (Score: {idea.predicted_score}) ---")
        print(idea.concept[:200] + "...")
