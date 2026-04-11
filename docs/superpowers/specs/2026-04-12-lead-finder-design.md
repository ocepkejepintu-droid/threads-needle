# Lead Finder Module Design

**Date:** 2026-04-12  
**Status:** Approved for implementation  
**Scope:** Phase 1 of Automation Suite expansion

---

## 1. Overview

The Lead Finder module transforms the threads-analytics dashboard from a read-only analytics tool into an active lead generation system. It searches Threads for people expressing pain points that the user's automation services can solve, presents them in a reviewable queue, and enables one-click helpful replies with AI-generated drafts that the user can edit before sending.

**Core Philosophy:** Helpful expert tone — provide genuine value first, soft CTA second. No spam. Human-in-the-loop approval for every reply.

---

## 2. Goals

- Discover high-intent leads daily without manual searching
- Maintain authentic, helpful brand voice through human review
- Track which conversations convert to actual business opportunities
- Automate the tedious parts while keeping human judgment on replies

---

## 3. User Stories

1. **As a** BPO/automation service provider  
   **I want** to find people complaining about manual data entry  
   **So that** I can offer helpful advice and potentially win new clients

2. **As a** Threads user building authority  
   **I want** to add my personal touch to AI-generated replies  
   **So that** my responses feel authentic and genuinely helpful

3. **As a** busy founder  
   **I want** leads discovered automatically each day  
   **So that** I can review and respond in a batch rather than constant monitoring

---

## 4. Architecture

### 4.1 New Database Models

**LeadSource** — Search configuration
```python
class LeadSource(Base):
    id: int (PK)
    name: str  # e.g., "Hiring Pain Points"
    keywords: list[str]  # JSON array: ["manual data entry", "hiring virtual assistant"]
    is_active: bool = True
    search_frequency_hours: int = 24
    last_searched_at: datetime | None
    created_at: datetime
    updated_at: datetime
```

**Lead** — Discovered opportunity
```python
class Lead(Base):
    id: int (PK)
    source_id: int (FK → LeadSource)
    
    # Thread/Post info
    thread_id: str
    author_username: str
    author_user_id: str
    author_bio: str | None
    post_text: str
    post_permalink: str
    post_created_at: datetime
    
    # Matching
    matched_keyword: str
    
    # Workflow status
    status: str  # enum: "new", "reviewed", "approved", "sent", "rejected"
    
    # AI-generated content
    ai_draft_reply: str | None
    ai_draft_generated_at: datetime | None
    
    # Final content (editable by user)
    final_reply: str | None
    notes: str | None  # Private user notes
    
    # Timestamps
    created_at: datetime
    reviewed_at: datetime | None
    sent_at: datetime | None
    rejected_at: datetime | None
    
    # Safety
    # Composite unique constraint: (author_user_id, created_at_date) prevents 
    # re-contacting same person within same day
```

**LeadSearchLog** — Audit trail
```python
class LeadSearchLog(Base):
    id: int (PK)
    source_id: int (FK → LeadSource)
    run_id: int (FK → Run)
    keywords_searched: list[str]
    posts_found: int
    leads_created: int
    searched_at: datetime
    error_message: str | None
```

### 4.2 New Services/Modules

**leads.py** — Core business logic
- `search_leads(source: LeadSource, client: ThreadsClient) → list[Lead]`
- `generate_reply_draft(lead: Lead) → str` (LLM-powered)
- `send_reply(lead: Lead, client: ThreadsClient) → bool`
- `should_skip_post(post_text: str, sentiment: str) → bool`

**leads_search.py** — Daily search orchestration
- `run_lead_searches()` — Called by pipeline or cron
- `deduplicate_leads(new_leads: list[Lead]) → list[Lead]`

### 4.3 LLM Integration

Uses existing `LLMClient` with helpful-expert system prompt:

```
You are an automation expert helping someone with a specific business problem.
Their post shows: {matched_keyword}

Reply guidelines:
1. Acknowledge their specific pain point (show you read it)
2. Share one genuinely helpful tip or insight (2-3 sentences)
3. Soft CTA: "I help businesses automate this. DM me if you want to chat" (ONLY if relevant)
4. Keep it under 280 characters
5. Never sound salesy. Sound like a helpful peer

Post content: {post_text}
Author bio: {author_bio}

Generate a reply that follows these guidelines:
```

---

## 5. UI Design

### 5.1 Navigation

New sidebar item: **"Leads"** (icon: target/crosshair)
- Position: Between "Experiments" and "Perception"
- Badge: Shows count of "new" leads

### 5.2 Page: `/leads` — Lead Queue (Kanban View)

