# Final checklist — Phase 13 verification

Run on 16 October 2026 against this exact code (Python 3.13, Django 6.1, openpyxl 3.1.5, SQLite).

| # | Check | Result | Detail |
|---|---|---|---|
| 1 | `python -m compileall .` | **PASS** | No syntax errors. |
| 2 | `python manage.py check` | **PASS** | “System check identified no issues (0 silenced).” |
| 3 | `python manage.py makemigrations --check --dry-run` | **PASS** | “No changes detected.” |
| 4a | Fresh SQLite: migrate forward → reverse 0009, 0008, 0007, 0006 one at a time → forward again | **PASS** | Every step “OK”. |
| 4b | Same round trip on a **copy of the shipped `db.sqlite3`** | **PASS** | `ICT-001` keeps “Senior Officer” as text after reversing to 0006 and as a Designation after migrating forward again. |
| 5 | `seed_roles --sync` then `seed_nbte`, twice | **PASS** | First run: 17 departments (3 existing matched), 37 states, 2 staff states, 117 designations, 116 ladder links. Second run: “Seed complete — no changes (already up to date).” |
| 6 | Full test suite | **PASS** (no new failures) | Baseline before this work: 339 passed, 15 failed, 1 xfailed. Now: **401 passed**, 15 failed, 1 skipped, 1 xfailed. The 15 failures are the same `review_checks/test_review_findings.py` tests as before (they assert the original review findings and were failing in the baseline). +62 new tests (staff/test_nominal_roll_importer.py, test_promotion.py, test_query.py, test_export.py, test_template_download.py). The 1 skip is the real-file test, which needs `NBTE_NOMINAL_ROLL_XLSX` — run with it: **passed**. |
| 7 | `git diff --stat` scope | **PASS, with documented exceptions** | 83 files. `staff/` (31), `templates/` (18: new `templates/staff/nominal_roll|promotions|query/`, edits to `staff/detail.html`, `staff/form.html`, and `partials/sidebar.html` — the sidebar lives there, not in base.html), `static/` (4: `css/nominal-roll.css`, `js/nominal-roll.js`, `js/promotions.js`, `js/query-builder.js`), `SETUP.md`, `README.md` (+1 line), the deliverable docs and `docs/nominal-roll/screenshots/`. Outside staff: `core/search.py`, `messaging/memos_views.py`, `messaging/memo_documents.py` (one line each — they used `designation` as text; required by the FK change) and `core/test_files.py` (one test line, same reason). |
| 8a | Real Excel imported end-to-end on a copy of the current DB | **PASS** | Staff before: 6 · after: 533. 527 new, 0 updated, 2 skipped (FILE NO 1487 held by two people, on two sheets), 12 date warnings, 7 other warnings, 82 people only on older sheets. Second run: 0 new, 0 updated, 527 unchanged. |
| 8b | Every department, designation and state from the file exists | **PASS** | All 33 department names resolved (20 seeded + 13 created), all 296 designation spellings matched to the 117 seeded designations (0 created), all states of origin present. |
| 8c | Spot checks | **PASS** | **1413 JABIR JIBRIL ABDULKADIR** — M · KANO · MADOBI · SNR. PROG. OFFR. · GL 9 · ICT · born 10 Jun 1996 · first appt 17 Mar 2020 · joined Board 28 Sep 2023 · present appt 1 Jan 2025 · confirmed 17 Mar 2022 · Confirmed · qualifications as in the file. **1270 BUGAJE IDRIS MUHAMMAD PROF.** — EXECUTIVE SECRETARY · 99 · ES OFFICE · born 7 Mar 1958 (from “7th Mar. 58”). **1018** — surname ABDULHAMEED from the October sheet (older sheets say DAN-LAWAN; newest wins). |
| 9 | Export vs source (October sheet, field by field) | **PASS** | All 15 fields reappear for every person: 0 mismatches in surname, other name, sex, state, LGA, designation, grade, all dates, remarks and qualifications. Expected differences only: department names map onto the existing “Human Resources”/“Finance” records (91 rows), and one impossible birth date (1583, 1 Oct 2026) is deliberately not stored. |
| 10 | UI: 375 px and 1440 px, light and dark, no horizontal scroll, no JS errors | **PASS** | Nominal roll (all tabs), import preview, promotions + dialog, promotion history, advanced query, saved queries, staff detail and form. Screenshots in `docs/nominal-roll/screenshots/`. |
| 11 | Contrast AA | **PASS** | Every text element on the 8 new pages measured in light and dark: 0 below 4.5:1 (3:1 for large text). |
| 12 | `prefers-reduced-motion` | **PASS** | Panel/row animations and transitions switched off in `nominal-roll.css`. |
| 13 | Rules: no inline `style=""`, no inline event handlers, no `confirm()`/`alert()`, no new pip packages, no build step | **PASS** | grep clean; openpyxl was already installed. |
| 14 | Permissions enforced server-side | **PASS** | Every view checks its permission (tests assert 403 for import, export, template, promote, query export, shared queries). All staff lists go through `scope_to_department`. |
| 15 | Audit | **PASS** | Import (per staff + automatic before/after), export (`export_nominal_roll`), promotion / rollback, report downloads, saved-query changes. |
| 16 | Existing core staff import/export still works | **PASS** | Its Designation column now maps text to the lookup; existing tests pass. |
| 17 | `birth_month`/`birth_day` kept | **PASS** | Unchanged fields, “Legacy” section in the admin; filled from date of birth when empty. |
| 18 | Sheet1/2/3 never imported | **PASS** | Skipped by name and reported as empty. |

