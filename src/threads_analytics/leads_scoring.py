"""Lead scoring algorithm for Lead Engine v2.

Calculates 0-100 scores for leads based on intent, engagement, profile, and recency.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import Lead, LeadScore

log = logging.getLogger(__name__)

# Scoring weights (must sum to 1.0)
WEIGHTS = {
    "intent": 0.4,
    "engagement": 0.2,
    "profile": 0.2,
    "recency": 0.2,
}

# Intent scores (0-100)
INTENT_SCORES = {
    "service_buyer": 100,
    "founder": 80,
    "job_seeker": 50,
    "other": 20,
    "unclear": 20,
}

# Profile bio keywords that indicate high-value leads
PROFILE_KEYWORDS = ["founder", "ceo", "hiring", "building", "startup"]

# Quality tier thresholds
TIER_HIGH = 80
TIER_MEDIUM = 50


def calculate_intent_score(intent: str | None) -> int:
    """Calculate intent score based on lead intent classification.

    Args:
        intent: The lead's intent classification

    Returns:
        Score from 0-100
    """
    if not intent:
        return INTENT_SCORES["other"]
    return INTENT_SCORES.get(intent, INTENT_SCORES["other"])


def calculate_engagement_score(reply_count: int) -> int:
    """Calculate engagement score based on reply count.

    Args:
        reply_count: Number of replies on the post

    Returns:
        Score from 0-100
    """
    if reply_count > 5:
        return 80
    elif reply_count > 0:
        return 50
    else:
        return 20


def calculate_profile_score(author_bio: str | None) -> int:
    """Calculate profile score based on bio keywords.

    Args:
        author_bio: The author's bio text

    Returns:
        Score from 0-100
    """
    if not author_bio:
        return 0

    bio_lower = author_bio.lower()
    matches = sum(1 for keyword in PROFILE_KEYWORDS if keyword in bio_lower)
    # Each match is worth 25 points, capped at 100
    return min(matches * 25, 100)


def calculate_recency_score(post_created_at: datetime) -> int:
    """Calculate recency score based on how fresh the post is.

    Args:
        post_created_at: When the post was created

    Returns:
        Score from 0-100
    """
    now = datetime.now(timezone.utc)

    # Ensure post_created_at is timezone-aware
    if post_created_at.tzinfo is None:
        post_created_at = post_created_at.replace(tzinfo=timezone.utc)

    hours_old = (now - post_created_at).total_seconds() / 3600

    if hours_old < 1:
        return 100
    elif hours_old < 6:
        return 80
    elif hours_old < 24:
        return 60
    else:
        return 40


def calculate_lead_score(lead: Lead) -> int:
    """Calculate 0-100 score for a lead based on multiple signals.

    Scoring breakdown:
    - Intent (40%): service_buyer=100, founder=80, job_seeker=50, other=20
    - Engagement (20%): based on reply_count (>5=80, >0=50, 0=20)
    - Profile (20%): bio keywords (founder, ceo, hiring, building, startup)
    - Recency (20%): how fresh is the post (<1h=100, <6h=80, <24h=60, else=40)

    Args:
        lead: The Lead object to score

    Returns:
        Total weighted score from 0-100
    """
    # Calculate individual component scores
    intent_score = calculate_intent_score(getattr(lead, "intent", None))
    engagement_score = calculate_engagement_score(lead.reply_count or 0)
    profile_score = calculate_profile_score(lead.author_bio)
    recency_score = calculate_recency_score(lead.post_created_at)

    # Calculate weighted total
    total = (
        intent_score * WEIGHTS["intent"]
        + engagement_score * WEIGHTS["engagement"]
        + profile_score * WEIGHTS["profile"]
        + recency_score * WEIGHTS["recency"]
    )

    return int(total)


def get_quality_tier(total_score: int) -> str:
    """Return quality tier based on total score.

    Args:
        total_score: The lead's total score (0-100)

    Returns:
        'high' (80+), 'medium' (50-79), or 'low' (<50)
    """
    if total_score >= TIER_HIGH:
        return "high"
    elif total_score >= TIER_MEDIUM:
        return "medium"
    else:
        return "low"


def save_lead_score(session: Session, lead: Lead, score: int, tier: str, *, commit: bool = True) -> LeadScore:
    """Save a lead score to the database.

    Args:
        session: Database session
        lead: The Lead being scored
        score: The total calculated score
        tier: The quality tier ('high', 'medium', or 'low')
        commit: Whether to commit immediately (set False for batching)

    Returns:
        The created LeadScore record
    """
    # Calculate component scores for detailed tracking
    intent_score = calculate_intent_score(getattr(lead, "intent", None))
    engagement_score = calculate_engagement_score(lead.reply_count or 0)
    profile_score = calculate_profile_score(lead.author_bio)
    recency_score = calculate_recency_score(lead.post_created_at)

    lead_score = LeadScore(
        lead_id=lead.id,
        intent_score=intent_score,
        engagement_score=engagement_score,
        profile_score=profile_score,
        recency_score=recency_score,
        total_score=score,
        quality_tier=tier,
        computed_at=datetime.now(timezone.utc),
    )

    session.add(lead_score)
    if commit:
        session.commit()

    log.debug(
        "Saved lead score for lead %s: total=%d, tier=%s",
        lead.id,
        score,
        tier,
    )

    return lead_score


def score_all_pending_leads(session: Session) -> int:
    """Find all leads without scores and calculate them.

    Args:
        session: Database session

    Returns:
        Number of leads scored
    """
    # Find leads without existing scores
    scored_lead_ids = session.query(LeadScore.lead_id).subquery()
    pending_leads = (
        session.query(Lead)
        .filter(~Lead.id.in_(scored_lead_ids))
        .all()
    )

    count = 0
    for lead in pending_leads:
        try:
            score = calculate_lead_score(lead)
            tier = get_quality_tier(score)
            save_lead_score(session, lead, score, tier)
            count += 1
        except Exception as exc:
            log.error("Failed to score lead %s: %s", lead.id, exc)
            session.rollback()
            continue

    log.info("Scored %d pending leads", count)
    return count
