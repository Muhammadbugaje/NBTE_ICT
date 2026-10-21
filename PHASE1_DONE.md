# NBTE QA System — Phase 1 definition of done

Checked against PHASES.md, "Phase 1 — Foundation and portal shell", including the clauses that end every phase. Evidence is the command or test that proves each item. Results are dated 16–18 Oct 2026.

## Phase 1 definition of done

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | An institution demo user can sign in **only** on the portal host | **PASS** | Browser: `admin@demo-poly.local` signed in at `qa.localhost` (forced password change, then the empty dashboard); the internal login refuses the same account. Tests: `test_external_guard.py::test_institution_user_signs_in_on_portal`, `::test_internal_login_form_refuses_external_users`, `::test_external_user_refused_and_logged_out_on_internal_host` (403 and logout) |
| 2 | The portal URLconf contains no internal route (an automated test lists them) | **PASS** | `test_host_routing.py::test_url_tables_are_disjoint` (lists the 10 portal routes); `::test_every_internal_url_is_404_on_the_portal_host` (every parameterless internal route answers 404 there); `::test_portal_urlconf_has_no_internal_prefixes` |
| 3 | Editing `qa.fee.programme_processing` creates a pending action that a second officer confirms | **PASS** | Browser step 6: PA-2026-000001 created by `qa.officer` and confirmed through the `NBTEConfirm` dialog by `qa.director`; the new formula is then in force. Test: `test_settings_dual_control.py::test_officer_edits_fee_via_page_and_director_confirms_via_page` |
| 4 | The enforcement test fails if someone writes `Institution.objects.all()` in a view (proved by a deliberately failing fixture) | **PASS** | `test_scoping_enforcement.py::test_layer2_deliberately_failing_fixture_is_caught`; at runtime `UnscopedQueryError` (`::test_layer2_runtime_guard_raises_on_unscoped_evaluation`) |
| 5 | §20 contract verified for every new page | **PASS, with items flagged for manual review** | The server side is fully tested (`test_interaction_contract.py`, 16 tests); light and dark at 375 px and 1440 px were captured for every page. The client-side failure paths (slow network overlay, timeout and retry, optimistic rollback on 500), the 320/768/1024 px widths, keyboard-only use and screen readers are built but **not exercised by hand**. PHASE1_TEST_PLAN.md lists exactly which ones |

## Clauses at the end of every phase

| Item | Status | Evidence |
|---|---|---|
| `python manage.py check` is clean | **PASS** | "System check identified no issues (0 silenced)" |
| The full existing test suite is green | **PASS (no regression)** | 721 passed. The 15 failures in `review_checks/test_review_findings.py` are the same 15 tests that fail on the code as received (a `diff` of the two failure lists is empty). Those tests were failing before this work; they are not caused by it |
| The scoping enforcement test (§4) is green | **PASS** | `test_scoping_enforcement.py`: 10 passed (model audit, source audit, cross-tenant 404s) |
| `seed_startup` re-runs with no changes on a seeded database | **PASS** | "Changes this run: 0 — already up to date." (step 7); `test_seed_startup.py::test_second_run_changes_nothing` |
| `seed_startup --with-demo` followed by `--reset-demo` leaves zero demo rows | **PASS** | Step 8: 7 tagged rows removed, `DemoTag` empty, untagged rows untouched (table-by-table count); `::test_reset_demo_removes_exactly_the_tagged_rows` |
| Screens checked at 320, 390, 768, 1024 and 1440 px in light and dark | **PARTIAL** | Captured at 375 and 1440 px in both themes (28 screenshots). The CSS breakpoints are 767/768, 900, 1023/1024 and 1100 px. 320, 768 and 1024 px were not captured: a quick DevTools spot-check is recommended |

## Deliverables (PHASES.md "Deliverables")

| Deliverable | Status | Where |
|---|---|---|
| Apps `qa_core`, `institutions`, `payments`, `accreditation`, `inspection`, `complaints` registered | PASS | `nbte_ict/settings/base.py` |
| The 15 `qa_core` models | PASS | `qa_core/models.py`, `qa_core/migrations/0001_initial.py` |
| `User.user_type`, `User.institution`, `Institution` stub (`code`, `name`, `handling_department`, `status`), check constraint | PASS | `accounts/models.py`, `institutions/models.py`; `test_scoping_enforcement.py::test_user_type_check_constraint` |
| `scope_to_institution` and `ExternalUserGuardMiddleware` | PASS | `accounts/scoping.py`, `accounts/middleware.py` |
| `ConfigEntry` types `decimal` / `date` / `formula`; `dual_control` and `editable_by` | PASS | `config/models.py`; `test_settings_dual_control.py::test_new_value_types_validate` |
| `tasks`: `task_state_changed` signal and `is_externally_managed` hook | PASS | `tasks/signals.py`, `tasks/hooks.py`, `tasks/services.py`; `test_tasks_hooks.py` |
| `notifications`: `qa` category | PASS | `notifications/models.py` |
| Portal pages: login, set password, forgot password, empty dashboard, account settings | PASS | `templates/qa_portal/`; screenshots |
| Internal pages: Settings → QA (rules, formulas with test panel, reference formats, workflows read-only, notification templates) and Pending confirmations | PASS | `templates/qa_core/`; screenshots |
| Settings: `qa.settings.*`, `qa.credentials.*`, `qa.impersonation.*`, `qa.portal.*`, `qa.stage.*`, Eligibility (7), §20 (2), payment vocabulary keys including the locked key | PASS | `qa_core/settings_catalogue.py` (27 keys); `test_seed_startup.py::test_first_run_creates_everything` |
| Payment flow seeded with `provider_reported_paid`, `awaiting_finance_confirmation` and `finance_confirmation_overdue`; only `final_confirm` reaches `confirmed` | PASS | `test_payment_flow.py` (7) |
| All `qa_core.*` permissions | PASS | 15 codenames on `FlowDefinition.Meta` |
| `seed_startup` with `--with-demo`, `--reset-demo` and `--dry-run` | PASS | `test_seed_startup.py` (7); steps 2, 7, 8 |

## Required tests (PHASES.md "Tests required")

| Test | Status | File |
|---|---|---|
| Formula evaluator, including 1,000-expression fuzz | PASS | `test_formula.py` (65) |
| Reference generator: 20 threads, no duplicates; mod-97 | PASS | `test_references.py` (15) |
| PendingAction: initiator ≠ confirmer, expiry, stale target, superuser self-confirm blocked | PASS | `test_pending_actions.py` (16) |
| Host routing: URL tables disjoint; internal URLs 404 on the portal | PASS | `test_host_routing.py` (7) |
| `ExternalUserGuardMiddleware`, both directions | PASS | `test_external_guard.py` (14) |
| Scoping enforcement, three layers | PASS | `test_scoping_enforcement.py` (10) |
| Dual-control settings write path | PASS | `test_settings_dual_control.py` |
| The locked key cannot change via settings, admin or `.save()` | PASS | `test_settings_dual_control.py::test_locked_key_*` (4) |
| Flow-engine fixture: only `final_confirm` reaches `confirmed`, with the final-approver role | PASS | `test_payment_flow.py` |

## Not done in Phase 1, by design (the plan places these later)

- **Phase 2:** institution management screens, the impersonation *start* flow (the portal already shows the band and honours the exception), payments, and `dashboards.action_queue` QA items.
- **Phase 4:** the QA task bridge (the hook points exist, but no receivers or managers are registered).
- **Phase 8:** the `backups` app.
- **Not configured:** email delivery in development. Notifications are queued; the email engine logs "no provider" until Brevo or Gmail keys are set, exactly as for the rest of the system.
