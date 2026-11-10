# Deploy: Render + Postgres (v2.2, small-server first)

`nbte_ict/settings/prod.py` reads deployment settings from environment
variables. `render.yaml` is a proposed Blueprint configuration, not a deployed
or production-validated service. Before creating resources, confirm current
Render plan availability and costs, database capacity and region, backup and
restore procedures, email delivery, payment-provider settings, and domain
ownership. No deployment or production data migration is performed by this
guide.

## What runs (and why it is shaped this way)

| Piece | Blueprint starting point | Before scaling |
|---|---|---|
| **Web + worker + scheduler** in one service (`start.sh`, `PROCESS_MODE=all`) | 1 gunicorn process × 8 threads + 1 Celery process × 2 threads + beat | Measure memory, request concurrency, and database connections; scale deliberately |
| Redis (cache, sessions, job queue) | Paid Render Key Value, `256mb`, journal + snapshot persistence | Confirm cost and capacity; monitor memory and queued jobs |
| PostgreSQL | External `DATABASE_URL` (Render Postgres or another managed provider) | Confirm connection limits, backups, restore test, and region |
| Disk `/var/data` | Persistent uploads, generated files, and beat schedule state | Use shared object storage before multiple app instances or a separate worker |

* **One service, not two.** With the current file-system storage, the web
  service runs the worker because a disk is attached to one service only.
  Keep one instance while the scheduler runs in the web service; use shared
  storage and a single designated beat scheduler before scaling out.
* **The scheduler now runs.** The v2.1 blueprint never started Celery beat, so the 25
  scheduled jobs never ran on Render: payment polls, nightly reconciliation, SLA
  sweeps, reminders and the backup check.
* **One set of secrets.** The web process and worker take their keys from the
  same `siteEnv` group. Losing or rotating the encryption key can make stored
  encrypted values unreadable.

## 1. Database
Put the database in the **same region** as the Render service. Every query
crosses that network, and a distant database is the most common cause of
slow pages.

* **Aiven:** copy the Service URI. It already carries `sslmode=require`.
* **Render Postgres:** use the *internal* connection string.
* TLS is required by default for remote PostgreSQL (`DB_SSLMODE`, default
  `require`); verify this is compatible with the selected provider.
* A statement timeout of 30 s (`DB_STATEMENT_TIMEOUT_MS`) stops one runaway
  query from holding a worker.
* Connections are reused for 5 minutes and health-checked before reuse
  (`DB_CONN_MAX_AGE`).
* Set worker/thread counts only after checking the database's actual connection
  limit. Connection demand grows with web processes × threads plus Celery.
* Using PgBouncer in transaction mode? Set `DB_POOLER=True`.

## 2. Render
1. Review the repository and its complete Git history for secrets, local
   databases, uploads, credentials, and files that should not be public.
   `.gitignore` prevents future additions but does not remove already-tracked
   files or purge Git history. In this workspace, `web/` is the Git repository
   root and contains `render.yaml`; connect that repository, not the parent
   workspace folder, unless intentionally setting a monorepo service root.
2. Push the reviewed repository to GitHub. In Render, choose **New → Blueprint**
   and select it. This blueprint requests a web service, Redis/Key Value,
   persistent disk, and the `siteEnv` group. Review resource costs before
   approving creation.
   The initial plans are a paid `0.5c-512mb` web service and paid `256mb` Key
   Value. These are explicit starting points, not capacity guarantees. Both
   services default to Oregon; if the database is elsewhere, set `region` on
   both services to the database's region before creating resources. Confirm
   Redis memory/persistence and disk capacity against expected workload.
   `journal-snapshot` persistence can still lose up to about one second of
   recent Key Value writes during a failure; treat the database as the durable
   system of record, not the task queue.
3. Fill the `sync:false` values **once, in `siteEnv`**:
   - `DATABASE_URL`
   - `ALLOWED_HOSTS`, for example `nbte-ict.onrender.com,qa.your-domain.gov.ng`
   - `QA_PORTAL_HOST`, for example `qa.your-domain.gov.ng`
   - `SITE_URL`, for example `https://nbte-ict.onrender.com`
   - `CSRF_TRUSTED_ORIGINS`: the same hosts, each with `https://`
   - `DEFAULT_FROM_EMAIL`
   - Email: `BREVO_API_KEY`, **or** both `GMAIL_USER` and `GMAIL_APP_PASSWORD`
   - Payments: leave `PAYMENTS_GATEWAYS` empty until the provider account,
     callback URLs, and approved test/live credentials are ready. Then add the
     matching `PAYSTACK_*` / `REMITA_*` values.
   - Optional: `ADMIN_ALLOWED_IPS`, the office IPs allowed to open `/admin/`.

   `SECRET_KEY`, `FIELD_ENCRYPTION_KEY` and `PAYMENT_QR_SIGNING_KEY` are
   generated once. Store protected recovery copies outside Render. Losing or
   rotating encryption/signing keys can make stored encrypted values or
   previously issued signed codes unreadable/unverifiable.
