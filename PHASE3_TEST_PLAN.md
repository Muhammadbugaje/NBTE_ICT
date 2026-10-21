# NBTE QA System — Phase 3 test plan

## Set-up

```bash
pip install -r requirements-dev.txt        # no new dependency in Phase 3
python manage.py check                     # → System check identified no issues
python manage.py makemigrations --check --dry-run   # → No changes detected
```

## Automated tests

Phase 3 tests: `accreditation/tests/test_qa_requests.py` (29) and `accreditation/tests/test_ssq.py` (32), fixtures in `qa_core/tests/phase3_fixtures.py` (an approved institution with an administrator, a chief executive, an academic-department account and programmes inside/outside the window; a second institution in another department; the QA fee confirmed).

```bash
python -m pytest accreditation/tests/test_qa_requests.py accreditation/tests/test_ssq.py   # Phase 3
python -m pytest qa_core/tests institutions/tests accreditation/tests/test_onboarding.py payments/tests   # Phases 1–2
python -m pytest                                                                         # the whole project
```

| Requirement (Phase 3 prompt / PHASES.md) | Tests |
|---|---|
| **Staff log in to the portal and see the Staff Dashboard** | `test_staff_land_on_the_staff_portal_dashboard`, `test_portal_login_form_accepts_staff` (test_external_guard), `test_staff_portal_dashboard`, `test_staff_dashboard_lists_work_with_internal_links` |
| **Staff cannot see institution data on the portal** (no names, no programmes; institution pages send them back to their dashboard) | `test_staff_portal_dashboard` |
| **Institution users cannot get the staff dashboard** | `test_institution_users_never_get_the_staff_dashboard` |
| The Phase 1 rule still works when switched off (`qa.portal.staff_sign_in_enabled = false`) | `test_staff_redirected_off_the_portal_when_switched_off`, `test_portal_login_form_refuses_staff_when_switched_off` |
| **SSQ schema validator** (valid seeds; ten kinds of invalid schema) | `test_seeded_templates_are_valid`, `test_schema_validator_rejects[…]` (10), `test_invalid_template_cannot_be_published` |
| **Submitting creates an immutable Submission** (save/delete refused, checksum, SSQ locked) | `test_submit_creates_an_immutable_submission` |
| Snapshot freezes catalogue names and quantities (a later catalogue change does not alter it) | `test_snapshot_freezes_catalogue_names_and_quantities` |
| **Prefill only from accepted documents — never drafts** (submitted, returned, superseded ignored) | `test_prefill_uses_only_accepted_documents`, `test_reuse_refuses_documents_that_are_not_accepted` |
| Evidence upload | `test_upload_evidence` |
| **Cancel only in the permitted stages (R-25)** | `test_cancel_allowed_in_r25_stages[draft/fee_due/payment_pending_confirmation]`, `test_cancel_allowed_when_submitted` (flow data), `test_cancel_refused_after_the_fee_is_confirmed` |
| Drop / replace only in the R-25 stages; last programme cannot be dropped | `test_drop_and_replace_in_r25_stages[…]` (3), `test_cancel_refused_after_the_fee_is_confirmed` |
| **Catalogue binding: quantities only, names never overwritten** | `test_catalogue_fields_take_quantities_only`, `test_catalogue_names_cannot_be_posted_through_the_portal`, `test_catalogue_revision_writes_a_new_version` |
| **Requirement 4 six-month window** (and the setting drives it) | `test_six_month_window_requirement_4`, `test_window_follows_the_setting` |
| **Department return path** (assigned sections only; admin locked meanwhile; department sends back; admin submits; recall) | `test_department_return_path`, `test_admin_can_recall_from_department`, `test_department_user_cannot_autosave_other_sections` |
| **Versioning — superseded, never overwritten** (SSQs, templates, catalogue) | `test_return_then_revise_supersedes_never_overwrites`, `test_published_template_is_never_edited_new_version_retires_old`, `test_catalogue_revision_writes_a_new_version` |
| **QA case created automatically when every SSQ is in** | `test_qa_case_created_when_every_ssq_is_submitted` |
| Fee for the programme count; unconfirmed fee waits and can be retried; re-issue after a drop | `test_submit_issues_the_qa_fee_for_the_programme_count`, `test_unconfirmed_fee_waits_at_fee_due_and_can_be_retried`, `test_drop_at_fee_due_reissues_the_unpaid_bill` |
| New programmes → programme clearance (NTIRD only) and its return path | `test_new_programme_goes_through_clearance` |
| **Add Programme to QA, Cancel Request, Drop Programmes go live** | `test_add_programme_to_qa_action_is_live`, `test_add_programme_action_refused_past_fee_due`, `test_cancel_request_action_uses_r25`, `test_drop_programmes_action_takes_the_programme_off_open_requests` |
| Who may submit (`qa.ssq.submit_roles`), completeness, review permissions and scope | `test_only_submit_roles_can_submit`, `test_incomplete_ssq_cannot_be_submitted`, `test_reviewer_needs_permission_and_scope`, `test_department_account_cannot_manage_requests`, `test_officer_without_publish_permission_cannot_publish` |
| **Autosave** (partial save keeps good answers, field errors out-of-band, reload shows the saved answer, section swap is a partial) | `test_autosave_persists_across_reload` |
| Queries: raise (permission), notify, respond with attachment, other institution 404, close, closed query refuses answers | `test_query_raise_respond_close`, `test_internal_query_form_and_review_screens` |
| Tenant isolation (portal 404 for another institution; internal 404 out of department) | `test_other_institution_gets_404_on_the_portal`, `test_internal_screens_are_scoped`; the Phase 1 layer-3 test walks every new `qa/…<int>` route; layer 2 audits the new view modules |
| Screens: new request, submit | `test_portal_new_request_and_submit_screens`, `test_internal_query_form_and_review_screens` |

