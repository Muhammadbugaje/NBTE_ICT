# NBTE QA System — Phase 1 test plan

## Set-up

```bash
pip install -r requirements-dev.txt        # Django, pytest, pytest-django (no new dependency was added)
python manage.py check                     # → System check identified no issues
python manage.py makemigrations --check --dry-run   # → No changes detected
```

## Automated tests

All Phase 1 tests live in `qa_core/tests/` (182 tests, about 2 minutes).

```bash
python -m pytest qa_core/tests                       # all of Phase 1
python -m pytest                                     # the whole project (about 17 minutes)
```

| Requirement (PHASES.md, Phase 1) | File | Command |
|---|---|---|
| Formula evaluator: allowed and rejected nodes, Decimal maths, `cfg()` cycles, time and size limits, **fuzz with 1,000 random expressions** | `test_formula.py` (65) | `python -m pytest qa_core/tests/test_formula.py` |
| Reference generator: **20 threads, no duplicates**; mod-97 check digit (IBAN test vector; every single-digit typo caught) | `test_references.py` (15) | `python -m pytest qa_core/tests/test_references.py` |
| PendingAction rules: initiator ≠ confirmer, **superuser self-confirm blocked**, expiry, **stale target**, department and rank, impersonation, one open item per target | `test_pending_actions.py` (16) | `python -m pytest qa_core/tests/test_pending_actions.py` |
| Host routing: URL tables disjoint; **every internal URL is 404 on the portal host** | `test_host_routing.py` (7) | `python -m pytest qa_core/tests/test_host_routing.py` |
| `ExternalUserGuardMiddleware` both directions, the impersonation exception, portal sign-in, set-password links, forced change | `test_external_guard.py` (14) | `python -m pytest qa_core/tests/test_external_guard.py` |
| Scoping enforcement, **all three layers**, including the deliberately failing `Institution.objects.all()` fixture | `test_scoping_enforcement.py` (10) | `python -m pytest qa_core/tests/test_scoping_enforcement.py` |
| Dual-control settings write path; new value types; **the locked key cannot change through the settings page, the admin or `.save()`** | `test_settings_dual_control.py` (12) | `python -m pytest qa_core/tests/test_settings_dual_control.py` |
| Flow-engine fixture: **only `final_confirm` reaches `confirmed`**, and it rejects an actor without the final-approver role | `test_payment_flow.py` (7) | `python -m pytest qa_core/tests/test_payment_flow.py` |
| §20 contract: full page and HTMX partial for every page, URL filters, 25 rows per page, Show-all limit enforced by the server, toast headers, optimistic toggle, sandboxed preview | `test_interaction_contract.py` (16) | `python -m pytest qa_core/tests/test_interaction_contract.py` |
| `seed_startup`: first run, **idempotent second run**, `--dry-run`, demo accounts, **`--reset-demo` removes exactly the tagged rows**, refused without DEBUG | `test_seed_startup.py` (7) | `python -m pytest qa_core/tests/test_seed_startup.py` |
| `tasks`: `task_state_changed` signal and the `is_externally_managed` hook; `qa` notification category | `test_tasks_hooks.py` (6) | `python -m pytest qa_core/tests/test_tasks_hooks.py` |
| Notification sandbox | `test_notify.py` (7) | `python -m pytest qa_core/tests/test_notify.py` |

**Results recorded on 16–18 Oct 2026:**
- Phase 1: **182 passed**.
- Whole project: **721 passed, 15 failed, 1 skipped, 1 xfailed**. The 15 failures are all in `review_checks/test_review_findings.py`, and they are **the same 15 tests that fail on the code as received** (baseline: 442 passed, 15 failed). There is no regression in Stages 1–3 or the nominal roll.

## Manual checklist (the brief's interactive verification)

Each step was run on 16 Oct 2026 against a brand-new SQLite database. Steps 3–6 were driven in a real Chromium browser (scripted with Playwright) on both hosts; the rest are the commands shown. ✔ = passed.

