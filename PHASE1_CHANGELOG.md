# NBTE QA System — Phase 1 changelog (16 October 2026)

**Scope:** Phase 1 only, "Foundation and portal shell" (PHASES.md; Master Plan §§1–4, §14, §20, and the matching rows of §5.1, §5.3, §5.7, §15).
**Open questions:** all 15 answered with the recommended defaults (OPEN_QUESTIONS.md). The defaults that touch Phase 1 are listed at the end.
**Nothing from Phase 2 or later** was built: there are no institution screens, payments, SSQs, inspections, decisions, complaints, bulk import/export or `backups` app.

Every block below gives what changed, the files involved, and a command that verifies it. Commands assume a working virtualenv in the project root.

---

## 1. New apps

**What:** `qa_core` holds the shared engines. `institutions`, `payments`, `accreditation`, `inspection` and `complaints` are registered now, so permissions, URL namespaces and migrations are stable from day one. `institutions` holds only the minimal `Institution` stub, which `User.institution` requires. The other four have no models.

**Files:** `qa_core/` (new), `institutions/` (new, stub), `payments/`, `accreditation/`, `inspection/`, `complaints/` (new, empty); `nbte_ict/settings/base.py` (INSTALLED_APPS).

**Verify:** `python manage.py check` → "System check identified no issues".

## 2. `qa_core` models (§5.1)

**What:** `FlowDefinition`, `FlowStage`, `FlowTransition`, `FlowInstance`, `FlowEvent`, `ActionPolicy`, `PendingAction`, `ReferenceFormat`, `ReferenceCounter`, `QATaskLink`, `TimelineEvent`, `Suspension`, `NotificationTemplate`, `NotificationRule` and `DemoTag`. All 15 `qa_core.*` permissions are added: the 13 from §12, plus `view_oversight_board` and `contact_on_oversight_board` from §21. They are declared on `FlowDefinition.Meta`.

Documented additions to the §5.1 field lists, each needed by a Phase 1 rule:

| Addition | Why |
|---|---|
| `FlowDefinition.subject_model` is a model label (text), not a ContentType FK | The payment flow is seeded now (v1.1), but `PaymentObligation` arrives in Phase 2 |
| `FlowTransition.required_role_setting` | Makes "only the role in `qa.payment.final_approver_role` may `final_confirm`" data, not code |
| `FlowStage.allows_cancel`, `pauses_sla` | Referenced by §5.4 (B6/R-25) and §11 |
| `PendingAction.scope_department`, `target_label` | §6: confirmers come from the same department unless they hold `confirm_any_department`; readable lists |
| `ReferenceFormat.name`, `sequence_scope` | §10.5 examples need one global series, per-institution series and per-department series |
| `Suspension.linked_complaint_reference` (text) | `complaints.Complaint` arrives in Phase 7 |
| `NotificationRule.recipient_kind = eligible_confirmers`, `recipient_role` | §7 step 1 notifies eligible confirmers |
| `created_by` / `updated_by` use SET_NULL | The audit log keeps the actor; PROTECT would block removing demo or departed users. `PendingAction` and `FlowEvent` actors stay PROTECT |

**Files:** `qa_core/models.py`, `qa_core/migrations/0001_initial.py`, `qa_core/admin.py`.

**Verify:** `python manage.py makemigrations --check --dry-run` → "No changes detected".

## 3. Engines

