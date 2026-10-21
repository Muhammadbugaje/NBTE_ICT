# NBTE QA System — Phase 4 changelog

**Phase 4 — SOP-03 internal workflow and Task Bounce** (PHASES.md Phase 4; Master Plan §5.4, §5.7, §8). Built 19 Oct 2026.

What you can do now: when every SSQ of a QA request is in, the QA case starts **SOP-03** on its own. Each of its 17 stages arrives as a task in the right person's Tasks inbox with an **Open in QA** button. The person does the stage's work on the case screen (cost sheet, personnel, visit, fund release) and approves or returns it there. Approving or returning closes their task and opens the next one ("Task Bounce").

Out of scope, as agreed: inspections and resource persons (Phase 5); stage 17's programme-record writes and letters, decisions and complaints (Phase 6). Stages 12–16 are plain approve / return stages with tasks; stage 17 only closes the case.

---

## 1. The SOP-03 flow (on the Phase 1 FlowDefinition engine — no hard-coded state machine)

Flow `qa_case` "SOP-03 QA case (17 stages)" — editable in **Settings → Workflows** like every other flow.

| # | Stage | Who acts (resolved at runtime) |
|---|---|---|
| 0 | Case opened — choose the desk officer | Director of the owning department |
| 1 | Parallel gate → **Audit**, **Finance**, **ICT** branches → join | Directors of Audit, Finance, ICT (ICT chooses **two** officers when `programme_count ≥ qa.assignment.second_ict_officer_threshold`, R-04) |
| 2 | Generate the cost sheet | Desk officer |
| 3 | Add the personnel list | Desk officer |
| 4 | Confirm the personnel list | Director (owning department) |
| 5 | Audit the personnel cost | Supervising Audit officer |
| 6 | Confirm the audited cost | Director of Audit |
| 7 | ES approval of the cost | Executive Secretary |
| 8 | Add the visit details | Desk officer |
| 9 | Confirm the visit details | Director |
| 10 | ES approval of the visit (requests the fund release) | ES |
| 11 | Release the funds | Pay Officer |
| 12–16 | Post-visit report · recommendation · CQAC recommendation · CQAC Chairman · ES final approval | Desk officer · Director · CQAC member · CQAC Chairman · ES |
| 17 | Programme record update (system) | closes the case (record writes are Phase 6) |

