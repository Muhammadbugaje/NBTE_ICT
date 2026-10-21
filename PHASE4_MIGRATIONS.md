# NBTE QA System — Phase 4 migrations

Phase 4 adds **two migrations**. No migration of any other app was added or changed, and no existing column was altered or dropped.

| Migration | App |
|---|---|
| `accreditation/migrations/0003_sop03_case.py` | SOP-03 case fields and the cost / personnel / visit / fund-release records |
| `qa_core/migrations/0003_task_bridge.py` | Task-bridge bookkeeping (SLA reminders) and the drift report |

```bash
python manage.py migrate                 # applies both
python manage.py seed_startup            # adds the Phase 4 data (idempotent)
python manage.py makemigrations --check --dry-run   # → No changes detected
```

## `accreditation.0003_sop03_case`

Depends on `accreditation.0002_qa_requests_ssq`, `staff.0011…` and the user model.

**New fields on `QACase`**

| Field | Purpose |
|---|---|
| `desk_officer` (FK user) | Officer of the owning department who builds the cost sheet, personnel list and visit details |
| `supervising_audit_officer`, `supervising_finance_officer` (FK user) | Chosen at stage 1 by the Directors of Audit and Finance |
| `tracking_ict_officers` (M2M user) | One, or two when `programme_count ≥ qa.assignment.second_ict_officer_threshold` (R-04) |
| `requires_second_ict_officer` | Set when the case starts |
| `stage_actors` (JSON) | Who acts for a role on this case once chosen (the Director who opened it, the ES who approved, a reassigned holder); later stages for that role go to the same person |
| `needs_reassignment` | An assignee returned the stage task; the department head must reassign it |
| `started_at`, `completed_at` | SOP-03 start and end |

New permissions on `QACase`: `act_on_qa_stage`, `edit_cost_sheet`, `audit_cost_sheet`, `approve_fund_release`, `release_funds`, `manage_rate_table`.

**New models** (all except `RateTable` are institution-scoped through `case__institution`)

| Model | Purpose |
|---|---|
| `RateTable` | Allowance rates (B12): code, role, allowance type, amount, per day / per trip, optional department override; unique `(code, department)` |
| `CostSheet` | Versioned cost sheet: `draft / submitted / audited / es_approved / returned`, total, prepared / audited / approved by |
| `CostLine` | One line: staff (optional), role on the exercise, rate (`PROTECT`), days, unit amount and amount **as priced**, Audit edit flag and note |
| `PersonnelList` / `PersonnelEntry` | The visit team; duty (team lead / member / finance / audit / ICT / support); unique `(list, staff)` |
| `VisitDetail` | Planned dates, team lead, logistics, ES approval |
| `FundRelease` | Requested on the ES's visit approval; the Pay Officer records the voucher reference and releases |

## `qa_core.0003_task_bridge`

Depends on `qa_core.0002_outbound_email`, `tasks.0002…`, `contenttypes`.

| Change | Purpose |
|---|---|
| `QATaskLink.due_at` | Stage deadline (from the stage's SLA hours) |
| `QATaskLink.reminders_sent` (JSON) | The percentages already reminded (`qa.stage.reminder_at_percent`) |
| `QATaskLink.breach_notified_at` | When the overdue notice went out |
| Index `(content_type, object_id, closed_at)` | Fast "open task for this case" look-ups |
| New model `TaskLinkDriftReport` | One row per reconciliation run: checked / opened / closed counts and details, dry-run flag |

## Checks done

| Check | Result |
|---|---|
| `makemigrations --check --dry-run` | No changes detected |
| Reverse then forward on a copy of the seeded database (`migrate accreditation 0002`, `migrate qa_core 0002`, `migrate`) | OK |
| Your `db.sqlite3` | Backed up to `db.sqlite3.before-phase4-20261019-042842`, then migrated. It had no QA migrations at all, so Phases 1–4 were applied together (16 migrations), then `seed_phase4_demo` ran on it. To go back: stop the server and copy the backup over `db.sqlite3`. |