| Engine | File | What it guarantees |
|---|---|---|
| Formula evaluator | `qa_core/formula.py` | Parses with `ast` and walks an allow-list of nodes; never calls `eval()`. Decimal arithmetic; `**` limited to an exponent of 4; at most 1,000 nodes and a 50 ms budget; `cfg('key')` with cycle detection; returns a snapshot of the expression, inputs and result. Only `FormulaError` can leave it |
| Reference generator | `qa_core/references.py` | `{YEAR} {INST} {DEPT} {SEQ:n} {CHK97} {CHK11}`; validated patterns (exactly one `{SEQ}`; the check comes after it); ISO 7064 mod 97-10 check; atomic counters (`select_for_update` + `F()` update). Retries on SQLite lock errors in development only; PostgreSQL waits on the row lock |
| Two-step confirmation | `qa_core/actions.py` | §7 hard rules: the confirmer is not the initiator (superusers included); not while impersonating; holds the confirm permission at or above the minimum rank, in the same department unless they hold `confirm_any_department`; applied atomically with the target locked; stale targets fail ("stale — please re-initiate"); one open item per action and target; expiry via `expire_overdue()`. Every step is audited and appears on the timeline |
| Flow engine | `qa_core/flow.py` | `start`, `check`, `available`, `transition`. Guards: `required_permission`, `required_role_setting` (superusers get no exception), `condition` (a formula), `requires_remark`, `requires_confirmation` |
| Scoping machinery | `qa_core/scoping.py` | `InstitutionScopedManager`; `objects.for_user(user)`; unscoped evaluation raises `UnscopedQueryError` when `QA_SCOPING_STRICT` is on (dev/test) and logs at ERROR in production; explicit `.unscoped` manager |
| Notifications | `qa_core/notify.py` | Sandboxed template engine (no loaders, no extra libraries, plain-value context only); in-app via `notifications.services.notify` (category `qa`); email via the existing engine; never raises into the caller |
| Timeline | `qa_core/timeline.py` | Single writer `record()`; append-only `TimelineEvent` |
| Settings write path | `qa_core/settings_service.py` | Checks `editable_by`; dual-control keys go to a PendingAction; locked keys have no edit path |

**Verify:** `python -m pytest qa_core/tests/test_formula.py qa_core/tests/test_references.py qa_core/tests/test_pending_actions.py qa_core/tests/test_payment_flow.py qa_core/tests/test_notify.py`

## 4. Changes to existing apps (the only ones touched)

| App | Change | Files |
|---|---|---|
| `accounts` | `User.user_type` (`staff` / `institution` / `resource_person`, indexed, default `staff`) and `User.institution` (nullable FK), plus the check constraint `accounts_user_type_institution_consistent` | `accounts/models.py`, `accounts/migrations/0014_user_type_institution.py` (renumbered on 18 Oct to follow your `0013_request_department_permission`) |
| `accounts` | `scope_to_institution()` next to `scope_to_department()`; `register_engagement_resolver()` hook for Phase 5 resource-person engagements | `accounts/scoping.py` |
| `accounts` | `ExternalUserGuardMiddleware` for both directions (§3). `ForcePasswordChangeMiddleware` sends portal users to the portal's own change page | `accounts/middleware.py` |
| `accounts` | The internal login form refuses institution and resource-person accounts with a pointer to the portal | `accounts/forms.py` |
| `config` | Value types `decimal`, `date`, `formula`; columns `dual_control` and `editable_by`; locked keys (`LOCKED_KEYS`), refused by `save()` and `delete()`; validators by value type (the formula evaluator is registered by `qa_core`); the two `qa.credentials.*` policy keys exempted from the secret-name ban | `config/models.py`, `config/migrations/0003_qa_value_types_dual_control.py` |
| `config` | Admin shows locked and dual-control rows read-only and refuses to delete locked rows. The generic settings page refuses `qa.*` keys and links to Settings → QA | `config/admin.py`, `config/views.py`, `templates/config/settings.html` |
| `tasks` | `task_state_changed` signal sent synchronously from `open_task`, `reassign`, `mark_assignee_done`, `review_task`, `cancel_task` and `return_to_assigner`. `is_externally_managed(task)` registry hook: such tasks cannot be cancelled or deleted. The registry is empty in Phase 1, so behaviour is unchanged | `tasks/signals.py`, `tasks/hooks.py` (new), `tasks/services.py` |
| `notifications` | Category `qa` (choices only) | `notifications/models.py`, `notifications/migrations/0004_notification_qa_category.py` |

Not touched, by design: `core` impersonation (Phase 2) and `dashboards.action_queue` (Phase 2+, when there are QA items to show).

**Verify:** `python -m pytest qa_core/tests/test_external_guard.py qa_core/tests/test_scoping_enforcement.py qa_core/tests/test_tasks_hooks.py`

## 5. Host routing and the portal (§3, §16)

**What:** `QAPortalHostMiddleware` serves every request whose host is `QA_PORTAL_HOST` from `qa_core.portal_urls`, which contains no internal route. Hosts are compared case-insensitively and without the port. Settings: `QA_PORTAL_HOST` (default `qa.nbte.gov.ng`; `qa.localhost` in development), `INTERNAL_HOST`, `QA_SCOPING_STRICT`. Development and production add the portal host to `ALLOWED_HOSTS`.

