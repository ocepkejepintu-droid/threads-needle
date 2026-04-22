"""FastAPI app factory."""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..db import init_db
from ..scheduler import start_scheduler, stop_scheduler
from .routes import build_router

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Simple in-memory rate limiting: max 360 requests per 60s per IP
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 360
_request_log: dict[str, list[float]] = defaultdict(list)


def _intcomma(value: int | float | None) -> str:
    """Format a number with commas as thousand separators."""
    if value is None:
        return "0"
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return str(value)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown events."""
    # Startup
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(
        title="threads-analytics",
        lifespan=lifespan,
        max_request_size=10 * 1024 * 1024,  # 10MB max request body
    )

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = _request_log[client_ip]
        # Prune old entries
        while window and window[0] < now - _RATE_LIMIT_WINDOW:
            window.pop(0)
        if len(window) >= _RATE_LIMIT_MAX:
            return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)
        window.append(now)
        return await call_next(request)

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["intcomma"] = _intcomma
    app.state.templates = templates
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(build_router(templates))
    return app
