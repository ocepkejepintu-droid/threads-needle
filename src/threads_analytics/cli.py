"""Typer CLI: run, serve, refresh."""

from __future__ import annotations

import csv
import json
import logging
from typing import cast

import typer
import uvicorn

from .account_scope import get_account_by_slug, get_or_create_default_account, get_scoped
from .backfill import backfill_history
from .config import get_settings
from .db import init_db, session_scope
from .leads_search import run_lead_searches
from .models import GeneratedIdea, Lead
from .pipeline import run_full_cycle
from .threads_client import ThreadsClient
from sqlalchemy import func, select
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = typer.Typer(help="Analytics + growth recommender for a personal Threads account.")


def _resolve_account_or_exit(account: str | None):
    with session_scope() as session:
        acct = (
            get_account_by_slug(session, account)
            if account
            else get_or_create_default_account(session)
        )
    if acct is None:
        typer.secho(f"Unknown account slug: {account}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return acct


@app.command()
def run(account: str | None = typer.Option(None, help="Account slug")) -> None:
    """Run a full ingest → analyze → recommend cycle."""
    init_db()
    summary = run_full_cycle(account_slug=account)
    typer.echo(json.dumps(summary, indent=2, default=str))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Start the local dashboard."""
    init_db()
    uvicorn.run(
        "threads_analytics.web.app:create_app", host=host, port=port, reload=reload, factory=True
    )


@app.command()
def refresh(
    account: str | None = typer.Option(None, help="Account slug"),
) -> None:
    """Refresh the long-lived Threads access token (run every ~50 days)."""
    acct = _resolve_account_or_exit(account)
    client = ThreadsClient.from_account(acct)
    new_token = client.refresh_long_lived_token()
    from .account_scope import DEFAULT_ACCOUNT_SLUG

    if acct.slug == DEFAULT_ACCOUNT_SLUG:
        _update_env_file("THREADS_ACCESS_TOKEN", new_token)
    with session_scope() as session:
        from .models import Account

        db_account = session.get(Account, acct.id)
        if db_account is not None:
            db_account.threads_access_token = new_token
    typer.echo("Token refreshed and account updated.")


@app.command()
def backfill(
    bucket_days: int = 1,
    max_days_back: int = 180,
    window_days: int = 14,
) -> None:
    """Populate historical Ground Truth snapshots from existing post data.

    Walks backwards from today in buckets of `bucket_days`, computing the
    same Ground Truth metrics at each point in time using the posts already
    in the database. Each bucket creates a synthetic run + account insight
    row so the sparklines on / show real history.

    Idempotent — running twice does not create duplicates.

    Caveats: follower count is not historically tracked, so reach rate and
    follower velocity are not accurate during backfill. Reply rate, reply
    ratio, zero-reply fraction, and top-decile reach multiple ARE accurate.
    """
    init_db()
    summary = backfill_history(
        bucket_days=bucket_days,
        max_days_back=max_days_back,
        window_days=window_days,
    )
    typer.echo(json.dumps(summary, indent=2, default=str))


@app.command()
def whoami(account: str | None = typer.Option(None, help="Account slug")) -> None:
    """Verify the token by calling /me on the Threads API."""
    try:
        acct = _resolve_account_or_exit(account)
        with ThreadsClient.from_account(acct) as client:
            data = client.get_me()
    except RuntimeError as exc:
        typer.secho(f"✗ {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"✗ Threads API call failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(data, indent=2))


def _update_env_file(key: str, value: str, path: str = ".env") -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    found = False
    out: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}\n")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)


@app.command()
def search_leads(
    manual: bool = typer.Option(False, "--manual", "-m", help="Run even if not due by frequency"),
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Manually trigger lead search across all active sources."""
    from .threads_client import ThreadsClient
    from .models import Run

    init_db()

    if manual:
        typer.echo("Running manual lead search...")
    else:
        typer.echo("Checking lead sources...")

    acct = _resolve_account_or_exit(account)

    with session_scope() as session:
        run = Run(account_id=acct.id, started_at=datetime.now(timezone.utc), status="running")
        session.add(run)
        session.flush()
        run_id = run.id

    try:
        with ThreadsClient.from_account(acct) as client:
            with session_scope() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError(f"Run {run_id} not found")
                summary = run_lead_searches(run=run, client=client)

        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeError(f"Run {run_id} not found")
            run.status = "complete"
            run.finished_at = datetime.now(timezone.utc)
    except Exception as exc:
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                run.notes = f"error: {exc!r}"[:2000]
        raise

    typer.echo(f"Sources searched: {summary['sources_searched']}")
    typer.echo(f"Posts found: {summary['posts_found']}")
    typer.echo(f"Leads created: {summary['leads_created']}")

    if summary["errors"]:
        typer.echo(f"Errors: {len(summary['errors'])}", err=True)


@app.command()
def leads_stats():
    """Show lead queue statistics."""
    from .models import LeadSource

    with session_scope() as session:
        # Count by status
        status_counts = {}
        for status in ["new", "reviewed", "approved", "sent", "rejected"]:
            count = (
                session.scalar(select(func.count()).select_from(Lead).where(Lead.status == status))
                or 0
            )
            status_counts[status] = count

        # Daily replies sent
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = (
            session.scalar(
                select(func.count())
                .select_from(Lead)
                .where(Lead.status == "sent", Lead.sent_at >= today_start)
            )
            or 0
        )

        # Active sources
        active_sources = (
            session.scalar(select(func.count()).select_from(LeadSource).where(LeadSource.is_active))
            or 0
        )

        typer.echo("Lead Finder Statistics")
        typer.echo("=" * 40)
        typer.echo(f"Active sources: {active_sources}")
        typer.echo("")
        typer.echo("Queue status:")
        typer.echo(f"  New: {status_counts['new']}")
        typer.echo(f"  Reviewed: {status_counts['reviewed']}")
        typer.echo(f"  Approved: {status_counts['approved']}")
        typer.echo(f"  Sent: {status_counts['sent']}")
        typer.echo(f"  Rejected: {status_counts['rejected']}")
        typer.echo("")
        typer.echo(f"Replies sent today: {sent_today}/10")


# =============================================================================
# Lead Engine v2 commands
# =============================================================================


@app.command()
def run_comments(
    manual: bool = typer.Option(
        False, "--manual", "-m", help="Run lead search even if not due by frequency"
    ),
    draft_max: int = typer.Option(15, "--draft-max", "-d", help="Maximum reply drafts to generate"),
    min_tier: str = typer.Option(
        "medium", "--min-tier", "-t", help="Minimum quality tier for drafting replies"
    ),
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Run the full comment/lead engine pipeline: search → intent → score → draft replies."""
    from .pipeline import run_comments_cycle

    init_db()
    typer.echo("Running full comment engine pipeline...")

    summary = run_comments_cycle(draft_max=draft_max, min_tier=min_tier, account_slug=account)

    typer.echo(f"Sources searched: {summary['leads']['sources_searched']}")
    typer.echo(f"Posts found: {summary['leads']['posts_found']}")
    typer.echo(f"Leads created: {summary['leads']['leads_created']}")
    typer.echo(f"Intent classified: {summary.get('leads_intent_classified', 0)}")
    typer.echo(f"Leads scored: {summary.get('leads_scored', 0)}")
    typer.echo(f"Reply drafts generated: {summary.get('leads_reply_drafts', 0)}")

    if summary["leads"].get("errors"):
        typer.echo(f"Errors: {len(summary['leads']['errors'])}", err=True)


@app.command()
def draft_replies(
    min_tier: str = typer.Option(
        "medium", "--min-tier", "-t", help="Minimum quality tier (high, medium, low)"
    ),
    max: int = typer.Option(20, "--max", "-n", help="Maximum drafts to generate"),
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Generate AI reply drafts for leads that don't have one yet."""
    from .leads import draft_replies_for_leads

    init_db()
    acct = _resolve_account_or_exit(account)
    typer.echo(f"Generating reply drafts (min_tier={min_tier}, max={max})...")

    with session_scope() as session:
        count = draft_replies_for_leads(
            session, min_tier=min_tier, max_per_run=max, account_id=acct.id
        )

    typer.echo(f"Generated {count} reply drafts.")


@app.command()
def update_reply_metrics(
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Check for responses to sent replies."""
    from .leads_analytics import update_reply_metrics as _update_reply_metrics

    init_db()
    acct = _resolve_account_or_exit(account)
    typer.echo("Updating reply metrics...")

    with session_scope() as session:
        with ThreadsClient.from_account(acct) as client:
            result = _update_reply_metrics(session, client)

    typer.echo(f"Checked: {result['checked']}")
    typer.echo(f"New responses: {result['new_responses']}")


@app.command()
def export_leads(
    intent: str = typer.Option(None, "--intent", "-i"),
    quality: str = typer.Option(None, "--quality", "-q"),
    output: str = typer.Option("leads_export.csv", "--output", "-o"),
):
    """Export leads to CSV."""
    from .models import LeadScore

    init_db()

    with session_scope() as session:
        # Build query with filters
        query = select(Lead)

        if intent:
            query = query.where(Lead.intent == intent)

        # Get leads and their scores
        leads = session.scalars(query).all()

        # Apply quality filter if specified
        if quality:
            filtered_leads = []
            for lead in leads:
                score = session.get(LeadScore, lead.id)
                if score and score.quality_tier == quality:
                    filtered_leads.append((lead, score))
            leads_with_scores = filtered_leads
        else:
            leads_with_scores = []
            for lead in leads:
                score = session.get(LeadScore, lead.id)
                leads_with_scores.append((lead, score))

        # Write CSV
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "author_username",
                    "intent",
                    "quality_tier",
                    "total_score",
                    "status",
                    "post_text",
                    "post_permalink",
                    "matched_keyword",
                ]
            )

            for lead, score in leads_with_scores:
                writer.writerow(
                    [
                        lead.id,
                        lead.author_username,
                        lead.intent or "unknown",
                        score.quality_tier if score else "unknown",
                        score.total_score if score else 0,
                        lead.status,
                        lead.post_text[:200] if lead.post_text else "",
                        lead.post_permalink,
                        lead.matched_keyword,
                    ]
                )

        typer.echo(f"Exported {len(leads_with_scores)} leads to {output}")


@app.command()
def template_stats():
    """Show reply template performance."""
    from .leads_analytics import calculate_template_stats

    init_db()

    with session_scope() as session:
        stats = calculate_template_stats(session)

        if not stats:
            typer.echo("No templates found.")
            return

        typer.echo("Template Performance")
        typer.echo("=" * 60)
        typer.echo(f"{'Name':<25} {'Used':>8} {'Responded':>10} {'Rate':>8} {'Winner':>7}")
        typer.echo("-" * 60)

        for template in stats:
            winner_mark = "✓" if template["is_winner"] else ""
            rate_pct = f"{template['response_rate'] * 100:.1f}%"
            typer.echo(
                f"{template['name'][:24]:<25} "
                f"{template['times_used']:>8} "
                f"{template['times_responded']:>10} "
                f"{rate_pct:>8} "
                f"{winner_mark:>7}"
            )


# =============================================================================
# Brand Brain commands
# =============================================================================


@app.command()
def brand_check(
    text: str = typer.Argument(...),
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Check brand alignment of text."""
    from .brand_validator import validate_content
    from .models import YouProfile

    init_db()
    acct = _resolve_account_or_exit(account)

    with session_scope() as session:
        you_profile = session.scalar(
            select(YouProfile)
            .where(YouProfile.account_id == acct.id)
            .order_by(YouProfile.run_id.desc())
        )

        if not you_profile:
            typer.secho("No YouProfile found. Run analysis first.", fg=typer.colors.RED)
            raise typer.Exit(code=1)

        result = validate_content(text, you_profile)

        # Display results
        color = typer.colors.GREEN if result.passed else typer.colors.RED
        typer.echo(f"Overall Score: {result.overall_score}/100")
        typer.secho(f"Passed: {result.passed}", fg=color)
        typer.echo(f"Voice Alignment: {result.voice_alignment}/100")

        if result.protect_violations:
            typer.echo("")
            typer.secho("Protect List Violations:", fg=typer.colors.RED)
            for v in result.protect_violations:
                typer.echo(f"  - {v}")

        if result.double_down_elements:
            typer.echo("")
            typer.secho("Double-Down Elements Present:", fg=typer.colors.GREEN)
            for e in result.double_down_elements:
                typer.echo(f"  - {e}")

        if result.suggestions:
            typer.echo("")
            typer.echo("Suggestions:")
            for s in result.suggestions:
                typer.echo(f"  - {s.issue}: {s.suggestion}")


@app.command()
def brand_health(
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Show brand health stats."""
    from .models import YouProfile

    init_db()
    acct = _resolve_account_or_exit(account)

    with session_scope() as session:
        you_profile = session.scalar(
            select(YouProfile)
            .where(YouProfile.account_id == acct.id)
            .order_by(YouProfile.run_id.desc())
        )

        if not you_profile:
            typer.secho("No YouProfile found. Run analysis first.", fg=typer.colors.RED)
            raise typer.Exit(code=1)

        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        previous_profile = session.scalar(
            select(YouProfile)
            .where(YouProfile.account_id == acct.id)
            .where(YouProfile.created_at < one_week_ago)
            .order_by(YouProfile.created_at.desc())
        )

        typer.echo("Brand Health Report")
        typer.echo("=" * 40)
        typer.echo("")

        # Core identity preview
        if you_profile.core_identity:
            typer.echo("Core Identity:")
            identity_preview = you_profile.core_identity[:200]
            if len(you_profile.core_identity) > 200:
                identity_preview += "..."
            typer.echo(f"  {identity_preview}")
            typer.echo("")

        # Protect list
        protect_list = (
            cast(list[object], you_profile.protect_list)
            if isinstance(you_profile.protect_list, list)
            else []
        )
        typer.echo(f"Protect List Items: {len(protect_list)}")
        for item in protect_list[:5]:
            typer.echo(f"  - {item}")
        if len(protect_list) > 5:
            typer.echo(f"  ... and {len(protect_list) - 5} more")
        typer.echo("")

        # Double-down list
        double_down = (
            cast(list[object], you_profile.double_down_list)
            if isinstance(you_profile.double_down_list, list)
            else []
        )
        typer.echo(f"Double-Down Items: {len(double_down)}")
        for item in double_down[:5]:
            typer.echo(f"  - {item}")
        if len(double_down) > 5:
            typer.echo(f"  ... and {len(double_down) - 5} more")
        typer.echo("")

        # Stylistic signatures
        signatures = you_profile.stylistic_signatures or []
        typer.echo(f"Stylistic Signatures: {len(signatures)}")

        # Comparison
        if previous_profile:
            typer.echo("")
            typer.echo("Week-over-week:")
            sig_diff = len(signatures) - len(previous_profile.stylistic_signatures or [])
            if sig_diff > 0:
                typer.secho(f"  +{sig_diff} new stylistic signatures", fg=typer.colors.GREEN)
            elif sig_diff < 0:
                typer.secho(f"  {sig_diff} stylistic signatures", fg=typer.colors.YELLOW)
            else:
                typer.echo("  No change in stylistic signatures")


# =============================================================================
# Growth OS commands
# =============================================================================


@app.command()
def extract_patterns(
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Extract patterns from top posts."""
    from .growth_patterns import extract_patterns as _extract_patterns

    init_db()
    acct = _resolve_account_or_exit(account)
    typer.echo("Extracting patterns from top posts...")

    with session_scope() as session:
        patterns = _extract_patterns(session, acct.id)

    if not patterns:
        typer.echo("No patterns found. Make sure you have posts in the database.")
        return

    # Group by type
    hooks = [p for p in patterns if p.pattern_type == "hook"]
    structures = [p for p in patterns if p.pattern_type == "structure"]
    timings = [p for p in patterns if p.pattern_type == "timing"]

    typer.echo(f"\nExtracted {len(patterns)} patterns:")

    if hooks:
        typer.echo(f"\n  Hooks ({len(hooks)}):")
        for p in hooks[:3]:
            typer.echo(f"    - {p.pattern_name} (conf: {p.confidence_score:.2f})")

    if structures:
        typer.echo(f"\n  Structures ({len(structures)}):")
        for p in structures[:3]:
            typer.echo(f"    - {p.pattern_name} ({p.example_count} examples)")

    if timings:
        typer.echo(f"\n  Timings ({len(timings)}):")
        for p in timings:
            typer.echo(f"    - {p.pattern_name}")


@app.command()
def predict_performance(text: str = typer.Argument(...)):
    """Predict performance of a draft post."""
    from . import idea_generator
    from .models import GeneratedIdea

    init_db()

    # Create a temporary idea for prediction
    temp_idea = GeneratedIdea(
        title="Preview",
        concept=text,
        patterns_used=[],  # No patterns for raw text
    )

    score, views_range = idea_generator.predict_performance(temp_idea)

    # Determine color based on score
    if score >= 70:
        color = typer.colors.GREEN
    elif score >= 50:
        color = typer.colors.YELLOW
    else:
        color = typer.colors.RED

    typer.echo("Performance Prediction")
    typer.echo("=" * 30)
    typer.secho(f"Score: {score}/100", fg=color)
    typer.echo(f"Predicted Views: {views_range}")

    # Provide feedback
    typer.echo("")
    if score >= 85:
        typer.secho("✓ Strong potential! Consider using this.", fg=typer.colors.GREEN)
    elif score >= 70:
        typer.secho("~ Good potential. Could be improved with patterns.", fg=typer.colors.YELLOW)
    elif score >= 50:
        typer.secho("! Average. Try adding hook patterns or structure.", fg=typer.colors.YELLOW)
    else:
        typer.secho("✗ Weak. Consider rewriting with proven patterns.", fg=typer.colors.RED)


@app.command("generate-ideas")
def generate_ideas(
    topic: str | None = typer.Argument(None, help="Optional topic to generate ideas about"),
    count: int = typer.Option(5, "--count", "-c", help="Number of ideas to generate"),
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Generate content ideas using the canonical idea engine."""
    from . import idea_generator

    init_db()

    if topic:
        typer.echo(f"Generating {count} ideas about: {topic}")
    else:
        typer.echo(f"Generating {count} ideas from your canonical idea pipeline")
    typer.echo("=" * 50)

    acct = _resolve_account_or_exit(account)
    ideas = idea_generator.generate_ideas(topic=topic, count=count, account_id=acct.id)

    if not ideas:
        typer.secho(
            "No ideas generated. Check that you have patterns, posts, and a YouProfile.",
            fg=typer.colors.YELLOW,
        )
        return

    for idea in ideas:
        score = idea.predicted_score
        if score >= 80:
            color = typer.colors.GREEN
        elif score >= 60:
            color = typer.colors.YELLOW
        else:
            color = typer.colors.RED

        typer.echo("")
        typer.secho(f"ID: {idea.id} | Score: {score}/100", fg=color)
        typer.secho(f"Title: {idea.title}", bold=True)
        if idea.predicted_views_range:
            typer.echo(f"Views: {idea.predicted_views_range}")

        concept = idea.concept
        if len(concept) > 200:
            concept = concept[:200] + "..."
        typer.echo(f"Concept: {concept}")

        typer.echo("-" * 50)

    typer.echo("")
    typer.echo(f"Generated {len(ideas)} ideas. View at /growth/ideas to schedule.")


@app.command()
def list_scheduled(
    account: str | None = typer.Option(None, help="Account slug"),
):
    """List all scheduled posts."""
    from .db import session_scope
    from .models import GeneratedIdea
    from sqlalchemy import select

    init_db()
    acct = _resolve_account_or_exit(account)

    with session_scope() as session:
        scheduled = session.scalars(
            select(GeneratedIdea)
            .where(GeneratedIdea.status == "scheduled")
            .where(GeneratedIdea.account_id == acct.id)
        ).all()

        if not scheduled:
            typer.echo("No scheduled posts.")
            return

        typer.echo(f"Scheduled Posts ({len(scheduled)})")
        typer.echo("=" * 50)

        for post in scheduled:
            scheduled_time = (
                post.scheduled_at.strftime("%Y-%m-%d %H:%M") if post.scheduled_at else "N/A"
            )
            typer.echo(f"ID: {post.id}")
            typer.echo(f"Title: {post.title}")
            typer.echo(f"Scheduled: {scheduled_time}")
            preview = post.concept[:100] + "..." if len(post.concept) > 100 else post.concept
            typer.echo(f"Preview: {preview}")
            typer.echo("-" * 50)


@app.command()
def post_now(
    idea_id: int = typer.Argument(..., help="ID of idea to post immediately"),
    account: str | None = typer.Option(None, help="Account slug"),
):
    """Post an idea immediately (bypass scheduling)."""
    from .publish_gate import gate_publish_idea
    from .publisher import publish_post

    init_db()
    acct = _resolve_account_or_exit(account)

    with session_scope() as session:
        idea = get_scoped(session, GeneratedIdea, idea_id, acct.id)
        if not idea:
            typer.secho(f"Idea {idea_id} not found", fg=typer.colors.RED)
            raise typer.Exit(1)

        if idea.status == "published":
            typer.secho("Already published!", fg=typer.colors.YELLOW)
            raise typer.Exit(1)

        gate = gate_publish_idea(idea_id)
        if not gate.allowed:
            typer.secho(f"✗ {gate.reason}", fg=typer.colors.RED)
            raise typer.Exit(1)

        typer.echo(f"Publishing: {idea.title}")
        typer.echo(idea.concept[:200] + "...")
        typer.echo("")

        try:
            thread_id = publish_post(
                idea.concept,
                account_id=acct.id,
                source_type="idea",
                source_id=idea.id,
                workflow_type="post_now",
            )
            idea.status = "published"
            idea.thread_id = thread_id
            idea.posted_at = datetime.now(timezone.utc)
            typer.secho(f"✓ Published! Thread ID: {thread_id}", fg=typer.colors.GREEN)
        except Exception as e:
            typer.secho(f"✗ Failed: {e}", fg=typer.colors.RED)
            raise typer.Exit(1)


@app.command()
def backup(
    output_dir: str = typer.Option("backups", help="Directory to write backup files"),
) -> None:
    """Create a timestamped backup of the SQLite database."""
    import shutil
    from pathlib import Path

    settings = get_settings()
    db_path = settings.database_url.replace("sqlite:///", "")
    db_file = Path(db_path)

    if not db_file.exists():
        typer.secho(f"Database not found at {db_file}", fg=typer.colors.RED)
        raise typer.Exit(1)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"threads_backup_{timestamp}.db"
    backup_path = out_dir / backup_name

    shutil.copy2(db_file, backup_path)
    typer.secho(f"✓ Backed up to {backup_path}", fg=typer.colors.GREEN)


@app.command()
def intake(
    account: str | None = typer.Option(None, help="Account slug"),
) -> None:
    """Run the daily intake fetcher manually."""
    from .intake.runner import run_intake_cycle

    with session_scope() as session:
        acct = _resolve_account(session, account)
        result = run_intake_cycle(account_id=acct.id)
        typer.secho(
            f"✓ Intake complete: {result['persisted']} new items from {result['sources']}",
            fg=typer.colors.GREEN,
        )


@app.command()
def tag_outcomes(
    account: str | None = typer.Option(None, help="Account slug"),
    backfill: bool = typer.Option(False, help="Backfill all published posts"),
) -> None:
    """Run outcome tagging for published posts."""
    from .outcome_tagger import backfill_outcomes, run_outcome_tagging_cycle

    with session_scope() as session:
        acct = _resolve_account(session, account)
        if backfill:
            result = backfill_outcomes(account_id=acct.id)
            typer.secho(
                f"✓ Backfilled {result['tagged']} posts ({result['errors']} errors)",
                fg=typer.colors.GREEN,
            )
        else:
            result = run_outcome_tagging_cycle(account_id=acct.id)
            typer.secho(
                f"✓ Tagged {result['tagged']} posts, skipped {result['skipped']}, errors {result['errors']}",
                fg=typer.colors.GREEN,
            )


if __name__ == "__main__":
    app()
