# Threads Content Pipeline v2 — Feature Plan

**Owner:** @yosephgratika
**Status:** Draft for engineering review
**Target kickoff:** Week of Apr 20, 2026
**Estimated effort:** 6–8 weeks for full scope; P0 shippable in 1 week

---

## 1. Context

### Current state
We have a Kanban-style Threads scheduler with columns: **Draft → Experiments → Queue → Scheduled → Published**. It integrates with the Threads API for automated posting. Idea generation is manual via two buttons: "Generate from Experiments" and "Generate Ideas."

### Problem
The pipeline is incomplete. Current snapshot shows 21 drafts, 9 experiments, 1 queued, 0 scheduled, 2 published — a classic clog pattern with three root causes:

1. **No intake layer.** All ideas come from the operator's head (inside-out generation). When no experiments are running, the well runs dry.
2. **No scoring/conviction layer.** Drafts accumulate because there's no rubric to decide "does this earn a slot?"
3. **No feedback loop.** Published posts don't feed data back into future decisions.

### Goal
Build the missing layers so the pipeline is self-sustaining: external signals flow in daily, drafts get objectively scored and auto-tiered, and published outcomes inform what to repeat.

### Success metrics
- **Zero-reply rate:** drop from 15.4% to &lt;8% within 4 weeks of full rollout
- **Reply:like ratio:** lift from 48% to 65%+
- **Published cadence:** sustain 7–8 posts/week without manual idea-hunting
- **Operator time:** reduce "what do I post?" decision time from ~30 min/day to ~10 min/day

### Non-goals
- Replacing the operator's voice. All final copy stays human-written for Tier 1/2 posts.
- Auto-posting without operator review. Every scheduled post is manually confirmed.
- Multi-account support. Single-account (@yosephgratika) only for v2.

---

## 2. Reference Framework

The team needs this vocabulary to implement features correctly. **Treat this section as canonical.**

### 2.1 The 6 Reply Mechanics

Every post belongs to exactly one mechanic. These are enum values used across the data model.

| Enum | Name | Description | Tier eligibility |
|------|------|-------------|------------------|
| `binary_verdict` | Binary Verdict | Contested opinion with named stakes ("X overrated, Y underrated"). Invites disagreement. | Hero, Engine |
| `community_ask` | Community Ask | Real operational pain + direct call for help ("Threads, do your magic!"). | Hero |
| `teaser_drop` | Teaser Drop | Concrete promise + "more details soon." Drives saves and reposts. | Hero, Engine |
| `structured_finding` | Structured Finding | Phase-based or tiered insight from real work. High repost rate. | Engine |
| `mid_experiment` | Mid-Experiment Drop | Live test posted before outcome known. Creates return visits. | Engine |
| `token_receipt` | Token Receipt | Specific number + blunt verdict + named model version. Operator credibility. | Engine |
| `signal` | Signal / Human | Golf, Bali ISP, non-AI tangent. Humanizer. | Signal only |

### 2.2 The 3 Post Tiers

| Tier | Score range | Cadence | Craft time | View target | Reply target |
|------|-------------|---------|------------|-------------|--------------|
| `hero` | 85–100 | 1–2 / week | 15–30 min | 5,000+ | 20+ |
| `engine` | 70–84 | 4–5 / week | 5–10 min | 800–2,000 | 3–8 |
| `signal` | 50–69 | 1 / week | 2 min | — | 1+ |
| `kill` | &lt;50 | Never | — | — | — |

### 2.3 The Slot Schedule

| Day | Time (WIB) | Tier allowed | Notes |
|-----|------------|--------------|-------|
| Mon | 11:00 | hero | Prime slot |
| Tue | 12:00 | engine | |
| Wed | 12:00 | engine | |
| Wed | 20:00 | engine | Recruitment angle |
| Thu | 12:00 | engine | |
| Fri | 11:00 | hero | Prime slot |
| Fri | 14:00 | engine | Optional fill |
| Sat | 10:00 | signal | Humanizer slot |
| Sun | — | — | No scheduled posts |

