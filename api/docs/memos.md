# Memos API (staff door)

Base: `api/v1/memos/` on the **staff door** only (portal accounts: `403 wrong_door`;
the routes do not exist on the portal host). Bearer token on every call.

The website's own services decide everything: who can see a memo
(`messaging.memos.can_view_memo`), the tab lists, the approval chain
(`messaging.routing`), and which buttons a user gets. The app shows the
buttons listed in `actions` and nothing else.

## Errors

* A memo you may not see → **404 `not_found`** (also for actions and files).
* An action you may not take on a memo you can see → **403 `permission_denied`**
  (e.g. approving when you are not the current approver, acknowledging when you
  are not a recipient).
* A rule violation → **400 `validation_error`** with the website's text, e.g.
  `"A comment is required when rejecting a memo."`, `"This memo is not awaiting an approval."`,
  `"This memo can no longer be cancelled."`.
* Form errors on create → 400 with `fields` named like the form fields:

```json
{"error": {"code": "validation_error", "message": "Please check the highlighted fields.",
           "fields": {"target_user": ["Pick a recipient."]}}}
```

Common: 401, 403 `wrong_door` / `password_change_required`, 429.

## Memo statuses

`draft` → `awaiting` (walking the approval chain) → `published`;
`awaiting` → `returned` (sender edits and resubmits) or `rejected` (final);
`draft`/`awaiting`/`returned` → `cancelled` by the sender.
Priorities: `normal`, `high`, `urgent`.

---

## GET `memos/` — list

Query: `tab` = `inbox` (default) · `awaiting` · `sent` · `drafts` · `archived`;
`q` (reference, subject or body); `priority`; `page`, `page_size`.

| Tab | Contents |
| --- | --- |
| `inbox` | published memos delivered to you, not archived/removed, not expired (newest first) |
| `awaiting` | memos waiting for **your** decision (urgent first) |
| `sent` | memos you sent, **except drafts** (any other status) |
| `drafts` | your drafts |
| `archived` | memos you received and archived |

