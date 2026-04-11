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
from .growth_patterns import extract_patterns
from .growth_generator import generate_content_ideas, should_generate_ideas
from .models import Lead
from sqlalchemy import select

log = logging.getLogger(__name__)


def run_full_cycle() -> dict:
    init_db()

    with session_scope() as session:
        run = Run(started_at=datetime.now(timezone.utc), status="running")
        session.add(run)
        session.flush()
        run_id = run.id

    summary: dict = {"run_id": run_id}
    try:
        with ThreadsClient() as client:
            # 1. Ingest
            with session_scope() as session:
                run = session.get(Run, run_id)
                summary["ingest"] = ingest_own_data(run, client)

            # 2. Topics
            topics = extract_and_persist_topics()
            summary["topics"] = [t.label for t in topics]

            # 2b. Lead discovery
            try:
                with session_scope() as session:
                    run = session.get(Run, run_id)
                    summary["leads"] = run_lead_searches(run, client)
            except Exception as exc:
                log.warning("Lead search failed: %s", exc)
                summary["leads_error"] = repr(exc)

            # 2c. Lead intent classification and scoring
            try:
                with session_scope() as session:
                    # Get new leads that need intent classification
                    new_leads = session.scalars(
                        select(Lead).where(Lead.status == "new")
                    ).all()

                    # Classify intent for new leads
                    for lead in new_leads:
                        if not lead.intent:
                            result = classify_lead_intent(
                                lead.post_text, lead.author_bio, lead.matched_keyword
                            )
                            lead.intent = result["intent"]
                            lead.intent_confidence = result["confidence"]

                    # Score new leads
                    for lead in new_leads:
                        if not lead.score:
                            score = calculate_lead_score(lead)
                            tier = get_quality_tier(score)
                            save_lead_score(session, lead, score, tier)

                    summary["leads_intent_classified"] = len(
                        [l for l in new_leads if l.intent]
                    )
                    summary["leads_scored"] = len([l for l in new_leads if l.score])
            except Exception as exc:
                log.warning("Lead intent/scoring failed: %s", exc)
                summary["leads_scoring_error"] = repr(exc)

            # 3. Affinity (still locked in dev mode; returns quickly)
            with session_scope() as session:
                run = session.get(Run, run_id)
                summary["affinity"] = discover_affinity_creators(run, client)

        # 4. Ground-truth metrics snapshot
        with session_scope() as session:
            panel = compute_ground_truth(session)
            summary["ground_truth_headline"] = panel.verdict_headline
            summary["ground_truth_metrics"] = {
                k: {"current": v.value, "baseline": panel.baselines[k].value, "delta": panel.deltas[k]}
                for k, v in panel.metrics.items()
            }

        # 5. Refresh classifications for any active experiments
        with session_scope() as session:
            touched = classify_active_experiments(session)
            summary["experiments_classified_posts"] = touched

        # 6. Auto-evaluate experiments whose variant window has closed
        with session_scope() as session:
            completed = auto_evaluate_due(session)
            summary["experiments_auto_completed"] = completed

        # 7. Build 'You' profile (anti-homogenization guardrail). Must run
        #    before suggestions so the suggester can respect the protect list.
        try:
            with session_scope() as session:
                run = session.get(Run, run_id)
                summary["you_profile_run_id"] = generate_you_profile(run)
        except Exception as exc:  # noqa: BLE001
            log.warning("You profile generation failed: %s", exc)
            summary["you_profile_error"] = repr(exc)

        # 8. Generate new suggestions (replaces recommendations)
        try:
            with session_scope() as session:
                new_ids = generate_suggestions(session)
                summary["new_suggestion_ids"] = new_ids
        except Exception as exc:  # noqa: BLE001
            log.warning("suggestion generation failed: %s", exc)
            summary["suggestions_error"] = repr(exc)

        # 8. Public Perception
        try:
            with session_scope() as session:
                run = session.get(Run, run_id)
                summary["public_perception_run_id"] = generate_public_perception(run)
        except Exception as exc:  # noqa: BLE001
            log.warning("public perception generation failed: %s", exc)
            summary["public_perception_error"] = repr(exc)

        # 9. Algorithm Inference
        try:
            with session_scope() as session:
                run = session.get(Run, run_id)
                summary["algorithm_inference_run_id"] = generate_algorithm_inference(run)
        except Exception as exc:  # noqa: BLE001
            log.warning("algorithm inference generation failed: %s", exc)
            summary["algorithm_inference_error"] = repr(exc)

        # 10. Noteworthy posts — outlier detection + Claude commentary
        try:
            with session_scope() as session:
                run = session.get(Run, run_id)
                summary["noteworthy_post_ids"] = generate_noteworthy_commentary(run)
        except Exception as exc:  # noqa: BLE001
            log.warning("noteworthy commentary failed: %s", exc)
            summary["noteworthy_error"] = repr(exc)

        # 11. Weekly pattern extraction (only run on Sundays)
        if datetime.now(timezone.utc).weekday() == 6:
            try:
                with session_scope() as session:
                    patterns = extract_patterns(session)
                    summary["patterns_extracted"] = len(patterns)
            except Exception as exc:  # noqa: BLE001
                log.warning("Pattern extraction failed: %s", exc)
                summary["patterns_extraction_error"] = repr(exc)

        # 12. Daily content idea generation
        try:
            with session_scope() as session:
                if should_generate_ideas(session, threshold=10):
                    ideas = generate_content_ideas(session, count=5)
                    summary["ideas_generated"] = len(ideas)
                else:
                    summary["ideas_generated"] = 0
        except Exception as exc:  # noqa: BLE001
            log.warning("Idea generation failed: %s", exc)
            summary["ideas_generation_error"] = repr(exc)

        with session_scope() as session:
            run = session.get(Run, run_id)
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