**Files:** `qa_core/middleware.py`, `qa_core/hosts.py`, `qa_core/portal_urls.py`, `nbte_ict/settings/base.py`, `dev.py`, `prod.py`.

**Verify:** `python -m pytest qa_core/tests/test_host_routing.py`

## 6. Settings seeded (§14; PHASES Phase 1)

27 keys, created by `seed_startup` and never overwritten:

- **Fees and payments (9):** `qa.fee.institutional_processing`, `qa.fee.programme_processing` and `qa.fee.qa_request_processing` (formulas, dual control); `qa.fee.qa_request_processing.confirmed` (false); `qa.fee.qa_request.base` / `.per_programme` (0; D15 unconfirmed); `qa.payment.final_approver_role` (`qa-finance-director`); `qa.payment.receipt_required_for_provider_payments` (true); and the **locked** `qa.payment.provider_confirmation_is_evidence_only` (true, "Fixed by policy"). The fee formulas are pulled forward from Phase 2 because the Phase 1 definition of done edits `qa.fee.programme_processing`.
- **Workflow:** `qa.stage.default_sla_hours` (72), `qa.stage.reminder_at_percent` ([50, 90]).
- **Eligibility (v1.1, 7):** `qa.institution.admin_required_department`, `qa.institution.chief_executive_required_position`, `qa.resource_person.min_rank`, `.required_designations`, `.max_active_engagements`, `.max_open_ssq_per_officer`, `.max_visits_per_year`.
- **Credentials, portal, impersonation:** `qa.credentials.resend_cooldown_minutes`, `.link_valid_hours`; `qa.impersonation.max_minutes` and `.allow_write` (dual control); `qa.portal.self_registration_enabled`, `.maintenance_message`.
- **Interaction (§20):** `qa.ui.max_show_all_rows` (500), `qa.ui.request_timeout_seconds` (15).
- **Settings:** `qa.settings.pending_action_expiry_hours` (72, dual control).

**Files:** `qa_core/settings_catalogue.py`.

**Verify:** `python -m pytest qa_core/tests/test_settings_dual_control.py qa_core/tests/test_seed_startup.py`

## 7. Pages

Every page renders a full page for a normal request and a partial for HTMX. Lists keep their filters in the URL, paginate at 25 rows, and offer "Show all" up to `qa.ui.max_show_all_rows` (enforced on the server). All pages use semantic tokens only and work in light and dark themes.

**Internal (NBTE host, existing `base.html` shell, new "Quality Assurance" sidebar group):**
- **Settings → QA → Rules & thresholds** (`/qa/settings/rules/`): search plus filters for group, type, control and editable; inline edit; dual-control rows say "Send for confirmation"; locked rows show "Fixed by policy" with no edit control; per-row change history.
- **Formulas** (`/qa/settings/formulas/`): current expression, variables, proposal editor, and a **"Test with sample values"** panel that tests the draft in the editor without saving.
- **Reference formats** (`/qa/settings/references/`): a sample per format, a dual-control change form, and a mod-97 check tool.
- **Workflows** (`/qa/settings/workflows/`): **read-only**; stages, moves, guards, and a callout listing the only moves into each success stage.
- **Notification templates** (`/qa/settings/templates/`): filters; an optimistic on/off switch (rolls back on failure); editing; a sandboxed preview.
- **Pending confirmations** (`/qa/pending/`): status tabs, search, filters for action, who started it and age; a detail page with a before/after diff, history, and a Confirm/Reject card that uses `NBTEConfirm`. It is never optimistic.

**Portal (`qa.nbte.gov.ng`, own shell):** sign in, forgot password, set password (one-time link with its own token salt and lifetime `qa.credentials.link_valid_hours`), forced password change, the empty dashboard (with institution and resource-person variants) and account settings. The top bar's section tabs become a bottom tab bar on phones. It includes an amber impersonation band and a maintenance banner, plus portal 403, 404 and 500 pages.

**Files:** `qa_core/views.py`, `qa_core/portal_views.py`, `qa_core/portal_forms.py`, `qa_core/portal_tokens.py`, `qa_core/ui.py`, `qa_core/permissions.py`, `qa_core/context_processors.py`, `qa_core/urls.py`, `qa_core/templatetags/qa_tags.py`, `templates/qa_core/**`, `templates/qa_portal/**`, `templates/partials/sidebar_qa.html`, `templates/partials/sidebar.html` (one include line), `templates/emails/qa_*.html`, `static/css/qa.css`, `static/css/qa-portal.css`, `static/css/tokens.css` (`--portal-accent` and `--impersonation-band` aliases only), `static/js/qa.js`, `nbte_ict/urls.py` (`/qa/`).

