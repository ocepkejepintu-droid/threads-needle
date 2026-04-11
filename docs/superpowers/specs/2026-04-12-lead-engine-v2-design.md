# Lead Engine v2 Design

**Date:** 2026-04-12  
**Status:** Approved for implementation  
**Scope:** Intent classification + Reply performance analytics

---

## 1. Overview

Upgrade the existing Lead Finder from "find and reply" to "find, classify, optimize, convert." Adds intelligence layer that tags leads by intent, tracks which replies actually work, and continuously improves templates based on real performance data.

---

## 2. Goals

- Automatically classify leads (job seeker / founder / service buyer / other)
- Track reply performance (open rate, response rate, conversion to DM)
- Score and rank leads by quality (high/medium/low intent)
- A/B test reply templates and auto-promote winners
- Export high-intent leads to CRM/CSV

---

## 3. Database Models (Additions)

**LeadIntent** (new enum on Lead model)
```python
intent: str  # "job_seeker", "founder", "service_buyer", "other", "unclear"
intent_confidence: float  # 0.0-1.0 from classifier
```

**LeadReply** (new table - track all replies sent)
```python
class LeadReply(Base):
    id: int (PK)
    lead_id: int (FK → Lead)
    reply_text: str  # what was actually sent
    reply_type: str  # "ai_draft", "edited", "manual"
    template_id: int | None (FK → ReplyTemplate)
    
    # Performance metrics (updated by periodic check)
    has_response: bool = False
    response_text: str | None  # what they replied back
    response_at: datetime | None
    
    # Conversion tracking
    converted_to_dm: bool = False  # they DM'd you
    converted_to_call: bool = False  # you marked as "had call"
    converted_to_client: bool = False  # you marked as "became client"
    
    sent_at: datetime
    metrics_checked_at: datetime | None
```

**ReplyTemplate** (new table - for A/B testing)
```python
class ReplyTemplate(Base):
    id: int (PK)
    name: str  # e.g., "Helpful Tip + Soft CTA"
    category: str  # "job_seeker", "founder", "service_buyer", "generic"
    template_text: str  # with {post_preview}, {keyword} placeholders
    
    # Performance stats (auto-calculated)
    times_used: int = 0
    times_responded: int = 0
    response_rate: float = 0.0  # 0.0-1.0
    
    is_active: bool = True
    is_winner: bool = False  # auto-promoted based on stats
    
    created_at: datetime
    retired_at: datetime | None
```

**LeadScore** (new table - track scoring history)
```python
class LeadScore(Base):
    """Computed score for a lead based on multiple signals."""
    lead_id: int (PK, FK → Lead)
    
    # Individual signal scores (0-100)
    intent_score: int  # based on intent classification
    engagement_score: int  # post has replies/likes (active discussion)
    profile_score: int  # author bio signals (founder, hiring, etc.)
    recency_score: int  # how fresh is the post
    
    # Composite
    total_score: int  # weighted average
    quality_tier: str  # "high" (80+), "medium" (50-79), "low" (<50)
    
    computed_at: datetime
```

---

## 4. Intent Classification System

**Classifier prompt (Claude via OpenRouter):**
```
Analyze this Threads post and classify the author's intent.

Post: {post_text}
Author bio: {author_bio}
Matched keyword: {keyword}

Classify into ONE category:
- "job_seeker": Looking for work, mentions skills, "hire me", "open to work"
- "founder": Building a company, mentions startup, "my company", "we're hiring"
- "service_buyer": Explicitly looking to hire/buy services, mentions budget, timeline
- "other": None of the above

Respond with JSON: {"intent": "...", "confidence": 0.0-1.0, "reasoning": "..."}
```

**When to classify:**
- Immediately after lead creation (in `create_lead_from_post`)
- Store in `Lead.intent` and `Lead.intent_confidence`

---

## 5. Reply Performance Tracking

**Cron job / periodic check:**
```python
def update_reply_metrics():
    """Check for responses to our sent replies."""
    # Find all LeadReply where has_response=False and sent > 1 hour ago
    # For each, fetch thread replies via API
    # Check if author replied after our reply
    # Update has_response, response_text, response_at
```

**Manual conversion tracking (UI buttons):**
- "Mark as DM'd" → converted_to_dm = True
- "Had call" → converted_to_call = True  
- "Became client" → converted_to_client = True

**Response rate calculation:**
```python
response_rate = times_responded / times_used
# Auto-promote template to is_winner if response_rate > 0.3 (30%)
```

---

## 6. Lead Scoring Algorithm

