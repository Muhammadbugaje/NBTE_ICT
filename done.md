# Done / routed log (HANDOVER_NEXT_AI.md)

Live record of what is already in the tree, what this session verified, and what is
still left. Do not redo anything under **Already done**. Follow the order in
`HANDOVER_NEXT_AI.md`.

Last updated: 2026-10-30 (verification session — no feature code changed).

Status words used below:

- **DONE** — code in the tree and the named tests pass.
- **IN TREE** — code exists but this session has not yet confirmed it with a test run.
- **PARTIAL** — some of the step exists; the missing pieces are listed.
- **NOT STARTED** — no implementation found.
- **BLOCKED** — the handover says to stop and ask the owner first.

---

## 1. Summary

| Step | Item | Status | Notes |
|---|---|---|---|
| A | Check where the previous AI stopped | IN TREE / verifying | `makemigrations --check` says **No changes detected**; `manage.py check` says **0 issues**. Focused test run in progress. |
| B | 2.1 Complaint routing (X9–X11) | **DONE (in tree)** | Models, migrations, services, flow v2, web, API and tests all present. Needs test run + `seed_nbte` on the owner DB (flow v2 is not seeded there yet). |
| C | 2.3 QA home by role + 2.4 registration trim (X50) | **DONE (in tree)** | `qa_core/home.py`, home template, `static/js/forms-dynamic.js`, registration form/tests present. Needs test run. |
| D.1 | X37b professional-body requirements in SSQ review | **NOT STARTED** | No `BodyRequirement` / `BodyRequirementCheck` models; `accept_ssq` has no mandatory-requirement check. |
| D.2 | D-19 provisional NBTE letter / wait for body letter | **PARTIAL** | `Programme.awaiting_body_letter`, the public label and `qa.pb.wait_for_body_letter` setting exist. The block on “approval to commence”, the `approved_pending_body_letter` status and the provisional-letter path are missing. |
| D.3 | X45 diff import specs | **PARTIAL** | Engine + 3 specs exist (`staff`, `professional_bodies`, `programme_master`). Missing: institutions + programmes, curriculum requirements, management roles. |
| E.1 | X38 road/air per state | **PARTIAL** | `RateTable` + `CostSheet` exist, but no `StateTravelProfile`, no per-state mode/distance. |
| E.2 | X39 per-state rates, calculator, actuals | **PARTIAL** | Existing cost-sheet/SOP-03 path only; no state/grade/effective-date rates, no `visit_costing.py`, no `VisitExpense`. |
| E.3 | X40 structured visit report VVT/001 | **NOT STARTED** | No `FormTemplate` / `VisitReport` / `VisitEvidence` / `VisitDiscrepancy`; `inspection/visit_report.py` absent. This is the XL item. |
| E.4 | X32–X34 honoraria | **PARTIAL** | Only X33 bank details exists (`honoraria/`). No honorarium accrual/rates/schedules, no receipt confirmation, no income-vs-expenditure report. |
| F | Part 5 finish (seed, CSS, smoke, guide, report) | **NOT STARTED** | Guide section 6 still says X9–X11, QA home and X50 are “not done”. |

**Gate:** the handover says *“Stop here and report to the owner before starting Part 3.”*
Steps D, E and F therefore need the owner’s sign-off before work begins.

---

## 2. Already done (previous AIs — do not redo)

### Access rules / need-to-know
- `qa.privacy.mode` default `assignment` for new installs
  (`assignments/0006_privacy_mode_default_assignment.py`). The owner’s DB stays on
  `department` until the dual-control `manage.py privacy_mode`.
- `assignments/access.py`, `assignments/work.py`; rotation uses the same rules.
- Document review and physical-verification assignment go through the work API.
- SOP walk + `assignments/tests/test_need_to_know_sop.py`.

### Part 1 — roles, ranks, permissions
- **1a** One director per department: `org/single_holder.py`, `org.services.assign_role`,
  `manage.py fix_duplicate_directors`, `org/0007_flag_duplicate_single_holders.py`.
