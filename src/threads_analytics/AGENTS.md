# threads_analytics — Core Package

**Scope:** Business logic, LLM pipelines, ORM models, and CLI commands.

## Structure

```
src/threads_analytics/
  cli.py              # Typer CLI entrypoint
  config.py           # Pydantic Settings + .env override logic
  db.py               # SQLAlchemy engine, session_scope(), init_db()
  models.py           # All ORM tables (Base, ~25 models)
  pipeline.py         # run_full_cycle() orchestrates the full run
  threads_client.py   # Thin wrapper around Threads Graph API
  web/                # FastAPI layer (separate AGENTS.md)
```

## Where to Look

| Task | Location | Notes |
|------|----------|-------|
| Add a CLI command | `cli.py` | Use `typer` decorators; follow existing pattern |
| Change DB schema | `models.py` | Then run `threads-analytics run` to auto-migrate via `Base.metadata.create_all()` |
| Add pipeline stage | `pipeline.py` | Wrap in try/except + log.warning; don't let LLM failures crash the run |
| Change experiment logic | `experiments.py`, `predicates.py`, `verdict.py` | Predicates classify posts; verdict runs Mann-Whitney + bootstrap CI |
| Change LLM prompts | `perception.py`, `algorithm_inference.py`, `you.py`, `noteworthy.py` | Prompts are inline strings; respect word limits |
| Add metric | `metrics.py` | Update `METRIC_META`, `METRIC_ORDER`, and direction logic |
| Change web routes | `web/routes.py` | Separate AGENTS.md for web conventions |

## Conventions

- **Transactions:** Always use `session_scope()` context manager. Never pass sessions across thread boundaries.
- **Entrypoints:** Both CLI and web call `init_db()` early.
- **LLM resilience:** All LLM stages in `pipeline.py` are wrapped in `try/except` with `log.warning`. A failed stage must not abort the run.
- **Type hints:** `from __future__ import annotations` at the top of every module. Target Python 3.11+.
- **Import style:** Absolute imports within package (`from .models import ...`).

## Anti-Patterns

- **Never** use a relative SQLite path without `_resolve_sqlite_url()` — it breaks when cwd changes.
- **Never** let an unhandled exception in an LLM call crash the pipeline.
- **Never** cite a specific numeric weight as fact in algo-facing copy unless it's from documented research.
- **Never** attribute causation to "the algorithm" for a single post (use "likely" / "consistent with").
