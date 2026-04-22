# Content Flywheel Design

## Overview

Extend the existing Growth OS "GeneratedIdea" system into a full content scheduling and auto-publishing flywheel. AI generates ideas using your voice patterns, you schedule with one click, and posts automatically publish to Threads at optimal times.

**Key constraint:** Content must feel human — specific details, casual voice, real struggles, not generic advice.

---

## Architecture

### Data Model Extension

Extend `GeneratedIdea` model with scheduling fields:

```python
# New fields on GeneratedIdea
scheduled_at: datetime | None   # When to post (UTC)
posted_at: datetime | None      # When actually posted
thread_id: str | None           # Threads post ID after publishing
error_message: str | None       # If publishing failed
```

**Status simplified to:** `draft` → `scheduled` → `published` (or `rejected`)

**Rationale:** Single table tracks full lifecycle from idea to published post. Keeps history, enables analytics, no joins needed.

---

## State Machine

```
┌─────────┐    generate     ┌─────────┐   schedule    ┌─────────┐   publish    ┌─────────┐
│  draft  │ ───────────────→ │  draft  │ ────────────→ │scheduled│ ───────────→ │published│
│ (empty) │                  │ (ready) │               │         │              │         │
└─────────┘                  └─────────┘               └─────────┘              └─────────┘
                                    │
                                    ↓ reject
                              ┌─────────┐
                              │rejected │
                              └─────────┘
```

| From | To | Trigger | Action |
|------|-----|---------|--------|
| empty | draft | AI generates | Validate anti-slop rules, score |
| draft | scheduled | User clicks "Schedule" | Set `scheduled_at`, status change |
| scheduled | published | Background job | Call Threads API, set `thread_id` |
| scheduled | failed | Publish error | Retry 3x, mark failed |
| draft | rejected | User clicks "Dismiss" | Soft delete |

---

## Anti-Slop Rules

**Core problem:** AI generates generic, template-y content.

**Solution:** Hard validation rules reject AI slop before it reaches user.

### Voice Patterns (extracted from top posts)

| Pattern | Example |
|---------|---------|
| Relatable Struggle Opener | "Susah juga ya cari akuntan..." |
| Specific Detail Requirement | "AR/AP/GL bank reconcile", "1.6B token" |
| Indo-English Mix | Casual Indonesian with English terms |
| Real Frustration | "Threads, do your magic!" |

### Validation Rules (reject if match)

1. **Generic opening** — "In today's fast-paced world...", "Hey everyone!", "I wanted to share..."
2. **No specific numbers** — Must contain one: price, count, percentage, duration
3. **Corporate speak** — "leverage", "synergy", "optimize", "strategic", "holistic"
4. **Advice-template** — "Here are 5 tips..." without personal context
5. **No emotional hook** — Must show frustration, curiosity, or excitement

### Scoring

- Pass all rules → Human-ness score 80-100
- Fail 1 rule → Score 50-70, flag for review
- Fail 2+ rules → Reject, regenerate with different pattern

---

## Background Publisher

**Pattern:** Threading.Timer-based polling (same as existing `/run` endpoint)

```python
def publisher_loop():
    due_posts = find_posts_where(
        scheduled_at <= now() AND status == "scheduled"
    )
    for post in due_posts:
        try:
            thread_id = threads_client.publish_post(post.concept)
            post.status = "published"
            post.thread_id = thread_id
            post.posted_at = now()
        except RateLimitError:
            pass  # Wait for next cycle
        except Exception as e:
            post.error_message = str(e)
            post.status = "failed"
```

**Rate limit handling:**
- Track posts sent in last 24 hours
- Warn user at 8/10 posts in UI
- Queue silently if at limit

---

## UI Components

### 1. Idea Card (Updated)

```
┌─────────────────────────────────────┐
│  Hiring accountant struggle         │  ← title
│  Score: 85                          │
├─────────────────────────────────────┤
│  "Susah juga ya cari akuntan..."    │  ← concept preview
│  ...                                │
├─────────────────────────────────────┤
│  [Schedule ▼]  [Edit]  [Dismiss]    │
└─────────────────────────────────────┘
```

### 2. Quick Schedule Dropdown

```
┌─────────────────┐
│ Quick Schedule  │
├─────────────────┤
│ Next Fri 9am    │  ← suggested optimal
│ Next Fri 10am   │
│ Custom...       │  ← opens datetime picker
└─────────────────┘
```

**One click** on "Next Fri 9am" → schedules immediately, shows "Scheduled for Fri 9am"

### 3. Calendar View

```
Week of Apr 14-20          [◀ Prev] [Next ▶]

Mon 14    Tue 15    Wed 16    Thu 17    Fri 18 ✨    Sat 19    Sun 20
────────  ────────  ────────  ────────  ──────────  ────────  ────────
          🟦 9am              🟦 10am   🟩 9am
          "Susah..."          "New..."  "Posted:   🟨 2pm
                                            Floo..."  "Failed"
```

**Color coding:**
- 🟦 Blue = Scheduled
- 🟩 Green = Published
- 🟨 Yellow = Failed
- ⬜ Empty = No post

**Drag behavior:**
- Drop on different day → updates `scheduled_at` to that day, same time
- Drop on same day → snaps to nearest slot (9am, 10am, 12pm)

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/growth/ideas/generate` | Generate new ideas with topic |
| POST | `/growth/ideas/{id}/schedule` | Schedule idea (body: `scheduled_at`) |
| PATCH | `/growth/ideas/{id}/reschedule` | Update scheduled time |
| POST | `/growth/ideas/{id}/dismiss` | Reject idea |
| GET | `/calendar` | Calendar view |
| GET | `/api/publisher/status` | Health check for background publisher |

---

## File Changes

### Database
- `src/threads_analytics/models.py` — Add fields to GeneratedIdea
- Alembic migration

### Services
- `src/threads_analytics/content_rules.py` — Anti-slop validation (new)
- `src/threads_analytics/idea_generator.py` — Pattern-driven generation (new)
- `src/threads_analytics/publisher.py` — Threads API publishing (new)
- `src/threads_analytics/scheduler.py` — Background polling (new)

### UI
- `src/threads_analytics/web/routes.py` — Add calendar route, schedule endpoints
- `src/threads_analytics/web/templates/calendar.html` — Calendar view (new)
- `src/threads_analytics/web/templates/growth_ideas.html` — Add schedule buttons
- `src/threads_analytics/web/static/calendar.js` — Drag-and-drop (new)
- `src/threads_analytics/web/static/ideas.js` — Schedule dropdown (new)
- `src/threads_analytics/web/app.py` — Startup/shutdown hooks for scheduler

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Rate limits (10/day) | High | Track quota in UI, warn at 8 posts |
| AI still generates slop | Medium | Pattern remix fallback, user edit before schedule |
| Scheduler dies silently | High | Health endpoint, restart on failure |
| Timezone confusion | Low | Store UTC, display WIB |

---

## Success Metrics

- Generate 5 ideas in < 10 seconds
- 0 AI-slop posts pass validation
- Schedule and auto-post works end-to-end
- Plan full week in < 15 minutes
