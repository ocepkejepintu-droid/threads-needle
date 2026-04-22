## 2026-04-15
- Web routes in `routes_pages.py` and `routes_experiments.py` now resolve `account_id` from the `account` query param before calling account-scoped helpers.
- `get_account_by_slug(... ) if account else get_or_create_default_account(...)` is the safe pattern for multi-account web handlers.
## 2026-04-15
- Moved idea ownership checks into `account_scope.require_idea_ownership()` so content and growth routes share one account-scope helper.
- Image upload/update routes should invalidate approvals after changing `image_url`; otherwise approved/scheduled ideas can drift out of sync.