**Results recorded on 18–19 Oct 2026:** Phase 3 **61 passed**; Phases 1–2 **292 passed**; whole project **892 passed, 15 failed** (the same 15 pre-existing `review_checks` failures as before Phase 1), 1 skipped, 1 xfailed. Details in PHASE3_DONE.md.

## The interactive walkthrough (done in a real browser, 18 Oct 2026)

Fresh database: `migrate` → `seed_startup --with-demo`; dev server on `localhost:8000` (internal) and `qa.localhost:8000` (portal); Playwright/Chromium at 1440 px and 390 px. Screenshots: `PHASE3_UI_SCREENSHOTS/walkthrough/` (numbers below). No browser console error and no 5xx response during the run.

| Step (prompt) | What was done | Result |
|---|---|---|
| 1 | `migrate`, `seed_startup --with-demo` | 7 QA types, 2 templates published, 16 catalogue lines, 2 flows; demo passwords printed once |
| 2 | Portal sign-in as `qa.officer@demo.nbte.local` | **Staff Portal Dashboard** with **Go to Internal System** (`01`) |
| 3 | Portal as `admin@demo-poly.local` → QA requests → New → Reaccreditation → two programmes → create; *Drop…* HND EEE ("Department not ready this session"); declaration → Submit | Draft `NBTE/QAR/2026/DMP/00001` → *QA processing fee due*, ₦200,000 bill (`02`–`06`) |
| 4 | `qa.finance` proposes a waiver; `finance.director` confirms it | Bill waived → fee confirmed → **SSQ in progress**, SSQ v1 opened with programme data prefilled (`07`–`10`) |
| 5 | SSQ: fill *Programme particulars* → status *All changes saved* → **reload** → answers still there; fill every section (tables with *Add row*, catalogue quantities), **upload** a PDF on *Previous visit*, declaration → **Submit SSQ** | Submission snapshot taken; request → *in the QA workflow*, QA case created (`11`–`15`) |
| 6 | Internal as `qa.officer` → **SSQ review queue** → open → **Raise query** | Snapshot verified, shortfalls highlighted; `NBTE-QRY-2026-00001` sent (`16`–`20`) |
| 7 | Portal as the institution → dashboard shows the query → **respond** with an attachment | Query *answered*; officer notified (`21`–`22`) |
| extra | Settings → SSQ templates (*New version* → builder → field editor), Curriculum catalogue; Institutional Administration SSQ with **prefilled governance data** (head of institution, registrar, re-used accepted organogram); phone layout; department account dashboard | `23`–`31` |

Defects found and fixed during the walkthrough and tests are listed in PHASE3_CHANGELOG.md §10.

## Manual checks still recommended

* DPAP / Curriculum Office review of the two SSQ templates' wording (they are a starting point; refine as version 2 in the builder).
* Real QA fee amounts and their confirmation (Settings → Fees) before go-live.
* Keyboard-only and screen-reader pass over the SSQ workspace (built to the §20 contract; status changes are announced through `role="status"`).
* Very large SSQs (hundreds of table rows) on slow connections.
