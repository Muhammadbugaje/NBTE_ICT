# NBTE QA System — Phase 2 definition of done

Checked against the Phase 2 prompt and PHASES.md "Phase 2 — Institutions, onboarding (SOP-01) and payments", including the clauses that end every phase. Evidence is the command, test or walkthrough step that proves each item. Dated 18 Oct 2026.

## Phase 2 requirements

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | "Quality Assurance Portal" button on the public landing page and a footer link, to `settings.QA_PORTAL_URL` | **PASS** | `templates/public/landing.html` (hero button, note, footer *Platform* link, mobile menu); `qa_core.context_processors.qa_portal_link`; screenshots `public_landing_qa_button_*` |
| 2 | institutions: full models; every §6 action through ActionPolicy/PendingAction | **PASS** | `institutions/models.py`, `institutions/handlers.py` (19 live handlers + 5 shown disabled until Phases 3–5); `test_actions.py` |
| 3 | Bulk import / export with openpyxl: rigorous, atomic, idempotent; new rows proposed | **PASS** | `institutions/importexport.py`; `test_import.py`; walkthrough steps 11–12 |
| 4 | accreditation: ApplicationCycle, Application, DepartmentalClearance; parallel clearance gate on the FlowDefinition engine; all four must clear, any one returns to draft | **PASS** | `accreditation/`, `qa_core/flow.py` (parallel gates, join), `onboarding` flow in `seed_data_phase2.py`; `test_onboarding.py`; walkthrough steps 5–6 |
| 5 | Document requirements differ for private and public institutions | **PASS** | `DOCUMENT_REQUIREMENTS` (editable under Settings → QA → Documents); `test_document_requirements_differ_by_ownership[private/federal/state]` |
| 6 | payments: all models; Remita, Paystack, bank transfer, offline | **PASS** | `payments/models.py`, `payments/providers/`; Settings → QA → Payment methods |
| 7 | Slip PDF (Appendix C): QR, Code 128, amount in words, bank details, locked reference | **PASS** | `payments/pdfs.py`; `test_slip_text_has_words_reference_and_bank`, `test_slip_qr_verifies`, `test_slip_qr_and_barcode_decode_from_the_pdf`; `payment_slip_pdf.png` |
| 8 | Finance queue: first pass then Director; CSV statement upload and manual matching | **PASS** | `payments/views.py` (queue, statements, unmatched, line match); `test_director_confirmation_flow`, `test_csv_parser_*`, `test_statement_*`; walkthrough steps 8–9 |
| 9 | Payment Advice PDF on confirmation | **PASS** | `services.generate_advice`; `test_director_confirmation_flow`; walkthrough step 10 (PDF text checked) |
| 10 | **Non-negotiable: a webhook never auto-confirms** | **PASS** | Only `payments.services.final_decision` reaches `confirmed` (role + permission + not the first-pass officer); the locked setting `qa.payment.provider_confirmation_is_evidence_only`; `test_paystack_webhook_only_reports_paid`, `test_remita_webhook_only_reports_paid`, `test_sweep_never_confirms`, `test_statement_auto_match_opens_verification_but_never_confirms`; walkthrough step 7 |
| 11 | UI: portal pages (profile, contacts, documents, users, application tracker, payments) and internal pages (institution actions, clearance queues, Finance queue), §20 contract | **PASS** | `templates/qa_portal/`, `templates/institutions/`, `templates/accreditation/`, `templates/payments/`; HTMX partials, skeletons, URL filters, two-mode pagination, mobile tab bar; 87 screenshots in `PHASE2_UI_SCREENSHOTS/` |
| 12 | Settings: every `qa.fee.*`, `qa.payment.*`, `qa.ownership_change.*` key seeded by `seed_startup` | **PASS** | `qa_core/settings_catalogue.py` (`SETTINGS_PHASE2`); 47 QA settings after seeding |
| 13 | E-mails logged to the console, not sent | **PASS** | `qa_core/mail.py` (`QA_EMAIL_DELIVERY=console`), **QA e-mail outbox** page |
| 14 | Interactive walkthrough (8 steps) | **PASS** | PHASE2_TEST_PLAN.md "The interactive walkthrough"; `PHASE2_UI_SCREENSHOTS/walkthrough/` |

## Clauses at the end of every phase

| Item | Status | Evidence |
|---|---|---|
| `python manage.py check` is clean | **PASS** | "System check identified no issues (0 silenced)" |
| `makemigrations --check` | **PASS** | "No changes detected" |
| Migrations reverse cleanly | **PASS** | `payments zero` → `institutions 0001` → `accreditation zero` → `qa_core 0001` → `migrate` → `seed_startup`, on a scratch copy (PHASE2_MIGRATIONS.md) |
| The Phase 1 demo database upgrades | **PASS** | `qa_phase2_demo.sqlite3` was built by migrating and re-seeding `qa_phase1_demo.sqlite3`; all 11 demo passwords verified |
| The Phase 2 tests are green | **PASS** | 108 passed (`institutions/tests`, `accreditation/tests`, `payments/tests`) |
| The full existing test suite is green | **PASS (no regression)** | 829 passed, 1 skipped, 1 xfailed. The 15 failures are the same 15 `review_checks/test_review_findings.py` tests that fail on the code as received |
| The scoping enforcement test (§4) is green, now covering every Phase 2 model, view and route | **PASS** | `qa_core/tests/test_scoping_enforcement.py` |
| `seed_startup` re-runs with no changes | **PASS** | `test_seed_startup.py::test_second_run_changes_nothing` |
| `--reset-demo` leaves zero demo rows | **PASS** | `test_seed_startup.py::test_reset_demo_removes_exactly_the_tagged_rows` (11 users, both demo institutions and everything hanging off them) |
| Phase 1 not broken; backups app, SSQ templates, workflows untouched | **PASS** | Phase 1 tests green after the five documented updates of pinned counts (PHASE2_CHANGELOG.md); no file in `workflow/` or any backups/SSQ module changed |
| No new dependency | **PASS** | `requirements*.txt` unchanged (openpyxl, reportlab, qrcode were already present) |
| Your `db.sqlite3` untouched | **PASS** | byte-identical to the copy you sent (`cmp`) |

## Not done / for you to decide

* **Live provider keys.** Remita and Paystack are implemented against their documented APIs and exercised end to end through the built-in simulator; they have not been called against the real sandboxes (this environment has no outside network). Put the keys in the environment on a staging server and run one small payment of each kind before switching them on in production.
* **NBTE's real bank account.** Seeding creates a placeholder account; bills wait at *issued* until Finance enters the real account and the Director of Finance confirms it (Settings → QA → Fees & bank accounts). The demo database has a clearly marked demo account.
* **`qa.fee.qa_request_processing` is unconfirmed** (SOP-02, Phase 3): set the amounts and switch `qa.fee.qa_request_processing.confirmed` on when NBTE approves them.
* Celery beat now schedules `payments.sweep`; if you run without Celery, schedule `python manage.py qa_payments_sweep` with cron every 15 minutes.
