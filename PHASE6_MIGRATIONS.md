# NBTE QA System — Phase 6 migrations

All additive: new tables, and new columns that are defaulted. No data is changed or removed.

| Migration | Adds |
|---|---|
| `institutions.0004_professional_bodies` | `ProfessionalBody` (name unique, acronym, address, contact person, e-mail, phone, active, created by) with permission `manage_professional_bodies`; `ProgrammeBodyMapping` (body, programme name, normalised programme key, level nullable = every level, note, active, created by) unique per body + programme + level |
| `accreditation.0004_professional_bodies` | `QARequest.is_joint_qa` (default false), `QARequest.joint_bodies` (M2M to `ProfessionalBody`); `ProfessionalBodyNotice` (case, body, e-mail, programmes, status, sent at) unique per case + body |
| `accounts.0015_user_workspace` | `User.workspace` (`main` / `qa`, default `main`) |

Settings added by `seed_startup` (Settings → QA → Access): `qa.portal.registration_contact_email`, `qa.portal.registration_contact_phone`.

Role grants added by `seed_startup`: `institutions.manage_professional_bodies` to Curriculum Officer and Director, and to the *QA Settings Admin* group.

## Applying

```bash
python manage.py migrate
python manage.py seed_startup --skip-base-seeders     # settings and role grants (idempotent)
python manage.py generate_institution_guides          # the Word guides for the how-to page
```

Demo data (`seed_startup --with-demo`) adds two example professional bodies (PCN, COREN) with placeholder e-mail addresses. Do not use `--with-demo` in production.

The development database was backed up before migrating: `db.sqlite3.before-phase6-20261019`.
