"""Content calendar and unified content pipeline routes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.orm import joinedload

from ..config import get_schedule_timezone, get_settings
from ..account_scope import require_idea_ownership
from ..db import session_scope
from ..publish_gate import gate_approve_idea, gate_publish_idea, invalidate_approval_on_edit
from ..models import Experiment, GeneratedIdea, IntakeItem, PostOutcome
from .routes_common import (
    log,
    redirect_to_account_route,
    reject_ambiguous_account_mutation,
    require_account,
    with_account_context,
)


def _validate_image_bytes(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


# P1.3 Tier-Slot Schedule (WIB times, stored as hour:minute strings)
_SLOT_SCHEDULE = [
    # (day_index 0=Mon, hour, minute, tier, label)
    (0, 11, 0, "hero", "Mon 11:00"),
    (1, 12, 0, "engine", "Tue 12:00"),
    (2, 12, 0, "engine", "Wed 12:00"),
    (2, 20, 0, "engine", "Wed 20:00"),
    (3, 12, 0, "engine", "Thu 12:00"),
    (4, 11, 0, "hero", "Fri 11:00"),
    (4, 14, 0, "engine", "Fri 14:00"),
    (5, 10, 0, "signal", "Sat 10:00"),
]


def _slot_matches_tier(slot_time: datetime, tier: str | None) -> bool:
    """Check if a scheduled datetime matches a tier's allowed slots."""
    if tier is None:
        return True
    tz = get_schedule_timezone()
    local = slot_time.astimezone(tz)
    wd = local.weekday()  # 0=Monday
    hh = local.hour
    mm = local.minute
    for d, h, m, t, _ in _SLOT_SCHEDULE:
        if d == wd and h == hh and m == mm and t == tier:
            return True
    return False


