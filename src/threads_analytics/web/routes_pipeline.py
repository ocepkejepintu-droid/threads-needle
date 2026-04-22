"""Pipeline trigger routes."""

from __future__ import annotations

import threading

from fastapi.responses import JSONResponse, Response
from sqlalchemy import desc, select

from ..account_scope import get_account_by_slug
from ..db import session_scope
from ..models import Run
from ..pipeline import run_comments_cycle, run_full_cycle
from .routes_common import (
    _get_comments_run_lock,
    _get_run_lock,
    _last_comments_run_summaries,
    _last_run_summaries,
    redirect_to_account_route,
    reject_ambiguous_account_mutation,
    require_account,
)


def _stage_progress_for_run(run_id: int | None) -> dict[str, object]:
    if run_id is None:
        return {}
    with session_scope() as session:
        run = session.get(Run, run_id)
        return run.stage_progress or {} if run is not None else {}


def _latest_run_progress(account_slug: str | None = None) -> dict[str, object]:
    with session_scope() as session:
        stmt = select(Run).order_by(desc(Run.started_at)).limit(1)
        if account_slug is not None:
            account = get_account_by_slug(session, account_slug)
            if account is None:
                return {}
            stmt = stmt.where(Run.account_id == account.id)
        run = session.scalar(stmt)
        return run.stage_progress or {} if run is not None else {}


def register_pipeline_routes(router):
    @router.post("/run")
    def trigger_run() -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/run")
    def trigger_run_prefixed(account_slug: str) -> JSONResponse:
        with session_scope() as session:
            require_account(session, account_slug)
        lock = _get_run_lock(account_slug)
        if not lock.acquire(blocking=False):
            return JSONResponse({"status": "already_running"}, status_code=409)

        def _bg() -> None:
            try:
                _last_run_summaries[account_slug] = run_full_cycle(account_slug=account_slug)
            finally:
                lock.release()

        threading.Thread(target=_bg, daemon=True).start()
        return JSONResponse({"status": "started"})

    @router.get("/run/status")
    def run_status() -> Response:
        return redirect_to_account_route("/run/status")

    @router.get("/accounts/{account_slug}/run/status")
    def run_status_prefixed(account_slug: str) -> JSONResponse:
        with session_scope() as session:
            require_account(session, account_slug)
        summary = _last_run_summaries.get(account_slug, {})
        run_id = summary.get("run_id") if summary.get("account") == account_slug else None
        progress = _stage_progress_for_run(run_id) if run_id else _latest_run_progress(account_slug)
        return JSONResponse(
            {
                "running": _get_run_lock(account_slug).locked(),
                "last_summary": summary,
                "stage_progress": progress,
            }
        )

    @router.post("/run/comments")
    def trigger_run_comments() -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/run/comments")
    def trigger_run_comments_prefixed(account_slug: str) -> JSONResponse:
        with session_scope() as session:
            require_account(session, account_slug)
        lock = _get_comments_run_lock(account_slug)
        if not lock.acquire(blocking=False):
            return JSONResponse({"status": "already_running"}, status_code=409)

        def _bg() -> None:
            try:
                _last_comments_run_summaries[account_slug] = run_comments_cycle(
                    draft_max=15, min_tier="medium", account_slug=account_slug
                )
            finally:
                lock.release()

        threading.Thread(target=_bg, daemon=True).start()
        return JSONResponse({"status": "started"})

    @router.get("/run/comments/status")
    def run_comments_status() -> Response:
        return redirect_to_account_route("/run/comments/status")

    @router.get("/accounts/{account_slug}/run/comments/status")
    def run_comments_status_prefixed(account_slug: str) -> JSONResponse:
        with session_scope() as session:
            require_account(session, account_slug)
        summary = _last_comments_run_summaries.get(account_slug, {})
        run_id = summary.get("run_id") if summary.get("account") == account_slug else None
        progress = _stage_progress_for_run(run_id) if run_id else _latest_run_progress(account_slug)
        return JSONResponse(
            {
                "running": _get_comments_run_lock(account_slug).locked(),
                "last_comments_run_summary": summary,
                "stage_progress": progress,
            }
        )
