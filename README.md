# NBTE ICT Directorate Internal Operating System

**Setting up a fresh database? Follow [SETUP.md](SETUP.md) — every command, in order.**

See `REPORT.md` for what is built/verified. Quick start (dev, SQLite, no Redis needed):

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    python manage.py migrate
    python manage.py seed_roles --sync     # --sync also fills roles created by migrations
    python manage.py seed_config
    python manage.py seed_departments      # creates ICT; add other NBTE departments in /admin/
    python manage.py createsuperuser
    python manage.py runserver
    python -m pytest

Dev runs Celery tasks inline (eager) and prints emails to the console.

## Real broker path (dev)
    redis-server &
    CELERY_EAGER=False celery -A nbte_ict worker -l info      # one terminal
    CELERY_EAGER=False python manage.py runserver             # another

## Other databases
    DATABASE_URL=postgres://user:pass@localhost:5432/nbte_ict python -m pytest
    DATABASE_URL="mysql://user:pass@127.0.0.1:3306/nbte_ict?charset=utf8mb4" python -m pytest
(Postgres needs `psycopg[binary]`, MySQL/MariaDB needs `mysqlclient`; the DB user needs CREATE DATABASE for the test DB.)

## Production pieces
* `deploy/nginx.conf` - /media/ (public, immutable cache), internal /protected/ (X-Accel-Redirect), proxy to gunicorn.
* `gunicorn nbte_ict.wsgi:application` (settings.prod), `celery -A nbte_ict worker` (needs Redis).
* **MySQL/MariaDB note:** load the timezone tables once if you later use DB-side date functions
  (`TruncDate`, `date_hierarchy`, ...): `mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql mysql`.
  Nothing in the current code needs them.

## Upgrading an existing database
    python manage.py migrate
    python manage.py seed_config               # adds the new email settings (never overwrites edits)
    python manage.py seed_departments
    python manage.py seed_roles --sync         # adds any missing baseline permissions to EXISTING roles (never removes)
Then assign a department to every existing user (Admin > Users). A user with no department sees
no department-scoped data (staff list, etc.) unless they are a superuser or hold "access all departments".

## Departments
* `Department` has a stable `code` (e.g. `ict`). Users and staff records both have a department (kept in step).
* Scoped permissions: `user.has_perm_for_department(perm, dept)`, `user.can_access_department(dept)`,
  `accounts.scoping.scope_to_department(queryset, user)`. The right `accounts.access_all_departments` lifts the limit.
* Department-specific behaviour: add a config row with scope `department:<code>`; read with
  `config.get_for_user(key, user, default)` (falls back to the global value).

## Managing groups and permissions

Permissions come from one resolver, `accounts/permissions_service.py`, which
`RolePermissionBackend` uses for every `has_perm` / `{{ perms }}` check:

    role baseline + role groups + direct groups + direct permissions
    + grant overrides − revoke overrides          (superusers: everything)

- **Pages:** sidebar → *User Management* (Users, Groups, Roles, Permission
  Matrix), or `/accounts/access/…`. Needs `auth.*_group` for group CRUD and
  `accounts.assign_groups` to give groups/permissions to roles and people.
- **Rules (server-side):** nobody can change a role or person ranked above
  them, or assign a group/permission they don't hold themselves.
- **Start from the standard set:** `python manage.py migrate` then
  `python manage.py seed_default_groups` (safe to re-run; never overwrites a
  group that already exists).
- **Django admin** edits the same data: *Groups* (Django's own permission
  picker + description) and *Roles* (new *Groups* field).
- Every change is written to the audit log.

Full design notes: `../GROUPS_PERMISSIONS.md`.
