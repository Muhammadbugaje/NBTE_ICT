# Work tab — tasks, tickets, requests, leave (staff door)

All endpoints in this file are **staff door only** (`https://<internal host>/api/v1/`, dev `http://<mac-ip>:8000/api/v1/` without the portal header). On the portal host these routes do not exist (404 `not_found`); a portal token on the staff host gets 403 `wrong_door`.

Every call needs `Authorization: Bearer <access>`. Common errors on every endpoint (not repeated below):

| Status | Code | When |
| --- | --- | --- |
| 401 | `not_authenticated` / `token_expired` | no / bad / expired token |
| 403 | `wrong_door` | portal account on the staff door |
| 403 | `password_change_required` | temporary password still in use |
| 403 | `account_inactive` | deactivated account |
| 429 | `throttled` | |

Conventions used below:

* **Lists** are paginated: `{"results": [...], "next": url|null, "previous": url|null, "count": n}`, `?page=` and `?page_size=` (max 100, default 25).
* **Not yours = 404.** An object you may not see (task, ticket, request, attachment) answers 404 `not_found`, never 403.
* **Action refused = 403 `permission_denied`** with the website's own message in `error.message` (e.g. "You cannot cancel this task.").
* **Validation = 400 `validation_error`** with `fields`. Field names are the request keys; a business rule from the website's service that is not tied to one field comes back under `non_field_errors` and also as `error.message`.
* `UserRef` = `{"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD"}`.
* `Attachment` = `{"id", "name", "description", "size" (bytes or null), "uploaded_by": UserRef, "created_at", "download_url"}`. Download with GET + Bearer; add `?inline=1` to preview PDFs/images (served with `Content-Disposition: inline`). Uploads are `multipart/form-data`, field `file` (+ optional `description`); allowed types are the website's (`pdf, doc, docx, xls, xlsx, jpg, jpeg, png` by default — configurable; others answer 400 on `file`).
* `Activity` = `{"id", "kind", "actor": UserRef|null, "message", "created_at"}` (timeline, oldest first).
* `actions` in every detail tells the app which buttons the website shows this user. Hide what is `false`; the server re-checks anyway.

---

## Tasks

The website's Tasks app (management pushing work out). Visibility: tasks you created, tasks you are assigned to, tasks owned by your department if you are a department lead, everything if you have cross-department access.

### Task states

`draft` → `open` → `in_progress` ↔ `awaiting_response` → `awaiting_review` → `completed`; also `returned` (assignee sent it back) and `cancelled`.
Assignee row states (`my_state`, `assignees[].state`): `assigned`, `accepted`, `in_progress`, `done`, `blocked`, `declined`.
Priorities: `urgent`, `high`, `normal`, `low`.

### GET `tasks/`

Query:

| Param | Values | Default |
| --- | --- | --- |
| `scope` | `assigned` (Assigned to me, open only), `created` (My tasks), `review` (waiting for my review), `all` (everything I may see) | `assigned` |
| `state` | `active` (not closed), `open`, `overdue`, `completed`, `cancelled`, `all`, or one exact state | `active` |
| `priority` | priority | |
| `category` | category **code** | |
| `due` | `overdue`, `today`, `week` (next 7 days), `none` (no due date) | |
| `q` | search reference / title / description | |
| `sort` / `direction` | `created`, `updated`, `due`, `priority`, `state`, `title`, `reference` / `asc`, `desc` | `due` for assigned, else `created`; `desc` |

Response 200:
```json
{"results": [{"id": 31, "reference": "TSK-2026-00031", "title": "Replace the UPS battery in the server room",
  "state": "open", "state_display": "Open", "priority": "high", "priority_display": "High",
  "category": null, "due_date": "2026-10-30", "due_label": "Fri 30 Oct", "is_overdue": false,
  "created_by": {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD"},
  "owner_department": null, "progress_percent": 0, "assignee_count": 1, "my_state": "assigned",
  "updated_at": "2026-10-22T01:04:51.613131+01:00"}],
 "next": null, "previous": null, "count": 1}
```
`category` when set: `{"id": 1, "code": "general", "name": "General"}`; `owner_department`: `{"id": 1, "name": "ICT"}`. `due_label` is `Overdue` / `Today` / `Tomorrow` / `Fri 30 Oct` (empty when closed). Errors: 400 on unknown `scope` etc.

### GET `tasks/categories/`

