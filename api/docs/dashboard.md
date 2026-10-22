# Executive dashboard (`dashboard/executive/`) — staff door

The website's leadership dashboards (`/dashboards/director/`, `/deputy/`, `/es/`,
`/chairman/`) in one JSON shape for the app. Bearer token required; common errors as in
`me.md`.

**Who may open it** — the website's rule for those boards (`dashboards.views._guard`):
a superuser, or role `executive-secretary`, `director` or `deputy-director`, or the
`accounts.view_chairman_dashboard` permission. Everyone else: 403 `permission_denied`.
`home/` → `features.executive_dashboard` tells the app whether to show the entry.
Which board: the role first (ES → `es`, director → `director`, deputy → `deputy`), then the
chairman permission (superusers get `chairman`).

**Scope** — as on the website: the director/deputy/ES boards count only requests the viewer may
see (`workflow.access`; holders of "view all requests", e.g. the ES, see everything); the
chairman board is NBTE-wide; attendance follows `attendance.access.attendance_scope`; the QA
overview (revenue, institutions, SLA, cases, complaints) is NBTE-wide on every board.

## GET `dashboard/executive/`

```json
{"title": "Director dashboard", "board": "director",
 "scope": "department", "scope_label": "DPAP (requests you can see)",
 "generated_at": "2026-10-22T09:30:00+01:00",
 "kpis": [
   {"key": "approvals_pending", "label": "Awaiting approval", "value": 4, "display": "4", "unit": null,
    "delta": null, "delta_label": null, "tone": "warn", "hint": "Requests waiting at an approval step.",
    "target": {"type": "web", "id": null, "route": null, "url": "https://<host>/workflow/"}},
   {"key": "qa_revenue_year", "label": "QA revenue this year", "value": 12500000.0, "display": "₦12.5m",
    "unit": "NGN", "delta": 12.5, "delta_label": "this month vs last month", "tone": "good",
    "hint": "Confirmed payments.", "target": null}
 ],
 "series": [
   {"key": "requests_opened", "label": "Requests opened (30 days)", "kind": "line", "unit": null,
    "points": [{"label": "Sep 23", "value": 0}, {"label": "Sep 24", "value": 2}]},
   {"key": "qa_revenue_monthly", "label": "QA revenue per month", "kind": "bar", "unit": "NGN",
    "points": [{"label": "Nov", "value": 0.0}, {"label": "Oct", "value": 4000000.0}]}
 ],
 "lists": [
   {"key": "my_queue", "label": "Waiting for you",
    "items": [{"title": "Laptop for new officer", "subtitle": "REQ-2026-0091 · Director approval · Musa Bello",
               "value": "3 days", "tone": "neutral",
               "target": {"type": "approval", "id": 91, "route": "/approvals/91", "url": "https://<host>/workflow/91/"}}]}
 ]}
```

- `board`: `es` | `director` | `deputy` | `chairman` (extra field; which website board this mirrors).
- `scope`: `organisation` | `department`; `scope_label` is ready to show.
- `kpis[].value` is a number (use it for logic), `display` is ready to show; `unit` `NGN` | `%` | `null`;
  `tone` `good` | `warn` | `bad` | `neutral`; `delta`/`delta_label` optional; `target` (notification
  target format) or `null`.
- KPI keys (present when they apply): `approvals_pending`, `approvals_aged` (> 7 days),
  `requests_in_flight`, `requests_completed_30d`, `requests_rejected_30d`, `es_signoff` and
  `filing_queue` (ES), `staff_total` (director/deputy/chairman), `present_today` (`display`
  "18 of 25", `delta` = % of staff) and `on_leave_today` (when the viewer has an attendance
  scope), `qa_revenue_year`, `institutions_approved`, `qa_cases_open`, `complaints_open`
  (`delta` = past deadline), `qa_sla` (% of QA tasks closed on time, `display` "—" when none
  closed), `unread_notifications`.
- Series keys: `requests_opened`, `requests_completed` (daily, 30 days), `approvals_weekly`
  (12 weeks), `qa_revenue_monthly` (12 months, NGN), `attendance_daily` (% came to work, completed
  working days), `qa_sla_monthly` (%, months without closed tasks are left out),
  `requests_by_status`. `kind` `line` | `bar`.
- List keys: `my_queue` (director/deputy: waiting at my approval step), `es_signoff` and
  `filing` (ES), `aged_approvals` (over 7 / 3 / 5 / 7 days for director / deputy / ES / chairman),
  `attendance_by_department` (30 days, `value` "84%"), `qa_cases_by_stage`,
  `complaints_by_layer`, `leadership` (ES and chairman). Lists can be empty; items have
  `title`, `subtitle`, `value` (string or null), `tone` (or null) and `target` (or null).

Cost: about 35–40 queries, well under a second on SQLite with the demo data. Cache it on the
phone and refresh on pull-to-refresh.
