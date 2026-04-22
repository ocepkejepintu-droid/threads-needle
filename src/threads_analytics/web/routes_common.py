"""Common helpers and shared state for dashboard routes."""

from __future__ import annotations

import logging
import threading
from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import desc, select

from ..account_scope import DEFAULT_ACCOUNT_SLUG, get_account_by_slug, get_or_create_default_account
from ..metrics import METRIC_META, METRIC_ORDER, compute_ground_truth
from ..models import (
    Experiment,
    ExperimentPostClassification,
    ExperimentVerdict,
    MyPost,
    MyPostInsight,
    Profile,
    Run,
    YouProfile,
)

log = logging.getLogger(__name__)

_run_locks: dict[str, threading.Lock] = {}
_last_run_summaries: dict[str, dict[str, object]] = {}

_comments_run_locks: dict[str, threading.Lock] = {}
_last_comments_run_summaries: dict[str, dict[str, object]] = {}


def _get_run_lock(account_slug: str) -> threading.Lock:
    if account_slug not in _run_locks:
        _run_locks[account_slug] = threading.Lock()
    return _run_locks[account_slug]


def _get_comments_run_lock(account_slug: str) -> threading.Lock:
    if account_slug not in _comments_run_locks:
        _comments_run_locks[account_slug] = threading.Lock()
    return _comments_run_locks[account_slug]


def resolve_optional_account(session, account_slug: str | None = None):
    acct = get_account_by_slug(session, account_slug) if account_slug else None
    if acct is None:
        acct = get_or_create_default_account(session)
    return acct


def require_account(session, account_slug: str):
    acct = get_account_by_slug(session, account_slug)
    if acct is None:
        raise HTTPException(404, "account not found")
    return acct


def account_path(account_slug: str | None, suffix: str = "") -> str:
    slug = account_slug or DEFAULT_ACCOUNT_SLUG
    return f"/accounts/{slug}{suffix}"


def account_template_context(account_slug: str | None) -> dict[str, str | None]:
    prefix = account_path(account_slug)
    return {
        "account_slug": account_slug or DEFAULT_ACCOUNT_SLUG,
        "account_path_prefix": prefix,
        "account_home_path": prefix,
        "portfolio_path": "/portfolio",
    }


def with_account_context(account_slug: str | None, **context):
    return {**context, **account_template_context(account_slug)}


def redirect_to_account_route(
    suffix: str = "", account_slug: str | None = None, status_code: int = 303, **query: object
) -> RedirectResponse:
    url = account_path(account_slug, suffix)
    filtered = {k: v for k, v in query.items() if v is not None}
    if filtered:
        url = f"{url}?{urlencode(filtered, doseq=True)}"
    return RedirectResponse(url, status_code=status_code)


def reject_ambiguous_account_mutation() -> JSONResponse:
    return JSONResponse({"error": "Use account-prefixed route"}, status_code=400)


def _profile_payload(session, account_id: int | None = None) -> dict[str, object] | None:
    stmt = select(Profile)
    if account_id is not None:
        stmt = stmt.where(Profile.account_id == account_id)
    profile = session.scalar(stmt.limit(1))
    if profile is None:
        return None
    return {
        "username": profile.username,
        "biography": profile.biography,
        "profile_picture_url": profile.profile_picture_url,
        "updated_at": profile.updated_at,
    }


def _recent_runs(session, account_id: int | None = None, n: int = 5) -> list[dict[str, object]]:
    stmt = select(Run).order_by(desc(Run.started_at))
    if account_id is not None:
        stmt = stmt.where(Run.account_id == account_id)
    runs = session.scalars(stmt.limit(n)).all()
    return [
        {
            "id": r.id,
            "started_at": r.started_at,
            "status": r.status,
            "queries_used": r.keyword_search_queries_used,
        }
        for r in runs
    ]


def _exp_summary(exp: Experiment, session) -> dict[str, object]:
    v = session.get(ExperimentVerdict, exp.id) if exp.status in ("completed", "active") else None
    variant_n = control_n = 0
    if exp.id is not None:
        variant_n = (
            session.scalar(
                select(ExperimentPostClassification)
                .where(
                    ExperimentPostClassification.experiment_id == exp.id,
                    ExperimentPostClassification.bucket == "variant",
                )
                .limit(1)
            )
            is not None
            and session.query(ExperimentPostClassification)
            .filter(
                ExperimentPostClassification.experiment_id == exp.id,
                ExperimentPostClassification.bucket == "variant",
            )
            .count()
            or 0
        )
        control_n = (
            session.query(ExperimentPostClassification)
            .filter(
                ExperimentPostClassification.experiment_id == exp.id,
                ExperimentPostClassification.bucket == "control",
            )
            .count()
        )
    return {
        "id": exp.id,
        "title": exp.title,
        "hypothesis": exp.hypothesis,
        "category": exp.category,
        "status": exp.status,
        "source": exp.source,
        "primary_metric": exp.primary_metric,
        "primary_metric_label": METRIC_META.get(exp.primary_metric, {}).get(
            "label", exp.primary_metric
        ),
        "predicate_spec": exp.predicate_spec,
        "target_delta_pct": exp.target_delta_pct,
        "notes": exp.notes,
        "created_at": exp.created_at,
        "started_at": exp.started_at,
        "ended_at": exp.ended_at,
        "variant_start": exp.variant_start,
        "variant_end": exp.variant_end,
        "baseline_start": exp.baseline_start,
        "baseline_end": exp.baseline_end,
        "variant_n": variant_n,
        "control_n": control_n,
        "verdict": (
            {
                "verdict": v.verdict,
                "primary_metric_baseline": v.primary_metric_baseline,
                "primary_metric_variant": v.primary_metric_variant,
                "effect_size_pct": v.effect_size_pct,
                "effect_cliffs_delta": v.effect_cliffs_delta,
                "p_value": v.p_value,
                "ci_low": v.ci_low,
                "ci_high": v.ci_high,
                "variant_n": v.variant_n,
                "control_n": v.control_n,
                "honest_interpretation": v.honest_interpretation,
                "computed_at": v.computed_at,
            }
            if v
            else None
        ),
    }


