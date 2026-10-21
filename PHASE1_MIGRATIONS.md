# NBTE QA System — Phase 1 migrations

> **Renumbered on 18 Oct 2026.** Your internal-system update added `accounts/0013_request_department_permission`. The Phase 1 accounts migration therefore became **`accounts/0014_user_type_institution`**, which runs after yours, so the history stays one straight line. Nothing else changed.
>
> **If you ran the first Phase 1 package (16 Oct) against a test database,** rebuild that database (`migrate` on a fresh file, then the seed commands). It was only for testing, and the old migration name cannot be carried across cleanly. Your real database has never had Phase 1 applied.
>
> For your real database, plain `python manage.py migrate` is all you need.

Five new Phase 1 migrations (plus `messaging/0008`, added on 18 Oct for memo routing; see MEMO_ROUTING.md). All are additive: no existing column is changed except `config.ConfigEntry.value_type` (widened from 5 to 10 characters, with new choices) and the `choices` list of `notifications.Notification.category`.
No data migration is included: QA data is created by `python manage.py seed_startup`, never by a migration.

Apply them in the usual way. Django orders them by their dependencies:

```bash
python manage.py migrate
```

| # | App | Migration | Forward | Reverse | Depends on |
|---|---|---|---|---|---|
| 1 | `institutions` | `0001_initial` | Creates table `institutions_institution`: `id`, `created_at`, `updated_at`, `code` (unique, 3–6 uppercase letters or digits), `name`, `status` (indexed, default `proposed`), `handling_department` (FK `staff.Department`, PROTECT) | Drops the table. Fails while any user still points at an institution, because `accounts.0014` depends on it; reverse that one first | `staff.0011_staff_file_transfers_accounts` |
| 2 | `accounts` | `0014_user_type_institution` | Adds `user_type` (varchar 20, indexed, default `staff`, so every existing user becomes staff); adds `institution_id` (nullable FK, PROTECT); adds check constraint `accounts_user_type_institution_consistent` (institution users must have an institution; staff must not; resource persons may) | Drops the constraint, then both columns. **Institution and resource-person accounts would then look like staff**: delete them first (`seed_startup --reset-demo` for the demo ones) | `accounts.0013_request_department_permission` (yours, 18 Oct update), `auth.0012`, `institutions.0001`, `staff.0011` |
| 3 | `config` | `0003_qa_value_types_dual_control` | Adds `dual_control` (bool, default false) and `editable_by` (varchar 120, blank); widens `value_type` to 10 characters with `decimal`, `date` and `formula` | Drops both columns and shrinks `value_type` back to 5 characters. **Delete the `qa.*` rows first** (`ConfigEntry.objects.filter(key__startswith="qa.")`): `decimal` and `formula` do not fit in 5 characters, and the locked key refuses `.delete()`, so remove it with a queryset `.delete()` | `config.0002_alter_configentry_scope` |
| 4 | `notifications` | `0004_notification_qa_category` | Adds `("qa", "Quality assurance")` to the `category` choices. No schema change (same column, same length) | Removes the choice. Existing `qa` notifications keep their value; recategorise or delete them if you need strict choices | `notifications.0003_notification` |
| 5 | `qa_core` | `0001_initial` | Creates the 15 tables: `flowdefinition` (15 `qa_core.*` permissions on its Meta), `flowstage`, `flowtransition`, `flowinstance`, `flowevent`, `actionpolicy`, `pendingaction`, `referenceformat`, `referencecounter`, `qatasklink`, `timelineevent`, `suspension`, `notificationtemplate`, `notificationrule` and `demotag`, with their unique constraints and indexes | Drops all 15 tables (cascade within the app). Permission rows are removed by Django's contenttypes clean-up only if you run `remove_stale_contenttypes` | `accounts.0014` (and therefore `institutions.0001`), `contenttypes`, `staff`, `tasks` (latest at generation) |

The other four apps (`payments`, `accreditation`, `inspection`, `complaints`) have **no migrations** in Phase 1.

## Full rollback of Phase 1 (if ever needed)

Run this on a copy first.

```bash
python manage.py seed_startup --reset-demo          # removes the tagged demo rows
python manage.py shell -c "from config.models import ConfigEntry; ConfigEntry.objects.filter(key__startswith='qa.').delete()"
python manage.py migrate qa_core zero
python manage.py migrate accounts 0013_request_department_permission
python manage.py migrate institutions zero
python manage.py migrate config 0002
python manage.py migrate notifications 0003
```

Then remove the six apps from `INSTALLED_APPS` and the two middleware entries, and restore the touched files listed in PHASE1_CHANGELOG.md §4.

## Verification performed

- `python manage.py makemigrations --check --dry-run` → `No changes detected`.
- `python manage.py migrate` on a brand-new SQLite database: all five migrations applied (see PHASE1_TEST_PLAN.md, step 1).
- The rollback sequence above was run on a scratch copy of a seeded database; each step completed. The result is recorded in PHASE1_DONE.md.
