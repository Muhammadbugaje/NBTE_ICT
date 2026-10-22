# Global search (`search/`) — staff door

The website's search bar (`core.search`) for the app: one call, results grouped by kind.
Every group is scoped exactly like the website page it comes from (the provider calls the same
visibility helper as that list page), so a user never finds something they could not open on
the website. Bearer token required. Common errors (401 `not_authenticated`/`token_expired`,
403 `wrong_door`/`password_change_required`/`account_inactive`, 429 `throttled`) apply.
Portal accounts: the route does not exist on the portal host (404); `home/` says
`features.search: false`.

## GET `search/?q=<text>&limit=<1..10>`

- `q`: at least 2 characters (trimmed), at most 200.
- `limit`: results **per group**, 1–10, default 5.

Response 200:
```json
{"query": "router",
 "total": 4,
 "groups": [
   {"kind": "people", "label": "People", "count": 1,
    "results": [{"id": "57", "title": "Musa Bello", "subtitle": "NBTE/0412 · Network Engineer · ICT",
                 "badge": null,
                 "target": {"type": "staff", "id": 57, "route": "/staff/57", "url": "https://<host>/staff/57/"},
                 "web_url": "/staff/57/"}]},
   {"kind": "tasks", "label": "Tasks", "count": 2,
    "results": [{"id": "418", "title": "Fix the router in Block A", "subtitle": "TSK-2026-0418 · In progress",
                 "badge": "High",
                 "target": {"type": "task", "id": 418, "route": "/tasks/418", "url": "https://<host>/tasks/418/"},
                 "web_url": "/tasks/418/"}]},
   {"kind": "documents", "label": "Documents", "count": 1,
    "results": [{"id": "12", "title": "Router configuration guide", "subtitle": "Manuals · v3",
                 "badge": null,
                 "target": {"type": "web", "id": null, "route": null, "url": "https://<host>/portal/documents/12/"},
                 "web_url": "/portal/documents/12/"}]}
 ]}
```
- Groups appear in this order and **empty groups are left out** (`groups: []` when nothing matched):

| `kind` | `label` | Source (website scoping) | `target.type` |
| --- | --- | --- | --- |
| `people` | People | staff directory (`staff.permissions.staff_visible_to`) | `staff` → `staff/<id>/` |
| `memos` | Memos | `messaging.memos.visible_memos_for`; badge "Awaiting you" / "Urgent" / "High" | `memo` |
| `tasks` | Tasks | `tasks.views._visible_tasks_qs` | `task` |
| `tickets` | Tickets | `tickets.views._visible_tickets_qs` | `ticket` |
| `announcements` | Announcements | `Announcement.objects.visible_to` (the /announcements/ page); badge "Pinned" | `announcement` |
| `requests` | Requests | `workflow.access.visible_requests_queryset` | `approval` |
| `institutions` | Institutions | QA Institutions list (`can_view_institutions` + `Institution.objects.for_user`) | `institution` → `qa/institutions/<code>/` |
| `chats` | Chats | threads I take part in (message text too) | `thread` |
| `assets` | Assets | `assets.access.visible_assets_queryset` | `asset` → `assets/<tag>/` |
| `documents` | Documents | published portal documents | `web` |
| `notices` | Portal notices | the website bar's portal notices | `web` |
| `attendance` | Attendance | attendance records in my attendance scope | `web` |

- `id` is a string (asset/institution ids are numeric too, but the app route uses `tag`/`code`).
- `subtitle`: the website's grey line (reference · status · …). `badge`: short label or `null`.
- `target`: same object as a notification's `target` (see `notifications.md`), so the same
  router opens it; `type: "web"` → open `target.url` in the browser.
- `web_url`: the website path of the result (relative to the website host), or `null`.
- `total` = sum of `count`.

Errors: 400 `validation_error` —
`{"q": ["Ensure this field has at least 2 characters."]}` (also when `q` is missing:
`{"q": ["This field is required."]}`), `{"limit": ["Ensure this value is less than or equal to 10."]}`.
