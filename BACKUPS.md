# Backups

Backup and restore for the whole NBTE ICT System (staff, tasks, attendance,
tickets, leave, messaging **and** the Digital QA System). Built as its own
Django app, `backups`, following Master Plan §22.

Page: **Administration → Backups** in the sidebar, or the *Backups* card on
**Settings** — URL `/settings/backups/`.

---

## 1. What is backed up

Every run produces one folder, `nbte-backup-YYYYMMDD-HHMMSS-r<id>`, holding:

| File | What it is |
|---|---|
| `database.sqlite.sql` *(SQLite)* or `database.pgdump` *(PostgreSQL)* | The full database. SQLite: a consistent snapshot (online-backup API) dumped as SQL. PostgreSQL: `pg_dump --format=custom` taken from an exported snapshot. The engine is detected from `DATABASES`. If `pg_dump` is missing the run fails with a clear message. |
| `media.tar.gz` | Everything under `MEDIA_ROOT` (public uploads). |
| `protected_media.tar.gz` | Everything under `PROTECTED_MEDIA_ROOT` (restricted files: evidence, proofs of payment, CVs, letters…). |
| `settings.json` | A snapshot of every `ConfigEntry`, `FlowDefinition`, `FlowStage`, `FlowTransition`, `ActionPolicy` and `ReferenceFormat` row, so the rules can be compared or restored on their own. |
| `manifest.json` | Every file with its size and SHA-256, the key-table row counts at backup time, the combined checksum. Its own hash is stored on the run (`manifest_hash`). Written last, so its presence marks a complete copy. |

When encryption is on (see §4) each file gets a `.enc` suffix and the
checksums are taken over the encrypted files (the manifest also keeps the
plain-file hashes, checked after decryption).

A run is written to **every active destination**. Status:
`succeeded` (all destinations), `partial` (some), `failed` (none, or the
files could not be built). ICT is notified in the app on `failed`,
`partial` and on a failed restore drill; success is silent. Every run and
drill writes an audit-log entry.

## 2. Destinations

| Kind | Use it for | Needs |
|---|---|---|
| **Folder on this server** (`local`) | A quick local copy, ideally on a second disk | A full path, writable by the user the system runs as |
| **USB or external drive / network mount** (`mounted`) | An office USB disk, NAS share, mounted network drive | The mount point. The page lists writable drives it finds under `/media`, `/mnt`, `/run/media`, `/Volumes`. If the drive is not mounted the run fails for that destination — it never writes into the empty folder on the server's own disk. |
| **S3-compatible storage** (`s3`) | Off-site copies: AWS S3, Cloudflare R2, Supabase Storage, Backblaze B2, MinIO | Bucket, folder in the bucket, region, endpoint URL; keys in `.env` |

**SFTP is not offered.** It needs the `paramiko` package or the `sftp`/`scp`
programs, and neither is installed on this server (no new packages are
allowed this round). Use a mounted network share or S3-compatible storage.

Recommended: one local or USB destination **and** one off-site (S3) destination.

### Credentials never go in the database

Each S3 destination stores only a **credential prefix**, e.g.
`BACKUP_S3_R2_`. The keys are read from the environment:

```
<PREFIX>ACCESS_KEY_ID        required
<PREFIX>SECRET_ACCESS_KEY    required
<PREFIX>ENDPOINT_URL         optional (overrides the saved endpoint)
<PREFIX>REGION               optional (overrides the saved region)
```

The page shows, per destination, which variables it needs and whether each
is set (never the values), with copy-ready `.env` lines. The destination
form refuses anything that looks like a key or password. Restart the web
server **and** the Celery worker after editing `.env`.

Every destination card has **Test connection**: it writes a small test
object, reads it back, deletes it, and shows "Connection OK" or the exact
error.

### Cloudflare R2
1. Cloudflare dashboard → **R2 → Create bucket**, e.g. `nbte-backups`.
2. **R2 → Manage API tokens → Create API token**, *Object Read & Write*, limited to that bucket.
3. Add a destination: S3-compatible, bucket `nbte-backups`, region `auto`, endpoint `https://<account-id>.r2.cloudflarestorage.com`, prefix `BACKUP_S3_R2_`.
```
BACKUP_S3_R2_ACCESS_KEY_ID=...
BACKUP_S3_R2_SECRET_ACCESS_KEY=...
BACKUP_S3_R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
BACKUP_S3_R2_REGION=auto
```