**Weighted scoring:**
```python
def calculate_lead_score(lead: Lead) -> int:
    scores = {
        'intent': 0,
        'engagement': 0,
        'profile': 0,
        'recency': 0,
    }
    
    # Intent score (40% weight)
    if lead.intent == "service_buyer": scores['intent'] = 100
    elif lead.intent == "founder": scores['intent'] = 80
    elif lead.intent == "job_seeker": scores['intent'] = 50
    else: scores['intent'] = 20
    
    # Engagement score (20% weight)
    # Based on reply_count/likes from original post
    if lead.reply_count > 5: scores['engagement'] = 80
    elif lead.reply_count > 0: scores['engagement'] = 50
    else: scores['engagement'] = 20
    
    # Profile score (20% weight)
    # Keywords in bio: "founder", "ceo", "hiring", "building"
    bio_keywords = ['founder', 'ceo', 'hiring', 'building', 'startup']
    matches = sum(1 for k in bio_keywords if k in (lead.author_bio or '').lower())
    scores['profile'] = min(matches * 25, 100)
    
    # Recency score (20% weight)
    hours_old = (now - lead.post_created_at).total_seconds() / 3600
    if hours_old < 1: scores['recency'] = 100
    elif hours_old < 6: scores['recency'] = 80
    elif hours_old < 24: scores['recency'] = 60
    else: scores['recency'] = 40
    
    # Weighted total
    weights = {'intent': 0.4, 'engagement': 0.2, 'profile': 0.2, 'recency': 0.2}
    total = sum(scores[k] * weights[k] for k in scores)
    
    return int(total)
```

---

## 7. UI Changes

### Lead Kanban (add filters)
```
[All] [High Intent] [Medium] [Low] | Intent: [All ▼] [Job Seeker] [Founder] [Buyer]
```

### Lead Card (add badges)
```
┌──────────────────────┐
│ @username       [HIGH] │ ← quality_tier badge (green/yellow/red)
│ "post preview..."      │
│                        │
│ 🏷️ founder  💬 12 replies │ ← intent + engagement
└──────────────────────┘
```

### Lead Detail (new sections)
```
Intent Classification
┌──────────────────────────────┐
│ Intent: Founder (85% confidence) │
│ Reasoning: Bio mentions "building  │
│ startup", post discusses hiring   │
└──────────────────────────────┘

Lead Score: 82/100 (High Tier)
├─ Intent: 80/100
├─ Engagement: 60/100
├─ Profile: 100/100
└─ Recency: 80/100

Reply Performance (if sent)
┌──────────────────────────────┐
│ Sent: 2 hours ago              │
│ Status: ✅ They responded      │
│ Response: "Thanks! DMing you"  │
│ Converted to DM: ✅            │
│ [Mark as Had Call] [Mark as Client] │
└──────────────────────────────┘
```

### New Page: `/leads/analytics`
```
Reply Performance
┌─────────────────────────────────────┐
│ Template              Used  Resp  Rate │
│ Helpful Tip + CTA      45    12   27% │ ← winner
│ Direct Pitch           23     2    9% │
│ Question-Based         18     4   22% │
└─────────────────────────────────────┘

Intent Distribution (Last 30 Days)
[Chart: Founder 45% | Job Seeker 30% | Buyer 15% | Other 10%]

Conversion Funnel
Sent → Response: 25%
Response → DM: 40%
DM → Call: 30%
Call → Client: 50%
```

---

## 8. New CLI Commands

```bash
# Update reply metrics (check for responses)
threads-analytics update-reply-metrics

# Export leads to CSV
threads-analytics export-leads --intent founder --quality high --since 2026-01-01

# Show template performance
threads-analytics template-stats

# Recalculate all lead scores
threads-analytics rescore-leads
```

---

## 9. Implementation Notes

**Files to modify:**
- `models.py` - Add LeadReply, ReplyTemplate, LeadScore tables
- `leads.py` - Add intent classification, scoring functions
- `leads_search.py` - Trigger classification after lead creation
- `routes.py` - Add analytics page, conversion tracking endpoints
- `cli.py` - Add new commands

**Files to create:**
- `leads_analytics.py` - Performance tracking, template optimization
- `leads_scoring.py` - Scoring algorithm
- `templates/leads_analytics.html` - Performance dashboard

**Cron jobs:**
- Every hour: `update_reply_metrics()`
- Daily: Recalculate template response rates

---

## 10. Success Metrics

After 30 days:
- [ ] Classifying 90%+ of leads with >70% confidence
- [ ] Tracking response rate for 100+ sent replies
- [ ] At least 3 templates with >100 uses for A/B comparison
- [ ] Conversion tracking: DM rate, call rate, close rate
- [ ] Users can identify top-performing template

---

**Spec approved for implementation.**