## Known limitations / follow-ups
* The 82 people found only on older sheets are imported by default (as the brief asks); HR should
  untick the old sheets or set those people's state afterwards (`IMPORT_PLAYBOOK.md`).
* CONTEDISS ranges are the ranges seen in the file (e.g. PROG. OFF. I = 7–8), so auto-fill uses the
  lowest; adjust per designation in the admin where HR wants a different starting grade.
* Phase 1 asked for a default rank on the Role model: `seed_roles --sync` already applies each role's
  rank, so the `accounts` app was not changed.
* Tests live as `staff/test_*.py` (not `staff/tests/`) because `staff/tests.py` already exists and a
  package of the same name would shadow it.
* The real HR workbook is not bundled (it holds personal data); its test runs when
  `NBTE_NOMINAL_ROLL_XLSX` points at it.

## Follow-up release (16 Oct 2026): accounts, one staff import/export, staff file, email

| # | Check | Result | Detail |
|---|---|---|---|
| F1 | Sign-in accounts on import (3 password schemes) | **PASS** | Real file onto a copy of the shipped DB: 526 accounts made in 12.6 s (whole import). `jabir1413` works for 1413; must change at first sign-in; Staff role + department set; linked to the existing record (no duplicate). |
| F2 | Email is the username | **PASS** | Staff with an EMAIL sign in with it; staff without sign in with the file number; adding an email later switches the login to it (test). Login form accepts “Email or file number”. |
| F3 | Welcome emails actually sent over SMTP | **PASS (local relay)** | With the app pointed at a local stand-in for Brevo's relay that requires STARTTLS and login, the import queued 2 welcome emails (only people with an email), the engine delivered both over TLS with authentication (`brevo_smtp`), and the email log shows *sent*. Real Brevo/Gmail could not be reached from the build machine (no internet access to them) — run `manage.py check_email --send --to <you>` on your server; your existing email log shows earlier messages delivered through Brevo, so your `.env` setup has worked before. |
| F4 | `check_email` | **PASS** | As shipped (no `.env`): reports the localhost sender and missing credentials. With the relay: DNS, TCP, STARTTLS, login OK, test message delivered and received. With a wrong key: login failure reported. |
| F5 | One staff import/export | **PASS** | Administration → Import / Export → Staff opens Staff import & export (page, HTMX panel, template and export links). The page lives under Administration and is visible to HR with only the import/export permission. |
| F6 | Existing account records merged | **PASS** | USR-00001 (Muhammad Bugaje) became FILE NO 1270 with the nominal-roll details; no second BUGAJE record. |
| F7 | Staff file | **PASS** | Timeline shows promotions, transfers, state changes, items given/returned, messages/notifications sent, imports, account creation; *Items held now*; filters; hidden from colleague-level viewers. Contrast AA 0 failures light/dark; 375 px and 1440 px clean. |
| F8 | Transfers | **PASS** | Transfer button (permission `transfer_staff`), reason required, notification optional; edit/import moves recorded automatically. |
| F9 | Migration 0011 forward/back | **PASS** | Applied on fresh SQLite and on the shipped DB copy; `makemigrations --check` clean. |
| F10 | Tests | **PASS** | 419 passed, 15 failed (the same baseline `review_checks` tests), 1 skipped (real-file test; passes with `NBTE_NOMINAL_ROLL_XLSX`), 1 xfailed. +16 new tests (staff/test_staff_accounts.py, staff/test_staff_file.py, notifications/test_check_email.py). |

Files changed outside `staff/` in this follow-up, and why: `accounts/forms.py`, `accounts/backends.py`,
`templates/accounts/login.html` (sign in with email **or** file number), `accounts/signals.py` (link the
new account to the existing staff record instead of creating a second one), `core/importexport_views.py`
and `templates/core/import_export.html` (the Staff module opens the new page),
`templates/partials/sidebar.html` (Administration → Staff import & export), and the new
`notifications/management/commands/check_email.py`.

## Memo routing review (16 Oct 2026)

- [x] `messaging/test_memo_routing.py`: 23 scenarios. Covers optional skips recorded; line head from office/division head; director found four ways; rank and self-skip rules; parked memos moving on after the step is fixed, made optional or deleted; admin acting and re-check; return/resubmit; reject needs a comment; rules by target role and designation; draft expected chain; urgent first; old links and deleted memos; `seed_memo_demo`.
- [x] `seed_memo_demo` on a copy of `db.sqlite3`: run twice (no duplicates), `--remove` (back to the original counts, staff records of demo accounts removed), `--dry-run` (nothing saved).
- [x] `messaging 0007` reversed to 0006 and re-applied on a copy with demo memos.
- [x] `DEBUG=False runserver`: CSS/JS served (200), styled 404 page, `/messaging/memos/<id>/` redirects, deleted memo → memo list with a message.
- [x] Theme (Playwright, emulated device setting): follows light→dark→light; a manual toggle lasts until the device setting changes; toggle `aria-pressed` correct in every step; no JS errors.
- [x] `makemigrations --check`: no changes.