def _format_metric_value(metric_name: str, value: float | None) -> str:
    if value is None:
        return "—"
    fmt = METRIC_META.get(metric_name, {}).get("format", "raw")
    if fmt == "pct":
        return f"{value * 100:.1f}%"
    if fmt == "multiple":
        return f"{value:.1f}×"
    return f"{value:.2f}"


def _format_delta(delta: float | None) -> dict[str, str]:
    if delta is None:
        return {"label": "—", "class": "flat"}
    if abs(delta) < 0.03:
        return {"label": f"{delta:+.0%}", "class": "flat"}
    return {"label": f"{delta:+.0%}", "class": "pos" if delta > 0 else "neg"}


def _ground_truth_payload(session, account_id: int) -> dict[str, object]:
    panel = compute_ground_truth(session, account_id)
    cards = []
    regressions: list[tuple[str, float]] = []
    improvements: list[tuple[str, float]] = []
    for name in METRIC_ORDER:
        mv = panel.metrics[name]
        base = panel.baselines[name]
        delta = panel.deltas[name]
        meta = METRIC_META[name]
        direction = meta["direction"]
        good = False
        if delta is not None and abs(delta) >= 0.03:
            good = (delta > 0 and direction == "up") or (delta < 0 and direction == "down")
            if good:
                improvements.append((name, abs(delta)))
            else:
                regressions.append((name, abs(delta)))
        delta_obj = _format_delta(delta)
        if delta is None or abs(delta) < 0.03:
            delta_obj["class"] = "flat"
        else:
            delta_obj["class"] = "pos" if good else "neg"
        cards.append(
            {
                "name": name,
                "label": meta["label"],
                "description": meta["description"],
                "value": _format_metric_value(name, mv.value),
                "raw_value": mv.value,
                "baseline": _format_metric_value(name, base.value),
                "delta": delta_obj,
                "n_posts": mv.n_posts,
                "sparkline": [p.value for p in panel.trend[name]],
                "direction": direction,
            }
        )

    hero_name: str | None = None
    hero_tone: str = "neutral"
    if regressions:
        regressions.sort(key=lambda x: x[1], reverse=True)
        hero_name = regressions[0][0]
        hero_tone = "negative"
    elif improvements:
        improvements.sort(key=lambda x: x[1], reverse=True)
        hero_name = improvements[0][0]
        hero_tone = "positive"
    else:
        hero_name = "zero_reply_fraction"
        hero_tone = "neutral"

    verdict_color = {
        "negative": "card-hero-coral",
        "positive": "card-hero-green",
        "neutral": "card-hero-yellow",
    }[hero_tone]
    hero_metric_color = {
        "negative": "card-hero-pink",
        "positive": "card-hero-green",
        "neutral": "card-hero-pink",
    }[hero_tone]

    return {
        "cards": cards,
        "headline": panel.verdict_headline,
        "computed_at": panel.computed_at,
        "window_days": panel.window_days,
        "hero_metric_name": hero_name,
        "hero_tone": hero_tone,
        "verdict_color": verdict_color,
        "hero_metric_color": hero_metric_color,
    }


def _get_latest_you_profile(session, account_id: int | None = None):
    stmt = select(YouProfile).order_by(desc(YouProfile.created_at))
    if account_id is not None:
        stmt = stmt.where(YouProfile.account_id == account_id)
    return session.scalar(stmt.limit(1))


def _latest_insights_with_posts(session, limit: int = 20) -> list[dict[str, object]]:
    posts = session.scalars(select(MyPost).order_by(desc(MyPost.created_at))).all()
    all_insights = session.scalars(
        select(MyPostInsight).order_by(MyPostInsight.fetched_at.desc())
    ).all()
    latest: dict[str, MyPostInsight] = {}
    for ins in all_insights:
        latest.setdefault(ins.thread_id, ins)
    rows = []
    for p in posts:
        ins = latest.get(p.thread_id)
        rows.append(
            {
                "thread_id": p.thread_id,
                "text": (p.text or "")[:240],
                "permalink": p.permalink,
                "created_at": p.created_at,
                "views": ins.views if ins else 0,
                "likes": ins.likes if ins else 0,
                "replies": ins.replies if ins else 0,
            }
        )
        if len(rows) >= limit:
            break
    rows.sort(key=lambda r: r["likes"], reverse=True)
    return rows