(The website's "Sent" tab also shows drafts; the app splits them.)

```json
{
  "results": [
    {
      "id": 21,
      "reference": "MEMO-2026-00021",
      "subject": "Smoke test memo",
      "summary": "Please note this smoke-test memo.",
      "priority": "normal",
      "status": "published",
      "status_label": "Published",
      "sender": {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD", "color": "c0"},
      "audience": "Ibrahim ICT Officer (Demo)",
      "created_at": "2026-10-22T00:19:56.311511+01:00",
      "published_at": "2026-10-22T00:19:56.451182+01:00",
      "updated_at": "2026-10-22T00:19:56.462160+01:00",
      "requires_acknowledgment": true,
      "unread": true,
      "acknowledged": false,
      "current_step": null,
      "attachment_count": 1
    }
  ],
  "next": null,
  "previous": null,
  "count": 1,
  "counts": {"inbox": 1, "awaiting": 0, "sent": 1, "drafts": 1, "archived": 0},
  "can_compose": true
}
```

* `summary`: first 160 characters of the body.
* `audience`: the "To" line (a name, "All Staff", a department, role names, or
  up to six names + "+N more").
* `unread` / `acknowledged`: your copy (recipients only; false otherwise).
* `current_step`: name of the step it waits on (`awaiting` only).
* `counts` feed the tab badges; the memo badge on `me/` is
  `unread.memos` = awaiting + unread inbox.

## GET `memos/<id>/` — detail

Opening it marks your copy **read** (and re-checks a memo stuck on a step
nobody holds), exactly like the website page.

```json
{
  "id": 21,
  "reference": "MEMO-2026-00021",
  "subject": "Smoke test memo",
  "summary": "Please note this smoke-test memo.",
  "priority": "normal",
  "status": "published",
  "status_label": "Published",
  "sender": {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD", "color": "c0"},
  "audience": "Ibrahim ICT Officer (Demo)",
  "created_at": "2026-10-22T00:19:56.311511+01:00",
  "published_at": "2026-10-22T00:19:56.451182+01:00",
  "updated_at": "2026-10-22T00:19:56.462160+01:00",
  "requires_acknowledgment": true,
  "unread": false,
  "acknowledged": false,
  "current_step": null,
  "attachment_count": 1,
  "body": "Please note this smoke-test memo.",
  "visibility": "individuals",
  "visibility_label": "A list of people",
  "target_user": null,
  "department": null,
  "expires_at": null,
  "attachments": [
    {"id": 1, "name": "agenda.txt", "description": "", "size_bytes": 6, "content_type": "text/plain",
     "download_path": "/api/v1/memos/attachments/1/", "expires_at": "2026-11-21T00:19:56.368877+01:00",
     "expired": false}
  ],
  "route": "General memo",
  "route_steps": [
    {"position": 1, "name": "Line Head", "state": "skipped", "is_current": false, "actor": null,
     "reason": "The sender is already at line head level or above.", "decided_by": null,
     "decided_at": "2026-10-22T00:19:56.450529+01:00", "comment": ""}
  ],
  "current_step_detail": null,
  "acting_for_someone": false,
  "parked_reason": "",
  "returned": null,
  "rejected": null,
  "my_receipt": {"read_at": "2026-10-22T00:19:56.854142+01:00", "acknowledged_at": null, "archived": false},
  "is_sender": false,
  "actions": ["acknowledge", "archive"],
  "stats": null,
  "documents": {"pdf_path": "/api/v1/memos/21/pdf/", "docx_path": "/api/v1/memos/21/docx/", "watermarked": false}
}
```

| Field | Notes |
| --- | --- |
| `visibility` | `specific_user` · `individuals` · `all_staff` · `all_directors` · `department` · `role` |
| `target_user` / `department` | set for `specific_user` / `department` |
| `attachments[].expired` | memo attachments expire after 30 days; the download then answers 404 |
| `route_steps[]` | the approval chain as the website shows it. `state`: `approved` · `returned` · `rejected` · `skipped` (with `reason`) · `current` (waiting on `actor`) · `parked` (nobody can act; `reason`) · `pending` (ahead; `actor` = who will act) · `will_skip` · `blocked` · `not_reached`. `decided_by`/`decided_at`/`comment` for recorded decisions. For a draft it is the *expected* chain |
| `current_step_detail` | `{"name", "instructions", "actor"}` while `awaiting` |
| `acting_for_someone` | you can act only as an administrator on someone else's step |
| `parked_reason` | why nobody can act on the current step |
| `returned` / `rejected` | `{"by": person, "at", "comment"}` when in that status |
| `my_receipt` | your copy as a recipient, else null |
| `actions` | buttons to show, in order (see below) |
| `stats` | sender / managers, published memos: `{"recipients", "read", "acknowledged", "pending_acknowledgment": [names, first 50]}` |
| `documents` | PDF/Word paths when you may download (published, or your own unpublished memo → `watermarked: true`); null otherwise |

Example while awaiting the director (director's view):

```json
"current_step": "Director approval",
"route_steps": [
  {"position": 1, "name": "Director approval", "state": "current", "is_current": true,
   "actor": {"id": 2, "name": "Dora Test", "initials": "DT", "color": "c2"},
   "reason": "", "decided_by": null, "decided_at": null, "comment": ""}
],
"current_step_detail": {"name": "Director approval", "instructions": "",
                        "actor": {"id": 2, "name": "Dora Test", "initials": "DT", "color": "c2"}},
"actions": ["approve", "return", "reject"],
"documents": null
```

## Actions — POST `memos/<id>/<action>/`

Request body (all optional unless stated):
`{"comment": "…", "reason": "…", "subject": "…", "body": "…"}`.

Response **200**:

```json
{"notice": "Approved. Memo MEMO-2026-00021 published to 1 recipient(s).", "memo": { …detail… }}
```

`notice` is the website's confirmation text; `memo` is the fresh detail (null
if you can no longer see the memo).

| Action | Who (shown in `actions` when allowed) | Body |
| --- | --- | --- |
| `approve` | current approver (or an administrator) | `comment` optional |
| `return` | current approver | `comment` optional (shown to the sender) |
| `reject` | current approver | `comment` **required** (400 otherwise) |
| `resubmit` | sender of a `returned` memo | `subject`, `body` (blank = unchanged), `comment` |
| `publish` | sender of a `draft` | — starts the chain; published at once when no step applies |
| `acknowledge` | recipient, memo requires acknowledgment | — (repeat is harmless) |
| `archive` / `unarchive` | recipient of a published memo | — |
| `cancel` | sender of a `draft`/`awaiting`/`returned` memo | `reason` optional |

Notices: approve → `"Approved. Memo … published to N recipient(s)."` /
`"Approved. Passed to the next step."`; return → `"Memo returned to the sender."`;
reject → `"Memo rejected."`; resubmit → `"Memo resubmitted for approval."` /
`"Memo … resubmitted and published."`; publish → `"Memo … sent for approval."` /
`"Memo … published to N recipient(s)."`; acknowledge → `"Acknowledged."`;
archive → `"Memo … archived."`; unarchive → `"Memo … restored to your inbox."`;
cancel → `"Memo … cancelled."`.

## POST `memos/` — create

JSON, or multipart when sending `attachments`. Validated by the website's
memo form, then saved as a draft and (with `action: "publish"`) sent into the
approval chain.

```json
{
  "subject": "Smoke test memo",
  "body": "Please note this smoke-test memo.",
  "priority": "normal",
  "visibility": "individuals",
  "recipients": [17],
  "requires_acknowledgment": true,
  "expires_at": null,
  "action": "publish",
  "from_message": 35
}
```

| Field | |
| --- | --- |
| `subject`, `body` | required (≥ 3 characters each) |
| `priority` | `normal` (default) · `high` · `urgent` |
| `visibility` | one of `compose-options/` → `visibility` (others → 400 `fields.visibility`) |
| `target_user` | user id, for `specific_user` (from `people/`) |
| `recipients` | user ids, for `individuals` |
| `department` | department id, for `department` |
| `roles` | group ids, for `role` |
| `requires_acknowledgment` | bool |
| `expires_at` | ISO date-time or null (minute precision) |
| `attachments` | multipart files, ≤ 5 MB in total |
| `action` | `draft` (default) or `publish` |
| `from_message` | chat message id ("Turn into a memo"); links the message to the memo |

**201**:

```json
{"notice": "Memo MEMO-2026-00021 published to 1 recipient(s).",
 "notices": [{"level": "success", "text": "Memo MEMO-2026-00021 published to 1 recipient(s)."}],
 "memo": { …detail… }}
```

`notices` may include a `warning` (e.g. `"Could not attach x.pdf."`, or
`"Draft saved, but the chain could not start: …"` - the memo then stays a
draft). Errors: 400 `fields.subject|body|visibility|target_user|recipients|department|roles|attachments`,
400 `"Subject must be at least 3 characters."` (non-field).

**Limits of the app's compose (by design):** editing an existing draft and
adding/removing attachments after creation are website-only for now (create a
new draft instead, or cancel). Memo deletion is website-only.

### GET `memos/compose-options/`

```json
{
  "visibility": [{"value": "specific_user", "label": "One specific person"},
                 {"value": "individuals", "label": "A list of people"}],
  "priorities": [{"value": "normal", "label": "Normal"}, {"value": "high", "label": "High"},
                 {"value": "urgent", "label": "Urgent"}],
  "departments": [],
  "roles": [],
  "can_broadcast": false,
  "attachments_total_bytes": 5242880
}
```

Users with the broadcast permission also get `all_staff`, `all_directors`,
`department`, `role`, and the `departments` / `roles` lists (`{"id", "name"}`).

### GET `memos/people/?q=` — recipient picker

Active users except you (the website memo form's list); `q` matches name,
e-mail or department. Paginated.

```json
{"results": [{"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD", "color": "c0",
              "email": "qa.ict_director@demo.nbte.local", "department": "ICT"}],
 "next": null, "previous": null, "count": 1}
```

## Files

| Method | Path | |
| --- | --- | --- |
| GET | `memos/attachments/<id>/` | the attachment (original file name in `Content-Disposition`; `?inline=1` for preview). 404 if you cannot see the memo or it expired |
| GET | `memos/<id>/pdf/` | `application/pdf`, `attachment; filename="MEMO-2026-00021.pdf"` |
| GET | `memos/<id>/docx/` | Word document |

PDF/Word: anyone who can see a **published** memo; before publication only the
sender/managers (watermarked draft). Others who can see it (e.g. the approver)
get 403 `permission_denied` - use `documents` in the detail to decide whether
to show the buttons. 503 `document_unavailable` if generation failed.

## Endpoint summary

| Method | Path |
| --- | --- |
| GET / POST | `memos/` |
| GET | `memos/compose-options/` · `memos/people/` |
| GET | `memos/<id>/` |
| POST | `memos/<id>/approve/` · `return/` · `reject/` · `resubmit/` · `publish/` · `acknowledge/` · `archive/` · `unarchive/` · `cancel/` |
| GET | `memos/<id>/pdf/` · `memos/<id>/docx/` · `memos/attachments/<id>/` |
