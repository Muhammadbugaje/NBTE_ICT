# Insights (`insights/`) — staff door only

The signed-in staff member's own numbers, exactly as the website's "My workspace" insight
charts (`dashboards/insights.py`). Every staff user may call it (it is personal data, not the
`capabilities.insights` analytics dashboards). Portal door: 404. Common errors: see `me.md`.

## GET `insights/?period=week|month|quarter`

`period` (default `month`):
- `week` — last 7 days, one bucket per day (label `Mon`…);
- `month` — last 4 weeks incl. this one, one bucket per week (label `6 Oct`, week starting Monday);
- `quarter` — last 13 weeks, one bucket per week.

Response 200:
```json
{
  "period": "month",
  "period_label": "Last 4 weeks",
  "arrivals": {
    "period": "month", "start": "2026-09-29", "end": "2026-10-21",
    "days_recorded": 14, "late_days": 2,
    "average_minutes": 497, "average": "08:17", "earliest": "07:41", "latest": "09:20",
    "buckets": [
      {"label": "29 Sep", "start": "2026-09-29", "end": "2026-10-05", "days": 5,
       "average_minutes": 490, "average": "08:10"}
    ]
  },
  "tasks": {
    "period": "month", "start": "2026-09-29", "end": "2026-10-21",
    "done": 6, "in_progress": 3, "waiting": 1, "overdue": 2, "total": 12,
    "states": [
      {"key": "done", "label": "Done", "count": 6},
      {"key": "in_progress", "label": "In progress", "count": 3},
      {"key": "waiting", "label": "Waiting", "count": 1},
      {"key": "overdue", "label": "Overdue", "count": 2}
    ]
  },
  "tickets": {
    "period": "month", "start": "2026-09-29", "end": "2026-10-21",
    "raised_total": 4, "resolved_total": 3,
    "buckets": [{"label": "29 Sep", "start": "2026-09-29", "end": "2026-10-05", "raised": 1, "resolved": 0}]
  }
}
```
Notes:
- Arrival times are check-in times (local time) on days marked present/late;
  `average_minutes` = minutes after midnight; `null`/`null` when nothing was recorded.
- `tasks.done` counts tasks completed inside the period; `in_progress`, `waiting` and `overdue`
  are the user's open tasks right now (overdue wins).
- `tickets`: tickets the user raised or handles, raised vs resolved per bucket.

Errors: 400 `{"period": ["\"year\" is not a valid choice."]}`.
