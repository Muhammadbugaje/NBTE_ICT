# NBTE QA System — Phase 2 test plan

## Set-up

```bash
pip install -r requirements-dev.txt        # no new dependency was added in Phase 2
python manage.py check                     # → System check identified no issues
python manage.py makemigrations --check --dry-run   # → No changes detected
```

Two slip tests use optional tools when they are installed and skip themselves when they are not: the text check reads the PDF with `pypdf`, and the decoding check uses `pdftoppm` (poppler-utils) and OpenCV. None of them is a project dependency.

## Automated tests

Phase 2 tests live in `institutions/tests/`, `accreditation/tests/` and `payments/tests/`. They share the Phase 1 fixtures and `qa_core/tests/phase2_fixtures.py`.

```bash
python -m pytest institutions/tests accreditation/tests payments/tests    # Phase 2
python -m pytest qa_core/tests                                            # Phase 1 (still green)
python -m pytest                                                          # the whole project
```

| Requirement (Phase 2 prompt) | Where | Tests |
|---|---|---|
| **Every §6 action: initiate, pending (nothing changes), confirm, applied** — Update State & LGA, Drop Programmes, Wipe Requests, Cancel Request, Update Contact, Update Address, Update Category, Update Name, Update Ownership, Delist, Suspend Portal Access | `institutions/tests/test_actions.py` | `test_initiate_pending_confirm_applied[…]` (11) |
| … **reject** (remark required, nothing changes) | same | `test_reject_changes_nothing[…]` (11) |
| … **stale** (record changed meanwhile → fails safely, never applied) | same | `test_stale_fails_safely[…]` (11) |
| … **expiry** | same | `test_expiry[…]` (11) |
| Add TVET (proposed, NTIRD, inactive admin, code allocated), Approve (department move), Relist, Restore portal access, Send Login Credentials (single step, link not password), disabled later-phase actions, scope | same | `test_add_tvet_*`, `test_approve_moves_department`, `test_relist_after_delist`, `test_delisted_institution_cannot_sign_in`, `test_restore_portal_access`, `test_send_credentials_*`, `test_later_phase_actions_are_disabled`, `test_officer_outside_scope_cannot_initiate` |
| **Update Category department transfer** (shown on the confirm screen; scope moves) | same | `test_update_category_transfers_department`, `test_update_category_of_proposed_institution_stays_in_ntird` |
| **Update Ownership document re-evaluation** (newly required kinds, application held) | same | `test_update_ownership_re_evaluates_documents_and_holds_application` |
| **Import: valid** (second officer, all proposed, NTIRD) | `institutions/tests/test_import.py` | `test_valid_sheet_waits_for_second_officer_then_applies`, `test_import_screen_flow` |
| **Import: invalid with full rollback** (one bad row rejects everything; a failure during apply rolls back every row; status column refused) | same | `test_one_bad_row_rejects_everything`, `test_failure_during_apply_rolls_back_every_row`, `test_status_column_cannot_change_status`, `test_stale_batch_is_released_for_resending` |
| **Import: round trip** (export → re-import changes nothing) | same | `test_round_trip_export_reimport_changes_nothing` |
| **Parallel gate**: four branches, waits for all, any one return → draft (siblings closed), round 2, only the branch department decides, documents locked after submission | `accreditation/tests/test_onboarding.py` | `test_submit_fans_out_*`, `test_gate_waits_for_all_four`, `test_any_one_return_*`, `test_resubmission_opens_round_two`, `test_only_the_branch_department_can_decide`, `test_documents_locked_after_submission`, `test_cannot_submit_until_*` |
| **Private vs public document requirements** | same | `test_document_requirements_differ_by_ownership[private/federal/state]` |
| **Fee snapshot** (frozen at issue; a later formula change never alters the bill) | same | `test_institutional_fee_snapshot_is_frozen` |
| Confirmed fee moves the application to programme selection | same | `test_confirmed_fee_moves_application_to_programme_selection` |
| **Remita and Paystack webhooks only ever produce provider_reported_paid**; bad signature rejected and logged; verify wins; failed payment | `payments/tests/test_payments.py` | `test_paystack_webhook_only_reports_paid`, `test_remita_webhook_only_reports_paid`, `test_paystack_webhook_with_bad_signature_is_rejected`, `test_signed_webhook_whose_verify_disagrees_does_not_pay`, `test_failed_online_payment_returns_to_awaiting_payment`, `test_sweep_never_confirms` |
| **Director confirmation flow** (first pass then Director; officer refused; Director without the permission refused; first-pass Director cannot also decide; reject → pay again; receipt required for provider payments) | same | `test_director_confirmation_flow`, `test_director_without_permission_is_refused`, `test_first_pass_director_cannot_also_decide`, `test_reject_lets_the_institution_pay_again`, `test_provider_payment_needs_receipt_before_confirmation` |
| **CSV parser and mod-97** (credit rows only, missing columns named, references in any narration format, bad check digits, typo candidates, auto-match opens verification but never confirms) | same | `test_csv_parser_*`, `test_mod97_reference_checks`, `test_statement_auto_match_*`, `test_statement_line_with_bad_check_digits_*` |
| **§10.6 edge cases** (duplicate after settlement → refund, money after cancellation, overpayment, expired slip regenerated with the same reference, same proof on two bills, future payment date) | same | `test_duplicate_payment_*`, `test_money_after_cancellation_is_flagged`, `test_overpayment_is_recorded`, `test_expired_slip_*`, `test_same_proof_file_*`, `test_future_payment_date_is_refused` |
| **Host isolation** (Finance screens internal only; another institution cannot open a bill) and **proof download permissions** (owner yes, other institution 404, Finance yes, unrelated staff no) | same | `test_finance_screens_are_internal_only`, `test_other_institution_cannot_open_bill`, `test_proof_download_permissions` |
| **Bank-transfer-only fallback** (both online methods off → only bank transfer offered, online start refused) and providers off without keys in production | same | `test_bank_transfer_only_fallback`, `test_online_methods_off_without_keys_in_production` |
| Bills wait for a real bank account; unconfirmed fee cannot be billed | same | `test_bills_wait_for_a_real_bank_account`, `test_unconfirmed_fee_cannot_be_billed` |
| **Slip content: amount in words, QR verifies, barcode decodes** | same | `test_slip_text_has_words_reference_and_bank`, `test_slip_qr_verifies` (verification page, tampered token 404), `test_slip_qr_and_barcode_decode_from_the_pdf` (renders the PDF at 300 dpi, decodes the QR with OpenCV and the Code 128 bars with a small reader built on reportlab's own pattern table) |
| Scoping (Phase 1 enforcement test now covers every Phase 2 model, view and route) | `qa_core/tests/test_scoping_enforcement.py` | layer 1 (every institution-bearing model uses the scoped manager), layer 2 (source audit of all Phase 2 views/forms), layer 3 (every internal `qa/…<id>` route answers 404 to an out-of-scope officer; portal routes never 200/403 for another tenant's ids) |

**Results recorded on 18 Oct 2026:**
- Phase 2: **108 passed** (`test_actions.py` 56, `test_import.py` 7, `test_onboarding.py` 13, `test_payments.py` 32), including the slip text and barcode/QR decoding tests (pypdf, poppler and OpenCV were present here).
- Phase 1 (`qa_core/tests`): **182 passed**, after the five documented updates of pinned Phase 1 counts (PHASE2_CHANGELOG.md).
- Whole project: **829 passed, 15 failed, 1 skipped, 1 xfailed** (52 minutes). The 15 failures are the same 15 tests in `review_checks/test_review_findings.py` that fail on the code as received and failed before Phase 1; nothing else fails. Phase 1's whole-project result was 721 passed; the difference is exactly the 108 new Phase 2 tests.

## The interactive walkthrough (done in a real browser, 18 Oct 2026)

Run with `migrate` → `seed_startup --with-demo` on a fresh database, the dev server on `localhost:8000` (internal) and `qa.localhost:8000` (portal), driven with Playwright/Chromium. Screenshots are in `PHASE2_UI_SCREENSHOTS/walkthrough/`.

| Step | What was done | Result |
|---|---|---|
| 1 | `migrate`, then `seed_startup --with-demo` | 4 new migrations applied; 529 changes; demo credentials printed once |
| 2 | `qa.officer` → **Add TVET** (Zaria Institute of Technology, private polytechnic, Kaduna/Zaria) → *Send for confirmation*; `qa.director` → Pending confirmations → *Confirm* | PA-2026-000001 applied; **ZIT** created, *proposed*, handled by NTIRD; the confirm page lists every field being created (`03`–`06`) |
| 3 | `qa.officer` → ZIT → **Send Login Credentials**; set-password link read from the e-mail log; the registrar sets a password and signs in at `qa.localhost` | Dashboard shows *Start your onboarding application* (`07`–`11`) |
| 4 | Registrar → **Documents** → uploads the 7 documents (6 required + optional organogram) | 6 of 6 required in, every row *Awaiting review* (`12`–`13`) |
| 5 | Registrar → **Application → Start → Submit** (declaration ticked) | Status *Under review by NBTE departments*; four clearance cards (ASPD, Finance, Legal, PPD) pending (`14`–`16`) |
| 6 | `legal.officer`, `finance.clearance`, `planning.academic`, `planning.physical` each → Departmental clearance → *Clear* | Gate joined after the fourth; status *Institutional processing fee due*; ₦4,000,000.00 bill issued with slip v1 (`17`–`19`) |
| 7 | Registrar → Payments → bill → **Pay with Paystack** → simulator *Pay successfully* | Signed webhook + server-side verify → *Institution claims paid — awaiting Finance confirmation*; **not confirmed** (`20`–`24`) |
| 8 | `qa.finance` → Verification queue → *Recommend confirm* → *Save first pass* | Item waits for the Director of Finance (`25`–`27`) |
| 9 | `finance.director` tries *Confirm payment* | Refused until the provider receipt is uploaded (`qa.payment.receipt_required_for_provider_payments`); registrar uploads it; Director confirms (`28`–`30`) |
| 10 | Payment Advice | NBTE-ADV-2026-000002 generated (PDF verified: amount, words, references, Director's name, QR); application moved to **Programme selection**; registrar adds *ND Computer Science* (`31`–`35`, mobile views) |
| 11 | `qa.officer` → **Bulk Import** → a three-row sheet with a misspelt LGA | Rejected as a whole: *Row 2: 'Nassarawa' is not an LGA of KANO*; nothing imported (`37a`) |
| 12 | Same sheet corrected → *Check the sheet* → *Send for confirmation*; `qa.director` confirms | 3 new institutions (KTI, LSNS, BVEI), all *proposed*, NTIRD (`37b`–`41`) |

The walkthrough found and fixed five defects (listed in PHASE2_CHANGELOG.md, "Fixes found during the walkthrough"), including a bulk-import confirmation that always failed as stale.

## Manual checks still recommended

* Real Remita and Paystack keys in a staging environment (the simulator follows their documented payloads, but has not been tested against the live sandboxes from this environment, which has no outside network access).
* Printing a slip on an office printer and scanning its QR code and barcode with a phone and a bank's barcode scanner.
* Keyboard-only use and a screen reader on the portal pages (built to the §20 contract, not exercised by hand).
* 320, 768 and 1024 px widths (captured: 390 px and 1440 px, light and dark).