| # | Step | How | Result |
|---|---|---|---|
| 1 | `python manage.py migrate` on a fresh SQLite DB | `DATABASE_URL=sqlite:///fresh.sqlite3 python manage.py migrate` | ✔ All migrations applied, including the 5 new ones |
| 2 | Seeds | `python manage.py seed_roles --sync && python manage.py seed_nbte && python manage.py seed_startup --with-demo` | ✔ 162 changes; 5 demo passwords printed once |
| 3 | Institution user on the portal | Run the server and open `http://qa.localhost:8000/login/` as `admin@demo-poly.local` | ✔ Forced password change first, then the empty dashboard (Welcome, DMP card, "Nothing needs your attention"); no internal menus |
| 4 | Officer on the internal host | `http://localhost:8000/accounts/login/` as `qa.officer@demo.nbte.local` | ✔ Signed in; the sidebar shows **Quality Assurance → Pending confirmations / QA settings** |
| 5 | Refusals | The institution user on the internal login; a staff user on the portal login | ✔ Both refused with a pointer to the right door. A session that reaches the wrong host gets 403 and logout (institution) or a redirect (staff); the automated tests cover this, because browser cookies are per host |
| 6 | Dual control | As `qa.officer`, Settings → QA → Formulas → `qa.fee.programme_processing` → *Send for confirmation*. As `qa.director`, Pending confirmations → the item → Confirm → `NBTEConfirm` dialog → *Confirm and apply* | ✔ PA-2026-000001 created; the officer is shown "a different officer must confirm"; after the director confirms, the new formula is in force |
| 7 | Re-run | `python manage.py seed_startup` | ✔ "Changes this run: 0 — already up to date." |
| 8 | Reset | `python manage.py seed_startup --reset-demo` | ✔ "7 tagged row(s) removed" (5 users, 1 institution, the demo officer's pending action). A table-by-table row count before and after showed nothing untagged removed. The only other changes were the demo users' 2 notifications (cascade) and new audit-log rows recording the deletion |
| 9 | Full suite | `python -m pytest` | ✔ 721 passed; the same 15 baseline failures |

## §20 checklist (every Phase 1 page)

The **How verified** column says exactly what was done. "Browser" means it was seen in a real Chromium session during the checks above or the screenshot runs. "Test" names the automated test. "Built, not exercised by hand" means the code is in place (`static/js/qa.js`) but nobody has yet run that scenario manually; please run those when you review.

| Check | How verified |
|---|---|
| Full page plus HTMX partial for every page | Test: `test_interaction_contract.py::test_internal_pages_full_and_partial`, `test_portal_pages_full_and_partial` |
| No full reloads: tabs, filters, pagination, row edits and decisions swap in place, with the URL pushed | Browser (steps 3–6); Test (HTMX partials) |
| Filters live in the URL (search plus ≥3 facets on each list) | Test: `test_rules_filters_live_in_the_url`, `test_pending_filters` |
| 25 per page; Show all up to `qa.ui.max_show_all_rows`, enforced by the server | Test: `test_pagination_25_per_page_and_show_all`, `test_show_all_limit_is_enforced_on_the_server` |
| Decisions never optimistic; `NBTEConfirm` dialog; waits for the server | Browser (step 6c clicked through the dialog) |
| Optimistic toggle (notification templates) | Test: endpoint returns 204 and saves. **Rollback on a 500: built, not exercised by hand** |
| Button busy state and disabled-while-sending | Browser (seen during step 6) |
| Skeleton shimmer, 400 ms overlay, timeout plus one GET retry, "Something went wrong — Retry", "session expired" message | **Built, not exercised by hand.** Suggested check: DevTools → Network → "Slow 3G" or "Offline", then switch tabs or filters |
| Request timeout comes from `qa.ui.request_timeout_seconds` | Test: `test_timeout_setting_reaches_the_client` |
| Dark mode complete; semantic tokens only | Browser: all 7 screens captured in dark (screenshots) |
| Phone and desktop layouts (table to cards, portal bottom tab bar) | Browser at 375 px and 1440 px (screenshots). **320, 768 and 1024 px were not captured**; please spot-check them |
| Keyboard use, focus ring, focus into and out of the dialog | `NBTEConfirm` (existing, already focus-trapping) plus `data-qa-focus` handling in `qa.js`. **Not exercised by hand with the keyboard only** |
| Screen-reader announcements (`aria-live` polite and assertive) | Live regions and `data-qa-announce` hooks are in the markup. **Not tested with VoiceOver or NVDA** |

## Screenshots

`PHASE1_UI_SCREENSHOTS/` holds 28 PNGs. Each of these 7 screens is captured at 375 px and 1440 px, in light and dark:
- the portal sign-in, the empty institution dashboard and account settings;
- the internal QA Settings (Rules and Formulas tabs);
- Pending confirmations and the confirmation detail.
