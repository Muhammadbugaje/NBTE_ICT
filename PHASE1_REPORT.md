# v2.3 Phase 1 — Foundation and performance (first pass)

Date: 11 November 2026. Companion to the design document
"NBTE Digital QA — Redesign Design & Workflow Document" (section 4, Phase 1).
Nothing was committed. No migrations were added in this pass.

## What changed

### 1. The "page freezes, but the URL works in a new tab" issue (D9)

Boosted portal navigation (`hx-boost` into `#qa-main`) could leave the page
stuck when the response was not a clean partial.

| File | Change |
|---|---|
| `static/js/boost-guard.js` (new) | A failed, slow (> 10 s), cross-host or whole-document answer to a boosted link becomes a normal full page load of that link. Runs in the capture phase, before the general handlers in `app.js` / `qa.js`. Non-boosted requests are untouched. |
| `templates/base.html`, `templates/qa_portal/base_portal.html` | Load `boost-guard.js` right after htmx. |
| `core/middleware.py` | `htmx_full_redirect()`: an HTMX request redirected to a sign-in page or to the other host gets `204 + HX-Redirect`, so htmx does a full navigation instead of swapping a sign-in page into the page (or failing CORS). Same-host redirects are left for htmx to follow. Cookies are kept. |
| `core/test_v23_boost_guard.py` (new) | 10 tests. |

Also checked in a real browser (Playwright): a partial swaps in place; a whole
document and a 404 both load as full pages, with no nested layout.

**Please still reproduce on Render** with the browser's Network tab open (or a
HAR export). If a freeze remains, the next suspect is thread starvation on the
single 0.5-CPU box (see "Not done yet").

### 2. The connection banner (D8)

| File | Change |
|---|---|
| `static/js/network-guard.js` | One banner for the whole site with four states: hidden, **offline** (the browser is offline *and* a `/healthz` probe fails), **slow** (online, but our requests are not answered; with a "Retry now" button) and **back online**, which hides itself after 3 s. Exposes `NBTE.net.report("ok" / "slow" / "offline")`. Kept clear of the phone tab bar. Runs once even if a page includes it twice. |
| `static/js/qa-live.js` | Failed polls while the browser is online show "Waiting for the server…" (not "Reconnecting…"/offline). The first good poll puts the pill back to Live. That reset was missing before, which is why the pill stuck. |
| `static/css/qa-live.css` | New quiet `slow` pill state. |
| Templates (8) | Removed the hand-written `?v=` cache busters. Production uses hashed static file names (`NBTEManifestStaticFilesStorage`), and the project's own test `test_v7_no_manual_cache_busters_in_templates` forbids them. That test now passes; it failed before. |

### 3. Query counts

Measured with the test client on a copy of the bundled database
(cold = empty cache, warm = second visit).

| Page | Role | Before (cold / warm) | After (cold / warm) |
|---|---|---|---|
| `/tasks/` | ES | 229 / 158 | 76 / 19 |
| `/tasks/` | Super admin | 193 / 123 | 39 / 12 |
| `/tasks/` | Director | 164 / 90 | 46 / 16 |
| `/notifications/` | ES | 114 / 42 | 72 / 14 |
| `/` (home) | ES | 98 / 14 | 45 / 14 |
| `/qa/cases/4/` (SOP-03 case) | ES | 119 / 38 | 71 / 38 |
| `/qa/` (QA home) | Super admin | 341 / 20 | 281 / 20, and cold far less often |
| `/qa/` (QA home) | Director | 138 / 25 | 87 / 25 |
| `/dashboards/es/` | ES | 323 / 88 | 267 / 60 |
| `/dashboards/director/` | Director | 146 / 69 | 100 / 69 |

| File | Change |
|---|---|
| `tasks/views.py` | `_with_row_data()`: every task list (list, kanban, created, assigned, review) loads categories, creators, departments, divisions, assignees, subtasks and the QA link in a fixed number of queries. Before, each row cost about 6 queries. |
| `messaging/templatetags/memo_tags.py`, `core/fastcache.py` | **The sidebar Memos badge was the hidden cost on every page.** It worked out the approver of up to 200 memos one by one. It is now cached per user (namespace `memo_badge`), dropped whenever a memo, recipient, approval, route, user, role, staff record, department or executive seat changes, with a 60 s safety TTL. |
| `config/registry.py` | `config.get()` loads all settings of a scope in **one** query (was one query per key: 61 on the QA home). Each process keeps a parsed copy while a small version token in the shared cache is unchanged, so a read costs one tiny cache GET, as before. A save or delete changes the token at once (and again on commit). `cache.clear()` also resets it. Writes that skip signals (`bulk_create`, `.update()`, imports) are picked up within 60 s. A key missing from the scope is looked up on its own and cached per key, exactly as before. `clear_local()` is for tests. |
| `qa_core/views.py` | QA home safety TTL went from 20 s to 120 s (charts from 60 s to 300 s). Freshness already comes from the epoch bump on every QA write. With 20 s, a quiet server rebuilt the home (about 150 queries) on almost every visit. |
| `conftest.py` | Clears the in-process settings map around each test. |
| `tasks/test_v23_query_budget.py` (new) | 5 tests. The task list and kanban must not grow with rows; budget 60. The memo badge is resolved once per user. 15 settings cost 1 query. A settings write is visible at once. |

