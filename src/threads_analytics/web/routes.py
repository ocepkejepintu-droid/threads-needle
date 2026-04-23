"""Dashboard routes coordinator — delegates to domain-specific route modules."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from .routes_brand import register_brand_routes
from .routes_content import register_content_routes
from .routes_comments import register_comments_routes
from .routes_experiments import register_experiments_routes
from .routes_feedback import register_feedback_routes
from .routes_growth import register_growth_routes
from .routes_pages import register_pages_routes
from .routes_events import register_events_routes
from .routes_pipeline import register_pipeline_routes


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()
    register_pages_routes(router, templates)
    register_experiments_routes(router, templates)
    register_pipeline_routes(router)
    register_brand_routes(router, templates)
    register_growth_routes(router, templates)
    register_feedback_routes(router, templates)
    register_content_routes(router, templates)
    register_events_routes(router)
    register_comments_routes(router, templates)
    return router
