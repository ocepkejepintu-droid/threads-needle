"""Page routes for main dashboard pages."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from ..config import get_settings
from ..notifier import get_active_notifications
from ..db import session_scope
from ..models import (
    AffinityCreator,
    AffinityPost,
    AlgorithmInference,
    MyPost,
    MyPostInsight,
    NoteworthyPost,
    PublicPerception,
    Recommendation,
    Topic,
    YouProfile,
)
from ..noteworthy import CATEGORY_META
from .routes_common import (
    _ground_truth_payload,
    _profile_payload,
    _recent_runs,
    redirect_to_account_route,
    require_account,
    with_account_context,
)


def register_pages_routes(router, templates: Jinja2Templates):
    @router.get("/", response_class=HTMLResponse)
    def ground_truth(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("", account_slug=account)

    @router.get("/accounts/{account_slug}", response_class=HTMLResponse)
    def ground_truth_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        settings = get_settings()
        with session_scope() as session:
            acct = require_account(session, account_slug)
            account_id = acct.id
            profile = _profile_payload(session, account_id)
            panel = _ground_truth_payload(session, account_id)
            from ..models import Experiment

            active_count = (
                session.query(Experiment)
                .filter(Experiment.account_id == account_id)
                .filter(Experiment.status == "active")
                .count()
            )
            proposed_count = (
                session.query(Experiment)
                .filter(Experiment.account_id == account_id)
                .filter(Experiment.status == "proposed")
                .count()
            )
            runs = _recent_runs(session, account_id)
        notifications = get_active_notifications(account_id)
        return templates.TemplateResponse(
            request,
            "ground_truth.html",
            with_account_context(
                account_slug,
                handle=acct.threads_handle or settings.threads_handle,
                profile=profile,
                panel=panel,
                active_count=active_count,
                proposed_count=proposed_count,
                runs=runs,
                notifications=notifications,
            ),
        )

    @router.get("/accounts/{account_slug}/ground-truth", response_class=HTMLResponse)
    def ground_truth_alias(request: Request, account_slug: str) -> HTMLResponse:
        return ground_truth_prefixed(request, account_slug)

    @router.get("/perception", response_class=HTMLResponse)
    def perception(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/perception", account_slug=account)

    @router.get("/accounts/{account_slug}/perception", response_class=HTMLResponse)
    def perception_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            pp = session.scalar(
                select(PublicPerception)
                .where(PublicPerception.account_id == acct.id)
                .order_by(desc(PublicPerception.created_at))
                .limit(1)
            )
            payload = None
            if pp is not None:
                raw = pp.raw_json or {}
                payload = {
                    "thin_slice": raw.get("thinSliceJudgment") or pp.one_sentence_cold,
                    "big_five": raw.get("bigFive") or {},
                    "cue_clarity": raw.get("cueClarity") or {},
                    "misread_risks": raw.get("misreadRisks") or [],
                    "signal_quality": raw.get("profileSignalQuality") or {},
                    "highest_leverage_fix": raw.get("highestLeverageFix") or {},
                    "follow_triggers": pp.follow_triggers or [],
                    "bounce_reasons": pp.bounce_reasons or [],
                    "created_at": pp.created_at,
                }
        return templates.TemplateResponse(
            request, "perception.html", with_account_context(account_slug, perception=payload)
        )

    @router.get("/algorithm", response_class=HTMLResponse)
    def algorithm(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/algorithm", account_slug=account)

    @router.get("/accounts/{account_slug}/algorithm", response_class=HTMLResponse)
    def algorithm_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            ai = session.scalar(
                select(AlgorithmInference)
                .where(AlgorithmInference.account_id == acct.id)
                .order_by(desc(AlgorithmInference.created_at))
                .limit(1)
            )
            payload = None
            if ai is not None:
                payload = {
                    "narrative_diagnosis": ai.narrative_diagnosis or ai.summary,
                    "signals": [
                        ("Reply velocity (first 30-60 min)", ai.reply_velocity_signal or {}),
                        (
                            "Conversation depth (replies vs likes)",
                            ai.conversation_depth_signal or {},
                        ),
                        ("Self-reply behavior (author → commenter)", ai.self_reply_signal or {}),
                        ("Zero-reply penalty loop", ai.zero_reply_penalty_signal or {}),
                        (
                            "Format diversity (text vs image/video)",
                            ai.format_diversity_signal or {},
                        ),
                        ("Posting cadence", ai.posting_cadence_signal or {}),
                    ],
                    "inferred_weights": ai.inferred_signal_weights or {},
                    "highest_roi_lever": ai.highest_roi_lever or {},
                    "legacy_penalties": ai.penalties or [],
                    "legacy_boosts": ai.boosts or [],
                    "legacy_levers": ai.levers or [],
                    "created_at": ai.created_at,
                }
        return templates.TemplateResponse(
            request, "algorithm.html", with_account_context(account_slug, algorithm=payload)
        )

    @router.get("/you", response_class=HTMLResponse)
    def you_route(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/you", account_slug=account)

    @router.get("/accounts/{account_slug}/you", response_class=HTMLResponse)
    def you_route_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            yp = session.scalar(
                select(YouProfile)
                .where(YouProfile.account_id == acct.id)
                .order_by(desc(YouProfile.created_at))
                .limit(1)
            )
            payload = None
            if yp is not None:
                payload = {
                    "core_identity": yp.core_identity,
                    "distinctive_voice_traits": yp.distinctive_voice_traits or [],
                    "unique_topic_crossovers": yp.unique_topic_crossovers or [],
                    "stylistic_signatures": yp.stylistic_signatures or [],
                    "posts_that_sound_most_like_you": yp.posts_that_sound_most_like_you or [],
                    "protect_list": yp.protect_list or [],
                    "double_down_list": yp.double_down_list or [],
                    "homogenization_risks": yp.homogenization_risks or [],
                    "created_at": yp.created_at,
                }
        return templates.TemplateResponse(
            request, "you.html", with_account_context(account_slug, you=payload)
        )

    @router.get("/posts", response_class=HTMLResponse)
    def posts(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/posts", account_slug=account)

    @router.get("/accounts/{account_slug}/posts", response_class=HTMLResponse)
    def posts_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            rows = session.scalars(
                select(NoteworthyPost)
                .where(NoteworthyPost.account_id == acct.id)
                .order_by(desc(NoteworthyPost.created_at))
            ).all()
            payload = []
            for np_row in rows:
                post = session.get(MyPost, np_row.post_thread_id)
                meta = CATEGORY_META.get(np_row.category, {})
                payload.append(
                    {
                        "category": np_row.category,
                        "category_label": meta.get("label", np_row.category.replace("_", " ")),
                        "category_lesson": meta.get("lesson", ""),
                        "remarkable_metric": np_row.remarkable_metric,
                        "remarkable_value": np_row.remarkable_value,
                        "ratio_vs_median": np_row.ratio_vs_median,
                        "commentary": np_row.claude_commentary,
                        "algo_hypothesis": np_row.algo_hypothesis,
                        "created_at": np_row.created_at,
                        "text": (post.text or "")[:400] if post else "",
                        "permalink": post.permalink if post else None,
                        "posted_at": post.created_at if post else None,
                        "media_type": post.media_type if post else None,
                        "likes": None,
                        "replies": None,
                        "views": None,
                    }
                )
                if post:
                    ins = session.scalar(
                        select(MyPostInsight)
                        .where(MyPostInsight.thread_id == post.thread_id)
                        .order_by(desc(MyPostInsight.fetched_at))
                        .limit(1)
                    )
                    if ins:
                        payload[-1]["likes"] = ins.likes
                        payload[-1]["replies"] = ins.replies
                        payload[-1]["views"] = ins.views
        return templates.TemplateResponse(
            request, "posts.html", with_account_context(account_slug, noteworthy=payload)
        )

    @router.get("/recommendations")
    def recommendations_redirect(account: str | None = None) -> RedirectResponse:
        return redirect_to_account_route("/recommendations", account_slug=account)

    @router.get("/accounts/{account_slug}/recommendations", response_class=HTMLResponse)
    def recommendations_page(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            recs = session.scalars(
                select(Recommendation)
                .where(Recommendation.account_id == acct.id)
                .order_by(Recommendation.rank.asc(), Recommendation.created_at.desc())
            ).all()
            recommendations = [
                {
                    "id": r.id,
                    "rank": r.rank,
                    "category": r.category,
                    "title": r.title,
                    "body": r.body,
                    "evidence": r.evidence_json,
                    "status": r.status,
                }
                for r in recs
            ]
        return templates.TemplateResponse(
            request,
            "recommendations.html",
            with_account_context(account_slug, recommendations=recommendations),
        )

    @router.post("/accounts/{account_slug}/recommendations/{rec_id}/status")
    def recommendations_status(account_slug: str, rec_id: int, status: str) -> RedirectResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            rec = session.get(Recommendation, rec_id)
            if rec is None or rec.account_id != acct.id:
                return RedirectResponse(
                    f"/accounts/{account_slug}/recommendations", status_code=303
                )
            if status in ("pending", "applied", "dismissed"):
                rec.status = status
        return RedirectResponse(f"/accounts/{account_slug}/recommendations", status_code=303)

    @router.get("/learning")
    def learning_redirect(account: str | None = None) -> RedirectResponse:
        return redirect_to_account_route("/experiments", account_slug=account)

    @router.get("/growth")
    def growth_root(account: str | None = None) -> RedirectResponse:
        return redirect_to_account_route("/content", account_slug=account)

    @router.get("/affinity", response_class=HTMLResponse)
    def affinity(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/affinity", account_slug=account)

    @router.get("/accounts/{account_slug}/affinity", response_class=HTMLResponse)
    def affinity_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            creators = session.scalars(
                select(AffinityCreator)
                .where(AffinityCreator.account_id == acct.id)
                .order_by(AffinityCreator.engagement_score.desc())
                .limit(50)
            ).all()
            payload = []
            for c in creators:
                posts_ = session.scalars(
                    select(AffinityPost)
                    .where(AffinityPost.creator_id == c.id)
                    .order_by(AffinityPost.likes.desc())
                    .limit(3)
                ).all()
                payload.append(
                    {
                        "handle": c.handle,
                        "engagement_score": round(c.engagement_score, 2),
                        "last_refreshed_at": c.last_refreshed_at,
                        "top_posts": [
                            {
                                "text": (p.text or "")[:240],
                                "likes": p.likes,
                                "replies": p.replies,
                            }
                            for p in posts_
                        ],
                    }
                )
        return templates.TemplateResponse(
            request,
            "affinity.html",
            with_account_context(account_slug, creators=payload, locked=True),
        )

    @router.get("/topics", response_class=HTMLResponse)
    def topics(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/topics", account_slug=account)

    @router.get("/accounts/{account_slug}/topics", response_class=HTMLResponse)
    def topics_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        with session_scope() as session:
            acct = require_account(session, account_slug)
            rows = session.scalars(
                select(Topic).where(Topic.account_id == acct.id).order_by(Topic.extracted_at.desc())
            ).all()
            topic_payload = [
                {
                    "id": t.id,
                    "label": t.label,
                    "description": t.description,
                    "last_searched_at": t.last_searched_at,
                }
                for t in rows
            ]
        return templates.TemplateResponse(
            request, "topics.html", with_account_context(account_slug, topics=topic_payload)
        )

    @router.get("/performance", response_class=HTMLResponse)
    def performance(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/performance", account_slug=account)

    @router.get("/accounts/{account_slug}/performance", response_class=HTMLResponse)
    def performance_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        from ..performance import (
            get_mechanic_performance,
            get_slot_performance,
            get_tier_hit_rates,
            get_topic_clusters,
            get_trend_tie_comparison,
        )

        with session_scope() as session:
            acct = require_account(session, account_slug)
            tier_hits = get_tier_hit_rates(account_id=acct.id)
            mechanics = get_mechanic_performance(account_id=acct.id)
            slots = get_slot_performance(account_id=acct.id)
            trend_tie = get_trend_tie_comparison(account_id=acct.id)
            topics = get_topic_clusters(account_id=acct.id)

        return templates.TemplateResponse(
            request,
            "performance.html",
            with_account_context(
                account_slug,
                tier_hits=tier_hits,
                mechanics=mechanics,
                slots=slots,
                trend_tie=trend_tie,
                topics=topics,
            ),
        )

    @router.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, account: str | None = None) -> Response:
        return redirect_to_account_route("/settings", account_slug=account)

    @router.get("/accounts/{account_slug}/settings", response_class=HTMLResponse)
    def settings_page_prefixed(request: Request, account_slug: str) -> HTMLResponse:
        from ..config import get_settings

        settings = get_settings()
        with session_scope() as session:
            acct = require_account(session, account_slug)
            threads_token_status = (
                "✅ Configured" if acct.threads_access_token else "❌ Not configured"
            )
            threads_user_status = "✅ Configured" if acct.threads_user_id else "❌ Not configured"
            return templates.TemplateResponse(
                request,
                "settings.html",
                with_account_context(
                    account_slug,
                    threads_token_status=threads_token_status,
                    threads_user_status=threads_user_status,
                    threads_user_id=acct.threads_user_id,
                    llm_provider=settings.llm_provider,
                    account_name=acct.name,
                ),
            )
