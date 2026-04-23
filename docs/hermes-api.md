# Hermes API Integration Guide

> **For:** Hermes agent or any external content agent integrating with Threads Analytics  
> **Base URL:** `https://threads.zipang.id`  
> **Auth:** `X-Hermes-Key` header (required)

---

## Authentication

Every request must include the Hermes API key in the header:

```
X-Hermes-Key: hermes-FIskj-hynYJgNF_Cpy-oEYgJ2OVH-2ui5MKwPQm-KFs
```

---

## Endpoints

### 1. Push Content (Create Draft or Publish)

Creates a new content draft. Optionally publishes immediately.

```
POST /accounts/default/api/hermes/push
Content-Type: application/json
X-Hermes-Key: <key>
```

**Request Body:**

```json
{
  "concept": "Full post text here. This is the body of the Threads post.",
  "title": "Optional headline",
  "image_url": "https://example.com/image.jpg",
  "tier": "A",
  "mechanic": "community_ask",
  "predicted_score": 85,
  "predicted_views_range": "5K–10K",
  "scheduled_at": "2026-04-25T09:00:00",
  "auto_publish": true,
  "rubric": {
    "hook_test": 10,
    "mechanic_fit": 10,
    "operator_standing": 10,
    "trend_freshness": 7,
    "reply_invitation": 7,
    "voice_signature": 5
  }
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `concept` | string | **Yes** | Full post body text |
| `title` | string | No | Headline. Auto-generated from first line if omitted |
| `image_url` | string | No | URL to image attachment |
| `tier` | string | No | Content tier (e.g. "A", "B", "C") |
| `mechanic` | string | No | Content mechanic tag (e.g. `community_ask`, `binary_verdict`, `credibility_anchor`) |
| `predicted_score` | number | No | Predicted performance score (default: 0) |
| `predicted_views_range` | string | No | Predicted view range (default: "") |
| `scheduled_at` | ISO datetime | No | Schedule for future publish |
| `auto_publish` | boolean | No | If `true`, approves and publishes immediately |
| `rubric` | object | No | Rubric scores. Defaults applied if omitted |

**Rubric defaults (if omitted):**
- `hook_test`: 10
- `mechanic_fit`: 10
- `operator_standing`: 10
- `trend_freshness`: 7
- `reply_invitation`: 7
- `voice_signature`: 5

**Response (auto_publish=false or omitted):**

```json
{
  "success": true,
  "idea_id": 28
}
```

**Response (auto_publish=true, success):**

```json
{
  "success": true,
  "idea_id": 28,
  "published": true
}
```

**Response (auto_publish=true, blocked):**

```json
{
  "success": true,
  "idea_id": 28,
  "published": false,
  "publish_error": "Publishing not enabled for this account"
}
```

> **Note:** No content gate will block you. Anti-slop, brand, and rubric checks are all advisory-only (logged as warnings, never blocking).

---

### 2. Fetch Pending Comments (Need Replies)

Returns comments that have no reply yet, sorted newest first by actual post time.

```
GET /accounts/default/comments/api/pending
X-Hermes-Key: <key>
```

**Response:**

```json
{
  "success": true,
  "comments": [
    {
      "id": 108,
      "comment_author_username": "vikivirgon",
      "comment_text": "persis - monthly + multiple providers outperforms single annual...",
      "source_post_text": "Annual subscription plan dari AI providers bahaya banget...",
      "ai_draft_reply": null,
      "final_reply": null,
      "status": "drafted",
      "comment_permalink": "https://www.threads.com/@vikivirgon/post/DXeL5SCkUEk",
      "comment_created_at": "2026-04-23T10:57:55",
      "last_seen_at": "2026-04-23T13:14:40"
    }
  ],
  "count": 1
}
```

**Comment fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | number | Inbox ID (use this for replying) |
| `comment_author_username` | string | Who wrote the comment |
| `comment_text` | string | The comment body |
| `source_post_text` | string | Your original post text (truncated to 200 chars) |
| `ai_draft_reply` | string \| null | Existing AI draft (always null in this endpoint) |
| `final_reply` | string \| null | Finalized reply (always null in this endpoint) |
| `status` | string | `drafted` or `approved` |
| `comment_permalink` | string | Direct link to the comment on Threads |
| `comment_created_at` | ISO datetime | When the comment was posted |
| `last_seen_at` | ISO datetime | When the poller last saw this comment |

> **Important:** This endpoint only returns comments with **no reply at all** (both `ai_draft_reply` and `final_reply` are empty). Once you reply, the comment disappears from this list.

---

### 3. Reply to a Comment

Submit a reply for a specific comment. Optionally sends it immediately.

```
POST /accounts/default/api/hermes/comments/reply
Content-Type: application/json
X-Hermes-Key: <key>
```

**Request Body:**

```json
{
  "inbox_id": 108,
  "reply_text": "Setuju banget — flexibility over lock-in selalu menang di long run.",
  "auto_send": true
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `inbox_id` | number | **Yes** | Comment inbox ID from `/pending` |
| `reply_text` | string | **Yes** | The reply text to post |
| `auto_send` | boolean | No | If `true`, approves and sends immediately. Default: `false` |

**Response (auto_send=true, sent):**

```json
{
  "success": true,
  "inbox_id": 108,
  "sent": 1,
  "failed": 0
}
```

**Response (auto_send=false or omitted):**

```json
{
  "success": true,
  "inbox_id": 108,
  "status": "drafted"
}
```

> The reply is saved as `final_reply`. If `auto_send=true`, it transitions to `approved`, attempts to post to Threads, and returns send results.

---

## Workflow Summary

### Content publishing
1. Generate post content
2. `POST /api/hermes/push` with `auto_publish: true`
3. Done — post is live (or error is returned if token/quota issues)

### Comment replies
1. Poll `GET /comments/api/pending` every few minutes
2. For each comment, generate a reply
3. `POST /api/hermes/comments/reply` with `auto_send: true`
4. Comment disappears from next `/pending` poll

---

## Error Responses

All endpoints return consistent error shapes:

**Unauthorized:**
```json
{"error": "Unauthorized"}
```

**Invalid JSON:**
```json
{"error": "Invalid JSON"}
```

**Missing required field:**
```json
{"error": "concept is required"}
```

**Not found:**
```json
{"error": "Comment not found"}
```

---

## Mechanics Reference

Common `mechanic` values used in the system:

- `community_ask`
- `binary_verdict`
- `credibility_anchor`
- `reality_check`
- `myth_busting`
- `level_framework`
- `listicle`
- `direct_ask`
- `cost_shock`

If unsure, omit the field — the system will work without it.
