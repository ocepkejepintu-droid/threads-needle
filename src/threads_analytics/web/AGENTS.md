# threads_analytics/web — Web Layer

**Scope:** FastAPI routes, Jinja2 templates, and static assets.

## Structure

```
web/
  app.py            # FastAPI factory (create_app), lifespan, scheduler
  routes.py         # All HTTP routes (~1700 lines)
  templates/        # Jinja2 HTML (~25 templates)
  static/           # CSS, JS, uploads
```

## Where to Look

| Task | Location | Notes |
|------|----------|-------|
| Add a page | `routes.py` + `templates/` | Register route in `build_router()`; extend `base.html` |
| Change global chrome | `templates/base.html`, `static/style.css` | Follow STYLE_GUIDE.md palette |
| Change ground truth UI | `templates/ground_truth.html` | Hero card logic lives in `routes.py` (`_ground_truth_payload`) |
| Add experiment action | `routes.py` | POST handlers redirect with 303 |
| Change animations | `static/animate.js` | View Transitions API for page swaps |

## Conventions

- **DB access:** Every route handler wraps DB work in `session_scope()`.
- **Redirects:** After POST, always return `RedirectResponse(..., status_code=303)`.
- **Templates:** All templates extend `base.html` and use the CSS tokens from `STYLE_GUIDE.md`.
- **Rate limiting:** In-memory middleware in `app.py` (360 req / 60s per IP).

## Anti-Patterns

- **No gradients, no glass-morphism, no heavy shadows** outside the run banner.
- **One hero per page** (Ground Truth gets two). Hero colors are function-coded — never decorative.
- **Hypothesis labels:** Every claim about the algorithm must show the `HYPOTHESIS` chip or a citation.
- **No walls of text:** If a block >30 words describes a distribution, replace with a chart or chip row.
- **Respect `prefers-reduced-motion`** — animations must be killable.
