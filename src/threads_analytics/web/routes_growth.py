"""Growth OS routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..db import session_scope
from ..account_scope import require_idea_ownership
from ..publish_gate import gate_publish_idea, invalidate_approval_on_edit
from ..scoring import account_growth_score
from .. import idea_generator
from ..models import ContentPattern, GeneratedIdea, MyPost, MyPostInsight
from .routes_common import (
    redirect_to_account_route,
    reject_ambiguous_account_mutation,
    require_account,
    with_account_context,
)


def register_growth_routes(router, templates: Jinja2Templates):
    @router.get("/growth/patterns", response_class=HTMLResponse)
    def growth_patterns(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/growth/patterns", account_slug=account)

    @router.get("/accounts/{account_slug}/growth/patterns", response_class=HTMLResponse)
    def growth_patterns_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            patterns = session.scalars(
                select(ContentPattern)
                .where(ContentPattern.account_id == acct.id)
                .where(ContentPattern.is_active == True)  # noqa: E712
                .order_by(ContentPattern.confidence_score.desc())
            ).all()

            patterns_by_type: dict[str, list[dict[str, object]]] = {}
            for p in patterns:
                pattern_type = p.pattern_type or "other"
                if pattern_type not in patterns_by_type:
                    patterns_by_type[pattern_type] = []

                examples = []
                for post_id in (p.example_post_ids or [])[:3]:
                    post = session.get(MyPost, post_id)
                    if post:
                        examples.append({"text": post.text or "", "permalink": post.permalink})

                patterns_by_type[pattern_type].append(
                    {
                        "id": p.id,
                        "pattern_name": p.pattern_name,
                        "description": p.description,
                        "confidence_score": p.confidence_score,
                        "example_count": p.example_count,
                        "avg_views": p.avg_views,
                        "success_rate": p.success_rate,
                        "examples": examples,
                    }
                )

        return templates.TemplateResponse(
            request,
            "growth_patterns.html",
            with_account_context(account_slug, patterns_by_type=patterns_by_type),
        )

    @router.post("/growth/patterns/{pattern_id}/generate")
    def growth_pattern_generate(pattern_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/growth/patterns/{pattern_id}/generate")
    def growth_pattern_generate_prefixed(pattern_id: int, account_slug: str) -> RedirectResponse:
        with session_scope() as session:
            pattern = session.get(ContentPattern, pattern_id)
            if not pattern:
                raise HTTPException(404, "pattern not found")

            acct = require_account(session, account_slug)
            if pattern.account_id != acct.id:
                raise HTTPException(404, "pattern not found")

            topic = pattern.pattern_name.lower()
            idea_generator.generate_ideas(topic=topic, count=3, account_id=acct.id)

        return redirect_to_account_route("/growth/ideas", account_slug=account_slug)

    @router.get("/growth/ideas", response_class=HTMLResponse)
    def growth_ideas(
        request: Request, status: str = "draft", account: str | None = None
    ) -> Response:
        return redirect_to_account_route("/growth/ideas", account_slug=account, status=status)

    @router.get("/accounts/{account_slug}/growth/ideas", response_class=HTMLResponse)
    def growth_ideas_prefixed(
        request: Request, account_slug: str, status: str = "draft"
    ) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            if status in ("draft", "scheduled", "published", "rejected"):
                ideas = session.scalars(
                    select(GeneratedIdea)
                    .where(GeneratedIdea.account_id == acct.id)
                    .where(GeneratedIdea.status == status)
                    .order_by(GeneratedIdea.created_at.desc())
                ).all()
            else:
                ideas = session.scalars(
                    select(GeneratedIdea)
                    .where(GeneratedIdea.account_id == acct.id)
                    .order_by(GeneratedIdea.created_at.desc())
                ).all()

            ideas_payload = [
                {
                    "id": idea.id,
                    "title": idea.title,
                    "concept": idea.concept,
                    "predicted_score": idea.predicted_score,
                    "predicted_views_range": idea.predicted_views_range,
                    "patterns_used": idea.patterns_used or [],
                    "status": idea.status,
                    "scheduled_at": idea.scheduled_at,
                }
                for idea in ideas
            ]

        return templates.TemplateResponse(
            request,
            "growth_ideas.html",
            with_account_context(account_slug, ideas=ideas_payload, current_status=status),
        )

    @router.post("/growth/ideas/generate")
    def growth_ideas_generate(topic: str = Form(""), account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/growth/ideas/generate")
    def growth_ideas_generate_prefixed(
        account_slug: str,
        topic: str = Form(""),
    ) -> RedirectResponse:
        if not topic:
            topic = "hiring and remote work"

        with session_scope() as session:
            acct = require_account(session, account_slug)
            idea_generator.generate_ideas(topic=topic, count=3, account_id=acct.id)

        return redirect_to_account_route("/growth/ideas", account_slug=account_slug)

    @router.get("/growth/ideas/{idea_id}/approve")
    def growth_idea_approve(idea_id: int, account: str | None = None) -> RedirectResponse:
        if account is None:
            raise HTTPException(400, "Use account-prefixed route")
        return redirect_to_account_route(f"/growth/ideas/{idea_id}/approve", account_slug=account)

    @router.get("/accounts/{account_slug}/growth/ideas/{idea_id}/approve")
    def growth_idea_approve_prefixed(idea_id: int, account_slug: str) -> RedirectResponse:
        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if idea is None:
                raise HTTPException(404, "idea not found")
        from ..publish_gate import gate_approve_idea

        gate = gate_approve_idea(idea_id)
        if not gate.allowed:
            raise HTTPException(400, gate.reason)
        with session_scope() as session:
            idea = require_idea_ownership(session, idea_id, account_slug)
            if idea is None:
                raise HTTPException(404, "idea not found")
            idea.status = "approved"
        return redirect_to_account_route("/growth/ideas", account_slug=account_slug)

    @router.get("/growth/ideas/{idea_id}/dismiss")
    def growth_idea_dismiss(idea_id: int, account: str | None = None) -> RedirectResponse:
        if account is None:
            raise HTTPException(400, "Use account-prefixed route")
        return redirect_to_account_route(f"/growth/ideas/{idea_id}/dismiss", account_slug=account)

    @router.get("/accounts/{account_slug}/growth/ideas/{idea_id}/dismiss")
    def growth_idea_dismiss_prefixed(idea_id: int, account_slug: str) -> RedirectResponse:
        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if idea is None:
                raise HTTPException(404, "idea not found")
            idea.status = "rejected"
        return redirect_to_account_route("/growth/ideas", account_slug=account_slug)

    @router.post("/growth/ideas/{idea_id}/schedule")
    async def growth_idea_schedule(
        request: Request, idea_id: int, account: str | None = None
    ) -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/growth/ideas/{idea_id}/schedule")
    async def growth_idea_schedule_prefixed(
        request: Request, idea_id: int, account_slug: str
    ) -> JSONResponse:
        from datetime import timedelta

        data = await request.json()
        time_slot = data.get("time_slot", "next-fri-9am")

        now = datetime.now(timezone.utc)

        if time_slot == "next-fri-9am":
            days_until_fri = (4 - now.weekday()) % 7
            if days_until_fri == 0:
                days_until_fri = 7
            scheduled = now + timedelta(days=days_until_fri)
            scheduled = scheduled.replace(hour=9, minute=0, second=0, microsecond=0)
        elif time_slot == "next-fri-10am":
            days_until_fri = (4 - now.weekday()) % 7
            if days_until_fri == 0:
                days_until_fri = 7
            scheduled = now + timedelta(days=days_until_fri)
            scheduled = scheduled.replace(hour=10, minute=0, second=0, microsecond=0)
        elif time_slot == "tomorrow-9am":
            scheduled = now + timedelta(days=1)
            scheduled = scheduled.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            return JSONResponse({"error": "Invalid time slot"}, status_code=400)

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if idea is None:
                return JSONResponse({"error": "Idea not found"}, status_code=404)

            if idea.scheduled_at is not None:
                invalidate_approval_on_edit(idea_id)
                idea = require_idea_ownership(session, idea_id, account_slug)
                if idea is None:
                    return JSONResponse({"error": "Idea not found"}, status_code=404)

        gate = gate_publish_idea(idea_id)
        if not gate.allowed:
            return JSONResponse({"error": gate.reason}, status_code=400)

        with session_scope() as session:
            idea = require_idea_ownership(session, idea_id, account_slug)
            if idea is None:
                return JSONResponse({"error": "Idea not found"}, status_code=404)
            idea.status = "scheduled"
            idea.scheduled_at = scheduled

        return JSONResponse({"success": True, "scheduled_at": scheduled.isoformat()})

    @router.get("/growth/ideas/{idea_id}/edit", response_class=HTMLResponse)
    def growth_idea_edit(request: Request, idea_id: int, account: str | None = None) -> Response:
        return redirect_to_account_route(f"/growth/ideas/{idea_id}/edit", account_slug=account)

    @router.get("/accounts/{account_slug}/growth/ideas/{idea_id}/edit", response_class=HTMLResponse)
    def growth_idea_edit_prefixed(
        request: Request, idea_id: int, account_slug: str
    ) -> HTMLResponse:
        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if idea is None:
                raise HTTPException(404, "idea not found")

            idea_payload = {
                "id": idea.id,
                "title": idea.title,
                "concept": idea.concept,
                "status": idea.status,
            }

        return templates.TemplateResponse(
            request,
            "idea_edit.html",
            with_account_context(account_slug, idea=idea_payload),
        )

    @router.post("/growth/ideas/{idea_id}/edit")
    def growth_idea_edit_save(
        idea_id: int,
        concept: str = Form(...),
        account: str | None = None,
    ) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/growth/ideas/{idea_id}/edit")
    def growth_idea_edit_save_prefixed(
        account_slug: str,
        idea_id: int,
        concept: str = Form(...),
    ) -> RedirectResponse:
        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if idea is None:
                raise HTTPException(404, "idea not found")

            idea.concept = concept

            from ..content_rules import validate_content

            validation = validate_content(concept)
            idea.predicted_score = validation.score

        invalidate_approval_on_edit(idea_id)
        return redirect_to_account_route("/growth/ideas", account_slug=account_slug)

    @router.get("/growth/performance", response_class=HTMLResponse)
    def growth_performance(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/growth/performance", account_slug=account)

    @router.get("/accounts/{account_slug}/growth/performance", response_class=HTMLResponse)
    def growth_performance_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            now = datetime.now(timezone.utc)
            this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta

            last_month_end = this_month_start - timedelta(seconds=1)
            last_month_start = last_month_end.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )

            this_month_posts = session.scalars(
                select(MyPost)
                .where(MyPost.account_id == acct.id)
                .where(MyPost.created_at >= this_month_start)
                .order_by(MyPost.created_at.desc())
            ).all()

            last_month_posts = session.scalars(
                select(MyPost)
                .where(
                    MyPost.account_id == acct.id,
                    MyPost.created_at >= last_month_start,
                    MyPost.created_at < this_month_start,
                )
                .order_by(MyPost.created_at.desc())
            ).all()

            def _avg_views(posts: list[MyPost]) -> float:
                total = 0
                count = 0
                for p in posts:
                    ins = session.scalar(
                        select(MyPostInsight)
                        .where(MyPostInsight.thread_id == p.thread_id)
                        .order_by(MyPostInsight.fetched_at.desc())
                        .limit(1)
                    )
                    if ins and ins.views:
                        total += ins.views
                        count += 1
                return total / count if count > 0 else 0

            this_month_avg = _avg_views(list(this_month_posts))
            last_month_avg = _avg_views(list(last_month_posts))
            change_pct = (
                ((this_month_avg - last_month_avg) / last_month_avg * 100)
                if last_month_avg > 0
                else 0
            )

            stats = {
                "this_month_avg_views": int(this_month_avg),
                "last_month_avg_views": int(last_month_avg),
                "change_pct": int(change_pct),
                "has_ai_comparison": False,
                "ai_avg_views": 0,
                "ai_engagement": 0.0,
                "manual_avg_views": 0,
                "manual_engagement": 0.0,
            }

        return templates.TemplateResponse(
            request,
            "growth_performance.html",
            with_account_context(account_slug, stats=stats),
        )

    @router.get("/portfolio", response_class=HTMLResponse)
    def portfolio(request: Request) -> HTMLResponse:
        from ..account_scope import list_accounts
        from ..planner import plan_account_items
        from ..models import ContentPattern, MyPost
        from ..scoring import post_outcome_score

        with session_scope() as session:
            accounts = list_accounts(session)

            account_rows = []
            all_planned = []
            for acct in accounts:
                score = account_growth_score(acct.id)
                account_rows.append(
                    {
                        "slug": acct.slug,
                        "name": acct.name,
                        "growth": score,
                    }
                )
                all_planned.extend(plan_account_items(acct.id))

            if account_rows:
                growth_scores = [a["growth"] for a in account_rows]
                growth = {
                    "score": sum(g["score"] for g in growth_scores) / len(growth_scores),
                    "follower_velocity_z": sum(g["follower_velocity_z"] for g in growth_scores)
                    / len(growth_scores),
                    "profile_clicks_z": sum(g["profile_clicks_z"] for g in growth_scores)
                    / len(growth_scores),
                    "views_z": sum(g["views_z"] for g in growth_scores) / len(growth_scores),
                    "conversation_depth_z": sum(g["conversation_depth_z"] for g in growth_scores)
                    / len(growth_scores),
                }
            else:
                growth = {
                    "score": 50.0,
                    "follower_velocity_z": 0.0,
                    "profile_clicks_z": 0.0,
                    "views_z": 0.0,
                    "conversation_depth_z": 0.0,
                }

            all_planned_sorted = sorted(all_planned, key=lambda x: x.score, reverse=True)[:10]

            top_patterns = session.scalars(
                select(ContentPattern)
                .where(ContentPattern.is_active.is_(True))
                .order_by(ContentPattern.confidence_score.desc())
                .limit(5)
            ).all()

            pattern_payload = [
                {
                    "name": p.pattern_name,
                    "type": p.pattern_type,
                    "confidence": p.confidence_score,
                }
                for p in top_patterns
            ]

            recent_posts = session.scalars(
                select(MyPost).order_by(MyPost.created_at.desc()).limit(5)
            ).all()
            recent_outcomes = []
            for post in recent_posts:
                outcome = post_outcome_score(post.account_id, post_id=post.thread_id)
                recent_outcomes.append(
                    {
                        "thread_id": post.thread_id,
                        "text": (post.text or "")[:100],
                        "score": outcome["score"],
                    }
                )

        return templates.TemplateResponse(
            request,
            "portfolio.html",
            {
                "growth": growth,
                "accounts": account_rows,
                "planned": all_planned_sorted,
                "patterns": pattern_payload,
                "recent_outcomes": recent_outcomes,
            },
        )