Categories I may create tasks in (filtered by my rank, as the website's form).
```json
{"results": [{"id": 1, "code": "general", "name": "General",
  "description": "Everyday work items that don't fit a specific category.", "handling_department": null,
  "default_priority": "normal", "default_due_days": 7, "requires_review": true, "icon": "check-square", "colour": ""}],
 "next": "…?page=2&page_size=1", "previous": null, "count": 8}
```

### GET `tasks/people/?q=&category=<id>&task=<id>`

Assignee picker (the website's eligible-assignees rules: only people of **lower rank** than me, never the Executive Secretary, limited to the category's allowed roles and handling department; with `task` the task's category/department apply).
```json
{"results": [{"id": 10, "name": "Amina Officer (Demo)", "initials": "AO", "email": "qa.officer@demo.nbte.local",
  "role": "Officer / Technician", "department": "DPAP", "used": 1, "max": 6, "remaining": 5, "at_capacity": false}],
 "next": "…", "previous": null, "count": 9}
```
Someone `at_capacity` cannot be assigned (the server refuses with a message).

### POST `tasks/` — create

```json
{"title": "Replace the UPS battery in the server room", "description": "The UPS alarm sounds every morning.",
 "category": null, "priority": "high", "start_date": null, "due_date": "2026-10-30", "estimated_hours": "2.50",
 "requires_review": true, "reviewer": null, "tags": ["power"], "recurrence_type": "none", "recurrence_rule": "",
 "assignees": [17], "open_now": true, "from_message": null}
```
Only `title` is required. Defaults: `priority` = the category's default (else `normal`), `due_date` = today + category's `default_due_days`, `requires_review` = true, `recurrence_type` = `none`. `open_now: true` opens the task right away (assignees notified) — otherwise it stays a **draft**. `from_message`: chat message id; the message gets linked to the new task ("Make a task" from chat). Assignees the rules refuse (rank/capacity) are skipped silently, exactly as on the website — check `assignees` in the response.

Response 201: `{"task": <task detail>, "warnings": []}`; `warnings` says why `open_now` could not open it (e.g. `"Could not open: Add at least one assignee or subtask before opening."`).
Errors 400: `title` (min 3 characters), `due_date` ("Due date must be after start date."), `category` (not allowed for your rank), `reviewer`/`assignees` (unknown user).

### GET `tasks/<id>/` — detail

```json
{"id": 31, "reference": "TSK-2026-00031", "title": "Replace the UPS battery in the server room",
 "state": "in_progress", "state_display": "In progress", "priority": "high", "priority_display": "High",
 "category": null, "due_date": "2026-10-30", "due_label": "Fri 30 Oct", "is_overdue": false,
 "created_by": {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD"}, "owner_department": null,
 "progress_percent": 0, "assignee_count": 1, "my_state": "in_progress", "updated_at": "2026-10-22T01:05:27+01:00",
 "description": "The UPS alarm sounds every morning.", "start_date": null, "estimated_hours": null,
 "logged_hours": 0.0, "requires_review": true, "reviewer": null, "reviewed_by": null, "reviewed_at": null,
 "review_notes": "", "tags": ["power"], "recurrence_type": "none", "recurrence_rule": "", "parent": null,
 "created_at": "2026-10-22T01:04:51.598498+01:00", "completed_at": null, "cancelled_at": null,
 "assignees": [{"user": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
   "state": "in_progress", "state_display": "In progress", "note": "",
   "assigned_by": {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD"},
   "assigned_at": "2026-10-22T01:04:51.604507+01:00", "accepted_at": null,
   "started_at": "2026-10-22T01:05:27.5+01:00", "completed_at": null}],
 "subtasks": [],
 "dependencies": [{"id": 4, "depends_on": {"id": 12, "reference": "TSK-2026-00012", "title": "Order battery",
   "state": "completed", "state_display": "Completed"}, "note": "", "met": true}],
 "dependencies_met": true,
 "comments": [{"id": 4, "author": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
   "body": "Battery ordered from the vendor.", "is_internal": false, "is_pinned": false,
   "created_at": "2026-10-22T01:05:27.774272+01:00", "can_delete": true}],
 "attachments": [{"id": 1, "name": "quote.pdf", "description": "Vendor quote", "size": 14,
   "uploaded_by": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
   "created_at": "2026-10-22T01:05:28.009993+01:00",
   "download_url": "http://localhost:8174/api/v1/tasks/31/attachments/1/"}],
 "time_entries": [{"id": 9, "user": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
   "started_at": "2026-10-22T09:00:00+01:00", "ended_at": "2026-10-22T10:30:00+01:00",
   "duration_seconds": 5400, "hours": 1.5, "note": "", "is_running": false}],
 "running_timer": null,
 "activity": [{"id": 193, "kind": "created", "actor": {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD"},
   "message": "Task created: Replace the UPS battery in the server room", "created_at": "2026-10-22T01:04:51.601058+01:00"}],
 "qa_link_url": "",
 "actions": {"is_creator": false, "is_assignee": true, "can_edit": false, "can_open": false, "can_cancel": false,
   "can_delete": false, "can_review": false, "can_assign": false, "can_accept": false, "can_start": false,
   "can_reopen": false, "can_start_timer": true, "can_stop_timer": false, "can_log_time": true, "can_ask": true,
   "can_resume": false, "can_return": true, "can_mark_done": true, "can_resend": false, "can_comment": true,
   "can_attach": true}}
```
`subtasks` items have the list-item shape. `time_entries` = last 20. `running_timer` = my running timer (same shape) or null. `qa_link_url` non-empty = the task is driven from the QA screens (QA stage); it closes there, so mark-done/cancel/review are off.
Errors: 404.

### PATCH `tasks/<id>/` — edit

Any subset of: `title, description, category, priority, start_date, due_date, estimated_hours, requires_review, reviewer, tags, recurrence_type, recurrence_rule`. Same rules as the website's edit page: creator, department head of the owning department, or superuser (else 403 "You cannot edit this task."); closed tasks → 400 "Closed tasks cannot be edited.". Response 200: task detail. Assignees are changed with the endpoints below.

### Transitions — POST `tasks/<id>/<action>/`

Each returns 200 with the updated task detail (except `delete`). Body keys and who may do it (from `actions`):

| Action | Body | Who / when (`actions` flag) | Result |
| --- | --- | --- | --- |
| `open/` | — | creator / dept head, draft with assignees (`can_open`) | `open`, assignees notified |
| `accept/` | `{"note"?}` | assignee, row `assigned` (`can_accept`) | my row `accepted` |
| `start/` | `{"note"?}` | assignee (`can_start`); refused while dependencies are open | `in_progress` |
| `ask/` | `{"note"}` required, 3+ chars | assignee (`can_ask`) | `awaiting_response`, creator notified |
| `resume/` | `{"note"?}` | assignee (`can_resume`) | `in_progress` |
| `return/` | `{"reason"}` required, 5+ chars | assignee (`can_return`) | `returned` to the assigner |
| `resend/` | `{"note"?}` | creator / dept head, `returned` (`can_resend`) | `open` again |
| `mark-done/` | `{"note"?}` | assignee (`can_mark_done`) | my row `done`; when everyone is done → `awaiting_review` (or `completed` if no review) |
| `review/` | `{"approve": true\|false, "notes"}` — notes 5+ chars when `approve` is false | reviewer / creator / dept head, `awaiting_review` (`can_review`) | `completed` or back to `in_progress` |
| `cancel/` | `{"reason"?}` | creator / dept head / higher rank (`can_cancel`) | `cancelled` |
| `reopen/` | `{"reason"}` required, 5+ chars | creator / reviewer / dept head, `completed` (`can_reopen`) | reopened, assignees notified |
| `delete/` | `{"reason"}` required, 5+ chars | creator / higher rank (`can_delete`) | **204**, task gone |
| `timer/start/` | — | assignee (`can_start_timer`) | `running_timer` set |
| `timer/stop/` | — | (`can_stop_timer`) | time entry logged |
| `log-time/` | `{"started_at", "ended_at", "note"?}` (ISO date-times) | assignee, task not closed (`can_log_time`) | entry added; max 24 h, not in the future |

Examples:
```http
POST tasks/31/ask/   {"note": "x"}
400 {"error": {"code": "validation_error", "message": "Please check the highlighted fields.",
               "fields": {"note": ["Please write your question."]}}}

POST tasks/31/review/   {"approve": true, "notes": "Thanks"}   (as the assignee)
403 {"error": {"code": "permission_denied", "message": "You cannot review this task."}}

POST tasks/31/start/    (dependency still open)
400 {"error": {"code": "validation_error", "message": "Cannot start — waiting on: TSK-2026-00012. Complete dependencies first.",
               "fields": {"non_field_errors": ["Cannot start — waiting on: TSK-2026-00012. Complete dependencies first."]}}}
```

### Assignees

* POST `tasks/<id>/assignees/` `{"user_ids": [17, 18]}` → 200
  `{"added": [17], "skipped": ["Jamila ICT Officer 2 (Demo) is at capacity (6/6 open tasks)."], "task": <task detail>}`.
  Creator / dept head only (otherwise every id is skipped with "You cannot assign this task."). Adding an assignee to a draft opens it (website rule).
* DELETE `tasks/<id>/assignees/<user_id>/` → 200 task detail. 400 "That user is not assigned." / 403.

### Comments

* POST `tasks/<id>/comments/` `{"body": "Battery ordered from the vendor.", "is_internal": false, "pinned": false}` → **201** task detail. `pinned` only takes effect for editors. Closed task → 400 "This task is closed. Comments are read-only."; empty body → 400 `fields.body`.
* DELETE `tasks/<id>/comments/<comment_id>/` → 204 (author, dept head, superuser — `can_delete` on the comment; else 403).

### Attachments

* POST `tasks/<id>/attachments/` multipart `file`, `description?` → **201** task detail. Closed task → 400.
* GET `tasks/<id>/attachments/<attachment_id>/` → the file (`Content-Type` of the file, `Content-Disposition: attachment; filename*=…`). 404 when the task is not visible.
* DELETE `tasks/<id>/attachments/<attachment_id>/` → 204 (uploader / dept head).

---

## Tickets

Help-desk tickets. Visibility (`tickets/access.py`): tickets I raised or that are assigned to me; department leads (and holders of "view department requests") see tickets handled by or raised in their department; admin staff of the handling department see its tickets; "view all requests" sees everything.

States: `new`, `assigned`, `in_progress`, `awaiting_response`, `escalated`, `awaiting_director`, `resolved`, `closed`, `reopened`, `cancelled`. Open = new/assigned/in_progress/awaiting_response/escalated/reopened/awaiting_director.

### GET `tickets/?scope=my|assigned|all&state=&priority=&category=&q=`

`my` (default) = raised by me; `assigned` = assigned to me (cancelled hidden); `all` = every ticket I may see. `state`: `open`, `closed` or an exact state. `category` = code. Newest first.
```json
{"results": [{"id": 3, "reference": "TKT-2026-00003", "subject": "Monitor flickers", "state": "new", "state_display": "New",
  "priority": "normal", "priority_display": "Normal", "category": {"id": 1, "code": "hardware", "name": "Hardware"},
  "raised_by": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"}, "assigned_to": null,
  "handling_department": {"id": 1, "name": "ICT"}, "resolution_due_at": "2026-10-26T01:05:35.947377+01:00",
  "sla_overdue": false, "created_at": "2026-10-22T01:05:35.947377+01:00", "updated_at": "2026-10-22T01:05:35.958684+01:00"}],
 "next": null, "previous": null, "count": 1}
```

### GET `tickets/categories/`
```json
{"results": [{"id": 1, "code": "hardware", "name": "Hardware",
  "description": "Physical device faults, replacement requests, upgrades.", "icon": "",
  "handling_department": {"id": 1, "name": "ICT"}, "minimum_priority": "normal",
  "priorities": ["normal", "high", "urgent"]}], "next": "…", "previous": null, "count": 5}
```
`priorities` = what the create form allows (the category's floor and up).

### POST `tickets/` — raise (JSON, or multipart with a photo)

Multipart fields / JSON keys: `category` (code, required), `subject` (4+ chars), `body` (10+ chars), `priority?` (default = category minimum; lower than the floor → 400), `current_location?`, `asset_tag?` (links the asset when the tag exists), `photo?` (file, attachment rules).
```bash
curl -X POST -H "Authorization: Bearer …" -F category=hardware -F subject="Monitor flickers" \
  -F body="My monitor flickers every few minutes since Monday." -F current_location="Room 12, ICT" \
  -F photo=@photo.jpg  …/api/v1/tickets/
```
Response **201** ticket detail:
```json
{"id": 3, "reference": "TKT-2026-00003", "subject": "Monitor flickers", "state": "new", "state_display": "New",
 "priority": "normal", "priority_display": "Normal", "category": {"id": 1, "code": "hardware", "name": "Hardware"},
 "raised_by": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"}, "assigned_to": null,
 "handling_department": {"id": 1, "name": "ICT"}, "resolution_due_at": "2026-10-26T01:05:35.947377+01:00",
 "sla_overdue": false, "created_at": "2026-10-22T01:05:35.947377+01:00", "updated_at": "2026-10-22T01:05:35.958684+01:00",
 "body": "My monitor flickers every few minutes since Monday.", "current_location": "Room 12, ICT", "asset": null,
 "resolution_notes": "", "collection_location": "", "collection_timeframe": "",
 "response_due_at": "2026-10-22T05:05:35.947377+01:00", "first_response_at": null, "resolved_at": null,
 "closed_at": null, "escalation_level": 0, "reopen_count": 0, "satisfaction_rating": null,
 "satisfaction_comment": "", "director_signoff_required": false, "comments": [],
 "attachments": [{"id": 1, "name": "photo.jpg", "description": "Attached at creation.", "size": 48211,
   "uploaded_by": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
   "created_at": "2026-10-22T01:05:35.965331+01:00", "download_url": "http://…/api/v1/tickets/3/attachments/1/"}],
 "activity": [{"id": 21, "kind": "created", "actor": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
   "message": "Ticket raised: Monitor flickers", "created_at": "2026-10-22T01:05:35.959854+01:00"}],
 "actions": {"is_raiser": true, "is_assignee": false, "can_assign": true, "can_start": false, "can_await": false,
   "can_resolve": false, "can_signoff": false, "can_close": false, "can_reopen": false, "can_cancel": true,
   "can_comment": true, "can_internal": false, "can_attach": true}}
```
`asset` when linked: `{"tag": "ICT-LAP-00007", "name": "Dell Latitude 5420", "can_open": true}` (`can_open` = may open `assets/<tag>/`).
Errors 400: `category` ("Choose an open ticket category."), `subject`, `body`, `priority`, `photo`.

### GET `tickets/<id>/`
Ticket detail as above. Internal notes and their activity entries are removed for users without `can_internal` (the raiser never sees them). 404 when not visible.

### GET `tickets/<id>/assignees/?q=`
People the ticket can be assigned to (active users of the handling department). Only when `actions.can_assign` (else 403).
```json
{"results": [{"id": 18, "name": "Jamila ICT Officer 2 (Demo)", "initials": "JI", "email": "qa.ict_officer2@demo.nbte.local"}],
 "next": null, "previous": null, "count": 8}
```

### Actions — POST `tickets/<id>/<action>/` → 200 ticket detail

| Action | Body | Who (`actions` flag) |
| --- | --- | --- |
| `assign/` | `{"assignee": 18, "reason"?}` | raiser (first assignment, or if the category allows raiser reassignment), head of the handling department (`can_assign`). Assignee outside the handling department → 400 `fields.assignee` |
| `start/` | `{"note"?}` | assignee (`can_start`) |
| `awaiting/` | `{"reason"?}` | assignee while in progress (`can_await`) |
| `resolve/` | `{"resolution_notes"}` 5+ chars, `collection_location?`, `collection_timeframe?` | assignee / dept head (`can_resolve`); goes to `awaiting_director` when the category needs sign-off |
| `signoff/` | `{"notes"?}` | director of the handling department (`can_signoff`) |
| `close/` | `{"rating"?: 1-5, "comment"?}` | raiser, resolved (`can_close`) |
| `reopen/` | `{"reason"}` 5+ chars | raiser, resolved/closed (`can_reopen`) |
| `cancel/` | `{"reason"?}` | raiser, new/assigned/awaiting (`can_cancel`) |

Example: `POST tickets/3/assign/ {"assignee": 18, "reason": "Nearest technician"}` → 200 with `"state": "assigned"`, `"assigned_to": {"id": 18, …}`. Refusals: 403 with the service message, e.g. "Only the assignee can start work.".

### Comments and files

* POST `tickets/<id>/comments/` `{"body": "…", "is_internal": false}` → **201** ticket detail. `is_internal: true` = internal note (`can_internal` only; raiser → 403 "Only staff can add internal notes.").
* POST `tickets/<id>/attachments/` multipart `file`, `description?` → **201**. Closed ticket (non-raiser) → 403.
* GET `tickets/<id>/attachments/<attachment_id>/` → the file.

---

## Requests (workflow)

Requests raised to departments (ICT faults, equipment, vehicles, facilities, **annual leave**…), routed through an approval chain. Visibility: requests I raised, am assigned to, acted on, or that are waiting for me; department leads see their department's; "view all requests" sees all.

Statuses: `awaiting_approval`, `awaiting_receiving`, `returned` (back to the requester), `in_progress`, `awaiting_fulfillment`, `ready_for_handover`, `completed`, `rejected`, `cancelled` (`draft` unused). Step kinds: `approval`, `assignment`, `fulfillment`, `closure`.

### GET `requests/types/`
Every active type (as the website's "New request" picker). `fields` are the type's form fields **without** picker options.
```json
{"results": [{"id": 3, "code": "ict-network", "name": "Network / internet issue", "description": "", "icon": "",
  "category": {"id": 1, "code": "ict", "name": "ICT"}, "handling_department": {"id": 1, "name": "ICT"},
  "is_leave": false,
  "fields": [{"key": "affected_area", "label": "Affected area", "type": "text", "required": true, "help": "",
              "max_length": 255, "options": null},
             {"key": "problem_type", "label": "What's the issue?", "type": "select", "required": true, "help": "",
              "max_length": null, "options": null}]}],
 "next": "…", "previous": null, "count": 10}
```

### GET `requests/types/<code>/`
One type with `options` filled: `select` → its options; `asset_picker` → the assets I may see (`value` = asset tag); `user_picker` → people of my department (`value` = e-mail).
```json
{"id": 3, "code": "ict-network", "name": "Network / internet issue", "description": "", "icon": "",
 "category": {"id": 1, "code": "ict", "name": "ICT"}, "handling_department": {"id": 1, "name": "ICT"}, "is_leave": false,
 "fields": [{"key": "affected_area", "label": "Affected area", "type": "text", "required": true, "help": "",
             "max_length": 255, "options": null},
            {"key": "problem_type", "label": "What's the issue?", "type": "select", "required": true, "help": "",
             "max_length": null, "options": [{"value": "No internet", "label": "No internet"},
                                             {"value": "Slow connection", "label": "Slow connection"},
                                             {"value": "Other", "label": "Other"}]}]}
```
Field types: `text` (string, `max_length`), `textarea`, `number` (integer), `date` (`YYYY-MM-DD`), `select`, `checkbox` (bool), `asset_picker`, `user_picker`. 404 for an unknown/inactive code.

### POST `requests/` — raise
```json
{"request_type": "ict-network", "subject": "No internet in Room 12",
 "body": "The wall socket in Room 12 gives no network since Monday.", "priority": "high",
 "fields": {"affected_area": "Room 12, ICT block", "problem_type": "No internet"}}
```
`priority`: `low|normal|high|urgent` (default `normal`). Response **201** request detail (below). Attach files afterwards with `requests/<id>/attachments/`.
Errors 400: `request_type` ("This request type is not available."), `subject`, `body`, and schema fields as **`fields.<key>`**:
```json
{"error": {"code": "validation_error", "message": "Please check the highlighted fields.",
  "fields": {"fields.problem_type": ["Select a valid choice. No connection at all is not one of the available choices."]}}}
```
For `annual-leave` the leave rules also apply (see Leave) — prefer `POST leave/applications/`.

### GET `requests/?scope=mine|awaiting|assigned&status=&type=&q=`
`mine` (default) = raised by me; `awaiting` = waiting for **my** action (the website's "Awaiting my action" queue: approvals, assignment, fulfilment, handover); `assigned` = assigned to me. `status`: `all` (default), `active` (not closed) or an exact status (ignored for `awaiting`). `type` = type code.
```json
{"results": [{"id": 18, "reference": "REQ-2026-00018", "subject": "No internet in Room 12", "status": "awaiting_approval",
  "status_display": "Awaiting approval", "priority": "high",
  "request_type": {"id": 3, "code": "ict-network", "name": "Network / internet issue"},
  "requester": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
  "receiving_department": {"id": 1, "name": "ICT"}, "assigned_to": null, "current_step": "ICT Director approval",
  "created_at": "2026-10-22T01:05:48.711055+01:00", "updated_at": "2026-10-22T01:05:48.744591+01:00"}],
 "next": null, "previous": null, "count": 1}
```

### GET `requests/<id>/`
```json
{"id": 18, "reference": "REQ-2026-00018", "subject": "No internet in Room 12", "status": "awaiting_approval",
 "status_display": "Awaiting approval", "priority": "high",
 "request_type": {"id": 3, "code": "ict-network", "name": "Network / internet issue"},
 "requester": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
 "receiving_department": {"id": 1, "name": "ICT"}, "assigned_to": null, "current_step": "ICT Director approval",
 "created_at": "2026-10-22T01:05:48.711055+01:00", "updated_at": "2026-10-22T01:05:48.744591+01:00",
 "body": "The wall socket in Room 12 gives no network since Monday.", "completed_at": null, "closed_at": null,
 "revision_count": 0, "returned_by": null, "returned_at": null,
 "form_data": [{"key": "affected_area", "label": "Affected area", "value": "Room 12, ICT block"},
               {"key": "problem_type", "label": "What's the issue?", "value": "No internet"}],
 "step": {"name": "ICT Director approval", "kind": "approval", "instructions": "",
          "actor": {"id": 1, "name": "Muhammad Bugaje", "initials": "MB"}, "is_me": false},
 "chain": [{"name": "ICT Director approval", "kind": "approval", "state": "current"},
           {"name": "Assign to staff", "kind": "assignment", "state": "upcoming"},
           {"name": "Mark fulfilled", "kind": "fulfillment", "state": "upcoming"},
           {"name": "Handover confirmation", "kind": "closure", "state": "upcoming"}],
 "approvals": [{"step": "Line head approval", "actor": {"id": 6, "name": "Aisha Line-Head (Demo)", "initials": "AL"},
                "actor_role": "Director", "decision": "approve", "decision_display": "Approve", "comment": "OK",
                "decided_at": "2026-10-22T09:12:00+01:00"}],
 "comments": [], "attachments": [],
 "activity": [{"id": 147, "kind": "created", "actor": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
               "message": "Request raised: No internet in Room 12", "created_at": "2026-10-22T01:05:48.720642+01:00"}],
 "leave": null, "has_record_pdf": false,
 "actions": {"can_approve": false, "can_return": false, "can_return_to_requester": false, "can_reject": false,
             "can_assign": false, "can_mark_fulfilled": false, "can_confirm_handover": false, "can_cancel": true,
             "can_resubmit": false, "can_comment": true, "can_comment_internal": false, "can_attach": true}}
```
* `step` is null when closed or returned; `step.actor` is who must act now (null if nobody fits the step).
* `chain[].state`: `done` / `current` / `upcoming` (the runtime chain incl. Finance/Audit/HR/ES gates).
* `comments`: internal notes are hidden from the requester. `leave`: for annual-leave requests:
  `{"start_date": "2026-11-09", "end_date": "2026-11-13", "requested_days": "5", "has_over_balance": false,
  "years": [{"year": 2026, "requested_days": "5", "remaining": "12", "is_set": true, "over_balance_days": "0", "is_over_balance": false}]}`.
* 404 when not visible.

### Actions — POST `requests/<id>/<action>/` → 200 request detail

| Action | Body | When (`actions` flag) |
| --- | --- | --- |
| `approve/` | `{"comment"?}` | I am the actor of an `approval` step (`can_approve`) |
| `return/` | `{"comment"}` required | back to the previous step (`can_return`; approval/fulfilment/closure steps) |
| `return-to-requester/` | `{"comment"}` required | back to the requester for revision (`can_return_to_requester`) |
| `reject/` | `{"comment"}` required | ends the request (`can_reject`) |
| `assign/` | `{"assignee": <user id>, "comment"?}` | `assignment` step (`can_assign`); assignee from the receiving department |
| `fulfilled/` | `{"comment"?}` | the assigned staff member (`can_mark_fulfilled`) |
| `close/` | `{"comment"?}` | the requester confirms handover (`can_confirm_handover`) |
| `cancel/` | `{"comment"?}` | the requester, while not closed (`can_cancel`) |
| `resubmit/` | `{"subject", "body", "comment"?, "fields": {…}}` | the requester, status `returned` (`can_resubmit`) |

Refusals: 403 `permission_denied` ("You are not the acting user for this step.", "Only the requester can cancel this request."); missing comment → 400 `fields.comment` ("A comment is required when returning a request.").

### Comments and files
* POST `requests/<id>/comments/` `{"body", "is_internal"?}` → **201**. A requester's note is always public. Closed → 400.
* POST `requests/<id>/attachments/` multipart `file`, `description?` → **201**. Closed → 400.
* GET `requests/<id>/attachments/<attachment_id>/` → the file.
* GET `requests/<id>/record.pdf` → the record PDF of a closed request (`has_record_pdf`), 404 otherwise.

---

## Leave

On the website, annual leave is a workflow request of type `annual-leave`; the leave app keeps the dates, balances and attendance sync. There is one leave type (annual leave). Balances are in **working days** (weekends and the holidays you see are excluded); numbers are decimal strings.

### GET `leave/?year=`
```json
{"staff_linked": true, "can_apply": true,
 "balances": [{"leave_type": "annual-leave", "name": "Annual leave", "year": 2026, "entitlement": "12", "used": "0",
               "pending": "5", "remaining": "12", "is_set": true, "entitlement_source": "role"}],
 "current": null, "upcoming": null, "resumption_date": null, "days_until_upcoming": null}
```
* `staff_linked: false` → no staff record; applying is refused (`balances` empty).
* `is_set: false` → no entitlement configured (`entitlement` "0").
* `entitlement_source`: `override` (HR set it for you) or `role`.
* `remaining` = entitlement − used (approved); `pending` = days in applications still awaiting a decision.
* `current` / `upcoming` = approved leave now / next (application shape below); `resumption_date` = first working day after it.

### GET `leave/preview/?start_date=2026-11-09&end_date=2026-11-13`
```json
{"start_date": "2026-11-09", "end_date": "2026-11-13", "working_days": 5, "resumption_date": "2026-11-16",
 "remaining_before": "12", "remaining_after": "7", "is_over_balance": false}
```
Runs the website's checks: 400 with the message for "Enter both…", "The last day of leave must be on or after the first day.", "The selected period contains no working days.", overlapping applications, no staff record. `remaining_*` are null when no entitlement is set (going over is allowed; the approver sees it).

### GET `leave/applications/?status=all|pending|approved|closed`
```json
{"results": [{"id": 2, "request_id": 19, "reference": "REQ-2026-00019", "start_date": "2026-11-09",
  "end_date": "2026-11-13", "working_days": 5, "status": "awaiting_approval", "status_display": "Awaiting approval",
  "current_step": "Line head approval", "created_at": "2026-10-22T01:06:02.293535+01:00", "can_cancel": true}],
 "next": null, "previous": null, "count": 1}
```
`request_id` opens the full request (`requests/<request_id>/`: approval chain, comments, files).

### POST `leave/applications/`
```json
{"start_date": "2026-11-09", "end_date": "2026-11-13", "subject": "Annual leave application",
 "body": "Annual leave from 09 Nov 2026 to 13 Nov 2026.", "priority": "normal"}
```
Only the dates are required (`subject`/`body` default as shown). Raises the `annual-leave` request through the same form, validation and approver routing as the website. **201** application item. Errors 400, e.g.:
```json
{"error": {"code": "validation_error",
  "message": "These dates overlap your leave application REQ-2026-00019 (09 Nov 2026 to 13 Nov 2026).",
  "fields": {"non_field_errors": ["These dates overlap your leave application REQ-2026-00019 (09 Nov 2026 to 13 Nov 2026)."]}}}
```
Also: "Your account is not linked to an active staff record. Contact HR before applying.", "Annual leave applications are closed." (type switched off), `fields.start_date` / `fields.end_date` for bad dates.

### POST `leave/applications/<id>/cancel/` `{"reason"?}`
Cancels my own application (any status until closed — approved leave is withdrawn and attendance restored, as on the website). 200 application item with `"status": "cancelled"`. Someone else's id → 404; already closed → 400 "This request is already closed.".

### GET `leave/approvals/`
Leave applications waiting for **my** decision:
```json
{"results": [{"request_id": 19, "reference": "REQ-2026-00019",
  "requester": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"}, "department": {"id": 1, "name": "ICT"},
  "start_date": "2026-11-09", "end_date": "2026-11-13", "requested_days": "5", "has_over_balance": false,
  "step": "Line head approval", "waiting_since": "2026-10-22T01:06:02.29+01:00"}],
 "next": null, "previous": null, "count": 1}
```
Decide with the request actions (as the website does on the request page): `requests/<request_id>/approve/`, `reject/` (comment required), `return-to-requester/`, `return/`. `requests/<request_id>/` shows the balance check in `leave`.

### GET `leave/holidays/?year=2026`
Holidays I see (public, all-staff, my department, my role):
```json
{"results": [{"id": 1, "name": "Test Public Holiday", "kind": "public", "date_start": "2026-10-06",
  "date_end": "2026-10-07", "is_recurring": false, "description": "Test"}], "next": null, "previous": null, "count": 1}
```
A recurring holiday keeps its original dates; repeat it every year on the same day/month.
