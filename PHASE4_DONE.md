# NBTE QA System — Phase 4 definition of done

Phase 4 — SOP-03 internal workflow and Task Bounce. Checked on 19 Oct 2026.

## Checkpoints

| Checkpoint | Status |
|---|---|
| A — Flow engine hook (stage listeners) + SOP-03 flow seeded (17 stages, parallel stage 1) | **PASS** |
| B — Models migrated (case fields, rate table, cost / personnel / visit / fund release, task-link SLA fields, drift report) | **PASS** |
| C — Screens: Case Board, case detail tabs, cost sheet editor, personnel, visit, funds, queues, rate table, drift, Open in QA | **PASS** |
| D — Task bridge (create / close / veto / reassign re-validation / reminders / reconciliation) + `seed_phase4_demo` | **PASS** |
| E — Tests pass, real database migrated and seeded, walkthrough and screenshots done | **PASS** |

## Definition of done

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Models QACase (extended), CostSheet, CostLine, RateTable, PersonnelList, PersonnelEntry, VisitDetail, FundRelease | **PASS** | `accreditation.0003_sop03_case`; PHASE4_MIGRATIONS.md |
| 2 | 17 stages on the Phase 1 FlowDefinition engine; no hard-coded state machine | **PASS** | flow `qa_case`; `test_qa_case_flow_is_seeded_on_the_engine` |
| 3 | Stage 1 parallel gate Audit / Finance / ICT; two ICT officers at the threshold | **PASS** | `test_stage_1_opens_three_parallel_branches_with_a_task_each`, `test_big_cases_need_two_ict_officers_r04` |
| 4 | Every action stage returns with a mandatory remark to the exact previous actor | **PASS** | `test_every_stage_returns_to_the_exact_submitter`, `test_return_needs_a_remark` |
| 5 | Return from stage 5 goes to stage 4 with the remark pinned | **PASS** | `test_return_from_stage_5_goes_to_stage_4_with_the_remark_pinned`; screenshot `12` |
| 6 | Task created on stage entry, linked through QATaskLink | **PASS** | `test_case_starts_with_a_task_for_the_owning_director`; screenshot `13` |
| 7 | "Open in QA"; tasks cannot be marked done from Tasks | **PASS** | `test_linked_task_cannot_be_completed_or_cancelled_from_tasks`; screenshots `07`, `08` |
| 8 | Reassign re-validated (R-01, R-02, R-20) with rollback; overworked user rejected | **PASS** | `test_reassign_to_an_overworked_officer_is_rejected_r01` (also via Tasks' own reassign, rolled back), `…_r20`, `…_r02`, `test_who_may_reassign` |
| 9 | Task auto-closed when the QA action is taken | **PASS** | `test_task_closes_when_the_qa_action_is_taken`, `test_seventeen_stage_walk_with_approvals` |
| 10 | Case Board by stage | **PASS** | screenshots `01`, `18`; `test_case_board_and_queues` |
| 11 | Case detail tabs: overview, cost, personnel, visit, fund release, timeline, tasks | **PASS** | screenshots `02`–`16`; `test_ict_monitor_can_view_every_tab_and_the_drift_report` |
| 12 | Cost sheet editor: department builds, Audit reviews / edits, ES approves | **PASS** | screenshots `03`, `09`, `11`; `test_cost_sheet_editor_rules` |
| 13 | Visit / fund release: ES approves the visit, Pay Officer releases | **PASS** | `test_only_the_pay_officer_releases_funds`, 17-stage walks |
| 14 | ICT monitor can view but not write (every POST URL) | **PASS** | `test_ict_monitor_cannot_write` ×13; screenshot `16` |
| 15 | SLA reminders at the configured percentages | **PASS** | `test_sla_reminders_at_the_configured_percentages`, `test_reminder_percentages_follow_the_setting`; `manage.py qa_stage_reminders` |
| 16 | Reconciliation repairs an orphaned task | **PASS** | `test_reconciliation_repairs_an_orphaned_task`; `manage.py qa_reconcile_tasks`; screenshot `17` |
| 17 | Settings `qa.assignment.*`, `qa.workload.*`, `qa.staff.ssq_grade_*`, `qa.visit.team_lead_roles` (+ `qa.cost.rates_confirmed`) | **PASS** | Settings → Rules & thresholds; PHASE4_CHANGELOG.md §5 |
| 18 | §20 HTMX contract | **PASS** | every screen is a full page and an `#qa-panel` partial with pushed URLs and toasts; `test_case_board_and_queues` checks a partial |
| 19 | `seed_phase4_demo`: early-stage case, rate table, real tasks, prints accounts, idempotent | **PASS** | `test_seed_phase4_demo_is_idempotent`; run twice on your database |
| 20 | Migrations run on your real `db.sqlite3` (backed up first) | **PASS** | backup `db.sqlite3.before-phase4-20261019-042842`; PHASE4_MIGRATIONS.md |
| 21 | Interactive verification steps 1–6 | **PASS** | PHASE4_TEST_PLAN.md §B; `PHASE4_UI_SCREENSHOTS/` (19) |
| 22 | Phases 1–3 not broken | **PASS** | results below |
| 23 | Out of scope untouched (Phase 5+: inspections, resource persons, decisions, complaints; backups; internal `workflow` app) | **PASS** | no file of those apps changed |
| + | Institution registration guide; seed includes an institution; scripts for tests, seeds and walkthroughs | **PASS** | INSTITUTION_REGISTRATION_GUIDE.md; `scripts/README.md` |

## Test results

| Run | Result |
|---|---|
| Phase 4 (`accreditation/tests/test_sop03.py`) | **53 passed** |
| Whole project (`python -m pytest`) | **945 passed, 15 failed, 1 skipped, 1 xfailed** (87 min). The 15 failures are the same 15 tests in `review_checks/test_review_findings.py` that failed on the code as received and in Phases 1–3; nothing else fails. Phase 3's whole-project result was 892 passed: + 53 Phase 4 tests = 945. |
| Pinned Phase 1–3 values updated | `test_seed_startup.py`: the flow set now includes `qa_case`. `test_qa_requests.py`: a new case's status is `case_opened` (it was `awaiting_sop03`, the Phase 3 shell). `test_scoping_enforcement.py`: `case_views.py` added to the audited view modules. |
| `manage.py check`, `makemigrations --check` | No issues; no changes detected |
| Migration reverse / forward | OK on a copy of the seeded database |
| `seed_startup --reset-demo` | On a copy of your database: removes the demo case, its 5 stage tasks, records, institution, 16 accounts with their staff profiles and the demo bank account; `seed_phase4_demo` then rebuilds cleanly |

## Known limitations (by design for Phase 4)

* Stages 12–16 are approve / return stages with tasks only (the post-visit report and recommendation forms come with Phase 5 inspections); stage 17 closes the case without writing programme status or letters (Phase 6).
* The rate table holds **placeholder** amounts; cost sheets say "provisional" until Finance confirms the rates (`qa.cost.rates_confirmed`).
* Rates can be edited by the QA Finance Director role (`qa-finance-director`) or the QA Settings Admin group; the demo's `qa.finance_director` is an ordinary director of Finance, so they see the rate table but can't edit it.
* R-02 (a visit-team member can't review) is enforced on reassignment; the first assignment of a stage follows the stage's actor rule.
