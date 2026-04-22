"""Account-level timing optimizer and quota-aware planner.

Ranks approved items per account using slot scores and exploit/explore policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .db import session_scope
from .models import ContentPattern, GeneratedIdea, Lead, MyPost, MyPostInsight

log = logging.getLogger(__name__)

# Product defaults
EXPLOIT_PCT = 0.70
MAX_PATTERN_REUSES_14D = 3
HOOK_COOLDOWN_DAYS = 7
SOFT_CAP_POSTS_PER_DAY = 4
SOFT_CAP_REPLIES_PER_DAY = 25


@dataclass
class PlannedItem:
    item_type: str  # "post" or "reply"
    item_id: int
    account_id: int
    scheduled_at: datetime
    score: float
    reason: str = ""


def _slot_scores(session, account_id: int) -> dict[tuple[int, int], float]:
    """Compute median views per (weekday, hour) bucket from trailing 90 days."""
    since = datetime.now(timezone.utc) - timedelta(days=90)
    posts = session.scalars(
        select(MyPost).where(MyPost.account_id == account_id).where(MyPost.created_at >= since)
    ).all()

    if not posts:
        # Fallback to 30 days
        since = datetime.now(timezone.utc) - timedelta(days=30)
        posts = session.scalars(
            select(MyPost).where(MyPost.account_id == account_id).where(MyPost.created_at >= since)
        ).all()

    bucket_views: dict[tuple[int, int], list[int]] = {}
    for post in posts:
        created = post.created_at
        if created is None:
            continue
        # Fetch latest insight
        insight = session.scalar(
            select(MyPostInsight)
            .where(MyPostInsight.thread_id == post.thread_id)
            .where(MyPostInsight.account_id == account_id)
            .order_by(MyPostInsight.fetched_at.desc())
            .limit(1)
        )
        views = insight.views if insight else 0
        wd = created.weekday()
        hr = created.hour
        bucket_views.setdefault((wd, hr), []).append(views)

    if not bucket_views:
        # Neutral defaults: weekday morning slots score slightly higher
        scores = {}
        for wd in range(7):
            for hr in range(24):
                scores[(wd, hr)] = 0.5
        return scores

    median_views = {
        bucket: sorted(view_list)[len(view_list) // 2] if view_list else 0
        for bucket, view_list in bucket_views.items()
    }
    max_median = max(median_views.values()) or 1
    return {bucket: val / max_median for bucket, val in median_views.items()}


def _pattern_usage_14d(session, account_id: int) -> dict[int, int]:
    since = datetime.now(timezone.utc) - timedelta(days=14)
    ideas = session.scalars(
        select(GeneratedIdea)
        .where(GeneratedIdea.account_id == account_id)
        .where(GeneratedIdea.status.in_(["approved", "scheduled", "published"]))
        .where(GeneratedIdea.created_at >= since)
    ).all()

    usage: dict[int, int] = {}
    for idea in ideas:
        for pid in idea.patterns_used or []:
            usage[pid] = usage.get(pid, 0) + 1
    return usage


def _hook_usage_7d(session, account_id: int, exclude_idea_id: int | None = None) -> set[str]:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    stmt = (
        select(GeneratedIdea)
        .where(GeneratedIdea.account_id == account_id)
        .where(GeneratedIdea.status.in_(["approved", "scheduled", "published"]))
        .where(GeneratedIdea.created_at >= since)
    )
    if exclude_idea_id is not None:
        stmt = stmt.where(GeneratedIdea.id != exclude_idea_id)
    ideas = session.scalars(stmt).all()

    hooks: set[str] = set()
    for idea in ideas:
        text = (idea.concept or "").strip()
        hook = text.split("\n")[0].strip()[:120].lower()
        if hook:
            hooks.add(hook)
    return hooks


def _posts_published_today(session, account_id: int) -> int:
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.scalar(
            select(func.count(GeneratedIdea.id))
            .where(GeneratedIdea.account_id == account_id)
            .where(GeneratedIdea.status == "published")
            .where(GeneratedIdea.posted_at >= day_start)
        )
        or 0
    )


def _replies_sent_today(session, account_id: int) -> int:
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.scalar(
            select(func.count(Lead.id))
            .where(Lead.account_id == account_id)
            .where(Lead.status == "sent")
            .where(Lead.sent_at >= day_start)
        )
        or 0
    )


def _score_idea(
    session,
    account_id: int,
    idea: GeneratedIdea,
    slot_scores: dict[tuple[int, int], float],
    pattern_usage: dict[int, int],
    posts_today: int,
) -> tuple[float, str]:
    # Soft cap check
    if posts_today >= SOFT_CAP_POSTS_PER_DAY:
        return 0.0, "Daily post soft cap reached"

    # Pattern fatigue
    fatigue_reasons = []
    for pid in idea.patterns_used or []:
        if pattern_usage.get(pid, 0) >= MAX_PATTERN_REUSES_14D:
            cp = session.get(ContentPattern, pid)
            name = cp.pattern_name if cp else f"pattern-{pid}"
            fatigue_reasons.append(f"{name} overused (max {MAX_PATTERN_REUSES_14D}/14d)")
    if fatigue_reasons:
        return 0.0, "; ".join(fatigue_reasons)

    # Hook cooldown
    hook = (idea.concept or "").split("\n")[0].strip()[:120].lower()
    recent_hooks = _hook_usage_7d(session, account_id, exclude_idea_id=idea.id)
    if hook in recent_hooks:
        return 0.0, "Hook cooldown active (7 days)"

    # Slot score for next available slot (default to next morning 9am)
    proposed = idea.scheduled_at or (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    slot_key = (proposed.weekday(), proposed.hour)
    slot_score = slot_scores.get(slot_key, 0.5)

    # Predicted performance bonus (exploit)
    perf_bonus = min(1.0, (idea.predicted_score or 0) / 100.0)

    # Novelty bonus (explore) — higher for patterns used fewer times
    novelty_bonus = 1.0
    for pid in idea.patterns_used or []:
        uses = pattern_usage.get(pid, 0)
        novelty_bonus *= max(0.2, 1.0 - (uses / MAX_PATTERN_REUSES_14D))

    # 70/30 exploit/explore weighting
    exploit = 0.7 * (slot_score + perf_bonus) / 2.0
    explore = 0.3 * novelty_bonus
    total = exploit + explore

    return total, f"slot={slot_score:.2f} perf={perf_bonus:.2f} novelty={novelty_bonus:.2f}"


def plan_account_items(account_id: int) -> list[PlannedItem]:
    """Rank approved posts and replies for an account."""
    planned: list[PlannedItem] = []
    with session_scope() as session:
        slot_scores = _slot_scores(session, account_id)
        pattern_usage = _pattern_usage_14d(session, account_id)
        # recent_hooks computed per-idea to avoid self-blocking
        posts_today = _posts_published_today(session, account_id)
        replies_today = _replies_sent_today(session, account_id)

        ideas = session.scalars(
            select(GeneratedIdea)
            .where(GeneratedIdea.account_id == account_id)
            .where(GeneratedIdea.status.in_(["approved", "scheduled"]))
            .order_by(GeneratedIdea.predicted_score.desc())
        ).all()

        for idea in ideas:
            score, reason = _score_idea(
                session, account_id, idea, slot_scores, pattern_usage, posts_today
            )
            scheduled = idea.scheduled_at or (
                datetime.now(timezone.utc) + timedelta(days=1)
            ).replace(hour=9, minute=0, second=0, microsecond=0)

            if score > 0:
                posts_today += 1  # anticipate publishing

            planned.append(
                PlannedItem(
                    item_type="post",
                    item_id=idea.id,
                    account_id=account_id,
                    scheduled_at=scheduled,
                    score=score,
                    reason=reason,
                )
            )

        # Approved replies
        leads = session.scalars(
            select(Lead)
            .where(Lead.account_id == account_id)
            .where(Lead.status == "approved")
            .order_by(Lead.id)
        ).all()

        for lead in leads:
            if replies_today >= SOFT_CAP_REPLIES_PER_DAY:
                reason = "Daily reply soft cap reached"
                score = 0.0
            else:
                reason = "approved reply"
                score = 0.5
                replies_today += 1

            planned.append(
                PlannedItem(
                    item_type="reply",
                    item_id=lead.id,
                    account_id=account_id,
                    scheduled_at=datetime.now(timezone.utc),
                    score=score,
                    reason=reason,
                )
            )

    # Sort by score descending
    planned.sort(key=lambda x: x.score, reverse=True)
    return planned
