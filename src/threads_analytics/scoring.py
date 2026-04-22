"""Composite KPI scoring for the closed learning loop.

- account_growth_score
- post_outcome_score
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .db import session_scope
from .models import MyAccountInsight, MyPostInsight

log = logging.getLogger(__name__)


def _z_score(value: float, mean: float, std: float) -> float:
    if std == 0:
        return 0.0
    return (value - mean) / std


def _account_insight_window(account_id: int, days: int = 30) -> list[MyAccountInsight]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as session:
        return list(
            session.scalars(
                select(MyAccountInsight)
                .where(MyAccountInsight.account_id == account_id)
                .where(MyAccountInsight.fetched_at >= since)
                .order_by(MyAccountInsight.fetched_at.desc())
            ).all()
        )


def _post_insights_window(account_id: int, days: int = 30) -> list[MyPostInsight]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as session:
        return list(
            session.scalars(
                select(MyPostInsight)
                .where(MyPostInsight.account_id == account_id)
                .where(MyPostInsight.fetched_at >= since)
            ).all()
        )


def account_growth_score(account_id: int) -> dict[str, float]:
    """Compute composite account growth score from recent account insights.

    Returns dict with overall score and component z-scores.
    """
    insights = _account_insight_window(account_id, days=30)
    if len(insights) < 2:
        return {
            "score": 50.0,
            "follower_velocity_z": 0.0,
            "profile_clicks_z": 0.0,
            "views_z": 0.0,
            "conversation_depth_z": 0.0,
        }

    # Compute deltas / velocities between consecutive snapshots
    follower_velocities = []
    profile_clicks = []
    views = []
    conversation_depths = []

    for i in range(len(insights) - 1):
        curr = insights[i]
        prev = insights[i + 1]
        days = max(1, (curr.fetched_at - prev.fetched_at).days) if curr.fetched_at and prev.fetched_at else 1
        follower_velocities.append((curr.follower_count - prev.follower_count) / days)
        profile_clicks.append(curr.profile_clicks or 0)
        views.append(curr.views or 0)
        # conversation depth proxy: replies / views
        depth = (curr.replies / curr.views) if curr.views else 0.0
        conversation_depths.append(depth)

    # z-scores require some distribution; with only one account we use simple normalization
    def _norm(values: list[float]) -> tuple[float, float, float]:
        if not values:
            return 0.0, 0.0, 1.0
        mean = statistics.mean(values)
        std = statistics.stdev(values) if len(values) > 1 else mean or 1.0
        latest = values[0]
        return latest, mean, std

    fv_latest, fv_mean, fv_std = _norm(follower_velocities)
    pc_latest, pc_mean, pc_std = _norm(profile_clicks)
    v_latest, v_mean, v_std = _norm(views)
    cd_latest, cd_mean, cd_std = _norm(conversation_depths)

    fv_z = _z_score(fv_latest, fv_mean, fv_std)
    pc_z = _norm_z = _z_score(pc_latest, pc_mean, pc_std)
    v_z = _z_score(v_latest, v_mean, v_std)
    cd_z = _z_score(cd_latest, cd_mean, cd_std)

    # Weighted composite mapped to 0-100
    raw = 0.40 * fv_z + 0.25 * pc_z + 0.20 * v_z + 0.15 * cd_z
    score = 50.0 + (raw * 15.0)  # center at 50, scale so ±3sd ≈ 0-100
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 1),
        "follower_velocity_z": round(fv_z, 2),
        "profile_clicks_z": round(pc_z, 2),
        "views_z": round(v_z, 2),
        "conversation_depth_z": round(cd_z, 2),
    }


def post_outcome_score(account_id: int, post_id: str | None = None) -> dict[str, float]:
    """Compute composite post outcome score.

    If post_id is given, score that specific post against the account's
    trailing 30-day distribution.
    """
    insights = _post_insights_window(account_id, days=30)
    if not insights:
        return {"score": 50.0, "views_z": 0.0, "reply_rate_z": 0.0, "quote_rate_z": 0.0, "repost_rate_z": 0.0, "like_rate_z": 0.0}

    views_list = [i.views or 0 for i in insights]
    reply_rates = [(i.replies / i.views) if i.views else 0.0 for i in insights]
    quote_rates = [(i.quotes / i.views) if i.views else 0.0 for i in insights]
    repost_rates = [(i.reposts / i.views) if i.views else 0.0 for i in insights]
    like_rates = [(i.likes / i.views) if i.views else 0.0 for i in insights]

    def _latest_and_dist(values: list[float] | list[int]) -> tuple[float, float, float]:
        if not values:
            return 0.0, 0.0, 1.0
        mean = statistics.mean(values)
        std = statistics.stdev(values) if len(values) > 1 else mean or 1.0
        return values[0], mean, std

    if post_id:
        with session_scope() as session:
            target = session.scalar(
                select(MyPostInsight)
                .where(MyPostInsight.account_id == account_id)
                .where(MyPostInsight.thread_id == post_id)
                .order_by(MyPostInsight.fetched_at.desc())
                .limit(1)
            )
        if target is None:
            return {"score": 50.0, "views_z": 0.0, "reply_rate_z": 0.0, "quote_rate_z": 0.0, "repost_rate_z": 0.0, "like_rate_z": 0.0}
        v = target.views or 0
        rr = (target.replies / target.views) if target.views else 0.0
        qr = (target.quotes / target.views) if target.views else 0.0
        rep_r = (target.reposts / target.views) if target.views else 0.0
        lr = (target.likes / target.views) if target.views else 0.0
    else:
        v, _, _ = _latest_and_dist(views_list)
        rr, _, _ = _latest_and_dist(reply_rates)
        qr, _, _ = _latest_and_dist(quote_rates)
        rep_r, _, _ = _latest_and_dist(repost_rates)
        lr, _, _ = _latest_and_dist(like_rates)

    v_z = _z_score(v, statistics.mean(views_list), statistics.stdev(views_list) if len(views_list) > 1 else 1)
    rr_z = _z_score(rr, statistics.mean(reply_rates), statistics.stdev(reply_rates) if len(reply_rates) > 1 else 1)
    qr_z = _z_score(qr, statistics.mean(quote_rates), statistics.stdev(quote_rates) if len(quote_rates) > 1 else 1)
    rep_r_z = _z_score(rep_r, statistics.mean(repost_rates), statistics.stdev(repost_rates) if len(repost_rates) > 1 else 1)
    lr_z = _z_score(lr, statistics.mean(like_rates), statistics.stdev(like_rates) if len(like_rates) > 1 else 1)

    raw = 0.30 * v_z + 0.25 * rr_z + 0.20 * qr_z + 0.15 * rep_r_z + 0.10 * lr_z
    score = 50.0 + (raw * 15.0)
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 1),
        "views_z": round(v_z, 2),
        "reply_rate_z": round(rr_z, 2),
        "quote_rate_z": round(qr_z, 2),
        "repost_rate_z": round(rep_r_z, 2),
        "like_rate_z": round(lr_z, 2),
    }
