## 2026-04-15
- Kept fixes limited to web callers only; no analytics helpers were changed.
- Accepted `account: str | None = None` on the affected routes to match the existing query-param pattern.
## 2026-04-15
- Use `account` query params on mutation routes to resolve ownership consistently across content and growth handlers.
- Keep approval invalidation outside the DB session block for image mutations and edit saves, matching the existing publish-gate pattern.
