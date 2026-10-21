# Phase 2 screenshots (18 Oct 2026)

Captured with Chromium on the dev server: internal host `localhost:8000`, portal host `qa.localhost:8000`. Desktop shots are 1440 px wide; mobile shots are 390 px (iPhone-sized, 2× pixel density). Dark-mode shots follow the operating-system setting.

## Top level — the finished screens

| Group | Files |
|---|---|
| Public site: the **Quality Assurance Portal** button | `public_landing_qa_button_light/dark/mobile.png` |
| Internal: institution management | `internal_institutions_list`, `internal_institution_*_tab`, `internal_action_launcher_update_category`, `internal_action_update_category_form`, `internal_bulk_import` |
| Internal: applications and clearance | `internal_applications_list`, `internal_application_detail` |
| Internal: Finance (QA) | `finance_item_confirmed(_dark)`, `finance_bills`, `finance_statements`, `finance_unmatched_lines`, `finance_webhook_log`, `finance_queue_empty_state` |
| Internal: Settings → QA | `internal_settings_fees`, `internal_settings_payment_methods`, `internal_settings_documents`, `internal_settings_lookups`, `internal_qa_email_outbox` |
| Portal | `portal_dashboard_*`, `portal_application_tracker_*`, `portal_application_mobile`, `portal_documents_*`, `portal_payments_light`, `portal_payment_confirmed_*`, `portal_payment_mobile`, `portal_institution_profile_light`, `portal_activity_light`, `portal_self_registration`, `portal_login_with_register_link` |
| PDFs | `payment_slip_pdf.png` (Appendix C slip: reference, Code 128 barcode, QR, amount in words, bank details) and `payment_advice_pdf.png` |

## `walkthrough/` — the interactive walkthrough, in order

Numbered as in PHASE2_TEST_PLAN.md: Add TVET (02–06), credentials and first sign-in (07–11), documents (12–13), application submitted (14–16), four clearances (17–19), Paystack simulator and the "awaiting Finance confirmation" state (20–24b), Finance first pass and the Director's confirmation (25–30), advice, programme selection and mobile views (31–35), bulk import rejected then applied (36–41).
