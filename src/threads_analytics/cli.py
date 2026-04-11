"""Typer CLI: run, serve, refresh."""

from __future__ import annotations

import csv
import json
import logging

import typer
import uvicorn

from .backfill import backfill_history
from .config import get_settings
from .db import init_db, session_scope
from .leads_search import run_lead_searches
from .models import Lead
from .pipeline import run_full_cycle
from .threads_client import ThreadsClient
from sqlalchemy import func, select
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = typer.Typer(help="Analytics + growth recommender for a personal Threads account.")


@app.command()
def run() -> None:
    """Run a full ingest → analyze → recommend cycle."""
    init_db()
    summary = run_full_cycle()
    typer.echo(json.dumps(summary, indent=2, default=str))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Start the local dashboard."""
    init_db()
    uvicorn.run("threads_analytics.web.app:create_app", host=host, port=port, reload=reload, factory=True)


@app.command()
def refresh() -> None:
    """Refresh the long-lived Threads access token (run every ~50 days)."""
    settings = get_settings()
    with ThreadsClient() as client:
        new_token = client.refresh_long_lived_token()
    # Persist by rewriting .env
    _update_env_file("THREADS_ACCESS_TOKEN", new_token)
    typer.echo("Token refreshed and .env updated.")


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
def whoami() -> None:
    """Verify the token by calling /me on the Threads API."""
    try:
        with ThreadsClient() as client:
            data = client.get_me()
    except RuntimeError as exc:
        typer.secho(f"✗ {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except Exception as exc:  # noqa: BLE001
        typer.secho(
            f"✗ Threads API call failed: {exc}", fg=typer.colors.RED, err=True
        )
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
):
    """Manually trigger lead search across all active sources."""
    from .threads_client import ThreadsClient
    from .models import Run
    
    init_db()
    
    if manual:
        typer.echo("Running manual lead search...")
    else:
        typer.echo("Checking lead sources...")
    
    with session_scope() as session:
        run = Run(started_at=datetime.now(timezone.utc), status="running")
        session.add(run)
        session.flush()
        run_id = run.id
    
    try:
        with ThreadsClient() as client:
            with session_scope() as session:
                run = session.get(Run, run_id)
                summary = run_lead_searches(run=run, client=client)
        
        with session_scope() as session:
            run = session.get(Run, run_id)
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
    
    if summary['errors']:
        typer.echo(f"Errors: {len(summary['errors'])}", err=True)


@app.command()
def leads_stats():
    """Show lead queue statistics."""
    from .models import LeadSource
    
    with session_scope() as session:
        # Count by status
        status_counts = {}
        for status in ["new", "reviewed", "approved", "sent", "rejected"]:
            count = session.scalar(
                select(func.count()).select_from(Lead).where(Lead.status == status)
            ) or 0
            status_counts[status] = count
        
        # Daily replies sent
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = session.scalar(
            select(func.count()).select_from(Lead).where(
                Lead.status == "sent", Lead.sent_at >= today_start
            )
        ) or 0
        
        # Active sources
        active_sources = session.scalar(
            select(func.count()).select_from(LeadSource).where(LeadSource.is_active == True)
        ) or 0
        
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
def update_reply_metrics():
    """Check for responses to sent replies."""
    from .leads_analytics import update_reply_metrics as _update_reply_metrics
    
    init_db()
    typer.echo("Updating reply metrics...")
    
    with session_scope() as session:
        with ThreadsClient() as client:
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
            writer.writerow([
                "id", "author_username", "intent", "quality_tier", "total_score",
                "status", "post_text", "post_permalink", "matched_keyword"
            ])
            
            for lead, score in leads_with_scores:
                writer.writerow([
                    lead.id,
                    lead.author_username,
                    lead.intent or "unknown",
                    score.quality_tier if score else "unknown",
                    score.total_score if score else 0,
                    lead.status,
                    lead.post_text[:200] if lead.post_text else "",
                    lead.post_permalink,
                    lead.matched_keyword
                ])
        
        typer.echo(f"Exported {len(leads_with_scores)} leads to {output}")


@app.command()
def template_stats():
    """Show reply template performance."""
    from .leads_analytics import calculate_template_stats
    from .models import ReplyTemplate
    
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
            rate_pct = f"{template['response_rate']*100:.1f}%"
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
def brand_check(text: str = typer.Argument(...)):
    """Check brand alignment of text."""
    from .brand_validator import validate_content
    from .models import YouProfile
    
    init_db()
    
    with session_scope() as session:
        # Get latest YouProfile
        you_profile = session.scalar(select(YouProfile).order_by(YouProfile.run_id.desc()))
        
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
def brand_health():
    """Show brand health stats."""
    from .models import YouProfile
    
    init_db()
    
    with session_scope() as session:
        # Get latest YouProfile
        you_profile = session.scalar(select(YouProfile).order_by(YouProfile.run_id.desc()))
        
        if not you_profile:
            typer.secho("No YouProfile found. Run analysis first.", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        
        # Get profile from last week for comparison
        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        previous_profile = session.scalar(
            select(YouProfile)
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
        protect_list = you_profile.protect_list or []
        typer.echo(f"Protect List Items: {len(protect_list)}")
        for item in protect_list[:5]:
            typer.echo(f"  - {item}")
        if len(protect_list) > 5:
            typer.echo(f"  ... and {len(protect_list) - 5} more")
        typer.echo("")
        
        # Double-down list
        double_down = you_profile.double_down_list or []
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
def extract_patterns():
    """Extract patterns from top posts."""
    from .growth_patterns import extract_patterns as _extract_patterns
    
    init_db()
    typer.echo("Extracting patterns from top posts...")
    
    with session_scope() as session:
        patterns = _extract_patterns(session)
    
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
def generate_ideas(count: int = typer.Option(5, "--count", "-c")):
    """Generate content ideas."""
    from .growth_generator import generate_content_ideas
    
    init_db()
    typer.echo(f"Generating {count} content ideas...")
    
    with session_scope() as session:
        ideas = generate_content_ideas(session, count=count)
    
    if not ideas:
        typer.secho("No ideas generated. Check that you have patterns and a YouProfile.", fg=typer.colors.YELLOW)
        return
    
    typer.echo(f"\nGenerated {len(ideas)} ideas:\n")
    
    for i, idea in enumerate(ideas, 1):
        typer.echo(f"{i}. {idea.title}")
        typer.echo(f"   Score: {idea.predicted_score}/100")
        if idea.predicted_views_range:
            typer.echo(f"   Views: {idea.predicted_views_range}")
        if idea.concept:
            concept_preview = idea.concept[:150].replace(chr(10), " ")
            if len(idea.concept) > 150:
                concept_preview += "..."
            typer.echo(f"   {concept_preview}")
        typer.echo("")


@app.command()
def predict_performance(text: str = typer.Argument(...)):
    """Predict performance of a draft post."""
    from .growth_generator import predict_performance as _predict_performance
    from .models import GeneratedIdea
    
    init_db()
    
    # Create a temporary idea for prediction
    temp_idea = GeneratedIdea(
        title="Preview",
        concept=text,
        patterns_used=[],  # No patterns for raw text
    )
    
    score, views_range = _predict_performance(temp_idea)
    
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


if __name__ == "__main__":
    app()