- **1b** Rank is not a permission: `institutions/tvet_access.py`, migrations `0017` / `0018`.
- **1c** Three layers (X47): `qa_core/actions.py`, `institutions/tvet_handlers.py`,
  `institutions/pb_views.py`.
- **1d** Tests: `org/tests/test_single_director.py`,
  `institutions/tests/test_x47_three_layer.py`.

### Part 2.2 — Strict SSQ assignment
- `accreditation/ssq_assign.py`, review panel, My work Return, API assign, Celery
  reminders, settings, `accreditation/tests/test_ssq_strict_assignment.py`.

### Part 2.1 — Complaint routing (X9–X11) — built in the tree
- `ComplaintLayer.handling_department` + `assigned_officer` + `submitted_for_review_at`
  (`complaints/models.py`).
- Migrations `complaints/0003_x9_x11_routing.py` and
  `complaints/0004_fill_handling_department.py` (data migration from routing rules).
- RoutingRule targets `institution_handling_department` and `institution_ict_supervisor`;
  settings screen edits the layer’s handling department.
- Visibility (`complaints/services.py`): handling director, assigned officer, ES /
  super admin; read-only overseeing director and ICT supervisor; everyone else 404.
  Notifications go to the director, never the whole department.
- Director assigns own staff through `assignments.work.assign` + time-boxed grant;
  flow v2 (`complaints/flow_v2.py`) with submit-for-review / resolve / return /
  confirm / reopen; audit on every step.
- Seeded by `qa_core/management/commands/seed_startup.py` (`flow_v2.seed()`).
- Web and API both call the services; tests `complaints/tests/test_x9_x11_routing.py`
  (11 tests) plus the existing complaint tests were updated for v2.

### Part 2.3 / 2.4 — QA home and registration (X50) — built in the tree
- `qa_core/home.py` (widgets by role/office, need-to-know counts), template
  `templates/qa_core/_home_panel.html`, `templates/qa_core/home.html`.
- `static/js/forms-dynamic.js` + `institutions/portal_views.py` `RegistrationForm`
  (fields removed, `data-show-if`, server-side matching rules).
- Tests `qa_core/tests/test_qa_home_roles.py` (7) and
  `institutions/tests/test_x50_registration.py` (6).

---

## 3. This session — verification (no code changes)

### Commands already run
- `.venv/bin/python manage.py check` → **System check identified no issues (0 silenced).**
- `.venv/bin/python manage.py makemigrations --check --dry-run` → **No changes detected.**
- Read-only DB check on the owner’s `db.sqlite3`:
  - `complaints` migrations 0001–0004 are applied;
  - `ComplaintLayer`: 10 rows, 5 with a handling department (the rest deliberately use
    the institution’s handling department);
  - `qa_core.FlowDefinition` for `complaint` has **only version 1**. Flow v2 is in the
    code but has **not** been seeded on the owner’s database yet.
- Focused test run (Step A.1 + B + C) — **result pending**:
  `org/tests/test_single_director.py institutions/tests/test_x47_three_layer.py
  accreditation/tests/test_ssq_strict_assignment.py
  assignments/tests/test_need_to_know_sop.py complaints/tests
  qa_core/tests/test_qa_home_roles.py institutions/tests/test_x50_registration.py`

### Step A status
- Migration drift: none.
- Wider regression set (Step A.2) was already running from the previous session when
  this session started; it held `/tmp/nbte_test_db.sqlite3`. Results were not captured
  in this session. Re-run it once the focused run is green.

---

## 4. What is left

### Step D — Part 3 (Phase R remainder) — **needs owner sign-off first**
1. **X37b** — add `institutions.BodyRequirement` and `accreditation.BodyRequirementCheck`
   (models + migration + idempotent seed defaults); load them on the SSQ review screen;
   record results; a mandatory `not_met` forces a return unless the reviewer overrides
   with a remark (server-side in `accreditation/qa_services.py::accept_ssq`); tests.