### 4. Dependencies and API schema

| File | Change |
|---|---|
| `requirements.txt` | `gunicorn==21.2.0` became `gunicorn>=22.0`, which fixes PYSEC-2026-1433/1434 and the clash with `requirements-prod.txt`. `cryptography` now uses platform markers: `<49` on macOS only (the old Intel Mac wheel), `>=50.0` everywhere else (PYSEC-2026-3552/3553/3554). `pip-audit` now reports **no known vulnerabilities**. |
| `api/schema.py`, `nbte_ict/settings/base.py` | A spectacular preprocessing hook drops the `/qa-portal/` path mirror from the schema. The 29 duplicate operationId warnings are gone, which avoids broken Flutter client generation. The endpoints still work. The `MemoVisibilityEnum` override was stale (the model gained `staff_filter` and `no_staff`), and `MemoKindEnum` was added. `spectacular --validate --fail-on-warn` is now clean, so `api/tests/test_tasks.py::test_schema_still_validates` passes; it failed before. |

### 5. Hosting (prepared, not switched)

`deploy/render.split.yaml` is a **draft** of the target layout: a web service,
a separate Celery worker, and a single beat scheduler, all in the same region as
Postgres. It is not active. It needs object storage first (Q8).

## Not done yet in Phase 1 (needs a decision or access)

1. **Object storage + service split.** This needs the provider and budget (Q8; R2 recommended). After that: storage settings, a media migration with checksum verification, signed download URLs, and then switching to `render.split.yaml`.
2. **Database region.** Confirm the Aiven region matches the Render region. If not, move one of them; this is likely the biggest single speed win on Render.
3. **Heavy work to Celery.** PDF letters, the backup tar, Data Hub imports and the media scan stream still run inside web requests.
4. **Cold paths.** The QA home (about 280 queries cold) and the ES dashboard (about 270 cold). The rotation suggestion and the home widgets need batching next.
5. **Error tracking** (Sentry or similar) and a CSP report endpoint.

## Test results

* New tests: `core/test_v23_boost_guard.py` (10) and `tasks/test_v23_query_budget.py` (6). All pass.
* Regression run on `core config tasks messaging notifications api qa_core dashboards`: **1,173 passed and 68 failed**. Every failure was then re-run on the **original, untouched code** to check whether it was already there. 63 fail the same way on the original, so they are **not caused by this work**. They come from files missing in the zip (`.env.example`, `.gitignore`), demo-seed expectations and the like.
* Of the remaining 5, one was a regression found by this check: `config/test_a9_appearance.py::test_a9_bad_stored_value_never_reaches_page`, caused by the first version of the settings cache. It is **fixed**, and it passes now.
* Two tests that failed on the original now pass: `test_v7_no_manual_cache_busters_in_templates` and `test_schema_still_validates`.
* `manage.py check`: 0 issues. `makemigrations --check`: no changes. `check --deploy` (prod settings): no Django security warnings and no schema warnings. The only items left are the two expected "payment keys empty" errors on a machine without keys. `pip-audit`: no known vulnerabilities.

Not counting the two that are now fixed, 61 tests were already failing before this work. They are worth a separate clean-up pass; the list is in `docs/reports/phase1_preexisting_failures.txt`.

## How to test

```
pip install -r requirements-dev.txt
python manage.py check
python manage.py makemigrations --check --dry-run    # expect: No changes detected
python -m pytest -q core/test_v23_boost_guard.py tasks/test_v23_query_budget.py
```

In the browser:
* **Portal:** click around the side nav. Then sign out in another tab and click a nav link: you get a clean sign-in page, never a page nested inside a page.
* **Offline:** DevTools → Network → Offline shows the dark "You're offline" pill. Back online shows the green "Back online", which disappears after 3 s.
* **Slow:** DevTools → Network throttling "Slow 3G" on a case page. The live pill says "Waiting for the server…" and returns to Live by itself.