4. Each deploy runs:
   - build (`build.sh`): install packages, `check --deploy`, then `collectstatic`;
   - pre-deploy: `migrate --noinput` against the configured database. Review
     migrations and take/verify a restorable database backup before production
     deploys; do not assume every schema migration is zero-downtime.
   - start: `./start.sh`.

   `.python-version` selects the latest available Python 3.12 patch on Render.
   The platform health check is `GET /healthz`; it is a liveness check, not a
   database-readiness check. `GET /health/` checks dependencies. Static assets
   are collected and served by WhiteNoise. Public media is served by Django
   from the persistent disk (`SERVE_FILES_LOCALLY=True`); this is a
   single-instance starting point, not a high-volume media-delivery solution.
   Restricted files stay outside `MEDIA_ROOT` and are delivered through
   permission-checked views.

## 3. First data (once)
After validating the production dataset and backup, use `render shell`, or a
machine that has the same environment variables. Do not create demo users in a
real production environment:
```bash
python manage.py seed_nbte                       # reference data, settings, flows, departments
python manage.py import_facilities core/seed_data/facilities/nbte_nd_hnd_facilities.zip
python manage.py qa_payments_reconcile --offline --first-run
python manage.py createsuperuser
```
`seed_nbte --with-demo --i-understand-production` is for an isolated test
deployment only.

### Or: copy your local database to Aiven (test deployments)
On your laptop, with `DATABASE_BACKEND=sqlite` (your local data), only after
reviewing privacy, authorization, and the full migration/rollback plan:
```bash
python manage.py push_database --to "postgres://avnadmin:…@….aivencloud.com:PORT/defaultdb?sslmode=require"
# --replace   overwrite an Aiven database that already has data
```
It migrates Aiven, copies every table in one transaction (all-or-nothing) and
checks the row counts. Then on Render set the same `DATABASE_URL`, and use
**the same `FIELD_ENCRYPTION_KEY` and `SECRET_KEY`** as
your laptop (encrypted fields and passwords were made with them). Uploaded
files are not in the database: copy `media/` and `protected_media/` to the
Render disk (`/var/data`). Set `DB_CONN_MAX_AGE` and worker/thread counts
according to the selected database's connection limits.

## 4. Speed and safety built in
* **Cache:** Redis holds the cache and the sessions. Sessions are written
  through to the database, so nobody is signed out when Redis restarts. If
  Redis goes down, pages keep working without the cache instead of failing.
  Locks are never "taken" during an outage.
* **Static files:** hashed names with long cache headers, served by WhiteNoise.
  Confirm Brotli/gzip behavior in a built deployment if content compression is
  required.
* **Pages:** the heavy dashboards dropped from 190–245 queries to 75–100.
  Permission checks went from 9 queries per request to 2. Statistics that
  don't change every second are cached and invalidated on change: facilities
  totals, onboarding progress, system health.
* **Client address:** the blueprint selects `CF-Connecting-IP`. Verify the
  selected host/proxy supplies and overwrites that header before relying on
  IP-based controls or webhook allowlists.
* **Headers:**
  - HSTS (1 year, preload), a strict Content-Security-Policy, `X-Frame-Options: DENY`,
    `nosniff`, `same-origin` referrer and opener policies;
  - secure, HttpOnly session cookies;
  - an idle and absolute session timeout.
* **Redirects:** every "return to" link is checked with Django's own safe-URL test.
* **Logs:** access logs record the method and path only. No query strings,
  so tokens are never logged.

## 5. Domains
Render → service → Settings → Custom Domains: add your domain and the `qa.`
subdomain, then update `ALLOWED_HOSTS`, `SITE_URL`, `CSRF_TRUSTED_ORIGINS` and
`QA_PORTAL_HOST`.

## 6. Smoke test after the first deploy
- `GET /healthz` returns `ok`; `GET /health/` returns `{"status": "ok"}`.
- In the logs: `celery@… ready` and `beat: Starting...`.
- Log in → Settings → **Send test email**.
- Register a test institution. Its bill appears on the portal Payments page,
  and Finance sees it.
- Make one Paystack test-key payment end to end. Then run
  Finance → Ledger & reconciliation → **Run now**.

## 7. Growing later
- More traffic: move to `standard`, then raise `WEB_CONCURRENCY`.
- A separate worker service, or several web instances: first move uploads to
  S3-compatible storage. Only one place may run beat.
- Many users at once on a small database: put PgBouncer in front and set
  `DB_POOLER=True`.