Three-column Kanban board:

```
┌──────────────────────────────────────────────────────────────────┐
│ Lead Finder                    [+ New Source]  [Search Now]      │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  📥 NEW (12)        👀 REVIEWED (5)       ✅ SENT (23)          │
│  ┌─────────────┐   ┌─────────────┐       ┌─────────────┐       │
│  │ @sarah_dev  │   │ @mike_cfo   │       │ @jane_ops   │       │
│  │             │   │             │       │             │       │
│  │ "so tired   │   │ "data entry │       │ "thanks for │       │
│  │ of manual   │   │ taking 20hrs│       │ the tip!    │       │
│  │ data entry" │   │ a week"     │       │ DMing you"  │       │
│  │             │   │             │       │             │       │
│  │ Matched:    │   │ Matched:    │       │ Sent: Jan 12│       │
│  │ "manual"    │   │ "data entry"│       │             │       │
│  │             │   │             │       │             │       │
│  │ [Review →]  │   │ [Approve →] │       │             │       │
│  └─────────────┘   └─────────────┘       └─────────────┘       │
│                                                                  │
│  [Load More]  [Mark All Reviewed]  [Filter by Source ▼]         │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Card interactions:**
- Click card → Navigate to detail page
- Drag card → Move between columns (status change)
- Hover → Show preview of AI draft

### 5.3 Page: `/leads/{id}` — Lead Detail

```
┌──────────────────────────────────────────────────────────────────┐
│ ← Back to Leads                                                │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Lead from @sarah_dev                    [Reject] [Save Draft]  │
│  Bio: "CTO at StartupX | Building the future"                    │
│                                                                  │
│  ─────────────────────────────────────────────────────────────  │
│                                                                  │
│  Original Post:                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ "So tired of manual data entry. My team spends 10hrs/week │  │
│  │  copying between spreadsheets. There has to be a better   │  │
│  │  way... 😫"                                                │  │
│  └───────────────────────────────────────────────────────────┘  │
│  Posted: 2 hours ago | Matched keyword: "manual data entry"      │
│                                                                  │
│  ─────────────────────────────────────────────────────────────  │
│                                                                  │
│  Your Reply (edit as needed):                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ "That spreadsheet copying sounds exhausting. We automated │  │
│  │  similar workflows and saved ~20 hrs/week. Happy to share│  │
│  │  how if you're curious — just DM me."                    │  │
│  └───────────────────────────────────────────────────────────┘  │
│  [Regenerate Draft]  Characters: 178/280                        │
│                                                                  │
│  ─────────────────────────────────────────────────────────────  │
│                                                                  │
│  Private Notes (only you see this):                             │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ CTO at fintech startup - high potential value             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│                      [🚀 Approve & Send Reply]                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Features:**
- Live character counter (Threads limit: 280)
- "Regenerate Draft" button (re-runs LLM with same prompt)
- Preview mode: See exactly how it will look when posted
- Auto-save draft edits to `final_reply` field

### 5.4 Page: `/leads/sources` — Keyword Management

```
┌──────────────────────────────────────────────────────────────────┐
│ Lead Sources                                    [+ Create Source] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Active Sources (3)                                              │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Hiring Pain Points                          [Edit] [🟢 On] │  │
│  │ Keywords: "hiring virtual assistant", "need VA",          │  │
│  │           "outsourcing help", "remote team"               │  │
│  │ Last searched: 2 hours ago | Leads this week: 12          │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Data Entry Complaints                       [Edit] [🟢 On] │  │
│  │ Keywords: "manual data entry", "spreadsheet hell",        │  │
│  │           "copy paste all day"                            │  │
│  │ Last searched: 2 hours ago | Leads this week: 8           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Paused Sources (1)                                              │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Startup Founders                            [Edit] [🔴 Off]│  │
│  │ Keywords: "startup grind", "burned out founder"           │  │
│  │ Last searched: 3 days ago | Leads this week: 0            │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Create/Edit Source Modal:**
- Name input
- Keywords textarea (one per line)
- Test Search button (shows preview results without saving)
- Frequency selector (12h, 24h, 48h, manual only)

---

## 6. Workflow

### 6.1 Daily Auto-Search

```
Pipeline Run
    │
    ▼
Run Lead Searches (new step)
    │
    ├── For each active LeadSource
    │       │
    │       ├── Call threads_keyword_search API
    │       │       (respecting rate limits)
    │       │
    │       ├── Filter posts:
    │       │   - Skip if author is you
    │       │   - Skip if already contacted (30-day window)
    │       │   - Skip if negative sentiment (rant/complaint)
    │       │   - Skip if reply count > 10 (too noisy)
    │       │
    │       └── Create Lead records (status="new")
    │               Generate AI draft reply
    │
    └── Log search results
