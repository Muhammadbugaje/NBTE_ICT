# Phase 8 — what changed (21 October 2026)

All Phase 8 work is merged into one codebase (`master`). The database (`db.sqlite3`) is migrated and seeded.

## Payments (Remita and Paystack)
- Remita endpoints now include the API path (`REMITA_API_PATH`, default `/exapp/api/v1/send/api`).
- The return page checks the payment when the payer comes back (`verify_on_return`), in addition to webhooks.
- `PAYMENTS_LIVE_TEST.md`: the `.env` keys, tunnel commands, webhook URLs and a step-by-step live test with test keys.

## QA (accreditation)
- Approval types (full, interim, approval to commence), decisions and outcome letters with duplicates.
- Number of streams per programme, used by the fee formulas (`streams`, `max_streams`).
- Capacity engine: add fees and issue bills from QA settings.
- Accreditation expiry and renewal. The re-accreditation window is configurable, with nightly expiry checks.
- Portal programmes: a status board with countdowns, a searchable and filterable list, and a details page showing the professional body.
- Import existing approved institutions and programmes from Excel without restarting their process (`institutions/migration.py`).
- Timeline, case, complaint, statement and service-history PDFs. Download an institution's full record (PDF or ZIP).
- Workload limits per rank (SSQ at once and per year, visits per year, per institution and per discipline), with fair distribution (`/qa/settings/workload/`).

## Experience
- Preview button for important downloads, scroll memory, and Edit scrolls to the editor.
- Bulk select and delete for messages, memos and notifications.
- Profile-completion reminders, plus an interrupt with "continue anyway".
- Sticky settings navigation.
- Redesigned QA heroes, grouped settings navigation, rebuilt notification templates, a new Add TVET form and a new task-link drift page.
- Editable site names and home-page section titles (`site.*`, `home.title.*`, `portal.home.title.*`).
- The QA settings guide (PDF and Word) is linked in the QA settings header.

## Administration
- Django admin themed with django-unfold, plus custom NBTE CSS and JS: logo, grouped sidebar, dashboard, filters and bulk actions.
- Dashboard card counts fixed.
- Media scanner quarantines files instead of deleting them, and never touches referenced files. New files go into per-institution folders. Old files are moved with `organize_institution_media`. See `MEDIA_STORAGE.md`.
- Backups at Settings → Backups:
  - destinations: local, mounted folder or S3-compatible, each with a connection test
  - monthly schedule, encryption, retention and restore drills
  - see `BACKUPS.md` and `.env.example`

## Known limits
- Postgres backup and restore have not yet been run against a live Postgres server; SQLite has been tested.
- There is no SFTP destination for backups.
- The capacity formulas are placeholders until DPAP's worksheet is confirmed.
- Workflows are read-only on screen.
- The demo accounts are not in the QA Settings Admin group. An administrator adds them in Admin → Groups.
