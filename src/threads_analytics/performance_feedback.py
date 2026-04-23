"""Performance feedback loop — prediction calibration & content learning.

Analyzes predicted vs actual performance for published ideas,
computes accuracy by dimension (mechanic, tier, time slot),
and generates calibration insights for the content pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import desc, func, select, case

from .db import session_scope
from .models import (
    GeneratedIdea,
    MechanicPerformance,
    MyPostInsight,
    PostOutcome,
    PredictionAccuracy,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VIEWS_RANGE_PATTERNS = {
    r"^[\d,]+\+": "high",
    r"^[\d,]+k\+": "high",
    r"^[\d,]+-[\d,]+k": "mid",
    r"^[\d,]+-[\d,]+": "mid",
    r"^[\d,]+k": "mid",
}


def _parse_views_range(range_str: str) -> tuple[int, int] | None:
    """Parse a views range like '1k-5k' or '5k-20k' or '20k+' into (low, high)."""
    s = (range_str or "").strip().lower().replace(",", "")
    if not s:
        return None

    # "20k+"
    m = re.match(r"^(\d+(?:\.\d+)?)k\+$", s)
    if m:
        low = int(float(m.group(1)) * 1000)
        return (low, low * 5)

    # "20k+" without k
    m = re.match(r"^(\d+)\+$", s)
    if m:
        low = int(m.group(1))
        return (low, low * 5)

    # "1k-5k"
    m = re.match(r"^(\d+(?:\.\d+)?)k\s*-\s*(\d+(?:\.\d+)?)k$", s)
    if m:
        return (int(float(m.group(1)) * 1000), int(float(m.group(2)) * 1000))

    # "1000-5000"
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))

    # "5k"
    m = re.match(r"^(\d+(?:\.\d+)?)k$", s)
    if m:
        v = int(float(m.group(1)) * 1000)
        return (v, v * 2)

    return None


def _derive_actual_score(views: int, likes: int, replies: int) -> int:
    """Derive a 0-100 score from raw performance for comparison with predicted_score."""
    # Simple model: views are primary, replies are weighted heavily
    # Calibrated so ~10k views + 100 likes + 20 replies ≈ 70-80
    score = 0
    if views > 0:
        score += min(50, views / 200)
    if likes > 0:
        score += min(25, likes / 4)
    if replies > 0:
        score += min(25, replies * 1.5)
    return int(score)


def _compute_views_error_pct(predicted_range: str, actual_views: int) -> float | None:
    """Compute prediction error as percentage of actual views.

    Positive = overpredicted, negative = underpredicted.
    """
    parsed = _parse_views_range(predicted_range)
    if parsed is None or actual_views <= 0:
        return None
    low, high = parsed
    mid = (low + high) // 2
    return round((mid - actual_views) / actual_views * 100, 1)


def _accuracy_bucket(error_pct: float | None) -> str:
    if error_pct is None:
        return "unknown"
    abs_err = abs(error_pct)
    if abs_err <= 20:
        return "bullseye"
    if abs_err <= 50:
        return "close"
    if abs_err <= 100:
        return "off"
    return "way_off"


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

@dataclass
class BiasReport:
    """Systematic bias detected in predictions for a specific dimension."""

    dimension: str  # "mechanic", "tier", "time_slot", "overall"
    value: str
    sample_size: int
    avg_error_pct: float  # positive = overpredict, negative = underpredict
    accuracy_rate: float  # % in "bullseye" or "close"
    insight: str


@dataclass
class FeedbackReport:
    """Complete performance feedback report for an account."""

    account_id: int
    total_published: int
    accuracy_rate: float  # % bullseye + close
    avg_error_pct: float
    top_performing_mechanic: str = ""
    bottom_performing_mechanic: str = ""
    bias_reports: list[BiasReport] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Accuracy computation
# ---------------------------------------------------------------------------

def compute_prediction_accuracy_for_idea(idea_id: int) -> PredictionAccuracy | None:
    """Compute or refresh prediction accuracy for a single published idea."""
    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        if idea is None or idea.status != "published":
            return None

        # Try PostOutcome first (24h snapshot), fallback to latest MyPostInsight
        post_outcome = session.scalar(
            select(PostOutcome)
            .where(PostOutcome.post_thread_id == idea.thread_id)
            .order_by(desc(PostOutcome.snapshot_at))
            .limit(1)
        )

        if post_outcome:
            actual_views = post_outcome.views
            actual_likes = post_outcome.likes
            actual_replies = post_outcome.replies
            actual_reposts = post_outcome.reposts
            actual_reach_multiple = post_outcome.reach_multiple
            actual_outcome = post_outcome.outcome_tag
        else:
            # Fallback to latest MyPostInsight
            insight = session.scalar(
                select(MyPostInsight)
                .where(MyPostInsight.thread_id == idea.thread_id)
                .order_by(desc(MyPostInsight.fetched_at))
                .limit(1)
            )
            if insight is None:
                log.warning("No performance data for idea %s (thread %s)", idea_id, idea.thread_id)
                return None
            actual_views = insight.views or 0
            actual_likes = insight.likes or 0
            actual_replies = insight.replies or 0
            actual_reposts = insight.reposts or 0
            actual_reach_multiple = None
            actual_outcome = None

        # Compute accuracy metrics
        views_error_pct = _compute_views_error_pct(idea.predicted_views_range, actual_views)
        actual_score = _derive_actual_score(actual_views, actual_likes, actual_replies)
        score_error = idea.predicted_score - actual_score
        bucket = _accuracy_bucket(views_error_pct)

        # Upsert PredictionAccuracy
        existing = session.scalar(
            select(PredictionAccuracy).where(
                PredictionAccuracy.account_id == idea.account_id,
                PredictionAccuracy.idea_id == idea.id,
            )
        )

        if existing:
            pa = existing
        else:
            pa = PredictionAccuracy(account_id=idea.account_id, idea_id=idea.id)
            session.add(pa)

        pa.post_thread_id = idea.thread_id
        pa.predicted_score = idea.predicted_score
        pa.predicted_views_range = idea.predicted_views_range
        pa.predicted_tier = idea.tier
        pa.predicted_mechanic = idea.mechanic
        pa.actual_views = actual_views
        pa.actual_likes = actual_likes
        pa.actual_replies = actual_replies
        pa.actual_reposts = actual_reposts
        pa.actual_reach_multiple = actual_reach_multiple
        pa.actual_outcome_tag = actual_outcome
        pa.views_error_pct = views_error_pct
        pa.score_error = score_error
        pa.accuracy_bucket = bucket
        pa.computed_at = datetime.now(timezone.utc)

        session.flush()
        return pa


def compute_all_prediction_accuracies(account_id: int) -> int:
    """Compute prediction accuracy for all published ideas missing accuracy data."""
    with session_scope() as session:
        # Find published ideas that don't have a PredictionAccuracy record
        subq = (
            select(PredictionAccuracy.idea_id)
            .where(PredictionAccuracy.account_id == account_id)
            .scalar_subquery()
        )
        ideas = session.scalars(
            select(GeneratedIdea)
            .where(
                GeneratedIdea.account_id == account_id,
                GeneratedIdea.status == "published",
                GeneratedIdea.thread_id.is_not(None),
                GeneratedIdea.id.notin_(subq),
            )
        ).all()

    count = 0
    for idea in ideas:
        pa = compute_prediction_accuracy_for_idea(idea.id)
        if pa:
            count += 1

    log.info("Computed prediction accuracy for %s new published ideas", count)
    return count


# ---------------------------------------------------------------------------
# Mechanic performance aggregation
# ---------------------------------------------------------------------------

def compute_mechanic_performances(account_id: int) -> int:
    """Compute rolling mechanic performance stats from PredictionAccuracy data."""
    now = datetime.now(timezone.utc)
    windows = {
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
        "90d": now - timedelta(days=90),
    }

    with session_scope() as session:
        for window_name, window_start in windows.items():
            # Aggregate by mechanic
            rows = session.execute(
                select(
                    PredictionAccuracy.predicted_mechanic,
                    func.count(PredictionAccuracy.id).label("posts_count"),
                    func.avg(PredictionAccuracy.actual_views).label("avg_views"),
                    func.avg(PredictionAccuracy.actual_likes).label("avg_likes"),
                    func.avg(PredictionAccuracy.actual_replies).label("avg_replies"),
                    func.avg(PredictionAccuracy.actual_reach_multiple).label("avg_reach"),
                    func.sum(
                        case(
                            (PredictionAccuracy.actual_outcome_tag.in_(["breakout", "healthy"]), 1),
                            else_=0,
                        )
                    ).label("wins"),
                )
                .where(
                    PredictionAccuracy.account_id == account_id,
                    PredictionAccuracy.computed_at >= window_start,
                    PredictionAccuracy.predicted_mechanic.is_not(None),
                )
                .group_by(PredictionAccuracy.predicted_mechanic)
            ).all()

            for row in rows:
                mechanic = row.predicted_mechanic
                if not mechanic:
                    continue

                posts_count = row.posts_count or 0
                avg_views = float(row.avg_views or 0)
                avg_likes = float(row.avg_likes or 0)
                avg_replies = float(row.avg_replies or 0)
                avg_reach = float(row.avg_reach or 0)
                win_rate = (row.wins or 0) / posts_count * 100 if posts_count > 0 else 0

                # Check previous window for trend
                prev = session.scalar(
                    select(MechanicPerformance)
                    .where(
                        MechanicPerformance.account_id == account_id,
                        MechanicPerformance.mechanic == mechanic,
                        MechanicPerformance.window == window_name,
                    )
                )

                trend = ""
                trend_delta = 0.0
                if prev and prev.avg_views > 0:
                    trend_delta = round((avg_views - prev.avg_views) / prev.avg_views * 100, 1)
                    if trend_delta > 10:
                        trend = "up"
                    elif trend_delta < -10:
                        trend = "down"
                    else:
                        trend = "flat"

                if prev:
                    prev.posts_count = posts_count
                    prev.avg_views = avg_views
                    prev.avg_likes = avg_likes
                    prev.avg_replies = avg_replies
                    prev.avg_reach_multiple = avg_reach
                    prev.win_rate = win_rate
                    prev.trend = trend
                    prev.trend_delta_pct = trend_delta
                    prev.computed_at = now
                else:
                    mp = MechanicPerformance(
                        account_id=account_id,
                        mechanic=mechanic,
                        window=window_name,
                        posts_count=posts_count,
                        avg_views=avg_views,
                        avg_likes=avg_likes,
                        avg_replies=avg_replies,
                        avg_reach_multiple=avg_reach,
                        win_rate=win_rate,
                        trend=trend,
                        trend_delta_pct=trend_delta,
                    )
                    session.add(mp)

        session.flush()

    log.info("Computed mechanic performances for account %s", account_id)
    return len(rows) if "rows" in dir() else 0


# ---------------------------------------------------------------------------
# Bias detection & insight generation
# ---------------------------------------------------------------------------

def detect_prediction_bias(account_id: int) -> list[BiasReport]:
    """Detect systematic prediction bias by mechanic, tier, and overall."""
    reports: list[BiasReport] = []

    with session_scope() as session:
        # Overall bias
        overall = session.execute(
            select(
                func.count(PredictionAccuracy.id).label("n"),
                func.avg(PredictionAccuracy.views_error_pct).label("avg_err"),
                func.sum(
                    case(
                        (PredictionAccuracy.accuracy_bucket.in_(["bullseye", "close"]), 1),
                        else_=0,
                    )
                ).label("accurate"),
            )
            .where(PredictionAccuracy.account_id == account_id)
        ).one()

        if overall.n and overall.n > 0:
            avg_err = float(overall.avg_err or 0)
            acc_rate = (overall.accurate or 0) / overall.n * 100
            insight = (
                f"Overall predictions average {abs(avg_err):.0f}% "
                f"{'too high' if avg_err > 0 else 'too low'}. "
                f"{acc_rate:.0f}% land within 50% of actual."
            )
            reports.append(
                BiasReport(
                    dimension="overall",
                    value="all",
                    sample_size=overall.n,
                    avg_error_pct=round(avg_err, 1),
                    accuracy_rate=round(acc_rate, 1),
                    insight=insight,
                )
            )

        # Bias by mechanic
        mechanic_rows = session.execute(
            select(
                PredictionAccuracy.predicted_mechanic,
                func.count(PredictionAccuracy.id).label("n"),
                func.avg(PredictionAccuracy.views_error_pct).label("avg_err"),
                func.sum(
                    case(
                        (PredictionAccuracy.accuracy_bucket.in_(["bullseye", "close"]), 1),
                        else_=0,
                    )
                ).label("accurate"),
            )
            .where(
                PredictionAccuracy.account_id == account_id,
                PredictionAccuracy.predicted_mechanic.is_not(None),
            )
            .group_by(PredictionAccuracy.predicted_mechanic)
            .having(func.count(PredictionAccuracy.id) >= 3)
        ).all()

        for row in mechanic_rows:
            avg_err = float(row.avg_err or 0)
            acc_rate = (row.accurate or 0) / row.n * 100
            insight = (
                f"'{row.predicted_mechanic}' predictions average {abs(avg_err):.0f}% "
                f"{'too high' if avg_err > 0 else 'too low'} ({row.n} posts)."
            )
            reports.append(
                BiasReport(
                    dimension="mechanic",
                    value=row.predicted_mechanic,
                    sample_size=row.n,
                    avg_error_pct=round(avg_err, 1),
                    accuracy_rate=round(acc_rate, 1),
                    insight=insight,
                )
            )

        # Bias by tier
        tier_rows = session.execute(
            select(
                PredictionAccuracy.predicted_tier,
                func.count(PredictionAccuracy.id).label("n"),
                func.avg(PredictionAccuracy.views_error_pct).label("avg_err"),
                func.sum(
                    case(
                        (PredictionAccuracy.accuracy_bucket.in_(["bullseye", "close"]), 1),
                        else_=0,
                    )
                ).label("accurate"),
            )
            .where(
                PredictionAccuracy.account_id == account_id,
                PredictionAccuracy.predicted_tier.is_not(None),
            )
            .group_by(PredictionAccuracy.predicted_tier)
            .having(func.count(PredictionAccuracy.id) >= 3)
        ).all()

        for row in tier_rows:
            avg_err = float(row.avg_err or 0)
            acc_rate = (row.accurate or 0) / row.n * 100
            insight = (
                f"'{row.predicted_tier}' tier predictions average {abs(avg_err):.0f}% "
                f"{'too high' if avg_err > 0 else 'too low'} ({row.n} posts)."
            )
            reports.append(
                BiasReport(
                    dimension="tier",
                    value=row.predicted_tier,
                    sample_size=row.n,
                    avg_error_pct=round(avg_err, 1),
                    accuracy_rate=round(acc_rate, 1),
                    insight=insight,
                )
            )

    return reports


def generate_feedback_report(account_id: int) -> FeedbackReport:
    """Generate a complete feedback report for the account."""
    # Ensure data is fresh
    compute_all_prediction_accuracies(account_id)
    compute_mechanic_performances(account_id)

    bias_reports = detect_prediction_bias(account_id)

    with session_scope() as session:
        total = session.scalar(
            select(func.count(PredictionAccuracy.id)).where(
                PredictionAccuracy.account_id == account_id
            )
        ) or 0

        accurate = session.scalar(
            select(func.count(PredictionAccuracy.id)).where(
                PredictionAccuracy.account_id == account_id,
                PredictionAccuracy.accuracy_bucket.in_(["bullseye", "close"]),
            )
        ) or 0

        avg_err = session.scalar(
            select(func.avg(PredictionAccuracy.views_error_pct)).where(
                PredictionAccuracy.account_id == account_id
            )
        ) or 0.0

        # Top / bottom mechanic in last 30d
        top_mech = session.scalar(
            select(MechanicPerformance.mechanic)
            .where(
                MechanicPerformance.account_id == account_id,
                MechanicPerformance.window == "30d",
            )
            .order_by(desc(MechanicPerformance.avg_views))
            .limit(1)
        ) or ""

        bottom_mech = session.scalar(
            select(MechanicPerformance.mechanic)
            .where(
                MechanicPerformance.account_id == account_id,
                MechanicPerformance.window == "30d",
            )
            .order_by(MechanicPerformance.avg_views)
            .limit(1)
        ) or ""

    suggestions: list[str] = []
    for bias in bias_reports:
        if bias.dimension == "mechanic" and bias.sample_size >= 5:
            if bias.avg_error_pct > 30:
                suggestions.append(
                    f"Your '{bias.value}' predictions are consistently {bias.avg_error_pct:.0f}% too optimistic. "
                    f"Consider downgrading predicted scores for this mechanic."
                )
            elif bias.avg_error_pct < -30:
                suggestions.append(
                    f"Your '{bias.value}' posts outperform predictions by {abs(bias.avg_error_pct):.0f}% — "
                    f"this mechanic is undervalued. Consider using it more."
                )

    # Add mechanic performance suggestion
    if top_mech and bottom_mech and top_mech != bottom_mech:
        suggestions.append(
            f"'{top_mech}' is your top-performing mechanic in the last 30 days. "
            f"'{bottom_mech}' is underperforming — consider reducing its usage."
        )

    return FeedbackReport(
        account_id=account_id,
        total_published=total,
        accuracy_rate=round(accurate / total * 100, 1) if total > 0 else 0.0,
        avg_error_pct=round(float(avg_err), 1),
        top_performing_mechanic=top_mech,
        bottom_performing_mechanic=bottom_mech,
        bias_reports=bias_reports,
        suggestions=suggestions,
    )


# ---------------------------------------------------------------------------
# Scheduler hook
# ---------------------------------------------------------------------------

def run_feedback_cycle(account_id: int | None = None) -> None:
    """Run the full feedback cycle for one or all accounts."""
    if account_id is None:
        from .models import Account

        with session_scope() as session:
            accounts = session.scalars(select(Account)).all()
            for account in accounts:
                try:
                    run_feedback_cycle(account.id)
                except Exception:
                    log.exception("Feedback cycle failed for account %s", account.id)
        return

    log.info("Running feedback cycle for account %s", account_id)
    compute_all_prediction_accuracies(account_id)
    compute_mechanic_performances(account_id)
    log.info("Feedback cycle complete for account %s", account_id)
