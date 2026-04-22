"""Experiment and suggestion routes."""

from __future__ import annotations

import json as _json
import re

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..db import session_scope
from ..experiments import (
    abandon_experiment,
    create_experiment,
    end_experiment,
    evaluate_now,
    list_experiments,
    personal_category_performance,
    start_experiment,
)
from ..metrics import METRIC_META, METRIC_ORDER
from ..models import Account, Experiment, ExperimentPostClassification
from .routes_common import (
    _exp_summary,
    redirect_to_account_route,
    reject_ambiguous_account_mutation,
    require_account,
    with_account_context,
)


def register_experiments_routes(router, templates: Jinja2Templates):
    @router.get("/experiments", response_class=HTMLResponse)
    def experiments_index(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/experiments", account_slug=account)

    @router.get("/accounts/{account_slug}/experiments", response_class=HTMLResponse)
    def experiments_index_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            account_id = acct.id
            active = [
                _exp_summary(e, session) for e in list_experiments(session, account_id, "active")
            ]
            completed = [
                _exp_summary(e, session) for e in list_experiments(session, account_id, "completed")
            ]
            proposed = [
                _exp_summary(e, session) for e in list_experiments(session, account_id, "proposed")
            ]
            abandoned = [
                _exp_summary(e, session)
                for e in list_experiments(session, account_id, "abandoned", limit=10)
            ]
            track = personal_category_performance(session, account_id)
            track_payload = {
                cat: {
                    "total": cs.total,
                    "wins": cs.wins,
                    "losses": cs.losses,
                    "nulls": cs.nulls,
                    "insufficient": cs.insufficient,
                    "win_rate": cs.win_rate(),
                    "avg_win_effect_pct": cs.avg_win_effect_pct,
                }
                for cat, cs in track.items()
            }
        return templates.TemplateResponse(
            request,
            "experiments.html",
            with_account_context(
                account_slug,
                active=active,
                completed=completed,
                proposed=proposed,
                abandoned=abandoned,
                track_record=track_payload,
            ),
        )

    @router.post("/experiments/{exp_id}/delete")
    def experiment_delete(exp_id: int) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/experiments/{exp_id}/delete")
    def experiment_delete_prefixed(account_slug: str, exp_id: int) -> RedirectResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            exp = session.get(Experiment, exp_id)
            if exp is None or exp.account_id != acct.id:
                raise HTTPException(404, "experiment not found")
            session.delete(exp)
        return redirect_to_account_route("/experiments", account_slug=account_slug)

    @router.get("/experiments/new", response_class=HTMLResponse)
    def experiment_new_form(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/experiments/new", account_slug=account)

    @router.get("/accounts/{account_slug}/experiments/new", response_class=HTMLResponse)
    def experiment_new_form_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "experiment_new.html",
            with_account_context(
                account_slug,
                metric_order=METRIC_ORDER,
                metric_meta=METRIC_META,
            ),
        )

    @router.post("/experiments/new")
    def experiment_new_submit(
        account: str | None = None,
        title: str = Form(...),
        hypothesis: str = Form(...),
        category: str = Form(...),
        primary_metric: str = Form(...),
        predicate_json: str = Form(""),
        variant_window_days: int = Form(14),
        target_delta_pct: str = Form(""),
        start_now: str = Form(""),
    ) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/experiments/new")
    def experiment_new_submit_prefixed(
        account_slug: str,
        title: str = Form(...),
        hypothesis: str = Form(...),
        category: str = Form(...),
        primary_metric: str = Form(...),
        predicate_json: str = Form(""),
        variant_window_days: int = Form(14),
        target_delta_pct: str = Form(""),
        start_now: str = Form(""),
    ) -> RedirectResponse:
        def _parse_predicate_json(raw: str) -> dict[str, object]:
            text = raw.strip()
            if not text:
                return {}
            if text.startswith("{") and text.endswith("}"):
                candidate = text
            else:
                m = re.search(r"(\{.*\})", text, re.DOTALL)
                candidate = m.group(1) if m else text

            def _clean(s: str) -> str:
                s = re.sub(r",(\s*[}\]])", r"\1", s)
                return s

            candidate = _clean(candidate)

            try:
                parsed = _json.loads(candidate)
            except _json.JSONDecodeError:
                try:
                    fixed = candidate.replace("'", '"')
                    parsed = _json.loads(fixed)
                except _json.JSONDecodeError as exc:
                    raise HTTPException(
                        400,
                        f"Predicate JSON is not valid. Received: {raw!r}. "
                        f"Try something like {{'hours': [19, 20, 21]}} or leave it empty.",
                    ) from exc

            if not isinstance(parsed, dict):
                raise HTTPException(
                    400,
                    f"Predicate JSON must be an object (dict). Received: {type(parsed).__name__}",
                )
            return parsed

        spec = _parse_predicate_json(predicate_json)

        tdp: float | None = None
        if target_delta_pct.strip():
            try:
                tdp = float(target_delta_pct)
            except ValueError:
                pass

        with session_scope() as session:
            acct = require_account(session, account_slug)
            account_id = acct.id
            exp = create_experiment(
                session,
                account_id=account_id,
                title=title,
                hypothesis=hypothesis,
                category=category.upper(),
                predicate_spec=spec,
                primary_metric=primary_metric,
                source="user_defined",
                target_delta_pct=tdp,
                variant_window_days=variant_window_days,
                status="proposed",
            )
            if start_now == "on":
                start_experiment(session, exp)
            exp_id = exp.id
        return redirect_to_account_route(f"/experiments/{exp_id}", account_slug=account_slug)

    @router.get("/experiments/{exp_id}", response_class=HTMLResponse)
    def experiment_detail(request: Request, exp_id: int) -> Response:
        with session_scope() as session:
            exp = session.get(Experiment, exp_id)
            if exp is None:
                raise HTTPException(404, "experiment not found")
            acct = session.get(Account, exp.account_id)
            account_slug = acct.slug if acct is not None else None
        return redirect_to_account_route(f"/experiments/{exp_id}", account_slug=account_slug)

    @router.get("/accounts/{account_slug}/experiments/{exp_id}", response_class=HTMLResponse)
    def experiment_detail_prefixed(
        request: Request, account_slug: str, exp_id: int
    ) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            exp = session.get(Experiment, exp_id)
            if exp is None or exp.account_id != acct.id:
                raise HTTPException(404, "experiment not found")
            payload = _exp_summary(exp, session)
            classifications = session.scalars(
                select(ExperimentPostClassification).where(
                    ExperimentPostClassification.experiment_id == exp_id
                )
            ).all()
            class_payload = []
            from ..models import MyPost

            for c in classifications[:40]:
                p = session.get(MyPost, c.post_thread_id)
                class_payload.append(
                    {
                        "thread_id": c.post_thread_id,
                        "bucket": c.bucket,
                        "reason": c.reason,
                        "text": (p.text or "")[:200] if p else "",
                        "permalink": p.permalink if p else None,
                        "created_at": p.created_at if p else None,
                    }
                )
        return templates.TemplateResponse(
            request,
            "experiment_detail.html",
            with_account_context(
                account_slug,
                exp=payload,
                classifications=class_payload,
                metric_meta=METRIC_META,
            ),
        )

    @router.post("/experiments/{exp_id}/start")
    def experiment_start(exp_id: int) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/experiments/{exp_id}/start")
    def experiment_start_prefixed(account_slug: str, exp_id: int) -> RedirectResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            exp = session.get(Experiment, exp_id)
            if exp is None or exp.account_id != acct.id:
                raise HTTPException(404, "experiment not found")
            start_experiment(session, exp)
        return redirect_to_account_route(f"/experiments/{exp_id}", account_slug=account_slug)

    @router.post("/experiments/{exp_id}/evaluate")
    def experiment_evaluate(exp_id: int) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/experiments/{exp_id}/evaluate")
    def experiment_evaluate_prefixed(account_slug: str, exp_id: int) -> RedirectResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            exp = session.get(Experiment, exp_id)
            if exp is None or exp.account_id != acct.id:
                raise HTTPException(404, "experiment not found")
            evaluate_now(session, exp)
        return redirect_to_account_route(f"/experiments/{exp_id}", account_slug=account_slug)

    @router.post("/experiments/{exp_id}/end")
    def experiment_end(exp_id: int) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/experiments/{exp_id}/end")
    def experiment_end_prefixed(account_slug: str, exp_id: int) -> RedirectResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            exp = session.get(Experiment, exp_id)
            if exp is None or exp.account_id != acct.id:
                raise HTTPException(404, "experiment not found")
            end_experiment(session, exp, final_status="completed")
        return redirect_to_account_route(f"/experiments/{exp_id}", account_slug=account_slug)

    @router.post("/experiments/{exp_id}/abandon")
    def experiment_abandon(exp_id: int) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/experiments/{exp_id}/abandon")
    def experiment_abandon_prefixed(account_slug: str, exp_id: int) -> RedirectResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            exp = session.get(Experiment, exp_id)
            if exp is None or exp.account_id != acct.id:
                raise HTTPException(404, "experiment not found")
            abandon_experiment(session, exp)
        return redirect_to_account_route("/experiments", account_slug=account_slug)

    @router.get("/suggestions")
    def suggestions_redirect(account: str | None = None) -> RedirectResponse:
        return redirect_to_account_route("/experiments", account_slug=account, status_code=303)

    @router.post("/suggestions/{exp_id}/run")
    def suggestions_run(exp_id: int) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/suggestions/{exp_id}/run")
    def suggestions_run_prefixed(account_slug: str, exp_id: int) -> RedirectResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            exp = session.get(Experiment, exp_id)
            if exp is None or exp.account_id != acct.id or exp.status != "proposed":
                raise HTTPException(400, "experiment not in proposed state")
            start_experiment(session, exp)
        return redirect_to_account_route(f"/experiments/{exp_id}", account_slug=account_slug)
