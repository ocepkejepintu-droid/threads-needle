"""Brand Brain violation reporter — generates weekly health reports and detects drift."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import desc, func, select

from .brand_validator import BrandCheck, calculate_brand_score, check_protect_list_violations
from .models import MyPost, YouProfile

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class WeeklyReport:
    """Weekly brand health report."""

    week_start: date
    week_end: date
    posts_analyzed: int
    passed: int
    failed: int
    avg_score: float
    score_trend: list[int]  # daily scores
    top_violations: list[dict]  # [{"type": "...", "count": int}]
    recommendations: list[str]


@dataclass
class DriftAlert:
    """Alert for brand drift issues."""

    alert_type: str  # consecutive_low_scores|weekly_drop|protect_violation
    severity: str  # warning|critical
    message: str
    details: dict = field(default_factory=dict)


def generate_weekly_report(session: "Session") -> dict:
    """Generate weekly brand health report.

    Returns:
        {
            "week_start": date,
            "week_end": date,
            "posts_analyzed": int,
            "passed": int,
            "failed": int,
            "avg_score": float,
            "score_trend": list[int],  # daily scores
            "top_violations": list[{"type": "...", "count": int}],
            "recommendations": list[str],
        }
    """
    # Calculate week boundaries (Monday to Sunday)
    today = date.today()
    week_end = today
    week_start = today - timedelta(days=today.weekday())  # Monday of current week

    # Get posts from this week
    posts = session.scalars(
        select(MyPost)
        .where(
            func.date(MyPost.created_at) >= week_start,
            func.date(MyPost.created_at) <= week_end,
        )
        .order_by(desc(MyPost.created_at))
    ).all()

    # Get latest YouProfile for brand validation
    you_profile = session.scalar(
        select(YouProfile).order_by(desc(YouProfile.created_at)).limit(1)
    )

    # Analyze each post
    scores = []
    violations: dict[str, int] = {}
    passed_count = 0
    failed_count = 0

    for post in posts:
        score = _analyze_post_brand_health(post, you_profile)
        scores.append(score)

        if score >= 70:
            passed_count += 1
        else:
            failed_count += 1

        # Track violations if protect list exists
        if you_profile and you_profile.protect_list:
            post_violations = check_protect_list_violations(
                post.text or "", you_profile.protect_list
            )
            for v in post_violations:
                violations[v] = violations.get(v, 0) + 1

    # Build daily score trend (last 7 days)
    score_trend = _build_daily_score_trend(session, you_profile)

    # Calculate top violations
    top_violations = [
        {"type": vtype, "count": count}
        for vtype, count in sorted(violations.items(), key=lambda x: x[1], reverse=True)[:5]
    ]

    # Generate recommendations
    recommendations = _generate_recommendations(
        scores=scores,
        violations=violations,
        passed=passed_count,
        failed=failed_count,
        you_profile=you_profile,
    )

    # Calculate average score
    avg_score = sum(scores) / len(scores) if scores else 0.0

    return {
        "week_start": week_start,
        "week_end": week_end,
        "posts_analyzed": len(posts),
        "passed": passed_count,
        "failed": failed_count,
        "avg_score": round(avg_score, 1),
        "score_trend": score_trend,
        "top_violations": top_violations,
        "recommendations": recommendations,
    }


def detect_drift(session: "Session") -> list[dict]:
    """Detect brand drift issues:
    - 3+ consecutive posts scoring < 70
    - Weekly average drops > 10 points
    - Any protect list violations

    Returns list of alerts.
    """
    alerts: list[DriftAlert] = []

    # Get latest YouProfile
    you_profile = session.scalar(
        select(YouProfile).order_by(desc(YouProfile.created_at)).limit(1)
    )

    # Get recent posts (last 30 days)
    thirty_days_ago = date.today() - timedelta(days=30)
    posts = session.scalars(
        select(MyPost)
        .where(func.date(MyPost.created_at) >= thirty_days_ago)
        .order_by(desc(MyPost.created_at))
    ).all()

    if not posts:
        return []

    # Check for consecutive low scores (3+)
    consecutive_low = 0
    low_score_posts = []
    for post in posts[:20]:  # Check last 20 posts
        score = _analyze_post_brand_health(post, you_profile)
        if score < 70:
            consecutive_low += 1
            low_score_posts.append(post.thread_id)
        else:
            consecutive_low = 0
            low_score_posts = []

        if consecutive_low >= 3:
            alerts.append(
                DriftAlert(
                    alert_type="consecutive_low_scores",
                    severity="critical",
                    message=f"{consecutive_low} consecutive posts scored below 70",
                    details={"post_ids": low_score_posts.copy(), "threshold": 70},
                )
            )
            break

    # Check for weekly average drop > 10 points
    weekly_scores = _get_weekly_average_scores(session, you_profile, weeks=4)
    if len(weekly_scores) >= 2:
        current_week = weekly_scores[0]["avg_score"]
        prev_week = weekly_scores[1]["avg_score"]
        drop = prev_week - current_week

        if drop > 10:
            alerts.append(
                DriftAlert(
                    alert_type="weekly_drop",
                    severity="warning" if drop <= 15 else "critical",
                    message=f"Weekly brand score dropped {drop:.1f} points",
                    details={
                        "current_week": current_week,
                        "previous_week": prev_week,
                        "drop": drop,
                    },
                )
            )

    # Check for protect list violations
    if you_profile and you_profile.protect_list:
        recent_violations = []
        for post in posts[:10]:  # Check last 10 posts
            post_violations = check_protect_list_violations(
                post.text or "", you_profile.protect_list
            )
            if post_violations:
                recent_violations.append({
                    "post_id": post.thread_id,
                    "violations": post_violations,
                })

        if recent_violations:
            alerts.append(
                DriftAlert(
                    alert_type="protect_violation",
                    severity="critical",
                    message=f"Protect list violations detected in {len(recent_violations)} recent posts",
                    details={"violations": recent_violations},
                )
            )

    # Convert to dict format
    return [
        {
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "message": alert.message,
            "details": alert.details,
        }
        for alert in alerts
    ]


def get_brand_health_trend(session: "Session", days: int = 30) -> list[dict]:
    """Get daily brand scores for trend chart.

    Returns list of daily data points:
    [{"date": "2024-01-01", "score": 85, "posts": 2}, ...]
    """
    you_profile = session.scalar(
        select(YouProfile).order_by(desc(YouProfile.created_at)).limit(1)
    )

    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    # Get posts in date range
    posts = session.scalars(
        select(MyPost)
        .where(
            func.date(MyPost.created_at) >= start_date,
            func.date(MyPost.created_at) <= end_date,
        )
        .order_by(MyPost.created_at)
    ).all()

    # Group posts by date
    daily_data: dict[str, list[int]] = {}
    for post in posts:
        post_date = post.created_at.date().isoformat()
        score = _analyze_post_brand_health(post, you_profile)
        if post_date not in daily_data:
            daily_data[post_date] = []
        daily_data[post_date].append(score)

    # Fill in all dates with data
    result = []
    for i in range(days + 1):
        current = start_date + timedelta(days=i)
        current_iso = current.isoformat()
        day_scores = daily_data.get(current_iso, [])

        result.append({
            "date": current_iso,
            "score": round(sum(day_scores) / len(day_scores), 1) if day_scores else None,
            "posts": len(day_scores),
        })

    return result


def _analyze_post_brand_health(post: MyPost, you_profile: YouProfile | None) -> int:
    """Calculate brand health score for a single post.

    Uses a simplified scoring model based on:
    - Protect list compliance (binary)
    - Content length and structure
    - Presence of voice signatures

    Returns score 0-100.
    """
    if not you_profile:
        return 50  # Neutral score if no profile

    text = post.text or ""

    # Check protect list violations
    violations = check_protect_list_violations(text, you_profile.protect_list or [])

    # Calculate base score
    base_score = 70  # Start with passing score

    # Violations penalty (critical)
    if violations:
        base_score -= min(40, len(violations) * 20)

    # Content quality indicators
    word_count = len(text.split())
    if word_count < 10:
        base_score -= 10  # Too short
    elif word_count > 50:
        base_score += 5  # Substantial content

    # Check for stylistic signatures presence
    signatures = you_profile.stylistic_signatures or []
    signature_matches = 0
    text_lower = text.lower()
    for sig in signatures:
        sig_text = sig.get("signature", "").lower()
        if sig_text and sig_text in text_lower:
            signature_matches += 1

    if signature_matches > 0:
        base_score += min(15, signature_matches * 5)

    # Ensure within bounds
    return max(0, min(100, base_score))


def _build_daily_score_trend(session: "Session", you_profile: YouProfile | None) -> list[int]:
    """Build 7-day score trend.

    Returns list of 7 daily average scores (most recent last).
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=6)

    posts = session.scalars(
        select(MyPost)
        .where(
            func.date(MyPost.created_at) >= start_date,
            func.date(MyPost.created_at) <= end_date,
        )
        .order_by(MyPost.created_at)
    ).all()

    # Group by date
    daily_scores: dict[date, list[int]] = {}
    for post in posts:
        post_date = post.created_at.date()
        score = _analyze_post_brand_health(post, you_profile)
        if post_date not in daily_scores:
            daily_scores[post_date] = []
        daily_scores[post_date].append(score)

    # Build trend list
    trend = []
    for i in range(7):
        current = start_date + timedelta(days=i)
        day_scores = daily_scores.get(current, [])
        if day_scores:
            trend.append(int(sum(day_scores) / len(day_scores)))
        else:
            trend.append(0)  # No posts = 0 score

    return trend


