"""End-to-end v2 pipeline.

Scientific flow:
    ingest → topics → affinity (still locked) →
    ground-truth metrics → classify active experiments →
    auto-evaluate experiments whose variant_end has passed →
    generate experiment suggestions → perception → algorithm inference.

The v1 recommender is no longer invoked by this pipeline. It stays in the tree
for backward compat only (the /recommendations route redirects to /suggestions).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .account_scope import get_account_by_slug, list_accounts
from .affinity import discover_affinity_creators
from .algorithm_inference import generate_algorithm_inference
from .db import init_db, session_scope
from .experiments import auto_evaluate_due, classify_active_experiments
from .ingest import ingest_own_data
from .metrics import compute_ground_truth
from .models import Run
from .noteworthy import generate_noteworthy_commentary
from .perception import generate_public_perception
from .suggestions import generate_suggestions
from .threads_client import ThreadsClient
from .topics import extract_and_persist_topics
from .you import generate_you_profile
from .leads_search import run_lead_searches
from .leads_intent import classify_lead_intent
from .leads_scoring import calculate_lead_score, save_lead_score, get_quality_tier
from .leads import draft_replies_for_leads
from .comment_inbox import poll_for_comments
from .comment_reply_drafts import draft_replies_for_inbox
from .growth_patterns import extract_patterns
from . import idea_generator
from .models import Account, Lead

log = logging.getLogger(__name__)


def _update_run_stage(run_id: int, stage: str, status: str = "running") -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is not None:
            progress = dict(run.stage_progress or {})
            progress[stage] = {"status": status, "at": datetime.now(timezone.utc).isoformat()}
            run.stage_progress = progress


def _resolve_accounts(account_slug: str | None = None) -> list[Account]:
    with session_scope() as session:
        if account_slug is not None:
            account = get_account_by_slug(session, account_slug)
            if account is None:
                raise ValueError(f"Unknown account slug: {account_slug}")
            return [account]
        return list_accounts(session)


def _sync_posts_for_comments(session, client, account_id: int, run_id: int) -> dict[str, int]:
    from .models import MyPost

    posts = client.list_my_posts(limit=None)
    inserted = 0
    for post in posts:
        existing = session.get(MyPost, post.id)
        if existing is None:
            session.add(
                MyPost(
                    account_id=account_id,
                    thread_id=post.id,
                    text=post.text,
                    media_type=post.media_type,
                    permalink=post.permalink,
                    created_at=post.created_at,
                    first_seen_run_id=run_id,
                )
            )
            inserted += 1
    return {"posts_fetched": len(posts), "new_posts": inserted}


def _run_comments_cycle_for_account(
    account: Account, draft_max: int = 15, min_tier: str = "medium"
) -> dict[str, Any]:
    init_db()

    with session_scope() as session:
        run = Run(account_id=account.id, started_at=datetime.now(timezone.utc), status="running")
        session.add(run)
        session.flush()
        run_id = run.id

    summary: dict[str, Any] = {"run_id": run_id, "account": account.slug}
    try:
        with ThreadsClient.from_account(account) as client:
            _update_run_stage(run_id, "ingest", "running")
            try:
                with session_scope() as session:
                    summary["ingest"] = _sync_posts_for_comments(
                        session, client, account.id, run_id
                    )
                _update_run_stage(run_id, "ingest", "complete")
            except Exception as exc:
                log.warning("Post sync for comments failed: %s", exc)
                summary["ingest"] = {"error": repr(exc)}
                _update_run_stage(run_id, "ingest", "failed")

            _update_run_stage(run_id, "comment_inbox_sync", "running")
            try:
                with session_scope() as session:
                    summary["comment_inbox_sync"] = poll_for_comments(
                        session, client, account.id, run_id
                    )
                _update_run_stage(run_id, "comment_inbox_sync", "complete")
            except Exception as exc:
                log.warning("Comment inbox sync failed: %s", exc)
                summary["comment_inbox_sync"] = {"error": repr(exc)}
                _update_run_stage(run_id, "comment_inbox_sync", "failed")

            _update_run_stage(run_id, "comment_drafts", "running")
            try:
                with session_scope() as session:
                    summary["comment_drafts"] = draft_replies_for_inbox(session, account.id)
                _update_run_stage(run_id, "comment_drafts", "complete")
            except Exception as exc:
                log.warning("Comment drafting failed: %s", exc)
                summary["comment_drafts"] = {"error": repr(exc)}
                _update_run_stage(run_id, "comment_drafts", "failed")

            _update_run_stage(run_id, "leads_search", "running")
            try:
                with session_scope() as session:
                    run = session.get(Run, run_id)
                    if run is None:
                        raise RuntimeError(f"Run {run_id} not found")
                    summary["leads"] = run_lead_searches(run, client)
                _update_run_stage(run_id, "leads_search", "complete")
            except Exception as exc:
                log.warning("Lead search failed: %s", exc)
                summary["leads_error"] = repr(exc)
                _update_run_stage(run_id, "leads_search", "failed")

            _update_run_stage(run_id, "leads_intent", "running")
            try:
                with session_scope() as session:
                    run = session.get(Run, run_id)
                    if run is None:
                        raise RuntimeError(f"Run {run_id} not found")
                    new_leads = session.scalars(
                        select(Lead).where(Lead.account_id == account.id, Lead.status == "new")
                    ).all()

                    for lead in new_leads:
                        if not lead.intent:
                            result = classify_lead_intent(
                                lead.post_text, lead.author_bio, lead.matched_keyword
                            )
                            lead.intent = result["intent"]
                            lead.intent_confidence = result["confidence"]

                        if not lead.score:
                            score = calculate_lead_score(lead)
                            tier = get_quality_tier(score)
                            save_lead_score(session, lead, score, tier, commit=False)

                    if new_leads:
                        session.commit()

                    summary["leads_intent_classified"] = len(
                        [lead for lead in new_leads if lead.intent]
                    )
                    summary["leads_scored"] = len([lead for lead in new_leads if lead.score])

                    drafted = draft_replies_for_leads(
                        session, min_tier=min_tier, max_per_run=draft_max, account_id=run.account_id
                    )
                    summary["leads_reply_drafts"] = drafted
                _update_run_stage(run_id, "leads_intent", "complete")
            except Exception as exc:
                log.warning("Lead intent/drafting failed: %s", exc)
                summary["leads_intent_error"] = repr(exc)
                _update_run_stage(run_id, "leads_intent", "failed")

        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeError(f"Run {run_id} not found")
            run.status = "complete"
            run.finished_at = datetime.now(timezone.utc)
            run.notes = str(summary)[:2000]

    except Exception as exc:
        log.exception("comments run %d failed", run_id)
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                run.notes = f"error: {exc!r}"[:2000]
        summary["error"] = repr(exc)

    return summary


def run_comments_cycle(
    draft_max: int = 15, min_tier: str = "medium", account_slug: str | None = None
) -> dict[str, Any]:
    accounts = _resolve_accounts(account_slug)
    results = [
        _run_comments_cycle_for_account(account, draft_max=draft_max, min_tier=min_tier)
        for account in accounts
    ]
    if account_slug is not None:
        return results[0]
    return {"accounts": results}


def _run_full_cycle_for_account(account: Account) -> dict[str, Any]:
    init_db()

    with session_scope() as session:
        run = Run(account_id=account.id, started_at=datetime.now(timezone.utc), status="running")
        session.add(run)
        session.flush()
        run_id = run.id

    summary: dict[str, Any] = {"run_id": run_id, "account": account.slug}
    try:
        with ThreadsClient.from_account(account) as client:
            # 1. Ingest
            _update_run_stage(run_id, "ingest", "running")
            with session_scope() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError(f"Run {run_id} not found")
                summary["ingest"] = ingest_own_data(run, client)
            _update_run_stage(run_id, "ingest", "complete")

            # 2. Topics
            _update_run_stage(run_id, "topics", "running")
            topics = extract_and_persist_topics(account_id=account.id)
            summary["topics"] = [t.label for t in topics]
            _update_run_stage(run_id, "topics", "complete")

            # 2b. Lead discovery
            _update_run_stage(run_id, "leads", "running")
            try:
                with session_scope() as session:
                    run = session.get(Run, run_id)
                    if run is None:
                        raise RuntimeError(f"Run {run_id} not found")
                    summary["leads"] = run_lead_searches(run, client)
                _update_run_stage(run_id, "leads", "complete")
            except Exception as exc:
                log.warning("Lead search failed: %s", exc)
                summary["leads_error"] = repr(exc)
                _update_run_stage(run_id, "leads", "failed")

            # 2c. Lead intent classification and scoring
            _update_run_stage(run_id, "leads_intent", "running")
            try:
                with session_scope() as session:
                    run = session.get(Run, run_id)
                    if run is None:
                        raise RuntimeError(f"Run {run_id} not found")

                    # Get new leads that need intent classification
                    new_leads = session.scalars(
                        select(Lead).where(Lead.account_id == account.id, Lead.status == "new")
                    ).all()

                    # Classify intent for new leads
                    for lead in new_leads:
                        if not lead.intent:
                            result = classify_lead_intent(
                                lead.post_text, lead.author_bio, lead.matched_keyword
                            )
                            lead.intent = result["intent"]
                            lead.intent_confidence = result["confidence"]

                        if not lead.score:
                            score = calculate_lead_score(lead)
                            tier = get_quality_tier(score)
                            save_lead_score(session, lead, score, tier, commit=False)

                    if new_leads:
                        session.commit()

                    summary["leads_intent_classified"] = len(
                        [lead for lead in new_leads if lead.intent]
                    )
                    summary["leads_scored"] = len([lead for lead in new_leads if lead.score])

                    # Auto-draft replies for high/medium tier leads
                    drafted = draft_replies_for_leads(
                        session, min_tier="medium", max_per_run=15, account_id=run.account_id
                    )
                    summary["leads_reply_drafts"] = drafted
                _update_run_stage(run_id, "leads_intent", "complete")
            except Exception as exc:
                log.warning("Lead intent/scoring failed: %s", exc)
                summary["leads_scoring_error"] = repr(exc)
                _update_run_stage(run_id, "leads_intent", "failed")

            # 3. Affinity (still locked in dev mode; returns quickly)
            _update_run_stage(run_id, "affinity", "running")
            with session_scope() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError(f"Run {run_id} not found")
                summary["affinity"] = discover_affinity_creators(run, client)
            _update_run_stage(run_id, "affinity", "complete")

        # 4-6. Ground truth + experiments (share one session)
        _update_run_stage(run_id, "ground_truth", "running")
        with session_scope() as session:
            panel = compute_ground_truth(session, account.id)
            summary["ground_truth_headline"] = panel.verdict_headline
            summary["ground_truth_metrics"] = {
                k: {
                    "current": v.value,
                    "baseline": panel.baselines[k].value,
                    "delta": panel.deltas[k],
                }
                for k, v in panel.metrics.items()
            }

            touched = classify_active_experiments(session, account.id)
            summary["experiments_classified_posts"] = touched

            completed = auto_evaluate_due(session, account.id)
            summary["experiments_auto_completed"] = completed
        _update_run_stage(run_id, "ground_truth", "complete")

        # 7. Build 'You' profile (anti-homogenization guardrail). Must run
        #    before suggestions so the suggester can respect the protect list.
        _update_run_stage(run_id, "you_profile", "running")
        try:
            with session_scope() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError(f"Run {run_id} not found")
                summary["you_profile_run_id"] = generate_you_profile(run)
            _update_run_stage(run_id, "you_profile", "complete")
        except Exception as exc:  # noqa: BLE001
            log.warning("You profile generation failed: %s", exc)
            summary["you_profile_error"] = repr(exc)
            _update_run_stage(run_id, "you_profile", "failed")

        # 8. Generate new suggestions (replaces recommendations)
        _update_run_stage(run_id, "suggestions", "running")
        try:
            with session_scope() as session:
                new_ids = generate_suggestions(session, account.id)
                summary["new_suggestion_ids"] = new_ids
            _update_run_stage(run_id, "suggestions", "complete")
        except Exception as exc:  # noqa: BLE001
            log.warning("suggestion generation failed: %s", exc)
            summary["suggestions_error"] = repr(exc)
            _update_run_stage(run_id, "suggestions", "failed")

        # 8. Public Perception
        _update_run_stage(run_id, "public_perception", "running")
        try:
            with session_scope() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError(f"Run {run_id} not found")
                summary["public_perception_run_id"] = generate_public_perception(run)
            _update_run_stage(run_id, "public_perception", "complete")
        except Exception as exc:  # noqa: BLE001
            log.warning("public perception generation failed: %s", exc)
            summary["public_perception_error"] = repr(exc)
            _update_run_stage(run_id, "public_perception", "failed")

        # 9. Algorithm Inference
        _update_run_stage(run_id, "algorithm_inference", "running")
        try:
            with session_scope() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError(f"Run {run_id} not found")
                summary["algorithm_inference_run_id"] = generate_algorithm_inference(run)
            _update_run_stage(run_id, "algorithm_inference", "complete")
        except Exception as exc:  # noqa: BLE001
            log.warning("algorithm inference generation failed: %s", exc)
            summary["algorithm_inference_error"] = repr(exc)
            _update_run_stage(run_id, "algorithm_inference", "failed")

        # 10. Noteworthy posts — outlier detection + Claude commentary
        _update_run_stage(run_id, "noteworthy", "running")
        try:
            with session_scope() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError(f"Run {run_id} not found")
                summary["noteworthy_post_ids"] = generate_noteworthy_commentary(run)
            _update_run_stage(run_id, "noteworthy", "complete")
        except Exception as exc:  # noqa: BLE001
            log.warning("noteworthy commentary failed: %s", exc)
            summary["noteworthy_error"] = repr(exc)
            _update_run_stage(run_id, "noteworthy", "failed")

        # 11. Weekly pattern extraction (only run on Sundays)
        if datetime.now(timezone.utc).weekday() == 6:
            _update_run_stage(run_id, "patterns", "running")
            try:
                with session_scope() as session:
                    patterns = extract_patterns(session, account.id)
                    summary["patterns_extracted"] = len(patterns)
                _update_run_stage(run_id, "patterns", "complete")
            except Exception as exc:  # noqa: BLE001
                log.warning("Pattern extraction failed: %s", exc)
                summary["patterns_extraction_error"] = repr(exc)
                _update_run_stage(run_id, "patterns", "failed")

        # 12. Daily content idea generation
        _update_run_stage(run_id, "ideas", "running")
        try:
            with session_scope() as session:
                if idea_generator.should_generate_ideas(
                    session, threshold=10, account_id=account.id
                ):
                    ideas = idea_generator.generate_ideas(
                        session=session, count=5, account_id=account.id
                    )
                    summary["ideas_generated"] = len(ideas)
                else:
                    summary["ideas_generated"] = 0
            _update_run_stage(run_id, "ideas", "complete")
        except Exception as exc:  # noqa: BLE001
            log.warning("Idea generation failed: %s", exc)
            summary["ideas_generation_error"] = repr(exc)
            _update_run_stage(run_id, "ideas", "failed")

        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeError(f"Run {run_id} not found")
            run.status = "complete"
            run.finished_at = datetime.now(timezone.utc)
            run.notes = str(summary)[:2000]

    except Exception as exc:
        log.exception("run %d failed", run_id)
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                run.notes = f"error: {exc!r}"[:2000]
        summary["error"] = repr(exc)

    return summary


def run_full_cycle(account_slug: str | None = None) -> dict[str, Any]:
    init_db()
    accounts = _resolve_accounts(account_slug)
    results = [_run_full_cycle_for_account(account) for account in accounts]
    if account_slug is not None:
        return results[0]
    return {"accounts": results}
