"""Growth OS pattern extractor.

Analyzes top-performing posts to identify reusable patterns:
- Hook patterns (contrarian, data, question, story, bold claim)
- Structure patterns (data sandwich, story arc, listicle)
- Timing patterns (day of week, time of day)

These patterns feed into the GeneratedIdea system for AI-assisted content creation.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .config import get_settings
from .llm_client import create_llm_client
from .models import ContentPattern, MyPost, MyPostInsight

log = logging.getLogger(__name__)


# =============================================================================
# Hook Analysis Prompts
# =============================================================================

HOOK_ANALYSIS_SYSTEM = (
    "You are a content strategist who analyzes top-performing social media posts "
    "to identify hook patterns. You look at the first 1-2 sentences of posts that "
    "got high engagement and extract the psychological mechanisms that made people "
    "stop scrolling and read. You are specific, cite evidence, and return structured data."
)

HOOK_ANALYSIS_PROMPT = """Analyze these top-performing posts and identify common hook patterns.

TOP POSTS:
{posts_text}

Identify hook patterns like:
- Contrarian takes (challenging conventional wisdom)
- Data-driven openers (starting with a surprising statistic)
- Question hooks (opening with a provocative question)
- Story-based hooks (starting with a personal anecdote)
- Bold claims (making an assertive, unexpected statement)
- Curiosity gaps (creating information asymmetry)
- Direct address (speaking directly to the reader)
- Hot take (strong opinion on a trending topic)

Return a JSON array of patterns with this structure:
[
  {{
    "pattern_name": "Short descriptive name (2-4 words)",
    "pattern_type": "hook",
    "description": "What this hook does and why it works (1-2 sentences)",
    "examples": ["Example excerpt from the posts"],
    "confidence": 0.0-1.0,
    "frequency": "How often this pattern appears (1-5 scale)"
  }}
]

