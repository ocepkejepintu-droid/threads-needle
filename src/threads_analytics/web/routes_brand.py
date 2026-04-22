"""Brand composer routes."""

from __future__ import annotations

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..brand_reporter import detect_drift, generate_weekly_report
from ..brand_validator import validate_content
from ..db import session_scope

from .routes_common import (
    _get_latest_you_profile,
    redirect_to_account_route,
    require_account,
    with_account_context,
)


def register_brand_routes(router, templates: Jinja2Templates):
    @router.get("/compose", response_class=HTMLResponse)
    def compose_page(request: Request, account: str | None = None) -> RedirectResponse:
        return redirect_to_account_route("/compose", account_slug=account)

    @router.get("/accounts/{account_slug}/compose", response_class=HTMLResponse)
    def compose_page_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
        return templates.TemplateResponse(
            request,
            "compose.html",
            with_account_context(acct.slug),
        )

    @router.get("/brand-report", response_class=HTMLResponse)
    def brand_report_page(request: Request, account: str | None = None) -> RedirectResponse:
        return redirect_to_account_route("/brand-report", account_slug=account)

    @router.get("/accounts/{account_slug}/brand-report", response_class=HTMLResponse)
    def brand_report_page_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            report = generate_weekly_report(session, account_id=acct.id)
            drift_alerts = detect_drift(session, account_id=acct.id)
        return templates.TemplateResponse(
            request,
            "brand_report.html",
            with_account_context(
                account_slug,
                report=report,
                drift_alerts=drift_alerts,
            ),
        )

    @router.post("/accounts/{account_slug}/api/brand-check")
    def api_brand_check(account_slug: str, text: str = Form(...)) -> JSONResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            you_profile = _get_latest_you_profile(session, account_id=acct.id)
            if you_profile is None:
                return JSONResponse(
                    {
                        "score": 50,
                        "passed": True,
                        "violations": [],
                        "double_down_elements": [],
                        "suggestions": [
                            {
                                "issue": "No profile",
                                "suggestion": "No You profile found. Run the pipeline first.",
                            }
                        ],
                    }
                )
            result = validate_content(text, you_profile)
            return JSONResponse(
                {
                    "score": result.overall_score,
                    "passed": result.passed,
                    "violations": result.protect_violations,
                    "double_down_elements": result.double_down_elements,
                    "suggestions": [
                        {"issue": s.issue, "suggestion": s.suggestion} for s in result.suggestions
                    ],
                }
            )