def register_content_routes(router, templates: Jinja2Templates):
    @router.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request, week: int = 0, account: str | None = None) -> Response:
        return redirect_to_account_route("/calendar", account_slug=account, week=week)

    @router.get("/accounts/{account_slug}/calendar", response_class=HTMLResponse)
    def calendar_prefixed(request: Request, account_slug: str, week: int = 0) -> HTMLResponse:
        from datetime import timedelta

        tz = get_schedule_timezone()

        today_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_local + timedelta(weeks=week, days=-today_local.weekday())
        week_end = week_start + timedelta(days=7)

        week_start_utc = week_start.astimezone(timezone.utc)
        week_end_utc = week_end.astimezone(timezone.utc)

        with session_scope() as session:
            acct = require_account(session, account_slug)
            posts = session.scalars(
                select(GeneratedIdea)
                .where(GeneratedIdea.account_id == acct.id)
                .where(GeneratedIdea.status.in_(["scheduled", "published", "failed"]))
                .where(GeneratedIdea.scheduled_at >= week_start_utc)
                .where(GeneratedIdea.scheduled_at < week_end_utc)
                .order_by(GeneratedIdea.scheduled_at)
            ).all()

            days = []
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

            for i in range(7):
                day_date = week_start + timedelta(days=i)
                day_posts = [
                    {
                        "id": p.id,
                        "time": p.scheduled_at.astimezone(tz).strftime("%H:%M")
                        if p.scheduled_at
                        else "",
                        "preview": p.concept[:100] + "..." if len(p.concept) > 100 else p.concept,
                        "full_content": p.concept,
                        "scheduled_at_iso": p.scheduled_at.isoformat() if p.scheduled_at else "",
                        "status": p.status,
                        "title": p.title,
                    }
                    for p in posts
                    if p.scheduled_at and p.scheduled_at.astimezone(tz).date() == day_date.date()
                ]

                days.append(
                    {
                        "name": day_names[i],
                        "date": day_date.strftime("%d"),
                        "is_today": day_date.date() == today_local.date(),
                        "posts": day_posts,
                    }
                )

        week_label = (
            f"{week_start.strftime('%b %d')} - {(week_end - timedelta(days=1)).strftime('%b %d')}"
        )

        return templates.TemplateResponse(
            request,
            "calendar.html",
            with_account_context(
                account_slug,
                days=days,
                week_label=week_label,
                prev_week=week - 1,
                next_week=week + 1,
            ),
        )

    @router.get("/content", response_class=HTMLResponse)
    def content_pipeline(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/content", account_slug=account)

    @router.get("/accounts/{account_slug}/content", response_class=HTMLResponse)
    def content_pipeline_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        from datetime import timedelta

        with session_scope() as session:
            acct = require_account(session, account_slug)

            # Intake items (new only, sorted by standing score desc)
            intake_items = (
                session.scalars(
                    select(IntakeItem)
                    .where(IntakeItem.account_id == acct.id)
                    .where(IntakeItem.status == "new")
                    .order_by(desc(IntakeItem.operator_standing_score))
                )
                .all()
            )

            all_drafts = (
                session.scalars(
                    select(GeneratedIdea)
                    .options(joinedload(GeneratedIdea.experiment))
                    .where(GeneratedIdea.account_id == acct.id)
                    .where(GeneratedIdea.status == "draft")
                    .order_by(desc(GeneratedIdea.predicted_score))
                )
                .unique()
                .all()
            )

            drafts = [d for d in all_drafts if d.experiment_id is None]
            experiment_drafts = [d for d in all_drafts if d.experiment_id is not None]

            approved = (
                session.scalars(
                    select(GeneratedIdea)
                    .options(joinedload(GeneratedIdea.experiment))
                    .where(GeneratedIdea.account_id == acct.id)
                    .where(GeneratedIdea.status == "approved")
                    .order_by(desc(GeneratedIdea.predicted_score))
                )
                .unique()
                .all()
            )

            scheduled = (
                session.scalars(
                    select(GeneratedIdea)
                    .options(joinedload(GeneratedIdea.experiment))
                    .where(GeneratedIdea.account_id == acct.id)
                    .where(GeneratedIdea.status == "scheduled")
                    .order_by(GeneratedIdea.scheduled_at)
                )
                .unique()
                .all()
            )

            published = (
                session.scalars(
                    select(GeneratedIdea)
                    .options(joinedload(GeneratedIdea.experiment))
                    .where(GeneratedIdea.account_id == acct.id)
                    .where(GeneratedIdea.status == "published")
                    .order_by(desc(GeneratedIdea.posted_at))
                    .limit(10)
                )
                .unique()
                .all()
            )

            # Fetch latest outcome for each published post
            outcome_map: dict[str, str] = {}
            for idea in published:
                if idea.thread_id:
                    outcome = session.scalar(
                        select(PostOutcome)
                        .where(PostOutcome.post_thread_id == idea.thread_id)
                        .order_by(PostOutcome.snapshot_at.desc())
                        .limit(1)
                    )
                    if outcome:
                        outcome_map[idea.thread_id] = outcome.outcome_tag or ""

        return templates.TemplateResponse(
            request,
            "content_pipeline.html",
            with_account_context(
                account_slug,
                intake_items=intake_items,
                drafts=drafts,
                experiment_drafts=experiment_drafts,
                approved=approved,
                scheduled=scheduled,
                published=published,
                outcome_map=outcome_map,
                timezone=timezone,
                timedelta=timedelta,
                schedule_tz=get_schedule_timezone(),
            ),
        )

    @router.post("/api/content/{idea_id}/edit")
    async def api_edit_idea(request: Request, idea_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/edit")
    async def api_edit_idea_prefixed(
        request: Request, account_slug: str, idea_id: int
    ) -> JSONResponse:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        concept = data.get("concept", "")

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

            idea.concept = concept

            from ..content_rules import validate_content

            validation = validate_content(concept)
            idea.predicted_score = validation.score

        invalidate_approval_on_edit(idea_id)
        return JSONResponse({"success": True})

    @router.post("/api/content/{idea_id}/rubric")
    async def api_save_rubric(request: Request, idea_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/rubric")
    async def api_save_rubric_prefixed(
        request: Request, account_slug: str, idea_id: int
    ) -> JSONResponse:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        rubric = data.get("rubric", {})
        mechanic = data.get("mechanic")

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

            idea.rubric_hook_test = rubric.get("hook_test")
            idea.rubric_mechanic_fit = rubric.get("mechanic_fit")
            idea.rubric_operator_standing = rubric.get("operator_standing")
            idea.rubric_trend_freshness = rubric.get("trend_freshness")
            idea.rubric_reply_invitation = rubric.get("reply_invitation")
            idea.rubric_voice_signature = rubric.get("voice_signature")
            if mechanic:
                idea.mechanic = mechanic

            # Auto-compute tier
            total = sum(
                v for v in [
                    idea.rubric_hook_test, idea.rubric_mechanic_fit,
                    idea.rubric_operator_standing, idea.rubric_trend_freshness,
                    idea.rubric_reply_invitation, idea.rubric_voice_signature,
                ] if v is not None
            )
            if total >= 85:
                idea.tier = "hero"
            elif total >= 70:
                idea.tier = "engine"
            elif total >= 50:
                idea.tier = "signal"
            else:
                idea.tier = "kill"

        return JSONResponse({"success": True, "tier": idea.tier, "total_score": total})

    @router.post("/api/content/create")
    async def api_create_idea(request: Request, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/create")
    async def api_create_idea_prefixed(request: Request, account_slug: str) -> JSONResponse:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        concept = data.get("concept", "").strip()
        if not concept:
            return JSONResponse({"error": "Content is required"}, status_code=400)

        with session_scope() as session:
            account = require_account(session, account_slug)

            title = concept.split("\n")[0].strip()
            if len(title) > 80:
                title = title[:77] + "..."

            idea = GeneratedIdea(
                account_id=account.id,
                title=title,
                concept=concept,
                status="draft",
                generated_by="manual",
            )
            session.add(idea)
            session.flush()
            idea_id = idea.id

            # Auto-publish if requested
            if data.get("auto_publish"):
                from ..publish_gate import gate_publish_idea
                from ..publisher import publish_scheduled_idea

                idea.status = "approved"
                session.flush()

                gate = gate_publish_idea(idea_id)
                if not gate.allowed:
                    return JSONResponse({
                        "success": True,
                        "idea_id": idea_id,
                        "published": False,
                        "publish_error": gate.reason,
                    })

                published = publish_scheduled_idea(idea_id)
                return JSONResponse({
                    "success": True,
                    "idea_id": idea_id,
                    "published": published,
                })

        return JSONResponse({"success": True, "idea_id": idea_id})

    # ── Hermes bridge ──────────────────────────────────────────────────────
    @router.post("/accounts/{account_slug}/api/hermes/push")
    async def hermes_push(request: Request, account_slug: str) -> JSONResponse:
        """Receive content from Hermes agent and queue it as a draft."""
        settings = get_settings()
        expected_key = settings.hermes_api_key or ""
        if expected_key:
            provided_key = request.headers.get("X-Hermes-Key", "")
            if not provided_key or provided_key != expected_key:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        concept = data.get("concept", "").strip()
        if not concept:
            return JSONResponse({"error": "concept is required"}, status_code=400)

        with session_scope() as session:
            account = require_account(session, account_slug)

            title = data.get("title", "").strip()
            if not title:
                title = concept.split("\n")[0].strip()
            if len(title) > 80:
                title = title[:77] + "..."

            scheduled_at = None
            if data.get("scheduled_at"):
                try:
                    scheduled_at = datetime.fromisoformat(data["scheduled_at"])
                except ValueError:
                    pass

            idea = GeneratedIdea(
                account_id=account.id,
                title=title,
                concept=concept,
                status="draft",
                generated_by="hermes",
                image_url=data.get("image_url"),
                tier=data.get("tier"),
                mechanic=data.get("mechanic"),
                predicted_score=data.get("predicted_score", 0),
                predicted_views_range=data.get("predicted_views_range", ""),
                rubric_hook_test=data.get("rubric", {}).get("hook_test"),
                rubric_mechanic_fit=data.get("rubric", {}).get("mechanic_fit"),
                rubric_operator_standing=data.get("rubric", {}).get("operator_standing"),
                rubric_trend_freshness=data.get("rubric", {}).get("trend_freshness"),
                rubric_reply_invitation=data.get("rubric", {}).get("reply_invitation"),
                rubric_voice_signature=data.get("rubric", {}).get("voice_signature"),
                scheduled_at=scheduled_at,
            )
            session.add(idea)
            session.flush()
            idea_id = idea.id

        return JSONResponse({"success": True, "idea_id": idea_id})

    @router.post("/api/content/{idea_id}/queue")
    def api_queue_idea(idea_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/queue")
    def api_queue_idea_prefixed(idea_id: int, account_slug: str) -> JSONResponse:
        from datetime import timedelta

        tz = get_schedule_timezone()

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

            if idea.scheduled_at is not None:
                invalidate_approval_on_edit(idea_id)
                idea = require_idea_ownership(session, idea_id, account_slug)
                if not idea:
                    return JSONResponse({"error": "Not found"}, status_code=404)

            now_local = datetime.now(tz)
            days_until_fri = (4 - now_local.weekday()) % 7
            if days_until_fri == 0:
                days_until_fri = 7

            scheduled_local = now_local + timedelta(days=days_until_fri)
            scheduled_local = scheduled_local.replace(hour=9, minute=0, second=0, microsecond=0)
            scheduled = scheduled_local.astimezone(timezone.utc)

            gate = gate_approve_idea(idea_id)
            if not gate.allowed:
                return JSONResponse({"error": gate.reason}, status_code=400)

            idea.status = "scheduled"
            idea.scheduled_at = scheduled

        return JSONResponse({"success": True, "scheduled_at": scheduled.isoformat()})

    @router.post("/api/content/{idea_id}/schedule")
    async def api_schedule_idea(
        request: Request, idea_id: int, account: str | None = None
    ) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/schedule")
    async def api_schedule_idea_prefixed(
        request: Request, account_slug: str, idea_id: int
    ) -> JSONResponse:
        from datetime import timedelta

        tz = get_schedule_timezone()

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        time_slot = data.get("time_slot")
        custom_time = data.get("custom_time")

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

            now_local = datetime.now(tz)

            if custom_time:
                scheduled = datetime.fromisoformat(custom_time.replace("Z", "+00:00"))
            elif time_slot == "next-fri-9am":
                days_until_fri = (4 - now_local.weekday()) % 7
                if days_until_fri == 0:
                    days_until_fri = 7
                scheduled_local = now_local + timedelta(days=days_until_fri)
                scheduled_local = scheduled_local.replace(hour=9, minute=0, second=0, microsecond=0)
                scheduled = scheduled_local.astimezone(timezone.utc)
            elif time_slot == "next-fri-10am":
                days_until_fri = (4 - now_local.weekday()) % 7
                if days_until_fri == 0:
                    days_until_fri = 7
                scheduled_local = now_local + timedelta(days=days_until_fri)
                scheduled_local = scheduled_local.replace(
                    hour=10, minute=0, second=0, microsecond=0
                )
                scheduled = scheduled_local.astimezone(timezone.utc)
            elif time_slot == "tomorrow-9am":
                scheduled_local = now_local + timedelta(days=1)
                scheduled_local = scheduled_local.replace(hour=9, minute=0, second=0, microsecond=0)
                scheduled = scheduled_local.astimezone(timezone.utc)
            else:
                return JSONResponse({"error": "Invalid time slot"}, status_code=400)

            if idea.scheduled_at is not None:
                invalidate_approval_on_edit(idea_id)
                idea = require_idea_ownership(session, idea_id, account_slug)
                if not idea:
                    return JSONResponse({"error": "Not found"}, status_code=404)

            gate = gate_approve_idea(idea_id)
            if not gate.allowed:
                return JSONResponse({"error": gate.reason}, status_code=400)

            # P1.3 Tier-slot validation
            if not _slot_matches_tier(scheduled, idea.tier):
                return JSONResponse(
                    {
                        "error": (
                            f"Tier '{idea.tier}' cannot be scheduled at this time. "
                            f"Allowed slots for {idea.tier}: "
                            + ", ".join(
                                label for d, h, m, t, label in _SLOT_SCHEDULE if t == idea.tier
                            )
                        )
                    },
                    status_code=400,
                )

            idea.status = "scheduled"
            idea.scheduled_at = scheduled

        return JSONResponse({"success": True, "scheduled_at": scheduled.isoformat()})

    @router.post("/api/content/{idea_id}/unschedule")
    def api_unschedule_idea(idea_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/unschedule")
    def api_unschedule_idea_prefixed(idea_id: int, account_slug: str) -> JSONResponse:
        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

            idea.status = "draft"
            idea.scheduled_at = None

        return JSONResponse({"success": True})

    @router.post("/api/content/{idea_id}/dismiss")
    def api_dismiss_idea(idea_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/dismiss")
    def api_dismiss_idea_prefixed(idea_id: int, account_slug: str) -> JSONResponse:
        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

            idea.status = "rejected"

        return JSONResponse({"success": True})

    @router.post("/api/content/{idea_id}/publish")
    def api_publish_now(idea_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/publish")
    def api_publish_now_prefixed(idea_id: int, account_slug: str) -> JSONResponse:
        from ..publish_gate import gate_publish_idea
        from ..publisher import publish_scheduled_idea

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

        gate = gate_publish_idea(idea_id)
        if not gate.allowed:
            return JSONResponse({"error": gate.reason}, status_code=400)

        success = publish_scheduled_idea(idea_id)

        if success:
            return JSONResponse({"success": True})
        else:
            with session_scope() as session:
                idea = session.get(GeneratedIdea, idea_id)
                error = idea.error_message if idea else "Unknown error"
            return JSONResponse({"error": error}, status_code=500)

    @router.post("/api/content/{idea_id}/move")
    async def api_move_idea(request: Request, idea_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/move")
    async def api_move_idea_prefixed(request: Request, account_slug: str, idea_id: int) -> Response:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        target_status = data.get("status")

        if target_status not in ("draft", "approved", "scheduled", "rejected"):
            return JSONResponse({"error": "Invalid status"}, status_code=400)

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

        if target_status == "scheduled":
            gate = gate_publish_idea(idea_id)
            if not gate.allowed:
                return JSONResponse({"error": gate.reason}, status_code=400)

        if target_status == "approved":
            gate = gate_approve_idea(idea_id)
            if not gate.allowed:
                return JSONResponse({"error": gate.reason}, status_code=400)

        with session_scope() as session:
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

            idea.status = target_status

            if target_status == "draft":
                idea.scheduled_at = None

        return JSONResponse({"success": True})

    # ------------------------------------------------------------------
    # Intake item endpoints
    # ------------------------------------------------------------------

    @router.post("/api/intake/{intake_id}/archive")
    def api_archive_intake(intake_id: int, account: str | None = None) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/intake/{intake_id}/archive")
    def api_archive_intake_prefixed(
        account_slug: str, intake_id: int
    ) -> JSONResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            item = session.get(IntakeItem, intake_id)
            if not item or item.account_id != acct.id:
                return JSONResponse({"error": "Not found"}, status_code=404)
            item.status = "archived"
        return JSONResponse({"success": True})

    @router.post("/api/intake/{intake_id}/angle-it")
    async def api_angle_it_intake(
        request: Request, intake_id: int, account: str | None = None
    ) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/intake/{intake_id}/angle-it")
    async def api_angle_it_intake_prefixed(
        request: Request, account_slug: str, intake_id: int
    ) -> JSONResponse:
        from ..intake.angle_it import generate_angle_variants
        from ..intake.fetchers import RawIntakeItem

        with session_scope() as session:
            acct = require_account(session, account_slug)
            item = session.get(IntakeItem, intake_id)
            if not item or item.account_id != acct.id:
                return JSONResponse({"error": "Not found"}, status_code=404)

        raw = RawIntakeItem(
            source=item.source,
            source_url=item.source_url,
            source_title=item.source_title,
            raw_data=item.raw_data or {},
            discovered_at=item.discovered_at,
        )
        try:
            variants = generate_angle_variants(raw)
        except Exception as exc:
            log.error("Angle-It generation failed for intake %s: %s", intake_id, exc)
            return JSONResponse(
                {"error": "Failed to generate angles. Please try again."},
                status_code=500,
            )

        return JSONResponse({
            "success": True,
            "variants": [
                {
                    "hook": v.hook,
                    "body": v.body,
                    "mechanic": v.mechanic,
                    "rubric": v.rubric,
                    "reasoning": v.reasoning,
                    "total_score": v.total_score,
                }
                for v in variants
            ],
        })

    @router.post("/api/intake/{intake_id}/convert")
    async def api_convert_intake(
        request: Request, intake_id: int, account: str | None = None
    ) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/intake/{intake_id}/convert")
    async def api_convert_intake_prefixed(
        request: Request, account_slug: str, intake_id: int
    ) -> JSONResponse:
        try:
            data = await request.json()
        except Exception:
            data = {}

        concept = data.get("concept", "").strip()
        mechanic = data.get("mechanic")
        rubric = data.get("rubric", {})

        with session_scope() as session:
            acct = require_account(session, account_slug)
            item = session.get(IntakeItem, intake_id)
            if not item or item.account_id != acct.id:
                return JSONResponse({"error": "Not found"}, status_code=404)

            if item.status != "new":
                return JSONResponse(
                    {"error": "Item already processed"}, status_code=400
                )

            draft = GeneratedIdea(
                account_id=acct.id,
                title=item.source_title[:80],
                concept=concept or item.summary or item.source_title,
                intake_item_id=item.id,
                mechanic=mechanic or (item.candidate_mechanics[0] if item.candidate_mechanics else None),
                status="draft",
                generated_by="manual",
            )

            # Apply rubric scores if provided
            if rubric:
                draft.rubric_hook_test = rubric.get("hook_test")
                draft.rubric_mechanic_fit = rubric.get("mechanic_fit")
                draft.rubric_operator_standing = rubric.get("operator_standing")
                draft.rubric_trend_freshness = rubric.get("trend_freshness")
                draft.rubric_reply_invitation = rubric.get("reply_invitation")
                draft.rubric_voice_signature = rubric.get("voice_signature")
                # Auto-assign tier from total score
                total = sum(v for v in [
                    draft.rubric_hook_test, draft.rubric_mechanic_fit,
                    draft.rubric_operator_standing, draft.rubric_trend_freshness,
                    draft.rubric_reply_invitation, draft.rubric_voice_signature,
                ] if v is not None)
                if total >= 85:
                    draft.tier = "hero"
                elif total >= 70:
                    draft.tier = "engine"
                elif total >= 50:
                    draft.tier = "signal"
                else:
                    draft.tier = "kill"

            session.add(draft)
            session.flush()  # so draft.id is available
            item.status = "converted"
            item.converted_to_idea_id = draft.id
            draft_id = draft.id

        return JSONResponse({"success": True, "draft_id": draft_id})

    @router.post("/api/content/{idea_id}/image")
    async def api_update_image(
        request: Request, idea_id: int, account: str | None = None
    ) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/image")
    async def api_update_image_prefixed(
        request: Request, account_slug: str, idea_id: int
    ) -> Response:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        image_url = data.get("image_url", "")

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if not idea:
                return JSONResponse({"error": "Not found"}, status_code=404)

            idea.image_url = image_url if image_url else None

        invalidate_approval_on_edit(idea_id)
        return JSONResponse({"success": True})

    @router.post("/api/content/{idea_id}/upload-image")
    async def api_upload_image(
        request: Request, idea_id: int, account: str | None = None
    ) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/upload-image")
    async def api_upload_image_prefixed(
        request: Request, account_slug: str, idea_id: int
    ) -> JSONResponse:
        import uuid

        try:
            form = await request.form()
            file = cast(Any, form.get("file"))

            if not file:
                return JSONResponse({"error": "No file uploaded"}, status_code=400)

            content_type = file.content_type
            if not content_type or not content_type.startswith("image/"):
                return JSONResponse({"error": "File must be an image"}, status_code=400)

            contents = await file.read()

            if not _validate_image_bytes(contents):
                return JSONResponse(
                    {"error": "File content does not match a valid image"}, status_code=400
                )

            if len(contents) > 5 * 1024 * 1024:
                return JSONResponse({"error": "File too large (max 5MB)"}, status_code=400)

            ext = Path(file.filename).suffix.lower() if file.filename else ".jpg"
            if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                ext = ".jpg"

            filename = f"{uuid.uuid4().hex}{ext}"

            upload_dir = (Path(__file__).parent / "static" / "uploads").resolve()
            upload_dir.mkdir(parents=True, exist_ok=True)

            file_path = (upload_dir / filename).resolve()
            if not str(file_path).startswith(str(upload_dir)):
                return JSONResponse({"error": "Invalid file path"}, status_code=400)

            with open(file_path, "wb") as f:
                f.write(contents)

            image_url = f"/static/uploads/{filename}"

            with session_scope() as session:
                require_account(session, account_slug)
                idea = require_idea_ownership(session, idea_id, account_slug)
                if not idea:
                    return JSONResponse({"error": "Not found"}, status_code=404)

                base_url = str(request.base_url).rstrip("/")
                full_image_url = f"{base_url}{image_url}"
                idea.image_url = full_image_url

            response_payload = {"success": True, "image_url": full_image_url}
            if "localhost" in base_url or "127.0.0.1" in base_url:
                response_payload["warning"] = (
                    "This image is hosted locally and cannot be published to Threads. "
                    "Meta's servers need a publicly accessible image URL. "
                    "Please use a public image URL for publishing."
                )

            invalidate_approval_on_edit(idea_id)
            return JSONResponse(response_payload)

        except Exception:
            log.exception("Image upload failed")
            return JSONResponse({"error": "Upload failed"}, status_code=500)

    @router.post("/api/content/{idea_id}/upload-image-external")
    async def api_upload_image_external(
        request: Request, idea_id: int, account: str | None = None
    ) -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/api/content/{idea_id}/upload-image-external")
    async def api_upload_image_external_prefixed(
        request: Request, account_slug: str, idea_id: int
    ) -> JSONResponse:
        from ..image_host import upload_image_file

        try:
            form = await request.form()
            file = cast(Any, form.get("file"))

            if not file:
                return JSONResponse({"error": "No file uploaded"}, status_code=400)

            content_type = file.content_type
            if not content_type or not content_type.startswith("image/"):
                return JSONResponse({"error": "File must be an image"}, status_code=400)

            contents = await file.read()

            if not _validate_image_bytes(contents):
                return JSONResponse(
                    {"error": "File content does not match a valid image"}, status_code=400
                )

            if len(contents) > 5 * 1024 * 1024:
                return JSONResponse({"error": "File too large (max 5MB)"}, status_code=400)

            public_url = upload_image_file(
                contents, filename=file.filename, content_type=content_type
            )

            with session_scope() as session:
                require_account(session, account_slug)
                idea = require_idea_ownership(session, idea_id, account_slug)
                if not idea:
                    return JSONResponse({"error": "Not found"}, status_code=404)
                idea.image_url = public_url

            invalidate_approval_on_edit(idea_id)
            return JSONResponse({"success": True, "image_url": public_url})

        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception:
            log.exception("External image upload failed")
            return JSONResponse({"error": "Upload failed"}, status_code=500)

    @router.patch("/growth/ideas/{idea_id}/reschedule")
    async def growth_idea_reschedule(
        request: Request, idea_id: int, account: str | None = None
    ) -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.patch("/accounts/{account_slug}/growth/ideas/{idea_id}/reschedule")
    async def growth_idea_reschedule_prefixed(
        request: Request, account_slug: str, idea_id: int
    ) -> JSONResponse:
        from datetime import timedelta

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        day_index = data.get("day_index", 0)
        if not isinstance(day_index, int) or day_index < 0 or day_index > 6:
            return JSONResponse({"error": "Invalid day_index (must be 0-6)"}, status_code=400)

        with session_scope() as session:
            require_account(session, account_slug)
            idea = require_idea_ownership(session, idea_id, account_slug)
            if idea is None:
                return JSONResponse({"error": "Idea not found"}, status_code=404)

            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            current_week_start = today + timedelta(days=-today.weekday())
            new_date = current_week_start + timedelta(days=day_index)

            if idea.scheduled_at:
                new_scheduled = new_date.replace(
                    hour=idea.scheduled_at.hour,
                    minute=idea.scheduled_at.minute,
                )
            else:
                new_scheduled = new_date.replace(hour=9, minute=0)

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
            idea.scheduled_at = new_scheduled

        return JSONResponse({"success": True})

    @router.get("/api/experiments/active")
    def api_active_experiments(account: str | None = None) -> Response:
        return redirect_to_account_route("/api/experiments/active", account_slug=account)

    @router.get("/accounts/{account_slug}/api/experiments/active")
    def api_active_experiments_prefixed(account_slug: str) -> JSONResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            experiments = session.scalars(
                select(Experiment)
                .where(Experiment.account_id == acct.id)
                .where(Experiment.status == "active")
                .order_by(Experiment.created_at.desc())
            ).all()

            return JSONResponse(
                [
                    {
                        "id": e.id,
                        "title": e.title,
                        "category": e.category,
                        "hypothesis": e.hypothesis[:120] + "..."
                        if len(e.hypothesis) > 120
                        else e.hypothesis,
                    }
                    for e in experiments
                ]
            )

    @router.post("/content/generate-from-experiments")
    async def generate_from_experiments(request: Request) -> Response:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/content/generate-from-experiments")
    async def generate_from_experiments_prefixed(
        request: Request, account_slug: str
    ) -> RedirectResponse:
        from datetime import timedelta
        from ..experiment_content_mapper import build_experiment_prompt
        from ..idea_generator import generate_ideas_from_experiment

        form_data = await request.form()
        experiment_id_value = form_data.get("experiment_id", 0)
        if not isinstance(experiment_id_value, (str, int)):
            experiment_id_value = 0
        experiment_id = int(experiment_id_value)

        count_value = form_data.get("count", 3)
        if not isinstance(count_value, (str, int)):
            count_value = 3
        count = int(count_value)

        if not experiment_id:
            return redirect_to_account_route("/content", account_slug=account_slug)

        with session_scope() as session:
            acct = require_account(session, account_slug)
            experiment = session.get(Experiment, experiment_id)
            if experiment is None or experiment.account_id != acct.id:
                raise HTTPException(404, "experiment not found")

        ideas = generate_ideas_from_experiment(experiment_id, count, account_id=acct.id)
        idea_ids = [idea.id for idea in ideas]

        with session_scope() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment:
                _, constraints = build_experiment_prompt(experiment)

                if constraints["category"] == "TIMING" and constraints.get("timing"):
                    import re

                    time_match = re.search(
                        r"(\d{1,2}):\d{2}-(\d{1,2}):\d{2}", constraints["timing"]
                    )
                    if time_match:
                        start_hour = int(time_match.group(1))
                        tz = get_schedule_timezone()
                        now_local = datetime.now(tz)

                        scheduled_local = now_local + timedelta(days=1)
                        scheduled_local = scheduled_local.replace(
                            hour=start_hour, minute=0, second=0, microsecond=0
                        )
                        scheduled_utc = scheduled_local.astimezone(timezone.utc)

                        for idea_id in idea_ids:
                            idea = session.get(GeneratedIdea, idea_id)
                            if idea:
                                gate = gate_publish_idea(idea_id)
                                if gate.allowed:
                                    idea.status = "scheduled"
                                    idea.scheduled_at = scheduled_utc

        return redirect_to_account_route("/content", account_slug=account_slug)
