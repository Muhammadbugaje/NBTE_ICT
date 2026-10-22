# Announcements (`announcements/…`) — staff door only

Official NBTE announcements (the website's "Announcements" channel). Only NBTE staff are ever
in an audience; on the portal door these routes do not exist (404). Common errors: see `me.md`.

## Summary object (list items)

```json
{
  "id": 7,
  "title": "Town hall on Friday",
  "body_preview": "All staff are invited to the town hall in the main auditorium…",
  "kind": "action_needed",
  "kind_label": "Action needed",
  "requires_ack": true,
  "pinned": false,
  "status": "published",
  "published_at": "2026-10-20T15:00:00+01:00",
  "expires_at": null,
  "author": {"id": 12, "name": "Aisha Bello"},
  "audience_label": "All staff",
  "read": false,
  "acknowledged": false,
  "has_poll": true,
  "attachments_count": 1,
  "in_audience": true,
  "can_edit": false,
  "can_report": false,
  "route": "/announcements/7"
}
```
- `kind`: `notice` | `action_needed` | `urgent`. `status`: `draft` | `published`.
- `body_preview`: first 240 characters (+ "…").
- `in_audience` false = the user manages it (author / editor / report viewer) but it is not
  addressed to them (e.g. a draft).

## GET `announcements/`

Query: `filter` = `all` (default) | `unread` | `needs_ack` | `drafts` | `sent`; `q` (search);
`page`, `page_size`. `drafts`/`sent` only for users who may post (others get `all`).
`all`/`unread`/`needs_ack`: live (published, not expired) announcements addressed to the user,
**pinned first**, then newest.

Response 200:
```json
{
  "results": [ { …summary… } ],
  "next": null, "previous": null, "count": 3,
  "filter": "all",
  "counts": {"all": 3, "unread": 2, "needs_ack": 1, "drafts": 0, "sent": 0},
  "can_post": false
}
```
Errors: 400 `{"filter": ["\"x\" is not a valid choice."]}`.

## GET `announcements/<id>/`

Marks it read (when addressed to the user). Expired announcements stay readable here.
Response 200 = summary fields plus:
```json
{
  "body": "Full text.\nLine breaks are kept.",
  "acknowledged_at": null,
  "audience": "all_staff",
  "department": null,
  "roles": [],
  "attachments": [
    {"id": 3, "name": "agenda.pdf", "size": 48211,
     "download_url": "https://<host>/api/v1/announcements/7/attachments/3/"}
  ],
  "poll": {
    "id": 2, "question": "Which day suits you?", "allow_multiple": false,
    "closes_at": null, "is_open": true, "has_voted": false, "can_vote": true,
    "show_results": false, "voters": null,
    "options": [
      {"id": 5, "text": "Thursday", "chosen": false, "votes": null, "percent": null},
      {"id": 6, "text": "Friday", "chosen": false, "votes": null, "percent": null}
    ]
  },
  "can_acknowledge": true
}
```
- `audience`: `all_staff` | `department` | `roles` (with `department` / `roles` filled).
- Poll results (`votes`, `percent`, `voters`) are `null` until the user has voted or the poll
  closed (people outside the audience who manage it see them straight away).
- Errors: 404 `not_found` (unknown, a draft of someone else, not addressed to the user).

## POST `announcements/<id>/acknowledge/`

No body. Response 200:
```json
{"id": 7, "acknowledged": true, "acknowledged_at": "2026-10-21T09:40:00+01:00", "created": true}
```
`created` false when it was already acknowledged (idempotent).
Errors: 400 `{"non_field_errors": ["This announcement does not ask for acknowledgement."]}`;
404 when not addressed to the user or expired.

## POST `announcements/<id>/vote/`

```json
{"option_ids": [6]}
```
The new choice replaces the earlier one (for multi-choice polls send the full selection).
Response 200 = the `poll` object above, now with results.
Errors: 400 `{"option_ids": ["Choose only one option."]}` / `"Choose a valid option."` /
`"This poll has closed."` / `["This list may not be empty."]`; 404 no poll, not addressed to
the user, or expired.

## GET `announcements/<id>/attachments/<aid>/`

Downloads the file (`Content-Disposition: attachment; filename="agenda.pdf"`). Same rule as the
website: the announcement must be visible to the user (expired included) or editable by them.
404 otherwise (also when `<aid>` belongs to another announcement).

## Posting (users with `capabilities.post_announcements`)

### POST `announcements/` — create (draft unless `publish: true`)

JSON, or `multipart/form-data` to include a file (`roles`, `poll_options`,
`remove_attachment_ids` then repeat the field).
```json
{
  "title": "Town hall on Friday",
  "body": "All staff are invited…",
  "kind": "action_needed",
  "requires_ack": true,
  "audience": "department",
  "department": 3,
  "roles": [],
  "pinned": false,
  "expires_at": "2026-11-01T17:00:00+01:00",
  "poll_question": "Which day suits you?",
  "poll_options": ["Thursday", "Friday"],
  "poll_allow_multiple": false,
  "poll_closes_at": null,
  "publish": false
}
```
Only `title` and `body` are required. Defaults: `kind` notice, `audience` all_staff, booleans
false. `attachment` (multipart file): PDF, Word, Excel or image, up to 10 MB.
Response 201 = the detail object. With `publish: true` the audience is notified at once.

Validation (the website's form), 400 with `fields`:
- `title` / `body` required; `department` required for `audience=department`; `roles` (ids)
  required for `audience=roles`;
- `expires_at`: "Choose a time in the future.";
- poll: `poll_options` — "A poll needs at least two options." / "Each option must be
  different."; `poll_question` — "Add the poll question, or clear the options."; max 6 options;
- `attachment`: type or size not allowed.
Errors: 403 `permission_denied` without the post permission.

### PATCH `announcements/<id>/` — edit

Same fields as create, only those that change; also `remove_attachment_ids: [3]` and
`publish: true` (drafts only). Once anybody voted, `poll_options` can no longer change
(400 `{"poll_options": ["People have voted, so the options can't change."]}`); a blank
`poll_question` removes the poll only while nobody has voted. Response 200 = detail.
Errors: 403 not the author (or an editor); 404 not visible.

### POST `announcements/<id>/publish/`

No body. Publishes a draft and notifies its audience. Response 200:
```json
{"notified": 148, "announcement": { …detail… }}
```
`notified` is 0 when it was already published. Errors: 403 / 404 as for PATCH.

## GET `announcements/<id>/report/` — author or report permission

Response 200:
```json
{
  "id": 7, "title": "Town hall on Friday", "requires_ack": true,
  "audience_size": 150, "read": 120, "read_percent": 80,
  "acknowledged": 98, "acknowledged_percent": 65,
  "pending_count": 52,
  "pending": [{"id": 44, "name": "Ibrahim Musa", "department": "ICT", "role": "Officer"}],
  "poll": {"question": "Which day suits you?", "voters": 90, "total_votes": 90,
           "options": [{"id": 5, "text": "Thursday", "votes": 40, "percent": 44},
                       {"id": 6, "text": "Friday", "votes": 50, "percent": 56}]}
}
```
`pending` = not acknowledged yet (when acknowledgement is asked), otherwise not read yet.
Errors: 403 in the audience but no report rights; 404 not visible.

## Related fields elsewhere

- `GET me/` → `unread.announcements` (live, unread), `capabilities.post_announcements`.
- `GET home/` (staff) → `announcement`: the newest live `action_needed`/`urgent` announcement
  still waiting for the user (not acknowledged when acknowledgement is asked, otherwise
  unread), or `null`:
  `{"id": 7, "title": "…", "kind": "urgent", "requires_ack": true, "acknowledged": false}`.
- Notifications about announcements have `kind: "announcements"` and
  `target: {"type": "announcement", "id": 7, "route": "/announcements/7"}`.
