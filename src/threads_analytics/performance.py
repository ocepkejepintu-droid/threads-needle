"""Performance dashboard analytics queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from .db import session_scope
from .models import GeneratedIdea, IntakeItem, MyPostInsight, PostOutcome, PostTopic, Topic


# Tier targets from the spec
_TIER_TARGETS = {
    "hero": {"min_views": 5000, "min_replies": 20},
    "engine": {"min_views": 800, "min_replies": 3},
    "signal": {"min_replies": 1},
}


@dataclass
class TierHitRate:
    tier: str
    posts: int
    hit_views: int
    hit_replies: int
    hit_both: int


@dataclass
class MechanicPerformance:
    mechanic: str
    posts: int
    avg_views: float
    avg_replies: float
    avg_likes: float
    breakout_rate: float


@dataclass
class SlotPerformance:
    slot_label: str
    tier: str
    posts: int
    avg_views: float
    avg_replies: float


@dataclass
class TrendTieComparison:
    group: str
    posts: int
    avg_views: float
    avg_replies: float
    avg_likes: float
    breakout_rate: float


@dataclass
class TopicCluster:
    topic: str
    posts: int
    avg_views: float
    avg_replies: float


def _rolling_window(days: int = 30) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def get_tier_hit_rates(account_id: int = 1, days: int = 30) -> list[TierHitRate]:
    """For each tier, what % of posts met their view/reply targets."""
    cutoff = _rolling_window(days)
    results: list[TierHitRate] = []

    with session_scope() as session:
        rows = session.execute(
            select(
                GeneratedIdea.tier,
                func.count(GeneratedIdea.id).label("posts"),
            )
            .where(GeneratedIdea.account_id == account_id)
            .where(GeneratedIdea.status == "published")
            .where(GeneratedIdea.posted_at >= cutoff)
            .where(GeneratedIdea.tier.isnot(None))
            .group_by(GeneratedIdea.tier)
        ).all()

        for tier, posts in rows:
            if not tier:
                continue

            # Get outcomes for this tier
            outcomes = session.execute(
                select(PostOutcome)
                .join(GeneratedIdea, PostOutcome.post_thread_id == GeneratedIdea.thread_id)
                .where(GeneratedIdea.account_id == account_id)
                .where(GeneratedIdea.tier == tier)
                .where(GeneratedIdea.posted_at >= cutoff)
                .where(PostOutcome.outcome_tag.isnot(None))
            ).all()

            targets = _TIER_TARGETS.get(tier, {})
            hit_views = 0
            hit_replies = 0
            hit_both = 0

            for (outcome,) in outcomes:
                views_ok = True
                replies_ok = True
                if "min_views" in targets:
                    views_ok = outcome.views >= targets["min_views"]
                if "min_replies" in targets:
                    replies_ok = outcome.replies >= targets["min_replies"]
                if views_ok:
                    hit_views += 1
                if replies_ok:
                    hit_replies += 1
                if views_ok and replies_ok:
                    hit_both += 1

            results.append(
                TierHitRate(
                    tier=tier,
                    posts=posts,
                    hit_views=hit_views,
                    hit_replies=hit_replies,
                    hit_both=hit_both,
                )
            )

    return results


def get_mechanic_performance(account_id: int = 1, days: int = 30) -> list[MechanicPerformance]:
    """Table: mechanic, posts count, avg views, avg replies, breakout rate."""
    cutoff = _rolling_window(days)
    results: list[MechanicPerformance] = []

    with session_scope() as session:
        rows = session.execute(
            select(
                GeneratedIdea.mechanic,
                func.count(GeneratedIdea.id).label("posts"),
                func.avg(PostOutcome.views).label("avg_views"),
                func.avg(PostOutcome.replies).label("avg_replies"),
                func.avg(PostOutcome.likes).label("avg_likes"),
                func.sum(
                    case((PostOutcome.outcome_tag == "breakout", 1), else_=0)
                ).label("breakouts"),
            )
            .join(
                PostOutcome,
                GeneratedIdea.thread_id == PostOutcome.post_thread_id,
                isouter=True,
            )
            .where(GeneratedIdea.account_id == account_id)
            .where(GeneratedIdea.status == "published")
            .where(GeneratedIdea.posted_at >= cutoff)
            .where(GeneratedIdea.mechanic.isnot(None))
            .group_by(GeneratedIdea.mechanic)
            .order_by(func.avg(PostOutcome.views).desc())
        ).all()

        for row in rows:
            mechanic, posts, avg_views, avg_replies, avg_likes, breakouts = row
            results.append(
                MechanicPerformance(
                    mechanic=mechanic or "unknown",
                    posts=posts or 0,
                    avg_views=round(float(avg_views or 0), 1),
                    avg_replies=round(float(avg_replies or 0), 1),
                    avg_likes=round(float(avg_likes or 0), 1),
                    breakout_rate=round((breakouts or 0) / max(posts, 1) * 100, 1),
                )
            )

    return results


def get_slot_performance(account_id: int = 1, days: int = 30) -> list[SlotPerformance]:
    """Which slots over/underperform vs tier expectation."""
    cutoff = _rolling_window(days)
    results: list[SlotPerformance] = []

    with session_scope() as session:
        rows = session.execute(
            select(
                GeneratedIdea.scheduled_at,
                GeneratedIdea.tier,
                func.count(GeneratedIdea.id).label("posts"),
                func.avg(PostOutcome.views).label("avg_views"),
                func.avg(PostOutcome.replies).label("avg_replies"),
            )
            .join(
                PostOutcome,
                GeneratedIdea.thread_id == PostOutcome.post_thread_id,
                isouter=True,
            )
            .where(GeneratedIdea.account_id == account_id)
            .where(GeneratedIdea.status == "published")
            .where(GeneratedIdea.posted_at >= cutoff)
            .where(GeneratedIdea.scheduled_at.isnot(None))
            .group_by(GeneratedIdea.scheduled_at, GeneratedIdea.tier)
            .order_by(func.avg(PostOutcome.views).desc())
        ).all()

        tz = __import__(
            "threads_analytics.web.routes_content", fromlist=["get_schedule_timezone"]
        ).get_schedule_timezone()

        for scheduled_at, tier, posts, avg_views, avg_replies in rows:
            if not scheduled_at or not tier:
                continue
            local = scheduled_at.astimezone(tz)
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            label = f"{days[local.weekday()]} {local.strftime('%H:%M')} ({tier})"
            results.append(
                SlotPerformance(
                    slot_label=label,
                    tier=tier,
                    posts=posts or 0,
                    avg_views=round(float(avg_views or 0), 1),
                    avg_replies=round(float(avg_replies or 0), 1),
                )
            )

    return results


def get_trend_tie_comparison(account_id: int = 1, days: int = 30) -> list[TrendTieComparison]:
    """Posts with an intake source vs without: do tied posts perform better?"""
    cutoff = _rolling_window(days)
    results: list[TrendTieComparison] = []

    with session_scope() as session:
        for has_intake in (True, False):
            stmt = (
                select(
                    func.count(GeneratedIdea.id).label("posts"),
                    func.avg(PostOutcome.views).label("avg_views"),
                    func.avg(PostOutcome.replies).label("avg_replies"),
                    func.avg(PostOutcome.likes).label("avg_likes"),
                    func.sum(
                        case((PostOutcome.outcome_tag == "breakout", 1), else_=0)
                    ).label("breakouts"),
                )
                .join(
                    PostOutcome,
                    GeneratedIdea.thread_id == PostOutcome.post_thread_id,
                    isouter=True,
                )
                .where(GeneratedIdea.account_id == account_id)
                .where(GeneratedIdea.status == "published")
                .where(GeneratedIdea.posted_at >= cutoff)
            )
            if has_intake:
                stmt = stmt.where(GeneratedIdea.intake_item_id.isnot(None))
            else:
                stmt = stmt.where(GeneratedIdea.intake_item_id.is_(None))

            row = session.execute(stmt).one()
            posts, avg_views, avg_replies, avg_likes, breakouts = row
            results.append(
                TrendTieComparison(
                    group="From intake" if has_intake else "Manual/original",
                    posts=posts or 0,
                    avg_views=round(float(avg_views or 0), 1),
                    avg_replies=round(float(avg_replies or 0), 1),
                    avg_likes=round(float(avg_likes or 0), 1),
                    breakout_rate=round((breakouts or 0) / max(posts, 1) * 100, 1),
                )
            )

    return results


def get_topic_clusters(account_id: int = 1, days: int = 30) -> list[TopicCluster]:
    """Topic clusters with performance per cluster."""
    cutoff = _rolling_window(days)
    results: list[TopicCluster] = []

    with session_scope() as session:
        rows = session.execute(
            select(
                Topic.label,
                func.count(PostTopic.post_thread_id).label("posts"),
                func.avg(PostOutcome.views).label("avg_views"),
                func.avg(PostOutcome.replies).label("avg_replies"),
            )
            .join(PostTopic, Topic.id == PostTopic.topic_id)
            .join(
                GeneratedIdea,
                PostTopic.post_thread_id == GeneratedIdea.thread_id,
            )
            .join(
                PostOutcome,
                GeneratedIdea.thread_id == PostOutcome.post_thread_id,
                isouter=True,
            )
            .where(GeneratedIdea.account_id == account_id)
            .where(GeneratedIdea.status == "published")
            .where(GeneratedIdea.posted_at >= cutoff)
            .group_by(Topic.label)
            .order_by(func.avg(PostOutcome.views).desc())
        ).all()

        for label, posts, avg_views, avg_replies in rows:
            results.append(
                TopicCluster(
                    topic=label,
                    posts=posts or 0,
                    avg_views=round(float(avg_views or 0), 1),
                    avg_replies=round(float(avg_replies or 0), 1),
                )
            )

    return results
