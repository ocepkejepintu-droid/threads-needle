"""Post-mortem auto-tagger: classify published post outcomes ~24h after publish."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .db import session_scope
from .models import GeneratedIdea, MyPost, MyPostInsight, PostOutcome
from .threads_client import ThreadsClient

log = logging.getLogger(__name__)

# Classification thresholds (configurable via PipelineConfig in future)
_BREAKOUT_REACH_MULT = 5.0
_BREAKOUT_REPLIES = 20
_HEALTHY_REACH_MULT = 1.5
_HEALTHY_REPLIES = 3
_STALL_REACH_MULT_LOW = 0.5
_STALL_REACH_MULT_HIGH = 1.5
_STALL_REPLIES_LOW = 1
_STALL_REPLIES_HIGH = 2


def _median_30d_views(session, account_id: int, before: datetime) -> float:
    """Compute median views from MyPostInsight snapshots in the last 30 days."""
    cutoff = before - timedelta(days=30)
    rows = session.execute(
        select(MyPostInsight.views)
        .where(MyPostInsight.account_id == account_id)
        .where(MyPostInsight.fetched_at >= cutoff)
        .where(MyPostInsight.fetched_at <= before)
        .order_by(MyPostInsight.views)
    ).all()
    views = [r[0] for r in rows if r[0] is not None]
    if not views:
        return 1.0  # avoid div by zero; will yield reach_multiple = views
    n = len(views)
    if n % 2 == 1:
        return float(views[n // 2])
    return (views[n // 2 - 1] + views[n // 2]) / 2.0


def classify_outcome(
    views: int, likes: int, replies: int, reach_multiple: float
) -> str:
    """Return outcome tag based on metrics."""
    if replies == 0:
        return "zero_reply"
    if reach_multiple >= _BREAKOUT_REACH_MULT or replies >= _BREAKOUT_REPLIES:
        return "breakout"
    if reach_multiple >= _HEALTHY_REACH_MULT and replies >= _HEALTHY_REPLIES:
        return "healthy"
    if (
        _STALL_REACH_MULT_LOW <= reach_multiple <= _STALL_REACH_MULT_HIGH
        and _STALL_REPLIES_LOW <= replies <= _STALL_REPLIES_HIGH
    ):
        return "stall"
    # Default: if it has replies but doesn't fit above, call it healthy
    if replies >= 1:
        return "healthy"
    return "zero_reply"


def tag_outcome_for_post(
    idea: GeneratedIdea,
    client: ThreadsClient | None = None,
    force: bool = False,
) -> PostOutcome | None:
    """Fetch latest insights and create a PostOutcome snapshot.

    Returns None if post is not old enough (>24h) or already tagged recently.
    Pass force=True to re-tag.
    """
    if idea.status != "published" or not idea.thread_id:
        return None

    now = datetime.now(timezone.utc)
    posted_at = idea.posted_at
    if posted_at is None:
        return None
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)

    # Only tag posts published 20–28 hours ago (hourly cron window)
    age_hours = (now - posted_at).total_seconds() / 3600
    if not force and not (20 <= age_hours <= 28):
        return None

    with session_scope() as session:
        # Check if already tagged in last 7 days
        if not force:
            existing = session.scalar(
                select(PostOutcome)
                .where(PostOutcome.post_thread_id == idea.thread_id)
                .where(PostOutcome.account_id == idea.account_id)
                .order_by(PostOutcome.snapshot_at.desc())
                .limit(1)
            )
            if existing and (now - existing.snapshot_at).total_seconds() < 7 * 86400:
                return None

        # Fetch insights
        if client is None:
            client = ThreadsClient()
        try:
            insight = client.get_post_insights(idea.thread_id)
        except Exception as exc:
            log.warning("Failed to fetch insights for %s: %s", idea.thread_id, exc)
            return None

        median_views = _median_30d_views(session, idea.account_id, now)
        reach_multiple = insight.views / max(median_views, 1.0)
        reply_to_like = insight.replies / max(insight.likes, 1)

        outcome_tag = classify_outcome(
            views=insight.views,
            likes=insight.likes,
            replies=insight.replies,
            reach_multiple=reach_multiple,
        )

        outcome = PostOutcome(
            account_id=idea.account_id,
            post_thread_id=idea.thread_id,
            views=insight.views,
            likes=insight.likes,
            replies=insight.replies,
            reposts=insight.reposts,
            reply_to_like_ratio=round(reply_to_like, 3),
            reach_multiple=round(reach_multiple, 2),
            outcome_tag=outcome_tag,
        )
        session.add(outcome)

    log.info(
        "Tagged post %s as %s (views=%s likes=%s replies=%s reach_mult=%.2f)",
        idea.thread_id,
        outcome_tag,
        insight.views,
        insight.likes,
        insight.replies,
        reach_multiple,
    )
    return outcome


def run_outcome_tagging_cycle(
    *, account_id: int = 1, force: bool = False
) -> dict[str, int]:
    """Run outcome tagging for all published posts in the ~24h window."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=28)
    window_end = now - timedelta(hours=20)

    with session_scope() as session:
        ideas = session.scalars(
            select(GeneratedIdea)
            .where(GeneratedIdea.account_id == account_id)
            .where(GeneratedIdea.status == "published")
            .where(GeneratedIdea.posted_at >= window_start)
            .where(GeneratedIdea.posted_at <= window_end)
            .where(GeneratedIdea.thread_id.isnot(None))
        ).all()

    client = ThreadsClient()
    tagged = 0
    skipped = 0
    errors = 0

    for idea in ideas:
        try:
            result = tag_outcome_for_post(idea, client=client, force=force)
            if result:
                tagged += 1
            else:
                skipped += 1
        except Exception as exc:
            log.error("Outcome tagging failed for idea %s: %s", idea.id, exc)
            errors += 1

    return {"tagged": tagged, "skipped": skipped, "errors": errors}


def backfill_outcomes(account_id: int = 1) -> dict[str, int]:
    """Backfill outcomes for all published posts."""
    with session_scope() as session:
        ideas = session.scalars(
            select(GeneratedIdea)
            .where(GeneratedIdea.account_id == account_id)
            .where(GeneratedIdea.status == "published")
            .where(GeneratedIdea.thread_id.isnot(None))
            .order_by(GeneratedIdea.posted_at.desc())
        ).all()

    client = ThreadsClient()
    tagged = 0
    errors = 0

    for idea in ideas:
        try:
            result = tag_outcome_for_post(idea, client=client, force=True)
            if result:
                tagged += 1
        except Exception as exc:
            log.error("Backfill failed for idea %s: %s", idea.id, exc)
            errors += 1

    return {"tagged": tagged, "errors": errors}
