# NBTE QA System — Phase 3 definition of done

Phase 3 — QA requests (SOP-02) and the dynamic SSQ, plus the Staff Portal Dashboard. Checked on 18–19 Oct 2026.

## Checkpoints

| Checkpoint | Status |
|---|---|
| A — Middleware changes + Staff Portal Dashboard UI | **PASS** |
| B — Models migrated + SSQ template builder working | **PASS** |
| C — Portal QA request + SSQ workspace (autosave / prefill) | **PASS** |
| D — Internal SSQ review + queries | **PASS** |
| E — Tests pass and screenshots captured | **PASS** (see results) |

## Definition of done (PHASES.md Phase 3 + the Phase 3 prompt)

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Staff can sign in on the portal and land on the **Staff Portal Dashboard** with **Go to Internal System** | **PASS** | walkthrough `01`; `test_staff_land_on_the_staff_portal_dashboard`, `test_portal_login_form_accepts_staff` |
| 2 | Three portal dashboards (institution, resource person, staff) | **PASS** | `qa_core/portal_views.dashboard`; `test_resource_person_sees_their_variant`, `test_institution_users_never_get_the_staff_dashboard` |
| 3 | Staff cannot see institution data on the portal; institution users cannot get the staff dashboard | **PASS** | `test_staff_portal_dashboard`, `test_institution_users_never_get_the_staff_dashboard` |
| 4 | Models: QAType, QARequest, QARequestProgramme, SSQTemplate, SSQ, Submission, Evidence, Query, Response, CatalogueItem (+ QACase shell) | **PASS** | `accreditation.0002_qa_requests_ssq`; PHASE3_MIGRATIONS.md |
| 5 | SOP-02 on the Phase 1 FlowDefinition engine (no engine change) | **PASS** | flows `qa_request`, `ssq` (Settings → Workflows) |
| 6 | Cancel / drop / replace only in draft, submitted, fee due, payment pending confirmation (R-25) | **PASS** | `test_cancel_allowed_in_r25_stages[…]`, `test_cancel_refused_after_the_fee_is_confirmed`, `test_drop_and_replace_in_r25_stages[…]` |
| 7 | Queries and responses | **PASS** | walkthrough `18`–`22`; `test_query_raise_respond_close` |
| 8 | SSQ template builder (JSON sections/fields) in Settings with draft / publish | **PASS** | walkthrough `23`–`24`; `test_published_template_is_never_edited_new_version_retires_old`, `test_invalid_template_cannot_be_published` |
| 9 | Portal SSQ workspace: autosave, prefill (accepted documents only), evidence upload / re-use | **PASS** | walkthrough `10`–`14`, `27`–`29`; `test_autosave_persists_across_reload`, `test_prefill_uses_only_accepted_documents`, `test_upload_evidence` |
| 10 | Immutable Submission snapshot on submit | **PASS** | `test_submit_creates_an_immutable_submission`, `test_snapshot_freezes_catalogue_names_and_quantities` |
| 11 | Internal SSQ review queue (accept / return) | **PASS** | walkthrough `16`–`17`; `test_return_then_revise_supersedes_never_overwrites` |
| 12 | Curriculum catalogue + management UI; catalogue fields take quantities only (R-22) | **PASS** | walkthrough `13`, `25`; `test_catalogue_fields_take_quantities_only`, `test_catalogue_names_cannot_be_posted_through_the_portal` |
| 13 | §20 contract: no full reloads for SSQ sections, loading states, filters on lists | **PASS** | section swaps are HTMX partials with pushed URLs (`test_autosave_persists_across_reload` checks the partial); filters on every list |
| 14 | Seed: 7 QA types, Institutional Administration + Reaccreditation templates, starter catalogue (+ settings, permissions) | **PASS** | `seed_startup` output; `test_seeded_templates_are_valid`, `test_first_run_creates_everything` |
| 15 | DoD: demo institution raises Reaccreditation for two programmes, completes an SSQ with prefilled data, drops one programme before submission, submits | **PASS** | walkthrough `04`–`15` (programme data prefilled); governance prefill shown on the Institutional Administration SSQ (`27`–`28`) |
| 16 | DoD: a QA case is created automatically (empty, waiting for Phase 4) | **PASS** | `test_qa_case_created_when_every_ssq_is_submitted`; walkthrough request → *in the QA workflow* |
| 17 | DoD: Add Programme to QA, Drop Programmes and Cancel Request actions go live | **PASS** | `test_add_programme_to_qa_action_is_live`, `test_drop_programmes_action_takes_the_programme_off_open_requests`, `test_cancel_request_action_uses_r25` |
| 18 | DoD: return-to-academic-department path | **PASS** | `test_department_return_path`, `test_admin_can_recall_from_department` |
| 19 | DoD: Requirement 4 six-month window | **PASS** | `test_six_month_window_requirement_4`, `test_window_follows_the_setting` |
| 20 | DoD: Requirement 16 versioning — superseded, never overwritten | **PASS** | `test_return_then_revise_supersedes_never_overwrites`, template and catalogue versioning tests |
| 21 | Interactive verification (prompt steps 1–7) done in a browser | **PASS** | PHASE3_TEST_PLAN.md; `PHASE3_UI_SCREENSHOTS/` |
| 22 | Phase 1 and 2 not broken; changed fixed counts listed | **PASS** | results below; PHASE3_CHANGELOG.md §11 |
| 23 | Out of scope untouched: workflow engine, inspections, complaints, backups | **PASS** | no file of those apps changed |

## Test results

| Run | Result |
|---|---|
| Phase 3 (`test_qa_requests.py` 29 + `test_ssq.py` 32) | **61 passed** |
| Phases 1–2 (`qa_core/tests`, `institutions/tests`, `accreditation/tests/test_onboarding.py`, `payments/tests`) | **292 passed** after the listed updates of pinned Phase 1 values (portal route list, reference-format count 9→10, flow set, demo users 11→12) and the changed staff-portal rule (2 guard tests replaced by 4) |
| Whole project (`python -m pytest`) | **892 passed, 15 failed, 1 skipped, 1 xfailed** (67 min). The 15 failures are the same 15 tests in `review_checks/test_review_findings.py` that failed on the code as received and in Phases 1–2; nothing else fails. Phase 2's whole-project result was 829 passed: +61 Phase 3 tests, +2 from the guard-test split = 892. |
| `manage.py check`, `makemigrations --check` | No issues; no changes detected |
| Migration reverse/forward | `migrate accreditation 0001` → `migrate` OK on a copy of the demo database |
| `seed_startup --reset-demo` on the walkthrough database | Removes exactly the demo rows, incl. 2 QA requests, 2 SSQs, 1 submission, 4 evidence rows, 1 query + response, 1 QA case |

## Known limitations (by design for Phase 3)

* The QA case is a shell: SOP-03 stages, visits, reports and decisions are Phase 4.
* The SSQ templates' wording is a starting point for DPAP to refine (as version 2).
* The QA fee amounts are unconfirmed in a real installation until Finance sets them (the request waits at *fee due* with a note and a staff *Issue the fee now* button).