### 2.4 The Scoring Rubric (6 dimensions, 100 pts total)

| Dimension | Max | Criteria |
|-----------|-----|----------|
| Hook test | 20 | First 8 words contain a number, named thing, or contested word |
| Mechanic fit | 20 | Clearly uses one of the 6 mechanics (not hybrid/none) |
| Operator standing | 20 | Based on real experience, token count, or team data — not speculation |
| Trend freshness | 15 | Ties to an external event in the last 7 days |
| Reply invitation | 15 | Ends with question, tease, or unresolved stake |
| Voice signature | 10 | Natural Bahasa campur + blunt/self-deprecating cue |

---

## 3. Feature Specifications

Features are grouped by priority. **P0 must ship first** — it alone solves ~70% of the pain. Resist building P1–P3 before P0 has run for at least 10 days.

---

### P0.1 — Daily Trend Digest (Intake Layer)

**Problem it solves:** Operator runs out of content ideas because all generation is inside-out.

**User story:**
> As the operator, I want to open my webapp at 8:30 AM and see 8–12 fresh AI/tech trend items from the last 24 hours, so I can pick 2–3 I have operator standing on and convert them into drafts — without manually browsing X, HN, and changelogs.

**Spec:**

- **Trigger:** Cron job at 08:00 WIB daily
- **Sources (P0 launch set):**
  - HackerNews front page (AI/LLM tag filter) — free API
  - Anthropic changelog + blog RSS
  - OpenAI changelog + blog RSS
  - Google DeepMind / Gemini release notes
  - X curated list (via user's own credential — build "add from X URL" fallback if no API access)
- **Processing:**
  - Fetch last 24h of items from each source
  - Dedupe by URL and title similarity (&gt;80% match)
  - Send batch to Claude API for relevance filtering + summarization
  - Each item → 1–2 sentence summary in English
  - Claude also tags each item with a suggested operator-standing score (0–100) and 1–2 candidate mechanics
- **Output:** 8–12 items written to `intake_items` table with status `new`
- **Delivery:** items appear as cards in a new "Intake" Kanban column (see P0.2)
- **Expiry:** items auto-archive after 7 days with status `expired`

**Claude API prompt contract:**

```
System: You are a content intake filter for an AI operator in Indonesia who runs
a BPO data pipeline business. The operator posts on Threads about AI tools,
model comparisons, token costs, team adaptation, and recruitment — from a
hands-on operator perspective (not theorizing).

For each input item, return JSON:
{
  "summary": "1–2 sentence English summary",
  "operator_standing_score": 0-100,
  "reasoning": "1 sentence why this is/isn't relevant",
  "candidate_mechanics": ["binary_verdict", "token_receipt"],
  "relevance": "high" | "medium" | "low" | "skip"
}

Skip items about consumer AI products, AI policy debates, or AI ethics unless
they have direct operational implications. Prefer items about: model releases,
pricing changes, developer tools, tokenomics, agent frameworks, enterprise AI
adoption patterns, recruitment/hiring in AI.
```

**Acceptance criteria:**

- [ ] Cron fires at 08:00 WIB daily without manual trigger
- [ ] At least 5 fresh items appear in the Intake column by 08:15 WIB
- [ ] No duplicates across multiple days (dedupe works)
- [ ] Items older than 7 days auto-archive
- [ ] If all sources fail, system posts an error card to the column (don't fail silently)
- [ ] Claude API calls are logged with token counts for cost tracking

---

### P0.2 — "Intake" Kanban Column

**Problem it solves:** Trend items need a home in the existing UI.

**Spec:**

- Add new column **before** Draft: `Intake` → `Draft` → `Experiments` → `Queue` → `Scheduled` → `Published`
- Intake card shows:
  - Source icon + name (HN, Anthropic, X, etc.)
  - Summary (2 lines, truncated)
  - Operator standing score badge (color-coded: green 80+, amber 50–79, gray &lt;50)
  - Candidate mechanic chips (up to 2)
  - Timestamp ("3h ago")
  - Actions: **Convert to Draft** (primary), **Archive**, **View source URL**
- Column shows count in header: "INTAKE · 12"
- Column is sortable by: standing score (default), time discovered, source
- Cards cannot be dragged to other columns — they must go through "Convert to Draft" action (which uses P1.1 Angle-It)

**Acceptance criteria:**

- [ ] Intake column renders with correct count
- [ ] Cards display all required fields
- [ ] Sort toggle works
- [ ] Archive action moves item to `archived` status (hidden from view)
- [ ] Clicking source URL opens in new tab

---

### P1.1 — Angle-It Feature (Conversion Engine)

**Problem it solves:** Converting a raw trend into a Yoseph-voice draft takes 10–15 minutes of creative work. Most of it is repeatable structure.

**User story:**
> As the operator, I want to click one button on an intake card and see 3 angle options — each pre-tagged with a mechanic and pre-drafted in Bahasa campur — so I can pick the best one, edit for voice, and promote to Draft in under 3 minutes.

**Spec:**

- Trigger: "Convert to Draft" button on intake card
- Opens a modal with loading state while Claude API generates
- Claude returns 3 angle variants, each with:
  - A proposed first-line hook
  - A full post body (80–280 chars, Bahasa campur, in Yoseph's voice)
  - The mechanic tag
  - An auto-computed rubric score (see 2.4)
- Operator selects one variant, edits freely in a text field, confirms → promotes to Draft column with:
  - `intake_item_id` linked
  - Mechanic tag set
  - Initial rubric scores pre-filled (editable)

**Claude API prompt contract:**

```
System: You write Threads posts for @yosephgratika, an Indonesian BPO founder
who runs AI data pipelines at production scale. Voice: blunt, operator-grade,
Bahasa Indonesia mixed with English technical terms (Bahasa campur). Uses
"wkwk", "koplak", "njir", "di nerf" naturally. Self-deprecating. Names specific
model versions. Always has reply-invitation at the end.

Input: {trend_summary}

Output 3 post variants as JSON:
{
  "variants": [
    {
      "hook": "first 8 words",
      "body": "full post, 80-280 chars",
      "mechanic": "binary_verdict",
      "rubric": {
        "hook_test": 0-20,
        "mechanic_fit": 0-20,
        "operator_standing": 0-20,
        "trend_freshness": 0-15,
        "reply_invitation": 0-15,
        "voice_signature": 0-10
      },
      "reasoning": "why this angle + mechanic works"
    },
    // ... 2 more
  ]
}

Each variant must use a DIFFERENT mechanic if possible. Prefer mechanics that
scored highest-performing in the last 30 days (feed this data in as context).
```

**Acceptance criteria:**

- [ ] Modal opens with clear loading state (Claude call takes 3–8s)
- [ ] 3 variants render with editable body text
- [ ] Selecting a variant shows rubric scores; operator can override
- [ ] "Confirm" button creates Draft, archives intake item, closes modal
- [ ] "Cancel" button discards, returns to Intake view
- [ ] API failure shows error with retry option (doesn't lose the intake item)

---

### P1.2 — Scoring Rubric Gate

**Problem it solves:** Drafts accumulate because there's no objective criteria to decide which deserve a slot.

**User story:**
> As the operator, I cannot move a draft from Draft → Queue without filling in the 6-dimension rubric. Once filled, the system auto-assigns the tier and only allows me to pick slots matching that tier.

**Spec:**

- Modify Draft → Queue promotion action
- Before promotion, show rubric modal with 6 sliders (one per dimension)
- "Auto-score" button triggers Claude API to pre-fill all 6 scores (operator can adjust)
- Total score computes live
- Tier auto-assigns from total:
  - 85–100 → `hero`
  - 70–84 → `engine`
  - 50–69 → `signal`
  - &lt;50 → warning banner: "This post is below quality floor. Consider archiving instead."
- "Promote to Queue" button enabled only when:
  - All 6 scores are filled (non-null)
  - Mechanic tag is set
  - Body text is ≥40 characters
- Kill floor: if score &lt;50, allow promotion but mark as "experimental" (operator acknowledges risk)

**Data validation:**

```ts
type RubricScore = {
  hook_test: number;        // 0-20
  mechanic_fit: number;     // 0-20
  operator_standing: number; // 0-20
  trend_freshness: number;  // 0-15
  reply_invitation: number; // 0-15
  voice_signature: number;  // 0-10
};

function computeTier(total: number): Tier {
  if (total >= 85) return "hero";
  if (total >= 70) return "engine";
  if (total >= 50) return "signal";
  return "kill";
}
```

**Claude API auto-score prompt contract:**

```
System: Score this Threads post draft on 6 dimensions using the rubric:

[rubric definition from section 2.4]

Return JSON:
{
  "hook_test": 0-20,
  "mechanic_fit": 0-20,
  "operator_standing": 0-20,
  "trend_freshness": 0-15,
  "reply_invitation": 0-15,
  "voice_signature": 0-10,
  "rationale": {
    "hook_test": "1 sentence",
    "mechanic_fit": "1 sentence",
    ...
  }
}

Be strict. Default to conservative scores. A 90+ total should be rare.
```

**Acceptance criteria:**

- [ ] Promote modal blocks submission until rubric is complete
- [ ] Auto-score populates all 6 fields in &lt;5s
- [ ] Total score and tier update live as sliders move
- [ ] Warning banner appears for scores &lt;50
- [ ] Rubric scores persist on the Draft record (for analytics later)

---

### P1.3 — Tier-Slot Validation

**Problem it solves:** Operator might schedule a low-scoring post in a prime slot, wasting distribution.

**Spec:**

- When promoting Queue → Scheduled, show the weekly slot picker
- Only show slots matching the post's tier (from 2.3 schedule)
- If no matching slots are free this week, show next week's slots
- Slots already filled are visually disabled
- Validation rule: one Hero post per Mon/Fri prime slot — if both are taken, operator sees "No Hero slots free this week; post will schedule for next week."

**Acceptance criteria:**

- [ ] Tier-mismatched slots are hidden (not just disabled)
- [ ] Already-scheduled slots show the post title on hover
- [ ] Operator cannot bypass validation via URL manipulation

---

### P2.1 — Post-Mortem Auto-Tagger (Learning Loop)

**Problem it solves:** Published posts don't teach the system anything. Next week's decisions are made blind.

**Spec:**

- Cron job runs every hour for posts published 24h ago (±30 min window)
- Fetches Threads metrics via API: views, likes, replies, reposts
- Computes derived fields:
  - `reply_to_like_ratio` = replies / max(likes, 1)
  - `reach_multiple` = views / operator's 30-day median views
- Auto-classifies outcome:
  - `breakout`: reach_multiple ≥ 5 OR replies ≥ 20
  - `healthy`: reach_multiple ≥ 1.5 AND replies ≥ 3
  - `stall`: reach_multiple between 0.5–1.5 AND replies 1–2
  - `zero_reply`: replies == 0
- Writes outcome tag + metrics snapshot to `post_outcomes` table
- Also stores:
  - `replies_in_first_hour` (critical for velocity analysis)
  - `self_reply_seeded` (boolean from operator behavior)

**Acceptance criteria:**

- [ ] Every published post gets an outcome tag within 24h+1h of publish
- [ ] Metrics are stored as a snapshot (not just current live values)
- [ ] Classification rules are configurable via environment/config (not hardcoded)
- [ ] Published column cards display outcome badge after tagging

---

### P2.2 — Performance Dashboard

**Problem it solves:** Operator has no visual on what's working across mechanics, topics, and slots.

**Spec:**

- New route: `/performance`
- Shows rolling 30-day data
- Sections:
  1. **Tier hit rate** — for each tier, what % of posts met their view/reply targets
  2. **Mechanic performance** — table with: mechanic, posts count, avg views, avg replies, breakout rate
  3. **Slot performance** — which slots over/underperform vs tier expectation
  4. **Topic clusters** — Claude-generated groupings of posts by theme (model comparisons, recruitment, operational grief, etc.) with performance per cluster
  5. **Trend-tie correlation** — posts with an intake source vs without: do tied posts perform better?

**Acceptance criteria:**

- [ ] Dashboard loads in &lt;2s
- [ ] All sections refresh on page load (no stale cache &gt;1h)
- [ ] Each table has sort controls
- [ ] Mechanic table highlights the top performer in green

---

### P2.3 — Swipe File + Mechanic Detector

**Problem it solves:** Operator sees viral posts in the wild but has no system to capture and learn from them.

**Spec:**

- New route: `/swipe`
- "Add Swipe" button → paste a Threads URL
- System scrapes public metadata (views if available, likes, replies)
- Claude API analyzes the post and returns:
  - Identified mechanic (from the 6)
  - Hook pattern description (1 sentence)
  - What makes it work (2 bullet points)
- Stored as a browsable library, filterable by mechanic
- From any Swipe card, "Inspire a draft" button seeds a new Draft using the same mechanic but the operator's data

**Acceptance criteria:**

- [ ] Paste-URL flow works for Threads.com URLs
- [ ] Claude analysis completes in &lt;10s
- [ ] Library renders as grid with filter chips (one per mechanic)
- [ ] "Inspire a draft" correctly pre-tags mechanic on the new Draft

---

### P3.1 — Calendar Availability Gate

**Problem it solves:** Operator schedules a Hero post at 11 AM while they have a meeting 11:00–12:00, killing the reply-velocity window.

**Spec:**

- OAuth Google Calendar integration (read-only)
- When scheduling a post, check for conflicts in the 60 min after publish time
- If conflict found, show warning: "You have 'Meeting X' from 11:00–12:00. Hero posts require reply-active hour. Reschedule?"
- Offers next free 60-min window that matches tier's allowed slots
- Operator can override with "Schedule anyway" (tracked for analytics — does override correlate with underperformance?)

**Acceptance criteria:**

- [ ] OAuth flow stores refresh token securely
- [ ] Conflict detection uses the user's primary calendar + any selected additional calendars
- [ ] All-day events are excluded from conflict check
- [ ] Override action is logged to `schedule_overrides` table

---

## 4. Data Model Changes

### 4.1 New tables

```sql
CREATE TABLE intake_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source TEXT NOT NULL CHECK (source IN ('x', 'hn', 'product_hunt', 'anthropic', 'openai', 'gemini', 'reddit', 'manual')),
  source_url TEXT NOT NULL,
  source_title TEXT NOT NULL,
  raw_data JSONB,
  summary TEXT,
  operator_standing_score INT,
  candidate_mechanics TEXT[],
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'converted', 'archived', 'expired')),
  converted_to_draft_id UUID REFERENCES drafts(id),
  discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '7 days'
);

CREATE INDEX idx_intake_status_score ON intake_items(status, operator_standing_score DESC);
CREATE UNIQUE INDEX idx_intake_url ON intake_items(source_url);

CREATE TABLE post_outcomes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id UUID NOT NULL REFERENCES posts(id),
  snapshot_at TIMESTAMPTZ NOT NULL,
  views INT,
  likes INT,
  replies INT,
  reposts INT,
  replies_in_first_hour INT,
  reply_to_like_ratio DECIMAL(5,3),
  reach_multiple DECIMAL(5,2),
  outcome_tag TEXT CHECK (outcome_tag IN ('breakout', 'healthy', 'stall', 'zero_reply')),
  self_reply_seeded BOOLEAN DEFAULT false
);

CREATE INDEX idx_outcomes_post ON post_outcomes(post_id);
CREATE INDEX idx_outcomes_tag ON post_outcomes(outcome_tag);

CREATE TABLE swipe_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_url TEXT NOT NULL UNIQUE,
  author_handle TEXT,
  post_body TEXT NOT NULL,
  posted_at TIMESTAMPTZ,
  views INT,
  likes INT,
  replies INT,
  identified_mechanic TEXT,
  hook_pattern TEXT,
  analysis_notes TEXT,
  added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE schedule_overrides (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id UUID NOT NULL REFERENCES posts(id),
  override_type TEXT NOT NULL,
  conflict_detail JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 4.2 Extend existing tables

```sql
ALTER TABLE drafts ADD COLUMN intake_item_id UUID REFERENCES intake_items(id);
ALTER TABLE drafts ADD COLUMN mechanic TEXT CHECK (mechanic IN (
  'binary_verdict', 'community_ask', 'teaser_drop',
  'structured_finding', 'mid_experiment', 'token_receipt', 'signal'
));
ALTER TABLE drafts ADD COLUMN rubric_hook_test INT CHECK (rubric_hook_test BETWEEN 0 AND 20);
ALTER TABLE drafts ADD COLUMN rubric_mechanic_fit INT CHECK (rubric_mechanic_fit BETWEEN 0 AND 20);
ALTER TABLE drafts ADD COLUMN rubric_operator_standing INT CHECK (rubric_operator_standing BETWEEN 0 AND 20);
ALTER TABLE drafts ADD COLUMN rubric_trend_freshness INT CHECK (rubric_trend_freshness BETWEEN 0 AND 15);
ALTER TABLE drafts ADD COLUMN rubric_reply_invitation INT CHECK (rubric_reply_invitation BETWEEN 0 AND 15);
ALTER TABLE drafts ADD COLUMN rubric_voice_signature INT CHECK (rubric_voice_signature BETWEEN 0 AND 10);
ALTER TABLE drafts ADD COLUMN total_score INT GENERATED ALWAYS AS (
  COALESCE(rubric_hook_test, 0) + COALESCE(rubric_mechanic_fit, 0) +
  COALESCE(rubric_operator_standing, 0) + COALESCE(rubric_trend_freshness, 0) +
  COALESCE(rubric_reply_invitation, 0) + COALESCE(rubric_voice_signature, 0)
) STORED;
ALTER TABLE drafts ADD COLUMN tier TEXT CHECK (tier IN ('hero', 'engine', 'signal', 'kill'));

ALTER TABLE posts ADD COLUMN mechanic TEXT;
ALTER TABLE posts ADD COLUMN tier TEXT;
ALTER TABLE posts ADD COLUMN intake_item_id UUID;
```

### 4.3 Configuration table (no hardcoded values)

```sql
CREATE TABLE pipeline_config (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed values
INSERT INTO pipeline_config (key, value) VALUES
  ('tier_thresholds', '{"hero": 85, "engine": 70, "signal": 50}'),
  ('outcome_rules', '{"breakout_reach_multiple": 5, "breakout_replies": 20, "healthy_reach_multiple": 1.5, "healthy_replies": 3}'),
  ('slot_schedule', '[{"day":"mon","time":"11:00","tier":"hero"}, ...]'),
  ('intake_sources_enabled', '["hn", "anthropic", "openai"]'),
  ('intake_fetch_cron', '"0 1 * * *"');  -- 08:00 WIB = 01:00 UTC
```

---

## 5. API Integrations

| Integration | Priority | Notes |
|-------------|----------|-------|
| Claude API (Anthropic) | P0 | Used for: intake summarization, angle generation, auto-scoring, swipe analysis. Use `claude-sonnet-4-6` for most tasks; `claude-opus-4-7` for angle generation where voice fidelity matters. Budget: ~$15–30/month at current volume. |
| HackerNews API | P0 | Free, no auth. Use Algolia-powered search endpoint for AI tag filter. |
| Anthropic/OpenAI/Gemini blogs | P0 | RSS feeds exist for all three. No auth needed. |
| X/Twitter API | P0 (fallback) | Paid tier required. If unaffordable, build "manual paste URL" fallback and scrape via a lightweight backend proxy. |
| Threads API | (existing) | Already integrated for posting. Extend to fetch post metrics for outcome tagging. |
| Product Hunt API | P1 | Free tier available. GraphQL. |
| Reddit API | P1 | Free with OAuth. Subreddits: r/LocalLLaMA, r/MachineLearning. |
| Google Calendar API | P3 | OAuth 2.0 read-only scope. |

### Rate limiting notes
- Claude API: implement exponential backoff, max 3 retries
- HN/Reddit: respect published rate limits, add 1s delay between calls
- Threads: batch metric fetches hourly, not per-post

---

## 6. UI/UX Changes

### 6.1 Kanban board
- Add "Intake" column as the leftmost column
- Add outcome badge to Published column cards (color + text: green "breakout", amber "healthy", gray "stall", red "zero-reply")
- Add tier badge to all Draft/Queue/Scheduled cards
- Add mechanic chip to all cards (small, colored per mechanic)

### 6.2 New modals
- Angle-It modal (P1.1)
- Rubric gate modal (P1.2)
- Slot picker modal (P1.3) — extend existing if present

### 6.3 New routes
- `/performance` (P2.2)
- `/swipe` (P2.3)
- `/settings/intake-sources` (lets operator toggle which sources are active)

### 6.4 Design system note
Keep visual language consistent with current Kanban (rounded cards, muted grays, single accent color for active state). Don't introduce new color schemes. Badges use the existing semantic palette (success green, warning amber, danger red, neutral gray).

---

## 7. Build Sequence

### Week 1 — P0 ships
- [ ] Schema migrations for `intake_items` + draft extensions
- [ ] Claude API wrapper with retry/logging
- [ ] Source fetchers: HN, Anthropic RSS, OpenAI RSS, Gemini RSS
- [ ] Dedupe logic
- [ ] Cron job setup (08:00 WIB)
- [ ] Intake Kanban column UI
- [ ] Convert-to-Draft stub (opens in existing Draft edit modal for now; Angle-It in Week 2)

**Gate to Week 2:** P0 has been running for 5 consecutive days with zero intervention, operator is reviewing intake daily.

### Week 2 — Angle-It (P1.1)
- [ ] Angle-It modal UI
- [ ] Claude API prompt tuning (test with 10 sample trends)
- [ ] Variant selection and edit flow
- [ ] Link intake item to draft on conversion

### Week 3 — Scoring gate (P1.2 + P1.3)
- [ ] Rubric modal with 6 sliders
- [ ] Auto-score endpoint
- [ ] Tier auto-assignment
- [ ] Tier-slot validation in scheduler

**Gate to Week 4:** Operator has scheduled at least 8 posts through the new rubric gate. Manual audit: do the auto-assigned tiers feel correct?

### Week 4 — Learning loop (P2.1)
- [ ] Threads metric fetcher (hourly cron)
- [ ] Outcome classification
- [ ] `post_outcomes` snapshots
- [ ] Outcome badges on Published cards

### Week 5 — Dashboard (P2.2)
- [ ] `/performance` route
- [ ] 5 dashboard sections
- [ ] Claude-powered topic clustering (run weekly, cache results)

### Week 6 — Swipe file (P2.3)
- [ ] `/swipe` route
- [ ] URL paste + scrape flow
- [ ] Mechanic detection via Claude API
- [ ] "Inspire a draft" action

### Week 7+ — Calendar + polish (P3.1)
- [ ] Google Calendar OAuth
- [ ] Conflict detection
- [ ] Override tracking
- [ ] General UX polish, error handling improvements

---

## 8. Acceptance Criteria Summary

A feature is "done" only when:

1. All per-feature acceptance criteria (listed in each spec) pass
2. Has automated tests covering the happy path + 1 failure case
3. Cron jobs have monitoring/alerting (Sentry or equivalent) for silent failures
4. New Claude API calls are logged with token count and cost
5. Operator has used the feature for 5 consecutive days without reporting a blocker

---

## 9. Risks &amp; Open Questions

### Risks

- **Claude API cost creep.** Angle-It + auto-score per draft + intake summarization could grow to $50+/month at higher volume. Mitigation: cache aggressively, use Haiku for intake summarization (cheaper), monitor monthly spend.
- **X API cost.** Official X API is expensive. Fallback to manual URL paste is acceptable for v2.
- **Threads API changes.** Meta has changed Threads API scope before. Monitor deprecation notices.
- **Over-automation risk.** If the system starts auto-drafting posts without enough operator review, voice degrades. Mitigation: Angle-It output is always editable; final Tier 1 copy stays hand-written.
- **Rubric drift.** Operator may start gaming the rubric by inflating scores. Mitigation: auto-score baseline is always visible next to operator's manual score.

### Open questions

1. **Who owns the daily trend digest review?** If operator is traveling or sick, does the digest still run? (Proposed: yes, items just accumulate and auto-expire after 7 days.)
2. **Multi-language support?** For now, Bahasa campur is hardcoded in prompts. Future: English-only variants for English audience experiments?
3. **Team access?** Will anyone besides @yosephgratika have login access? (Proposed: single-user v2; multi-user is v3 scope.)
4. **Version-control for prompts?** Claude API prompts will evolve. Should prompts live in git, in the DB, or both? (Proposed: git as source of truth, DB as deployed cache.)
5. **Outcome tagging retroactive?** Should we backfill outcome tags for existing Published posts? (Proposed: yes, one-time migration to run after P2.1 ships.)

---

## 10. Appendix — Example Intake → Publish flow

Full reference flow for engineering acceptance testing.

**09:00 WIB, Monday**

1. Operator opens webapp.
2. Intake column shows 11 items fetched at 08:00. Top item by standing score (92):
   - Source: Anthropic Changelog
   - Summary: "Claude with computer use now 30% cheaper per 1k tokens. Released this morning."
   - Candidate mechanics: `binary_verdict`, `token_receipt`
3. Operator clicks "Convert to Draft."
4. Angle-It modal opens. After 4 seconds, 3 variants appear:
   - Variant A (binary_verdict, score 88): "Computer Use baru di-drop 30% lebih murah, semua X pada excited. Reality check dari 60 agent di production..."
   - Variant B (token_receipt, score 82): "60 swarm agent, 5 terminal, 11% weekly quota — Computer Use cheaper sekarang worth it? ..."
   - Variant C (teaser_drop, score 75): "Gue abis tes Computer Use di 30% price drop. Writeup lengkap malam ini..."
5. Operator picks A, edits 2 words, confirms.
6. Draft is created, intake item is archived, modal closes.
7. Operator drags Draft to Queue → Rubric modal opens.
8. Auto-score populates (88 total, tier = `hero`). Operator adjusts one slider (operator_standing from 18 to 20). Total becomes 90.
9. Operator clicks "Promote to Queue."
10. Slot picker opens → shows only Hero slots. Monday 11 AM is free (just after this moment). Operator picks it.
11. Post is scheduled. At 11:00, Threads API fires. Operator gets T+5 notification.
12. Operator seeds self-reply at 11:05. Replies to 7 commenters over the next hour.
13. **Next day 11:05:** outcome tagger runs. Views: 8,400. Likes: 96. Replies: 47. Reach multiple: 25x. Outcome: `breakout`. Card updates with green badge.
14. Performance dashboard refreshes. `binary_verdict` mechanic row updates: breakout rate now 22% (up from 18%).

**This is the pipeline working end to end.**

---

*End of spec. Questions or pushback → comment in the doc or ping in #threads-pipeline Slack channel.*
