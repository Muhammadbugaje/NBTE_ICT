# Phase 7 migrations

| Migration | What it does |
|---|---|
| `complaints/0001_initial` | Creates the nine complaint tables (layers, routing rules, complaints, stages, messages, attachments, escalation rules, escalations, knowledge articles) and the §12 complaint permissions. |

No existing table is altered. Data (flow, layers, rules, settings, templates, permissions) comes from `seed_startup`.

## Your database
- Backed up to `db.sqlite3.before-phase7-20261020`, then `migrate`, `seed_startup --with-demo`, `seed_phase7_demo --skip-startup` and `set_demo_passwords` were run.
- Roll back: stop the server and copy the backup over `db.sqlite3`.
