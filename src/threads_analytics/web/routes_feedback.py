"""Performance feedback loop API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from ..db import session_scope
from ..models import MechanicPerformance, PredictionAccuracy
from ..performance_feedback import generate_feedback_report
from .routes_common import with_account_context, require_account


def register_feedback_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    @router.get("/accounts/{account_slug}/feedback")
    def feedback_page(request: Request, account_slug: str) -> HTMLResponse:
        report = generate_feedback_report_for_slug(account_slug)
        return templates.TemplateResponse(
            request,
            "feedback_dashboard.html",
            with_account_context(account_slug, report=report),
        )

    @router.get("/accounts/{account_slug}/api/feedback/report")
    def feedback_report_api(account_slug: str) -> JSONResponse:
        report = generate_feedback_report_for_slug(account_slug)
        return JSONResponse({"success": True, "report": report})

    @router.get("/accounts/{account_slug}/api/feedback/accuracies")
    def feedback_accuracies_api(
        account_slug: str,
        limit: int = 50,
        bucket: str | None = None,
    ) -> JSONResponse:
        with session_scope() as session:
            account = require_account(session, account_slug)
            stmt = (
                select(PredictionAccuracy)
                .where(PredictionAccuracy.account_id == account.id)
                .order_by(desc(PredictionAccuracy.computed_at))
                .limit(limit)
            )
            if bucket:
                stmt = stmt.where(PredictionAccuracy.accuracy_bucket == bucket)

            items = session.scalars(stmt).all()
            payload = [
                {
                    "id": item.id,
                    "idea_id": item.idea_id,
                    "predicted_score": item.predicted_score,
                    "predicted_views_range": item.predicted_views_range,
                    "predicted_mechanic": item.predicted_mechanic,
                    "predicted_tier": item.predicted_tier,
                    "actual_views": item.actual_views,
                    "actual_likes": item.actual_likes,
                    "actual_replies": item.actual_replies,
                    "actual_outcome_tag": item.actual_outcome_tag,
                    "views_error_pct": item.views_error_pct,
                    "score_error": item.score_error,
                    "accuracy_bucket": item.accuracy_bucket,
                    "computed_at": item.computed_at.isoformat() if item.computed_at else None,
                }
                for item in items
            ]
        return JSONResponse({"success": True, "items": payload, "count": len(payload)})

    @router.get("/accounts/{account_slug}/api/feedback/mechanics")
    def feedback_mechanics_api(
        account_slug: str,
        window: str = "30d",
    ) -> JSONResponse:
        with session_scope() as session:
            account = require_account(session, account_slug)
            items = session.scalars(
                select(MechanicPerformance)
                .where(
                    MechanicPerformance.account_id == account.id,
                    MechanicPerformance.window == window,
                )
                .order_by(desc(MechanicPerformance.avg_views))
            ).all()
            payload = [
                {
                    "mechanic": item.mechanic,
                    "posts_count": item.posts_count,
                    "avg_views": round(item.avg_views, 1),
                    "avg_likes": round(item.avg_likes, 1),
                    "avg_replies": round(item.avg_replies, 1),
                    "avg_reach_multiple": round(item.avg_reach_multiple, 2),
                    "win_rate": round(item.win_rate, 1),
                    "trend": item.trend,
                    "trend_delta_pct": round(item.trend_delta_pct, 1),
                }
                for item in items
            ]
        return JSONResponse({"success": True, "mechanics": payload, "count": len(payload)})


def generate_feedback_report_for_slug(account_slug: str) -> dict:
    with session_scope() as session:
        account = require_account(session, account_slug)
        report = generate_feedback_report(account.id)
        return {
            "total_published": report.total_published,
            "accuracy_rate": report.accuracy_rate,
            "avg_error_pct": report.avg_error_pct,
            "top_performing_mechanic": report.top_performing_mechanic,
            "bottom_performing_mechanic": report.bottom_performing_mechanic,
            "suggestions": report.suggestions,
            "bias_reports": [
                {
                    "dimension": b.dimension,
                    "value": b.value,
                    "sample_size": b.sample_size,
                    "avg_error_pct": b.avg_error_pct,
                    "accuracy_rate": b.accuracy_rate,
                    "insight": b.insight,
                }
                for b in report.bias_reports
            ],
            "computed_at": report.computed_at.isoformat(),
        }
