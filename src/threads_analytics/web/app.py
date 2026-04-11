"""FastAPI app factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..db import init_db
from .routes import build_router

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def _intcomma(value: int | float | None) -> str:
    """Format a number with commas as thousand separators."""
    if value is None:
        return "0"
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return str(value)


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="threads-analytics")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["intcomma"] = _intcomma
    app.state.templates = templates
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(build_router(templates))
    return app
