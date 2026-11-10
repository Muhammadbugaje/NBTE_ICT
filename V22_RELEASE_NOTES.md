# NBTE Digital — v2.2 release notes (4 November 2026)

Builds on the v2.1 review build. Every change has migrations and tests named `test_v22_*`.

## How to upgrade your copy

```bash
pip install -r requirements-prod.txt     # adds whitenoise, Brotli (production); requirements.txt for dev
python manage.py migrate                 # all v2.2 migrations, including the data moves below
python manage.py import_facilities core/seed_data/facilities/nbte_nd_hnd_facilities.zip   # once
python manage.py qa_payments_reconcile --offline --first-run                                # opening ledger
python manage.py generate_institution_guides   # refresh the Word guides (content updated)
```

`migrate` does the following to existing data:

* **Offices become units.** Every Office is merged into the Department list, with a kind and a parent. Legal Unit and the Liaison office were duplicates, so each is merged into one. Office permission groups move to the unit.
* **Fees by category.** One fee row per institution category is created from your current figures and SOP Annex A. Categories with no figure stay "Off", and the old formula keeps working for them.
* **Finance seat.** The Director of Finance seat group always carries `final_confirm_payment`, and a director's account department follows their seat.
* **Missing settings.** New `qa.*` and `honoraria.*` settings are added. Existing values are never changed.

## What changed

### Organisation
- Departments, units, offices, zonal offices and liaison offices are now **one list**, managed under Organisation → Departments & units.
- The staff transfer dialog has a single "department, unit or office" choice.

### Fees and payments
- **Settings → Fees by institution category.** Each category has its own registration fee, registration processing fee, programme fee, programme processing fee (once per bill or per programme), extra-stream fee and QA-request fees.
  - Only the Director of Finance, the ES and super admins can edit. Every change is audited.
  - Bills already issued never change.
- **Online payments never double-charge.**
  - Every attempt gets a unique reference (an idempotency key).
  - A double-click reuses the same checkout.
  - Before any new checkout, earlier open attempts are checked with the provider, and a paid one stops it.
  - If the provider can't be reached, the attempt stays *pending*. It is asked again with the same reference by the 15-minute poll and the nightly run.
- **Plain-language errors.**
  - Offline or timeout: "check your connection; nothing was charged".
  - Bad keys: "temporarily unavailable"; the Director of ICT is alerted.
  - Provider refusal: the provider's message.
  - An offline banner appears, and payment buttons refuse to submit while offline.
- **Remita webhook** caller check uses the unforgeable client address. The Paystack HMAC check and "verify wins" are unchanged.
- **Double-entry ledger** (`payments.LedgerJournal/LedgerLine`). Bills, confirmed payments, waivers, cancellations and honoraria are posted automatically. Journals are append-only and keyed, so nothing is posted twice.
- **Nightly reconciliation** (Celery `qa.payments.reconcile`, 01:45, or `manage.py qa_payments_reconcile`). It compares the provider, NBTE's records and the ledger:
  - corrects the ledger;
  - records payments the provider has but NBTE missed;
  - flags double payments, amount mismatches, payments the provider no longer confirms and stale items.
  - Finance → **Ledger & reconciliation** shows the runs, the issues (each can be marked resolved) and the trial balance.
- **Director of Finance.**
  - The director of a department can always act on that department's role-based stages, e.g. fund release (SOP stage 11), which was locked to the Pay Officer role.
  - The payment page shows who holds the DoF seat when someone else expects to confirm.

### Institutions and onboarding
- **Registration loader.** Your crest sits in a progress ring, with live steps ticking off. A light, brand-coloured confetti burst plays once on arrival, with a "registered" banner. Reduced motion is respected.
- **Chief Executive account.**
  - The set-up link is **never shown to the Head of ICT**. It is e-mailed only to the CE.
  - On Institution → Users, the ES, super admins, the Director of ICT and the assigned ICT officer can e-mail it again, get it once to send manually, or issue a one-time password (the CE must change it). All of this is logged.
  - Setting `qa.onboarding.ce_auto_email` switches automatic sending off.