def _get_weekly_average_scores(
    session: "Session", you_profile: YouProfile | None, weeks: int = 4
) -> list[dict]:
    """Get average brand scores for the last N weeks.

    Returns list of weekly data, most recent first.
    """
    today = date.today()
    weekly_data = []

    for week_offset in range(weeks):
        week_end = today - timedelta(weeks=week_offset, days=today.weekday())
        week_start = week_end - timedelta(days=6)

        posts = session.scalars(
            select(MyPost)
            .where(
                func.date(MyPost.created_at) >= week_start,
                func.date(MyPost.created_at) <= week_end,
            )
        ).all()

        if posts:
            scores = [_analyze_post_brand_health(p, you_profile) for p in posts]
            avg_score = sum(scores) / len(scores)
        else:
            avg_score = 0

        weekly_data.append({
            "week_start": week_start,
            "week_end": week_end,
            "avg_score": round(avg_score, 1),
            "post_count": len(posts),
        })

    return weekly_data


def _generate_recommendations(
    scores: list[int],
    violations: dict[str, int],
    passed: int,
    failed: int,
    you_profile: YouProfile | None,
) -> list[str]:
    """Generate recommendations based on analysis.

    Returns list of recommendation strings.
    """
    recommendations = []

    if not you_profile:
        recommendations.append("Run the pipeline to build your You profile for better brand analysis.")
        return recommendations

    # Score-based recommendations
    if scores:
        avg = sum(scores) / len(scores)
        if avg < 60:
            recommendations.append(
                "Your brand health score is below 60. Review your recent posts against the protect list."
            )
        elif avg < 70:
            recommendations.append(
                "Your brand health score is borderline. Focus on incorporating more stylistic signatures."
            )

    # Pass/fail ratio
    total = passed + failed
    if total > 0:
        pass_rate = passed / total
        if pass_rate < 0.7:
            recommendations.append(
                f"Only {pass_rate:.0%} of posts passed brand checks. Review your content creation process."
            )

    # Violation-based recommendations
    if violations:
        top_violation = max(violations.items(), key=lambda x: x[1])
        recommendations.append(
            f"Most common violation: '{top_violation[0]}' ({top_violation[1]} times). "
            "Add this to your pre-publish checklist."
        )

    # Protect list recommendation
    if you_profile.protect_list and len(you_profile.protect_list) < 3:
        recommendations.append(
            "Consider expanding your protect list with more voice elements to preserve."
        )

    # Double-down recommendation
    if you_profile.double_down_list:
        recommendations.append(
            f"You have {len(you_profile.double_down_list)} double-down elements. "
            "Ensure these appear in at least one post per week."
        )

    # Default if no specific issues
    if not recommendations:
        recommendations.append("Brand health is stable. Keep maintaining your voice consistency.")

    return recommendations