* Every action stage has **approve** and **return**. A return **needs a remark**, goes back **one node to the exact person who submitted it**, and the remark is **pinned** on their new task (priority high).
* Returning a stage-1 branch sends the case back to the Director (the other branches' tasks close). The Director's re-approval goes straight back to stage 2 when the supervisors are already chosen.
* Transition conditions are formulas (`supervisors_ready`, `ict_officers >= 2 or (… programme_count < cfg('qa.assignment.second_ict_officer_threshold'))`), and every action needs `accreditation.act_on_qa_stage`.
* **Pinned actors** (`QACase.stage_actors`): once someone acts for a role on a case (the Director who opened it, the ES …) later stages for that role go to the same person; a reassignment re-pins. The demo seed pins its demo people, so a database that already has a real ES or Directors keeps their inboxes clean.

## 2. Task Bounce — the task bridge (`qa_core/task_bridge.py`)

* **Engine hook:** `qa_core.flow.on_stages_changed(listener)` — called after `start`, every move and `force_stage` with the stages entered and left. This is the only engine change; it is additive.
* **On stage entry:** a task is created by the system user `qa-system@nbte.local` (category *QA — SOP-03 case stage*, due from the stage SLA), assigned directly to the resolved person (so the ES can hold one), linked with `QATaskLink`, opened. No one found → the case is flagged *needs reassignment* and department heads are notified.
* **On stage exit:** the task is closed (reason *approved / returned / superseded / reconciled*).
* **Tasks can't be finished in Tasks:** *Mark done*, *Cancel*, *Review* are refused for linked tasks (service-level veto through `task_state_changed` plus `tasks.hooks.external_close()`), and the task screen hides them and shows **Open in QA**. Task cards in every Tasks list show an **Open in QA** button.
* **Reassignment** (`task_state_changed` with action `reassign`, and the QA Tasks tab): re-validated, rolled back if it breaks a rule —
  * R-01 workload (`qa.resource_person.max_open_ssq_per_officer` open officer-level cases),
  * R-20 grade level (`qa.staff.ssq_grade_min`–`max`),
  * R-02 reviewer ≠ member of the visit team (`qa.workload.reviewer_cannot_verify`),
  * who may reassign: the ES; holders of `qa_core.reassign_qa_task` in the task's department (or `confirm_any_department`); never the assignee (they use *Return to assigner*, which flags the case) and never the ICT monitor.
* **SLA reminders:** `python manage.py qa_stage_reminders` (hourly) reminds holders once at each percentage in `qa.stage.reminder_at_percent` (default 50, 90) and sends one breach notice to the holder and the department head.
* **Nightly reconciliation:** `python manage.py qa_reconcile_tasks [--dry-run]` closes orphaned tasks, opens missing ones and writes a `TaskLinkDriftReport` (screen: *QA cases → Task-link drift*).

## 3. Screens (internal system, §20 HTMX contract: partial swaps, pushed URLs, toasts, focus)

Sidebar group **QA cases (SOP-03)**: Case Board · My stage work (with a badge) · Task-link drift.

| Screen | URL | Notes |
|---|---|---|
| Case Board | `/qa/cases/` | Columns by stage group; card shows stage number, holder, due / overdue, *needs reassignment*; filters: search, department, *waiting on me*, flags |
| My stage work | `/qa/cases/my-stages/?kind=cost\|visit\|funds` | The ES's cost and visit queues, the Pay Officer's release queue, anyone's open stages |
| Case detail | `/qa/cases/<id>/` | 17-stage stepper; a stage-action panel per open stage (holders, due, pinned remark, prerequisites, approve / return with remark); tabs below |
| · Overview | `?tab=overview` | Case facts, people on the case, open tasks; desk-officer and supervisor pickers live in the stage panels |
| · Cost sheet editor | `?tab=cost` | Department builds it at stage 2; **Audit edits** at stage 5 (days, rate, add / remove — every change needs a note shown to the department); read-only otherwise; ES approves at stage 7; *rates not confirmed* banner until `qa.cost.rates_confirmed` |
| · Personnel | `?tab=personnel` | Add / remove at stage 3; the team lead must hold a role in `qa.visit.team_lead_roles` |
| · Visit | `?tab=visit` | Dates (not in the past), team lead, logistics at stage 8 |
| · Fund release | `?tab=funds` | Created by the ES's visit approval for the approved total; the Pay Officer records the voucher reference (`accreditation.release_funds`) |
| · Timeline | `?tab=timeline` | Flow events (incl. returns with remarks and reassignments) and case timeline rows |
| · Tasks | `?tab=tasks` | Every stage task, holder, opened / closed and why; reassign (rules above) |
| Task-link drift | `/qa/cases/drift/` | Reports; *Check (dry run)* / *Run and repair* |
| Settings → Rate table | `/qa/settings/rates/` | Edit amounts, retire / restore, add department overrides (`accreditation.manage_rate_table`); a changed rate never re-prices an existing line |
| Tasks: task detail / cards | `/tasks/…` | **Open in QA** panel and buttons; done / cancel / delete / reopen / review / assign hidden for linked tasks |

* **ICT monitor** (`ict-qa-monitor`): sees every case, tab, task and the drift report; every POST answers **403** (R-13).
* **Scope:** cases are visible to the owning department, to anyone named on the case or holding one of its tasks, and to the ES, Pay Officer, CQAC, ICT monitor and the Audit / Finance / ICT departments; anything else answers 404. The view module is under the layer-2 scoping audit.
* QA request detail (SOP-02) now links to its case.

## 4. Send Back To Audit is live

*TVET Support → Send Back To Audit* (two-officer action, §6): choose a case at stage 7 or later and give a reason; on confirmation the case returns to stage 5, the cost sheet goes back to *submitted*, any pending fund release is returned, and the supervising Audit officer gets a new task. The case screen links to it from stage 7 on.

## 5. Settings added (Settings → Rules & thresholds)

| Key | Default | |
|---|---|---|
| `qa.assignment.second_ict_officer_threshold` | 5 | Programmes at which a case needs two ICT officers (R-04) |
| `qa.assignment.max_programmes_per_verifier` | 5 | For Phase 5 verifier assignment |
| `qa.assignment.verifier_same_discipline` | true | For Phase 5 |
| `qa.workload.reviewer_cannot_verify` | true (two officers) | R-02 |
| `qa.staff.ssq_grade_min` / `_max` | 7 / 12 | R-20 |
| `qa.visit.team_lead_roles` | director, deputy-director, zonal-director | Who may lead a visit |
| `qa.cost.rates_confirmed` | false (two officers) | Turns off the "provisional" warnings once Finance confirms the rates |

## 6. Seed data (`seed_startup`, idempotent)

Flow `qa_case` (+1 flow, +23 stages, +43 transitions), 8 settings, 9 **placeholder** rates (marked *rates not confirmed*), the task category `qa-sop03`, the system user, role permissions (ES, directors, officers, Pay Officer, Finance, CQAC, ICT monitor), the *QA Settings Admin* group gets `manage_rate_table`, three notification templates and rules (reminder, breach, needs reassignment), and the Send Back To Audit policy enabled. `--reset-demo` now also removes the Phase 4 records and the stage tasks of demo cases.

## 7. New command: `seed_phase4_demo`

```bash
python manage.py seed_phase4_demo [--stage N] [--reset-passwords] [--new-case] [--skip-startup]
```

Runs `seed_startup`; creates (or reuses) the demo institution **Kaduna Demo Polytechnic (Phase 4)** with two accredited programmes due for reaccreditation, 16 demo accounts with staff profiles and grade levels, a real QA request (fee waived, SSQs submitted) and its QA case; drives the case to stage N (default 2) through the real services and tasks; prints every account and password. Idempotent. Refused when DEBUG is off unless `--i-understand-production`.

## 8. Other changes

* Phase 3's case "shell" now starts SOP-03 at once (status `case_opened` instead of `awaiting_sop03`).
* `qa_core.permissions.SETTINGS_VIEW_PERMS` includes `accreditation.manage_rate_table`, so the Finance Director can open Settings → Rate table.
* **Fix found on the way:** in `static/css/tasks.css` a "Better card" rule laid every task card out as a three-column grid for markup the card template doesn't use, squeezing titles into a narrow column. Cards now get the stacked layout back (one CSS rule at the end of the file).
* `INSTITUTION_REGISTRATION_GUIDE.md` — where and how an institution registers, and what follows.
* `scripts/` — the test runners, seed scripts and Playwright walkthroughs used to build and check Phases 3–4.

## 9. Files

**New:** `accreditation/case_services.py`, `case_views.py`, `case_urls.py`, `case_demo.py`, `migrations/0003_sop03_case.py`, `tests/test_sop03.py`; `qa_core/task_bridge.py`, `seed_data_phase4.py`, `migrations/0003_task_bridge.py`, `management/commands/seed_phase4_demo.py`, `qa_stage_reminders.py`, `qa_reconcile_tasks.py`, `tests/phase4_fixtures.py`; `templates/accreditation/case/*` (18 templates), `templates/accreditation/settings/rates.html`, `_rates_panel.html`; `static/css/qa-p4.css`; `scripts/*`; the Phase 4 documents.

**Changed:** `accreditation/models.py`, `effects.py`, `qa_services.py`, `settings_views.py`, `tests/conftest.py`, `tests/test_qa_requests.py`; `institutions/forms.py`, `handlers.py`; `nbte_ict/urls.py`; `qa_core/flow.py` (stage listeners), `models.py`, `signals.py`, `context_processors.py`, `permissions.py`, `settings_catalogue.py`, `settings_urls.py`, `views.py` (settings tab), `templatetags/qa_tags.py`, `management/commands/seed_startup.py`, `tests/test_scoping_enforcement.py`; `tasks/hooks.py`, `services.py`, `views.py`, `templatetags/tasks_tags.py`; `templates/partials/sidebar_qa.html`, `templates/qa_core/_shell.html`, `templates/qa_core/settings/_base.html`, `templates/accreditation/qa/_detail_panel.html`, `templates/tasks/_card.html`, `templates/tasks/detail.html`; `static/css/tasks.css`, `static/js/tasks.js`.

Not touched: the internal `workflow` app, the backups app, inspections, complaints. No new pip dependencies; no file renamed or moved; e-mails are still logged, not sent.