**Verify:** `python -m pytest qa_core/tests/test_interaction_contract.py`, then the manual checklist in PHASE1_TEST_PLAN.md.

## 8. `seed_startup` (§15)

**What:** `python manage.py seed_startup [--with-demo] [--reset-demo] [--dry-run] [--i-understand-production]`.
- It runs `seed_roles --sync`, `seed_config`, `seed_departments`, `seed_default_groups` and `seed_nbte --with-lgas` first.
- It then creates 13 roles with QA grants (additive), 12 groups with descriptions, the missing departments (it reuses an existing department under an alias code rather than duplicating it), the 27 settings, 9 reference formats, 4 templates, 4 rules, 2 action policies, and 2 published flows (payment with the v1.1 vocabulary, and pending action).
- It is idempotent: a second run prints "Changes this run: 0".
- `--with-demo` creates the five demo accounts and *Demo Polytechnic, Kaduna (DMP)*, all with `must_change_password=True`, prints each password once, and tags everything in `DemoTag`.
- `--reset-demo` deletes exactly the tagged rows in one transaction, and is refused when `DEBUG` is false unless `--i-understand-production` is also given.

**Files:** `qa_core/management/commands/seed_startup.py`, `qa_core/seed_data.py`.

**Verify:** `python manage.py seed_startup --with-demo && python manage.py seed_startup` (second run: "Changes this run: 0"), then `python manage.py seed_startup --reset-demo`.

## 9. Tests

There are 182 new tests in 12 files under `qa_core/tests/`; see PHASE1_TEST_PLAN.md. Result of the full suite:

| | Passed | Failed | Notes |
|---|---|---|---|
| Baseline (code as received) | 442 | 15 | All 15 are in `review_checks/test_review_findings.py` |
| After Phase 1 + your 18 Oct update + memo fix | 721 | 15 | **The same 15 tests**, unchanged. No regression |

## 10. Bugs found and fixed while testing

- **Formula evaluator:** `//`, `%`, `ceil()` and `floor()` raised `TypeError` (a `decimal.Context.to_integral_value` misuse). They are fixed, and the evaluator now converts any stray arithmetic or type error into `FormulaError`. The 1,000-expression fuzz test caught this.
- **Reference generator under SQLite:** lock errors during 20-way contention. The retry is now time-based and also covers the format lookup. Production PostgreSQL is unaffected.
- **Template preview:** escaping of admin-authored subject and body. The email preview renders in a sandboxed `<iframe srcdoc>`.
- **`qa.js`:** warning toasts used the wrong class (`toast-warning` instead of `toast-warn`); focus and announcements now also work after row-level (outerHTML) swaps.

## Open-question defaults that Phase 1 applies

| Q | Applied as |
|---|---|
| Q-02 | `Institution.code` validator: 3–6 uppercase letters or digits; never changes once issued |
| Q-04 | Fee formulas read the first Annex A figure as Private and the second as Public; `qa.fee.qa_request_processing.confirmed = false` |
| Q-07 / Q-08 | Payment flow: only `final_confirm` (role from `qa.payment.final_approver_role` = `qa-finance-director`) reaches `confirmed`; the locked key documents that provider reports are evidence only |
| Q-12 | Dual-control settings need a second officer of rank ≥ 50 holding `edit_qa_dual_control_settings` (Directors and the Director of Finance) |
| Q-13 | `qa.impersonation.max_minutes = 30`, `qa.impersonation.allow_write = false`, both dual control; the portal shows an amber read-only band during impersonation (the start flow ships in Phase 2) |
| Q-14 | Same department, unless the confirmer holds `qa_core.confirm_any_department` (ES role, "QA Senior Confirmers" group); expiry 72 h |
| Q-15 | The portal inherits the NBTE design system with its own simpler shell, branded "NBTE Quality Assurance Portal" |

## Deviations to note

- `qa.officer` (role `officer`) is also placed in the seeded **QA Settings Admin** group by the demo. §12 gives the officer role no settings permission, but interactive check 6 has the officer edit a fee formula.
- Line endings: `tasks/services.py` and the other existing files were CRLF in the original; they are kept CRLF in the delivered copy.