- **People and contacts.** Institution → Users lists the whole management team with e-mails and phones, plus every portal user.
- **New-institution spotlight.**
  - Bold cards appear on the QA home and on the ES, super admin, Director of ICT and director dashboards. Handling directors see only their own institutions.
  - Each card shows a progress bar and whether an ICT officer is tracking it.
  - The Director of ICT assigns an officer on the card, with the rotation's suggestion pre-selected.
  - Officers get **Institutions you are tracking**.
- **Documents are department-only.** Other departments see a locked row with the name only. The ES, super admins and the Director of ICT see and assign everything. The ICT officer watches read-only.
- **Institution dashboard.** New "Your journey" tracker (shown while onboarding or while a case runs), KPI strip, "Your contacts at NBTE" and "Help & guides".

### Honoraria
- **Who's next to be paid** (Finance → Honoraria).
  - Everyone owed, in payment order: confirmed bank details first, oldest work first.
  - Tick, prepare, approve, pay. The payee confirms receipt.
  - A payment log is shown, and payments post to the ledger.
- **Bank details.** The DoF, ES and super admins can **view** them (each view is logged); only the owner can change them.
- **External SSQ reviews** by resource persons accrue at `honoraria.rate.external_ssq_review` (₦30,000 default).
- Honoraria settings now appear in QA Settings; before, they could not be edited anywhere.

### Facilities & equipment register (new, QA → Facilities & equipment)
- **Data.** 87 ND/HND programmes, 665 facilities and 15,415 curriculum lines were read from the Word files. Counts match the index exactly. 71 exact repeats inside one facility were merged.
- **Search and analysis.** Search across all programmes; "most common items", "largest lists", and flags for items without numbers, programmes with no list and programmes not linked to the master list.
- **Editing.** Quantities can be edited inline and save as you leave the box. Programme notes are available, and each programme can have a maintaining officer.
- **Excel.** Export everything or one programme. Import shows New / Changed / Unchanged / Missing before saving. Word/zip files can also be imported through the same preview.
- **Handlers.** The ES, super admins and the Curriculum director assign them.
- **Publish to SSQ catalogue.** A programme's list becomes the yardstick for SSQ shortfall scores; Critical items count double.

### Roadmap v2.1 items completed
- **X40 visit report (VVT/001).**
  - The module was broken: it imported a model that does not exist there, and fields were missing. Both are fixed.
  - The report page now has the VVT/001 panel: prefilled sections, conflict-of-interest declarations (they block the report), declared-vs-found per catalogue item, a GPS check against the institution's site (`qa.visit.gps_max_km`) and billing of the falsification fine.
- **X39.**
  - The cost tab has **Calculate from the state's rates**: DTA per night; transport, fuel and airport taxi per trip; rebuilds safely.
  - **Record actual spend** and planned / actual / variance are shown.
  - Fixed: adding a rate in Settings crashed (NameError).
- **X38.** Travel mode (road/air) and distance from HQ can be edited in Settings → States & LGAs.
- **D-19.** While a professional body's letter is awaited, the institution gets a **provisional approval letter** and the body is asked for its letter. The approval to commence is held back and issued automatically when the body uploads its letter.

### Guides
- Every institution guide can now be **read online** (the preview), printed or saved as PDF, as well as downloaded in Word. Content is updated for the CE set-up, fees by category and "never pay twice".
- New **staff guides** (QA → Guides): new institutions and ICT oversight; who sees documents; fees, payments and reconciliation; paying honoraria; the facilities register; departments and units.
- Dashboards link to the guides and the public QA portal.
- Preview buttons were added to letters, receipts, slips and documents that only had a download.