```

### 6.2 Human Review Flow

```
User visits /leads
    │
    ├── Sees Kanban of "new" leads
    │
    ├── Clicks lead to review
    │       │
    │       ├── Reads original post
    │       ├── Reviews/edits AI draft
    │       ├── Optionally adds private notes
    │       │
    │       └── Decides:
    │           │
    │           ├── [Approve & Send]
    │           │       │
    │           │       ├── POST reply via API
    │           │       ├── Update Lead status="sent"
    │           │       └── Move to "sent" column
    │           │
    │           ├── [Save Draft]
    │           │       └── status="reviewed", stay in queue
    │           │
    │           └── [Reject]
    │                   └── status="rejected", archive
    │
    └── Bulk actions available
            (Mark all reviewed, etc.)
```

---

## 7. Safety & Limits

| Constraint | Implementation |
|------------|----------------|
| **Daily reply limit** | Configurable, default 10/day. Counter resets at midnight UTC. Dashboard shows "3/10 replies remaining today" |
| **Duplicate prevention** | Composite unique on (author_user_id, DATE(created_at)). Cannot contact same person twice same day |
| **Cooldown period** | 30-day minimum between contacts to same user |
| **Sentiment filter** | Skip posts with strong negative sentiment scores (rants vs. solution-seeking) |
| **Reply length** | Enforce 280 char limit in UI + API validation |
| **Rate limiting** | Respect Threads API rate limits. Queue searches if limit hit |
| **Audit log** | Every search, every reply logged with timestamps |

---

## 8. Integration Points

### 8.1 Pipeline Integration

New step in `pipeline.py` after ingest:
```python
# 2b. Lead discovery
with session_scope() as session:
    run = session.get(Run, run_id)
    summary["leads"] = run_lead_searches(run, client)
```

### 8.2 CLI Integration

New command:
```bash
threads-analytics search-leads    # Manual trigger
threads-analytics leads-stats     # Show queue counts
```

### 8.3 Web API Routes

New routes in `web/routes.py`:
- `GET /leads` — Queue page (Kanban)
- `GET /leads/{id}` — Detail page
- `POST /leads/{id}/approve` — Approve and send
- `POST /leads/{id}/reject` — Reject lead
- `POST /leads/{id}/save-draft` — Save edited draft
- `POST /leads/{id}/regenerate` — Re-run AI draft
- `GET /leads/sources` — Sources list page
- `GET /leads/sources/new` — Create source form
- `POST /leads/sources` — Create source
- `GET /leads/sources/{id}/edit` — Edit source form
- `POST /leads/sources/{id}` — Update source

---

## 9. Success Metrics

Track in database for future analytics:
- Leads discovered per day/week
- Conversion rate: new → approved → sent
- Response rate: % of sent replies that get engagement
- Time from discovery to reply
- Most effective keywords/sources

---

## 10. Out of Scope (Future Phases)

These are intentionally NOT in this design:

- **Auto-posting/scheduler** (Phase 2: Auto-Pilot)
- **Mention monitoring** (Phase 3: Mentions)
- **Auto-reply without human approval** (intentionally excluded for safety)
- **Multi-account management** (single user only)
- **Advanced sentiment analysis** (basic filter only)
- **CRM integration** (manual tracking via notes only)

---

## 11. Files to Create/Modify

### New Files
- `src/threads_analytics/leads.py` — Core business logic
- `src/threads_analytics/leads_search.py` — Search orchestration
- `src/threads_analytics/web/templates/leads.html` — Queue page
- `src/threads_analytics/web/templates/lead_detail.html` — Detail page
- `src/threads_analytics/web/templates/lead_sources.html` — Sources list
- `src/threads_analytics/web/templates/lead_source_form.html` — Create/edit form

### Modified Files
- `src/threads_analytics/models.py` — Add Lead, LeadSource, LeadSearchLog
- `src/threads_analytics/pipeline.py` — Add lead search step
- `src/threads_analytics/web/routes.py` — Add lead routes
- `src/threads_analytics/web/templates/base.html` — Add sidebar item
- `src/threads_analytics/cli.py` — Add lead commands

---

## 12. Approval Checklist

- [x] Kanban queue view approved
- [x] Editable AI drafts approved  
- [x] Separate keyword management page approved
- [x] Daily auto-search approved
- [x] Helpful expert tone specified
- [x] Human-in-the-loop required
- [x] Safety limits defined

---

**Next Step:** Invoke `writing-plans` skill to create implementation plan.