### Supabase Storage
1. Supabase → **Storage → New bucket** (private), e.g. `nbte-backups`.
2. **Project Settings → Storage → S3 Connection**: note the endpoint and region; create an access key.
3. Add a destination: S3-compatible, endpoint `https://<project-ref>.supabase.co/storage/v1/s3`, the project's region, **addressing style: Path**, prefix `BACKUP_S3_SUPABASE_`.
```
BACKUP_S3_SUPABASE_ACCESS_KEY_ID=...
BACKUP_S3_SUPABASE_SECRET_ACCESS_KEY=...
BACKUP_S3_SUPABASE_ENDPOINT_URL=https://<project-ref>.supabase.co/storage/v1/s3
BACKUP_S3_SUPABASE_REGION=eu-west-2
```

### AWS S3
1. Create a private bucket (block public access).
2. Create an IAM user whose policy allows `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListBucket` on that bucket only; create an access key.
3. Add a destination: S3-compatible, the bucket and region, endpoint blank, prefix `BACKUP_S3_AWS_`.
```
BACKUP_S3_AWS_ACCESS_KEY_ID=AKIA...
BACKUP_S3_AWS_SECRET_ACCESS_KEY=...
BACKUP_S3_AWS_REGION=eu-west-2
```

### Backblaze B2
Private bucket + an Application Key limited to it; endpoint `https://s3.<region>.backblazeb2.com`; prefix `BACKUP_S3_B2_`.

### A USB / external drive
1. Plug it in. Desktops mount it at `/media/<user>/<label>`; on a server: `sudo mkdir -p /mnt/backup && sudo mount /dev/sdb1 /mnt/backup` (add to `/etc/fstab` to survive restarts).
2. Make it writable by the system user: `sudo chown www-data /mnt/backup`.
3. Add a destination: *USB or external drive*, pick it from the list (press **Look again** after plugging in). Backups go into the folder `nbte-backups` on the drive.

### A local folder
`sudo mkdir -p /var/backups/nbte && sudo chown www-data /var/backups/nbte`, then add a destination *Folder on this server* with that path. No keys needed. A local copy alone does not survive the loss of the server — add an off-site one.

## 3. `.env` lines

All backup variables are listed (commented) in `.env.example` at the project root:

```
BACKUP_ENCRYPTION_KEY=          # see §4
BACKUP_STAGING_DIR=             # optional: where runs build files (default: system temp)
BACKUP_PG_BIN_DIR=              # optional: folder with pg_dump/pg_restore
BACKUP_S3_<NAME>_ACCESS_KEY_ID=
BACKUP_S3_<NAME>_SECRET_ACCESS_KEY=
BACKUP_S3_<NAME>_ENDPOINT_URL=
BACKUP_S3_<NAME>_REGION=
```

## 4. Encryption

When **Encrypt backups** (`qa.backup.encrypt_at_rest`, default on) is on
**and** `BACKUP_ENCRYPTION_KEY` is set, every file is encrypted with
AES-256-GCM (the `cryptography` package, already installed) in 4 MiB
chunks before it leaves the server. Generate a key once:

```
python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

**Keep a copy of the key off the server** (e.g. a sealed envelope or the
NBTE password manager). Without it encrypted backups cannot be restored.

If the setting is on but the key is not set (or the library is missing),
runs still happen, unencrypted; each run records the reason as a warning
and the page's *Encryption* tile shows **Off** with the reason.

## 5. Scheduling and retention

Settings (on the Backups page under *Schedule*; also listed on Settings → QA;
editable with `backups.manage_destinations`):

| Key | Default | Meaning |
|---|---|---|
| `qa.backup.interval_days` | **30** (monthly) | Days between scheduled backups |
| `qa.backup.keep_count` | 8 | Backups kept at each destination |
| `qa.backup.drill_every_n_runs` | 4 | Automatic restore drill after this many successful runs |
| `qa.backup.encrypt_at_rest` | true | See §4 |

A Celery Beat entry, `backups.scheduled_check`, runs **daily at 01:30**
(beat time zone) and starts a backup when `interval_days` have passed since
the last successful or partial run. Run beat and a worker:

```
celery -A nbte_ict beat -l info
celery -A nbte_ict worker -l info
```

Without Celery, cron can do the same: `30 1 * * * cd /srv/nbte && python manage.py run_backup --if-due`.

**Retention** is per destination: once a run succeeds at a destination, the
copies there beyond the newest `keep_count` are deleted. A failed run never
deletes anything.

Only one backup runs at a time (a shared cache lock plus a check for a
running row; a run left "running" for over 12 hours is marked failed).

**Back up now** (permission `backups.run_backup`) starts a run immediately.
It runs on the Celery worker (or a background thread in development, where
Celery is eager), and the page shows its progress.

## 6. Commands

```
python manage.py run_backup             # back up now (exit 1 = failed, 2 = partial)
python manage.py run_backup --if-due    # only if the schedule says so (for cron)
python manage.py restore_backup --drill <run id> [--skip-check]
```

## 7. Restore drills

A drill (button *Run restore drill* in the history, permission
`backups.run_restore_drill`, or `restore_backup --drill <id>`):

1. fetches the run from the first destination still holding it (local, then mounted, then S3);
2. verifies the manifest hash and every file's size and SHA-256; decrypts if needed;
3. restores the database into a **scratch** database (a temporary SQLite file, or a new PostgreSQL database `<name>_drill_<id>_<time>`);
4. runs `manage.py check --database default` against it (SQLite also runs `PRAGMA integrity_check`);
5. smoke test: key table counts must match the manifest, one institution and its timeline load, one file opens from each archive;
6. records a `RestoreDrill` with the full report, then drops the scratch database and temporary files.

The live system is never touched. Drills also run automatically after every
`qa.backup.drill_every_n_runs` successful backups. A failed drill notifies ICT.

## 8. Restore runbook (superusers only — never a button)

A real restore overwrites live data.

1. **Drill first**: `python manage.py restore_backup --drill <run id>` and read the report.
2. Announce downtime. **Stop** the web server (gunicorn/uwsgi) and the Celery worker and beat.
3. Restore:
   ```
   # a run listed on the Backups page
   python manage.py restore_backup <run id> --as <superuser e-mail> --confirm <backup label>

   # the database itself is lost: restore from a backup folder you copied back
   # (from the USB drive, or downloaded from S3 with the aws/rclone CLI)
   BACKUP_ENCRYPTION_KEY=... python manage.py restore_backup --from-dir /path/to/nbte-backup-... --confirm <backup label>

   # only one part
   ... --db-only      or      ... --media-only
   ```
   * SQLite: the current `db.sqlite3` is copied to `db.sqlite3.before-restore-<time>`, then replaced.
   * PostgreSQL: `pg_restore --clean --if-exists --single-transaction` into the configured database. Take a fresh `run_backup` first if the current data may still matter.
   * File roots: the current `MEDIA_ROOT` and `PROTECTED_MEDIA_ROOT` are renamed to `*.before-restore-<time>`, then the archives are extracted (safe `data` filter).
4. `python manage.py migrate` (the backup may predate the code) and `python manage.py check`.
5. Start the services, sign in, open a few records and restricted files.
6. When satisfied, delete the `*.before-restore-*` copies.

On a brand-new server: install the code, create `.env` (with the same
`BACKUP_ENCRYPTION_KEY`), create an empty database, then run step 3 with
`--from-dir`, then step 4, then `python manage.py seed_startup`.

## 9. Permissions and seed

| Permission | Who (seeded by `seed_startup`) |
|---|---|
| `backups.view_backups` | ICT QA Monitor role, Backup administrators group, Super Admin role |
| `backups.run_backup` | Backup administrators, Super Admin |
| `backups.manage_destinations` | Backup administrators, Super Admin |
| `backups.run_restore_drill` | Backup administrators, Super Admin |

The **Backup administrators** group is given to every active Director of
ICT (role *Director of Department* in the ICT department) each time
`seed_startup` runs; superusers have everything. `seed_startup` also creates
a placeholder, inactive local destination and, while no destination is
active, prints **"No backup destination configured. Set one before going live."**

## 10. Files

`backups/` (models, engine, destinations, crypto, views, tasks, commands,
templates, tests), `static/css/backups.css`, `nbte_ict/celery.py` (beat
entry), `qa_core/settings_catalogue.py` (`SETTINGS_PHASE8`),
`qa_core/management/commands/seed_startup.py` (`_backups`),
`templates/config/settings.html` and `templates/partials/sidebar.html` (links).