2. **D-19** — when `programme.awaiting_body_letter` and
   `qa.pb.wait_for_body_letter` is true, set the programme to
   `approved_pending_body_letter` (“Approved — awaiting <Body> letter”), issue the
   provisional approval letter with the documents engine, and block the official
   approval-to-commence until the body uploads its letter; tests.
3. **X45 specs** — add `DiffSpec`s for institutions + programmes, curriculum
   requirements (`accreditation.CatalogueItem`) and management roles
   (`institutions.ManagementRole`), using `core/importdiff.py` and the
   `institutions/diffspecs.py` pattern; require the group’s **approve** permission or
   ES / super admin; tests.

### Step E — Part 4 (Phase H core) — **needs owner sign-off first**
1. **X38** — `accreditation.StateTravelProfile` (mode road/air, distance from HQ,
   road days each way, airports), seeded for 36 states + FCT, editable in settings.
2. **X39** — extend `RateTable` with `state`, `grade_band`, `effective_from/to`, new
   rate codes and `per_km`; add `accreditation/visit_costing.py` (calculate →
   `CostSheet`/`CostLine` → existing Audit → Director of Audit → ES flow);
   `VisitLogistics` and `VisitExpense` for actual spend; expenditure report.
3. **X40** — VVT/001 form engine (`inspection/FormTemplate`, `VisitReport`,
   `VisitEvidence`, `VisitDiscrepancy`), prefilled sections A–D, declared-vs-found,
   falsification fine (₦500,000 each, setting) onto the institution’s bill,
   conflict-of-interest declaration before start, GPS check, Director routing.
   This is the largest item (XL).
4. **X32–X34** — honoraria: ₦10,000 per internal SSQ/document item and ₦30,000 per
   academic resource person (settings); payment schedules from owner-only bank
   details; staff confirm receipt; visibility to the person, their director, Director
   of Finance and ES; income-vs-expenditure report.

### Step F — Part 5 finish
1. `makemigrations --check`, `build_css`, `seed_nbte --with-demo` **twice**
   (second run: “No changes — everything is already up to date.”).
2. Smoke-test every new page with the right demo role (200) and a wrong role (403/404).
3. Update `TESTING_GUIDE_v2_1.md` (and copy to `START_HERE_v2_1_TESTING_GUIDE.md`):
   section 6 still lists X9–X11, the QA home and X50 as not done; add “how to test”
   for B, C and every D/E feature, with demo accounts and TVET Support group setup.
4. Final report per part (files, migrations, settings, tests, skips) and a suggested
   commit message — **do not commit**.

---

## 5. Environment notes / risks

- **Owner DB flow v2:** the complaint migrations are applied, but `seed_nbte` has not
  run since `flow_v2.seed()` was added. Until it runs, new complaints stay on v1.
  Run `seed_nbte --with-demo` (safe twice) before smoke-testing B.
- **Docs are stale:** `TESTING_GUIDE_v2_1.md` still says X9–X11, the new QA home and
  X50 are not built; `docs/master_plan_v2_1/PROGRESS.md` still shows Phase 0 only.
  Do not treat those statements as current.
- **Step gate:** D/E/F require the owner’s sign-off after C, per `HANDOVER_NEXT_AI.md`.
- **X40 is XL** and touches inspection, documents, payments, sanctions, attendance
  (on-duty) and the form engine; plan it as several sub-steps, not one.
- **Owner decisions:** D-16/D-17/D-18 and VR-1…VR-6 already have defaults in the
  master plan; confirm them before building X38–X40 if the owner wants changes.

---

## 6. Session 2026-10-31 (the owner's sign-off build — no commits)

The owner signed off D–F and gave the live-registration brief. Built today (code only,
tests listed below; DB backup taken before migrate):