### Performance and security
- **Query counts.**
  - Director dashboard: 582 → 246 queries (grouped counts instead of per-department loops).
  - QA home: 756 → 360 cold, mostly cached when warm.
  - Facilities page: 1.5 s → 0.17 s.
  - Holiday lookups, QA department lookups and the rotation suggestion are now memoised or cached.
- **New indexes** on payment attempts, bills, the verification queue and honoraria entries.
- **Images.** 74 `<img>` tags now lazy-load.
- **Redis.** Redis cache in development when `REDIS_URL` is set (production already required it).
- **Content-Security-Policy.** Site-wide (`CSP_ENABLED`, `CSP_REPORT_ONLY`). Everything is self-hosted, so it is `'self'`-only.
- **Static files.** WhiteNoise serves `/static/` with year-long cache headers when there is no nginx (Render).
- Already in place and re-checked:
  - secrets only in `.env`;
  - admin IP guard;
  - rate limits on sign-in, registration (5 per IP per hour) and verify;
  - need-to-know row scoping on every QA query (strict mode raises on unscoped queries);
  - signed and verified webhooks.

### Production hardening (Render / small server)
- **Render blueprint fixed.**
  - The scheduler (Celery beat) now runs. Before, the 25 scheduled jobs never ran on Render.
  - The web service and the worker share one set of secrets. Before, the worker got different keys and could not read encrypted bank details.
  - The site, worker and scheduler run in one service (`start.sh`), so they share the uploads disk. See `DEPLOY_RENDER.md`.
- **Sized for 512 MB.** `gunicorn.conf.py` runs 1 process × 8 threads plus Celery (2 threads), about 310 MB at idle. Scale with `WEB_CONCURRENCY` / `GUNICORN_THREADS`.
- **`/healthz` probe.** It answers before host checks and the HTTPS redirect, with no database hit.
- **Database.** Health-checked persistent connections, TLS required for remote hosts, a 30 s statement timeout, and PgBouncer support (`DB_POOLER`).
- **Redis.**
  - Cache and sessions (`cached_db`) live in Redis.
  - A Redis outage degrades to "no cache" instead of failing every page.
  - Lean Celery: no result keys, prefetch 1, late acks, time limits.
- **Trusted client address.**
  - One header (`CLIENT_IP_HEADER`; Cloudflare's `CF-Connecting-IP` on Render).
  - Forged `X-Forwarded-For` / `X-Real-IP` no longer dodge rate limits, the admin IP rule or the Remita webhook check.
- **Open redirects closed.** Every "return to" link goes through `core.urlsafe.safe_next`.
- **Postgres fix.** The audit `action` column was widened from 20 to 64 characters: registering an institution crashed on Postgres. A fresh install keeps the `legal` code for the Legal Unit.
- **Fewer queries.**
  - Permission checks dropped from 9 to 2 queries per request; old and new results were compared for every user on SQLite and Postgres.
  - Director seats are remembered for one request.
  - Grouped counts on the dashboards; batch stage labels and bank accounts.
  - Cached facilities statistics, onboarding progress and system health, each refreshed on change.
- **Front end.**
  - Hashed static files are pre-compressed (Brotli and gzip).
  - The chart library is deferred on the dashboards.
- **`.gitignore`** keeps `.env`, databases, uploads and build output out of Git.
- **Rehearsal.** Tested on Postgres 16 limited to 20 connections, plus a 25 MB Redis: fresh `migrate`, full seed and the v2.2 tests on Postgres.

## Known limits / next
- **Gateways.** Payments could not be tested against the real Paystack/Remita here; network failures were simulated. Please make one test-key payment on your machine.
- **Row-level security.** Postgres RLS is not turned on. Row access is enforced in the application for every query, website and API alike.
- **Tests.** The full test suite had 126 failing tests and 62 errors **before** v2.2 (stale demo data and old expectations). v2.2 adds no new failures; see the test report delivered with this release.