Only include patterns with confidence >= 0.6. Return 3-6 distinct patterns maximum."""


# =============================================================================
# Structure Analysis
# =============================================================================

STRUCTURE_PATTERNS = {
    "data_sandwich": {
        "name": "Data Sandwich",
        "description": "Statistical opener, interpretation in the middle, actionable takeaway at the end",
        "indicators": [r"\d+%", r"\d+ percent", r"study", r"research", r"data", r"survey"],
    },
    "story_arc": {
        "name": "Story Arc",
        "description": "Personal narrative with setup, conflict/tension, and resolution",
        "indicators": [r"I was", r"I had", r"When I", r"Last week", r"Yesterday", r"Years ago"],
    },
    "listicle": {
        "name": "Listicle",
        "description": "Numbered list format (X ways, Y things, Z reasons)",
        "indicators": [r"^\d+\s+(ways?|things?|reasons?|tips?|lessons?|mistakes?)"],
    },
    "thread_chain": {
        "name": "Thread Chain",
        "description": "Multi-post thread with connected ideas",
        "indicators": [r"\(\d+/\d+\)", r"thread", r"🧵", r"part \d"],
    },
    "hot_take": {
        "name": "Hot Take",
        "description": "Controversial opinion stated upfront with supporting arguments",
        "indicators": [r"unpopular opinion", r"hot take", r"controversial", r"fight me"],
    },
    "how_to": {
        "name": "How-To Guide",
        "description": "Step-by-step instructional format",
        "indicators": [r"how to", r"step \d", r"here['']s how", r"guide to", r"tutorial"],
    },
}


# =============================================================================
# Helper Functions
# =============================================================================


def _safe_json(text: str) -> dict | list | None:
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


def _get_first_sentence(text: str) -> str:
    """Extract the first sentence from text."""
    if not text:
        return ""
    # Split on sentence-ending punctuation
    match = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return match[0] if match else text[:200]


def _count_pattern_occurrences(posts: list[MyPost], indicators: list[str]) -> int:
    """Count how many posts match a set of regex indicators."""
    count = 0
    for post in posts:
        text = (post.text or "").lower()
        for pattern in indicators:
            if re.search(pattern, text, re.IGNORECASE):
                count += 1
                break
    return count


def _calculate_engagement_rate(insight: MyPostInsight | None) -> float:
    """Calculate engagement rate (likes + replies) / views."""
    if not insight or insight.views == 0:
        return 0.0
    return (insight.likes + insight.replies) / insight.views


# =============================================================================
# Core Functions
# =============================================================================


def get_top_performing_posts(
    session: Session,
    account_id: int,
    percentile: float = 0.2,
    min_posts: int = 5,
) -> list[MyPost]:
    """Get top N% of posts by views.

    Args:
        session: SQLAlchemy session
        percentile: Top percentage to select (0.2 = top 20%)
        min_posts: Minimum number of posts to return regardless of percentile

    Returns:
        List of top-performing MyPost objects with insights loaded
    """
    # Get all posts with their latest insights
    posts = session.scalars(select(MyPost).where(MyPost.account_id == account_id)).all()
    if not posts:
        return []

    # Get latest insights for each post
    latest_insights: dict[str, MyPostInsight] = {}
    for ins in session.scalars(
        select(MyPostInsight)
        .where(MyPostInsight.account_id == account_id)
        .order_by(desc(MyPostInsight.fetched_at))
    ).all():
        latest_insights.setdefault(ins.thread_id, ins)

    # Pair posts with their insights and sort by views
    posts_with_views: list[tuple[MyPost, int]] = []
    for post in posts:
        insight = latest_insights.get(post.thread_id)
        views = insight.views if insight else 0
        posts_with_views.append((post, views))

    if not posts_with_views:
        return []

    # Sort by views descending
    posts_with_views.sort(key=lambda x: x[1], reverse=True)

    # Calculate how many posts to take
    total_posts = len(posts_with_views)
    top_n = max(int(total_posts * percentile), min(min_posts, total_posts))

    return [post for post, _ in posts_with_views[:top_n]]


def analyze_hooks(top_posts: list[MyPost]) -> list[dict]:
    """Analyze first sentences for hook patterns using LLM.

    Args:
        top_posts: List of top-performing posts

    Returns:
        List of pattern dictionaries with name, type, description, examples, confidence
    """
    if not top_posts:
        return []

    settings = get_settings()

    # Format posts for the prompt
    posts_text = ""
    for i, post in enumerate(top_posts[:15], 1):  # Limit to 15 for context
        first_sentence = _get_first_sentence(post.text or "")
        posts_text += f'Post {i}:\n"{first_sentence[:200]}"\n\n'

    prompt = HOOK_ANALYSIS_PROMPT.format(posts_text=posts_text)

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
            max_tokens=2000,
            system=HOOK_ANALYSIS_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        data = _safe_json(resp.text)
    except Exception as e:
        log.warning("Hook analysis LLM call failed: %s", e)
        return []

    if not data or not isinstance(data, list):
        log.warning("Hook analysis produced no parseable patterns")
        return []

    # Normalize and validate patterns
    patterns = []
    for item in data:
        if not isinstance(item, dict):
            continue

        confidence = item.get("confidence", 0)
        if isinstance(confidence, str):
            try:
                confidence = float(confidence)
            except ValueError:
                confidence = 0.5

        if confidence < 0.6:
            continue

        pattern = {
            "pattern_name": item.get("pattern_name", "Unknown"),
            "pattern_type": "hook",
            "description": item.get("description", ""),
            "examples": item.get("examples", [])[:3],  # Limit examples
            "confidence": confidence,
            "frequency": item.get("frequency", 3),
        }
        patterns.append(pattern)

    return patterns


def analyze_structure(posts: list[MyPost]) -> list[dict]:
    """Analyze post structure patterns.

    Identifies patterns like:
    - Data sandwich (statistic -> interpretation -> takeaway)
    - Story arc (personal narrative)
    - Listicle (numbered items)
    - Thread chain (multi-part posts)
    - Hot take (controversial opinion)
    - How-to guide (instructional)

    Args:
        posts: List of posts to analyze

    Returns:
        List of pattern dictionaries
    """
    if not posts:
        return []

    patterns = []
    total_posts = len(posts)

    for pattern_id, pattern_def in STRUCTURE_PATTERNS.items():
        count = _count_pattern_occurrences(posts, pattern_def["indicators"])

        if count == 0:
            continue

        frequency = count / total_posts if total_posts > 0 else 0

        # Find example posts for this pattern
        examples = []
        for post in posts:
            text = post.text or ""
            for indicator in pattern_def["indicators"]:
                if re.search(indicator, text, re.IGNORECASE):
                    examples.append(text[:200])
                    break
            if len(examples) >= 3:
                break

        pattern = {
            "pattern_name": pattern_def["name"],
            "pattern_type": "structure",
            "description": pattern_def["description"],
            "examples": examples,
            "confidence": min(0.95, 0.5 + (frequency * 0.5)),  # Scale confidence by frequency
            "frequency": count,
            "frequency_pct": round(frequency * 100, 1),
        }
        patterns.append(pattern)

    # Sort by frequency (descending)
    patterns.sort(key=lambda x: x["frequency"], reverse=True)

    return patterns


def analyze_timing(posts: list[MyPost]) -> list[dict]:
    """Analyze when top posts were published.

    Extracts patterns for:
    - Day of week (which days perform best)
    - Time of day (morning, afternoon, evening, night)

    Args:
        posts: List of posts to analyze

    Returns:
        List of timing pattern dictionaries
    """
    if not posts:
        return []

    patterns = []

    # Day of week analysis
    day_counts = Counter()
    for post in posts:
        if post.created_at:
            day_name = post.created_at.strftime("%A")
            day_counts[day_name] += 1

    if day_counts:
        most_common_day = day_counts.most_common(1)[0]
        day_names = list(day_counts.keys())

        patterns.append(
            {
                "pattern_name": f"Top Day: {most_common_day[0]}",
                "pattern_type": "timing",
                "description": f"Posts published on {', '.join(day_names)} tend to perform best.",
                "examples": [
                    f"{day}: {count} top posts" for day, count in day_counts.most_common()
                ],
                "confidence": 0.7,
                "frequency": most_common_day[1],
                "metadata": {
                    "day_distribution": dict(day_counts),
                    "best_day": most_common_day[0],
                    "best_day_count": most_common_day[1],
                },
            }
        )

    # Time of day analysis
    hour_buckets = {
        "early_morning": (5, 8),  # 5am-8am
        "morning": (8, 12),  # 8am-12pm
        "afternoon": (12, 17),  # 12pm-5pm
        "evening": (17, 21),  # 5pm-9pm
        "night": (21, 24),  # 9pm-12am
        "late_night": (0, 5),  # 12am-5am
    }

    bucket_counts: dict[str, int] = {name: 0 for name in hour_buckets}

    for post in posts:
        if post.created_at:
            hour = post.created_at.hour
            for bucket_name, (start, end) in hour_buckets.items():
                if start <= hour < end:
                    bucket_counts[bucket_name] += 1
                    break

    if any(bucket_counts.values()):
        best_bucket = max(bucket_counts.items(), key=lambda x: x[1])
        bucket_labels = {
            "early_morning": "Early Morning (5-8am)",
            "morning": "Morning (8am-12pm)",
            "afternoon": "Afternoon (12-5pm)",
            "evening": "Evening (5-9pm)",
            "night": "Night (9pm-12am)",
            "late_night": "Late Night (12-5am)",
        }

        if best_bucket[1] > 0:
            patterns.append(
                {
                    "pattern_name": f"Best Time: {bucket_labels[best_bucket[0]]}",
                    "pattern_type": "timing",
                    "description": f"Posts published in the {bucket_labels[best_bucket[0]].lower()} perform best.",
                    "examples": [
                        f"{bucket_labels[name]}: {count} posts"
                        for name, count in sorted(bucket_counts.items(), key=lambda x: -x[1])
                        if count > 0
                    ],
                    "confidence": 0.65,
                    "frequency": best_bucket[1],
                    "metadata": {
                        "time_distribution": bucket_counts,
                        "best_time_slot": best_bucket[0],
                        "best_time_label": bucket_labels[best_bucket[0]],
                    },
                }
            )

    return patterns


def extract_patterns(session: Session, account_id: int) -> list[ContentPattern]:
    """Find patterns in top 20% of posts by views.

    Extracts:
    - Hook patterns (contrarian, data, question, story, bold claim)
    - Structure patterns (data sandwich, story arc, listicle)
    - Timing patterns (day of week, time of day)

    Args:
        session: SQLAlchemy session

    Returns:
        List of persisted ContentPattern objects
    """
    # Get top performing posts
    top_posts = get_top_performing_posts(session, account_id)

    if not top_posts:
        log.info("No posts available for pattern extraction")
        return []

    log.info("Analyzing %d top posts for patterns", len(top_posts))

    # Get insights for performance calculations
    latest_insights: dict[str, MyPostInsight] = {}
    for ins in session.scalars(
        select(MyPostInsight)
        .where(MyPostInsight.account_id == account_id)
        .order_by(desc(MyPostInsight.fetched_at))
    ).all():
        latest_insights.setdefault(ins.thread_id, ins)

    # Calculate aggregate metrics for top posts
    total_views = 0
    total_engagement = 0.0
    for post in top_posts:
        insight = latest_insights.get(post.thread_id)
        if insight:
            total_views += insight.views
            total_engagement += _calculate_engagement_rate(insight)

    avg_views = int(total_views / len(top_posts)) if top_posts else 0
    avg_engagement = total_engagement / len(top_posts) if top_posts else 0.0

    # Analyze different pattern types
    hook_patterns = analyze_hooks(top_posts)
    structure_patterns = analyze_structure(top_posts)
    timing_patterns = analyze_timing(top_posts)

    # Combine all patterns
    all_pattern_data = hook_patterns + structure_patterns + timing_patterns

    # Persist patterns to database
    persisted_patterns: list[ContentPattern] = []

    for pattern_data in all_pattern_data:
        # Check if similar pattern already exists
        existing = session.scalar(
            select(ContentPattern).where(
                ContentPattern.account_id == account_id,
                ContentPattern.pattern_name == pattern_data["pattern_name"],
                ContentPattern.pattern_type == pattern_data["pattern_type"],
            )
        )

        if existing:
            # Update existing pattern
            existing.description = pattern_data.get("description", "")
            existing.example_post_ids = [
                p.thread_id for p in top_posts[: pattern_data.get("frequency", 1)]
            ]
            existing.example_count = pattern_data.get("frequency", 0)
            existing.avg_views = avg_views
            existing.avg_engagement_rate = avg_engagement
            existing.confidence_score = pattern_data.get("confidence", 0.5)
            existing.is_active = True
            existing.extracted_at = datetime.now(timezone.utc)
            persisted_patterns.append(existing)
        else:
            # Create new pattern
            new_pattern = ContentPattern(
                account_id=account_id,
                pattern_type=pattern_data["pattern_type"],
                pattern_name=pattern_data["pattern_name"],
                description=pattern_data.get("description", ""),
                example_post_ids=[
                    p.thread_id for p in top_posts[: pattern_data.get("frequency", 1)]
                ],
                example_count=pattern_data.get("frequency", 0),
                avg_views=avg_views,
                avg_engagement_rate=avg_engagement,
                confidence_score=pattern_data.get("confidence", 0.5),
                is_active=True,
                extracted_at=datetime.now(timezone.utc),
            )
            session.add(new_pattern)
            persisted_patterns.append(new_pattern)

    log.info(
        "Extracted and persisted %d patterns (%d hooks, %d structure, %d timing)",
        len(persisted_patterns),
        len(hook_patterns),
        len(structure_patterns),
        len(timing_patterns),
    )

    return persisted_patterns