1. **X19 completed (registration → ICT oversight):**
   - registration now opens a "[QA] Assign an ICT supervisor" task for the ICT
     director (`institutions/services.py::queue_ict_supervisor_task`, QATaskLink
     stage `oversight_ict`, task category `qa-onboarding` seeded in
     `qa_core/seed_data_phase4.py TASK_CATEGORIES`);
   - the oversight card on the institution page preselects the rotation's
     suggestion (`institutions/views.py _oversight_context` + `_overview.html`);
   - appointing/reassigning a supervisor closes the task
     (`_close_oversight_task` → `task_bridge.close_for_stage`).
   - Tests: `institutions/tests/test_x19_oversight_task.py` (3).
2. **X27b auto-selector:** a pool flagged `auto_assign` (or listed in
   `qa.rotation.auto_assign_pools`) assigns the rotation's best officer to a
   document review at once (system assignment: `assigned_by=None`, audit note
   "(auto by rotation)"); director notified "auto_assigned" (template+rule added
   to `accreditation/seed_sets.py`) and may reassign. Tests:
   `institutions/tests/test_auto_assign_flow.py`.
3. **Programme adds notify NBTE too** (`institutions/programme_master.py
   _notify_nbte_of_programme`, rule `programmes.added_master`): master-list adds
   now notify the NTIRD master-list department + the handling department's
   director (hand-typed ones already did).
4. **Visit panel notifications** (`inspection/services.py team_members_of`
   + `_notify_team_of_visit`, rule `inspection.visit_scheduled`): when the ES
   approves the visit, the team lead, the mapped personnel and mapped resource
   people all get the dates; the professional bodies were already told.
5. **Honoraria fixes:** SSQ rows created by the sync hook are now `payable=True`
   (the reviewer earns the ₦10,000 item); physical-verification work marked
   `payable=False` (X32 covers SSQ/document items only; PV is paid through the
   visit cost sheet — no more dangling payable rows that could never accrue).
6. **Fee regression fixed:** `falsified` was added to `FEE_VARIABLES` for X40
   but `payments/services.py fee_variables` never supplied it, so **every** bill
   (including the institutional processing fee) failed with
   `Missing inputs: falsified`. It now defaults to `0`; the visit fine passes
   the real count in `variables={"falsified": n}` (`accreditation/visit_report.py
   raise_fine`), which overrides the default.

Test-run notes: use masked provider creds
(`BREVO_API_KEY= BREVO_SMTP_LOGIN= BREVO_SMTP_KEY= GMAIL_USER=`) and
`QA_EMAIL_DELIVERY=console` so tests never send real e-mail through Brevo.
- No feature code was changed in this session; nothing was committed.

## 7. Session 2026-11-01 (visibility & payments — no commits)

1. **D-ICT institution visibility fixed** (`accounts/scoping.py`): the
   `qa.privacy.mode = department` branch now honours
   `assignments.access.full_viewer_everywhere` (X2: ES, super admin, Director
   of ICT) in both modes — see SESSION_REPORT §7.1. New test in
   `institutions/tests/test_x19_oversight_task.py`.
2. **Missing v3 fee schedule seeded** (`qa_core/seed_data_phase2.py
   FEE_SCHEDULES` + catalogue formula `qa.fee.institutional_processing_v3`):
   this is why new institutions' SOP-01 bills never formed and NBTE "saw no
   payments" — components were silently skipped
   (`payments/seed_sets.py::seed_fee_components`).
3. **Payment pages pinned by tests** (`payments/tests/
   test_v21_payment_pages_flow.py`): portal detail through the whole gateway
   flow + the NBTE finance queue / obligation page; non-finance directors are
   correctly 403 on the queue.
4. **Page checker extended** (`scripts/check_pages.py`): both payment detail
   pages; wrong URL guesses fixed (`/qa/inspection/inspections/`,
   portal `/messages/`+`/notifications/` removed); the two remaining 403s are
   the intentional internal-site guard. Report:
   `docs/reports/page_check_2026-11-01.md`.
5. **Render deploy files**: `render.yaml`, `build.sh`, `Procfile`, plus
   `GET /health/` (`core.views.health`) for the health check. Aiven Postgres
   through `DATABASE_URL` (see SESSION_REPORT §7 for the variable list).
