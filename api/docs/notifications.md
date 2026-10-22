# Notifications (`notifications/…`) — both doors

The signed-in user's own in-app notifications (the website's bell and "All notifications"
page; the QA portal's notifications page). A user only ever sees and changes their own rows.
Common errors: see `me.md` (401 `not_authenticated` / `token_expired` / `token_revoked`, 403
`wrong_door` / `password_change_required` / `account_inactive`, 429 `throttled`).

## The notification object

```json
{
  "id": 9021,
  "title": "Task assigned to you",
  "body": "TSK-2026-0418 — Update the server inventory",
  "level": "info",
  "kind": "tasks_tickets",
  "category": "task",
  "created_at": "2026-10-21T09:12:00+01:00",
  "read": false,
  "read_at": null,
  "actor": {"id": 17, "name": "Musa Bello"},
  "target": {"type": "task", "id": 418, "route": "/tasks/418", "url": "https://<host>/tasks/418/"}
}
```
- `level`: `info` | `success` | `warning` | `error` (icon/colour).
- `kind`: the notification-preference kind — `chats` | `tasks_tickets` | `approvals_memos` |
  `payments` | `qa_cases` | `announcements` | `general`.
- `category`: the raw website category (for display only).
- `actor`: who caused it, or `null`.
- `target`: where tapping goes, or `null` (no link). `url` is always the absolute website link
  (use it in a browser when `type` is `web`).

| `target.type` | `route` | Website link it comes from |
| --- | --- | --- |
| `task` | `/tasks/<id>` | `/tasks/<id>/` |
| `approval` | `/approvals/<id>` | `/workflow/<id>/` |
| `thread` | `/chats/<id>` | `/messages/<id>/` |
| `memo` | `/memos/<id>` | `/messages/memos/<id>/`, `/messaging/memos/<id>/` |
| `ticket` | `/tickets/<id>` | `/tickets/<id>/` |
| `announcement` | `/announcements/<id>` | `/announcements/<id>/` (or `metadata.announcement`) |
| `confirmation` | `/confirmations/<id>` | `/qa/pending/<id>/` (QA two-step confirmation) |
| `case` | `/cases/<id>` | `/qa/cases/<id>/` |
| `qa_request` | `/qa-requests/<id>` | `/qa/requests/<id>/`, portal `/qa-requests/<id>/` |
| `ssq` | `/ssq/<id>` | `/qa/requests/ssq/<id>/`, portal `/qa-requests/<r>/ssq/<id>/` |
| `query` | `/queries/<id>` | `/qa/requests/queries/<id>/`, portal `/queries/<id>/` |
| `complaint` | `/complaints/<id>` | `/qa/complaints/<id>/`, portal `/complaints/<id>/` |
| `finance_item` | `/finance/queue/<id>` | `/qa/finance/queue/<id>/` |
| `bill` | `/bills/<id>` | portal `/payments/<id>/` |
| `application` | `/application` (`id` null) | portal `/application/` |
| `web` | `null` (`id` null) | anything else — open `url` in the browser |

## GET `notifications/`

Query: `filter` = `all` (default) | `unread` | `read`; `kind` = one of the kinds above;
`q` = text search in title/body; `page`, `page_size` (max 100). Newest first.

Response 200:
```json
{"results": [ { …notification… } ], "next": "https://…/api/v1/notifications/?page=2", "previous": null, "count": 57}
```
Errors: 400 `validation_error` — `{"filter": ["\"x\" is not a valid choice."]}`, same for `kind`.

## GET `notifications/unread-count/`

`{"unread": 4}` — use for the bell badge (also in `me/` as `unread.alerts`).

## POST `notifications/<id>/read/`  ·  POST `notifications/<id>/unread/`

No body. Response 200:
```json
{"id": 9021, "read": true, "unread": 3}
```
`unread` is the new total. 404 `not_found` for an unknown id or someone else's notification.

## POST `notifications/read-all/`

No body. Response 200: `{"marked": 4, "unread": 0}`.

## POST `notifications/bulk/`

Same action as the website's "select and act" bar.
```json
{"ids": [9021, 9019, 9001], "action": "mark_read"}
```
`action`: `mark_read` | `mark_unread` | `delete`. `ids`: 1–500 integers.

Response 200:
```json
{"action": "mark_read", "done": [9021, 9019],
 "skipped": [{"id": 9001, "reason": "Some items are not yours and were left alone."}],
 "message": "2 notifications marked as read.", "unread": 1}
```
Ids that are not the user's are skipped (never an error). Errors: 400 `validation_error` —
`{"ids": ["This list may not be empty."]}`, `{"action": ["\"x\" is not a valid choice."]}`.
