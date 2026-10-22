# Media storage

Two roots hold uploaded and generated files:

| Root | Setting | Served by |
|---|---|---|
| Public | `MEDIA_ROOT` (default `media/`) | nginx at `/media/` |
| Restricted | `PROTECTED_MEDIA_ROOT` (default `protected_media/`) | only `core:protected_download` / `qa_portal:file` after `can_download()` |

A file stays in the root its field uses; nothing here moves files between roots.

## Folder layout

Everything that belongs to an institution is stored per institution:

```
institutions/<CODE> - <Institution Name>/
    documents/            institution statutory documents (InstitutionDocument)
    ssq-evidence/         SSQ evidence (accreditation.Evidence)
    queries/              query responses (accreditation.Response)
    payments/slips/       payment slips (PaymentSlip)
    payments/proofs/      proofs of payment (ProofOfPayment)
    payments/advices/     payment advices (PaymentAdvice)
    complaints/           complaint attachments (when the complaint names an institution)
resource-persons/<Name>/  CVs and promotion proofs (inspection.ResourcePersonFile)
```

Example: `protected_media/institutions/KDP - Kaduna Demo Polytechnic/payments/slips/3f9c….pdf`.

* File names on disk are still random (`<uuid>.<ext>`); the name the user uploaded is kept in the database.
* Folder names keep spaces, drop slashes and odd characters, and are cut to 80 characters
  (`qa_core.files.safe_folder_name`).
* The folder is chosen by `qa_core.files.qa_upload_to`, the `upload_to` of every QA file field. A model opts in
  with `INSTITUTION_MEDIA_KIND = "payments/slips"`; the institution is found through the model's
  `INSTITUTION_PATH` (e.g. `obligation__institution`) or a `media_institution()` method. Resource-person files use
  `media_owner_folder()`. To add a new kind (e.g. `letters`, `inspections`), set `INSTITUTION_MEDIA_KIND` on the
  model and point its file field at `qa_upload_to`.
* With no institution (or for files that are not institution-owned, such as bank statements and import sheets),
  the old layout is used: `<MEDIA_SUBDIR>/<uuid>.<ext>` (e.g. `qa/statements/…`).
* Renaming an institution does not move its files; run the move command below to tidy up.

Other folders are unchanged: `staff/…`, `portal/…`, `workflow/…`, `messaging/…`, `showcase/`,
`qa/guides/` (the Word guides built by `generate_institution_guides`), `import-tmp/`.

## Moving existing files: `organize_institution_media`

```
python manage.py organize_institution_media                       # dry run (default): report only
python manage.py organize_institution_media --apply               # needs a fresh database backup
python manage.py organize_institution_media --apply --i-have-a-backup
python manage.py organize_institution_media --report /path/moves.csv
```

* For each institution-linked file it works out the new folder, moves the file inside the same root, and updates
  the database field (a direct update, so no save hooks run and no file is deleted).
* Files already in place are skipped; a file is never overwritten (a `-1`, `-2` … suffix is added); a row whose
  file is missing on disk is reported and left alone. Image variants (`_w300.jpg`) move with their original.
* Every decision (`moved`, `would_move`, `in_place`, `missing`, `skipped`, `error`) is written to a CSV, by default
  `PROTECTED_MEDIA_ROOT/.reports/organize-institution-media-<mode>-<time>.csv`.
* Safe to re-run.
* `--apply` refuses to run unless a database backup newer than 24 hours exists (a `db.sqlite3.*` copy next to a
  SQLite database, or any file in the folder named by `DB_BACKUP_DIR`), or you pass `--i-have-a-backup`.
  Back up both media roots too.

Suggested order on a server: back up the database and both media roots, run the dry run, read the CSV, run
`--apply`, then run `python manage.py scan_media` to confirm nothing is reported.

## Orphan scanner: how it decides

`python manage.py scan_media` and **Settings → Files** use `core.media_scanner`. A file is an orphan only when
**all** of these are true:

1. **No file field refers to it.** Every file and image field of every model is read, through a plain query that
   ignores all managers — so soft-deleted, archived and institution-scoped rows count — and through
   `_base_manager`, `unscoped` and `all_objects` as well. A name stored in the other root also counts.
2. **No text or JSON value names it.** Every text, char and JSON field in the database is searched for file
   names (paths stored as strings, HTML bodies, JSON snapshots). Matching is on the file name, so it errs on the
   side of keeping files.
3. **It is not an image variant** of a referenced file.
4. **It is not in a protected folder.** `MEDIA_SCAN_PROTECTED_FOLDERS` in `nbte_ict/settings/base.py`
   (`qa/guides`, `guides`, `docs`, `user-guides`, `showcase`, `import-tmp`, `staff/imports`) plus the
   `files.orphan_protected_folders` setting (comma-separated; prefix with `public:` or `protected:` to limit an
   entry to one root).
5. **It is older than the age threshold** — `files.orphan_purge_min_age_days` (default 30). Newer unreferenced
   files are listed as "kept (too new)", never as orphans. The settings page no longer lets a manual selection
   skip this check.

Dry-run report (the default):

```
python manage.py scan_media                    # each orphan with "why: …"
python manage.py scan_media --report orphans.csv
python manage.py scan_media --show-kept        # also list unreferenced files kept because they are new
python manage.py scan_media --older-than 60
```

## Quarantine, purge and restore

Nothing is deleted straight away. Cleaning up (`scan_media --quarantine`, or the buttons on Settings → Files)
re-checks each file against a fresh index, then moves it to

```
MEDIA_ROOT/.quarantine/<YYYY-MM-DD>/<original path>
PROTECTED_MEDIA_ROOT/.quarantine/<YYYY-MM-DD>/<original path>     (restricted files stay restricted)
```

with a `manifest.csv` per day (original path, stored as, size, reason, who). Dot-folders are never scanned.

```
python manage.py scan_media --restore 2026-10-21                        # put a day back (never overwrites)
python manage.py scan_media --purge-quarantine --dry-run                # what would go
python manage.py scan_media --purge-quarantine [--retention 30]         # remove days older than the retention
```

The Celery Beat job `media-purge-quarantine` (`core.tasks.purge_media_quarantine`, daily 02:17) removes quarantine
days older than `files.quarantine_retention_days` (default 30).

## Settings

| Key | Default | Meaning |
|---|---|---|
| `files.orphan_purge_min_age_days` | 30 | Age before an unreferenced file counts as an orphan |
| `files.quarantine_retention_days` | 30 | Days a quarantined file is kept before the purge |
| `files.orphan_protected_folders` | (empty) | Extra protected folders, added to `MEDIA_SCAN_PROTECTED_FOLDERS` |
| `files.orphan_scan_max_files` | 5000 | Files enumerated per scan |
