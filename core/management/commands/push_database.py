"""v2.3: copy this database, every row, to another database (e.g. Aiven Postgres).

    python manage.py push_database --to "postgres://avnadmin:…@….aivencloud.com:12345/defaultdb?sslmode=require"
    python manage.py push_database --to "$TARGET_DATABASE_URL" --replace     # overwrite a database that has data

Run it on the machine that holds the database you want to copy (normally your
laptop, with DATABASE_BACKEND=sqlite). It:

1. migrates the target, so it has exactly this code's tables;
2. refuses to touch a target that already has users, unless --replace (which
   empties every table first);
3. copies every table — content types and permissions included, keeping every
   id, so links between records (and "generic" links such as timelines and
   audit entries) stay correct — in ONE transaction: if anything fails,
   nothing is half-copied;
4. resets the id sequences and checks that every table has the same number of
   rows on both sides.

Uploaded files are not in the database: copy the media and protected_media
folders to the server's disk separately (see DEPLOY_RENDER.md).
"""
from __future__ import annotations

import time

import dj_database_url
from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

TARGET = "push_target"
CHUNK = 2000


def _models():
    """Every concrete table, auto-created many-to-many tables included."""
    seen, out = set(), []
    for model in apps.get_models(include_auto_created=True):
        meta = model._meta
        if meta.proxy or not meta.managed or meta.db_table in seen:
            continue
        seen.add(meta.db_table)
        out.append(model)
    return out


def _manager(model):
    # The base manager never applies need-to-know scoping or soft-delete filters.
    return model._base_manager


class Command(BaseCommand):
    help = "Copy every row of this database to another database (e.g. Aiven Postgres for a Render demo)."

    def add_arguments(self, parser):
        parser.add_argument("--to", dest="url", required=True, help="Target database URL.")
        parser.add_argument("--replace", action="store_true",
                            help="Empty the target first (it already has data you want to overwrite).")
        parser.add_argument("--no-migrate", action="store_true", help="The target is already migrated.")

    def handle(self, *args, **opts):
        url = (opts["url"] or "").strip()
        if not url:
            raise CommandError("Give the target database URL with --to.")
        target = dj_database_url.parse(url, conn_max_age=0)
        if target["ENGINE"].endswith("postgresql"):
            target.setdefault("OPTIONS", {})
            if "sslmode" not in target["OPTIONS"] and target.get("HOST") not in ("localhost", "127.0.0.1", ""):
                target["OPTIONS"]["sslmode"] = "require"
        source = settings.DATABASES["default"]
        if (target.get("NAME"), target.get("HOST")) == (source.get("NAME"), source.get("HOST")):
            raise CommandError("The target is the same database as the source.")
        connections.databases[TARGET] = {**connections.databases["default"], **target,
                                         "TIME_ZONE": None, "ATOMIC_REQUESTS": False, "AUTOCOMMIT": True,
                                         "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": False, "TEST": {}}
        conn = connections[TARGET]
        try:
            conn.ensure_connection()
        except Exception as exc:
            raise CommandError(f"Cannot connect to the target database: {exc}") from exc
        self.stdout.write(f"Target: {target['ENGINE'].rsplit('.', 1)[-1]} on {target.get('HOST') or 'local'}")

        if not opts["no_migrate"]:
            self.stdout.write("1/4 Migrating the target …")
            # In a child process whose *default* database is the target: data
            # migrations and post-migrate hooks then read and write the target.
            import os
            import subprocess
            import sys

            env = {**os.environ, "DATABASE_URL": url, "DATABASE_BACKEND": "url"}
            done = subprocess.run([sys.executable, "manage.py", "migrate", "--noinput", "-v", "0"],
                                  cwd=str(settings.BASE_DIR), env=env)
            if done.returncode != 0:
                raise CommandError("Migrating the target failed (see the messages above).")

        from accounts.models import User

        models = _models()
        has_data = _manager(User).using(TARGET).exists()
        if has_data and not opts["replace"]:
            raise CommandError("The target already has users. Run again with --replace to overwrite it "
                               "(every table on the target is emptied first).")

        started = time.monotonic()
        totals = {}
        with transaction.atomic(using=TARGET):
            self.stdout.write("2/4 Emptying the target tables …")
            self._empty(conn, models)
            self.stdout.write("3/4 Copying …")
            for model in models:
                n = self._copy(model)
                if n:
                    totals[model._meta.label] = n
        self.stdout.write("4/4 Resetting id sequences and checking …")
        self._reset_sequences(conn, models)
        problems = []
        for model in models:
            # Compare with what was read during the copy: the site may keep
            # writing here (sessions, audit) while the copy runs.
            a = totals.get(model._meta.label, 0)
            b = _manager(model).using(TARGET).count()
            if a != b:
                problems.append(f"{model._meta.label}: {a} copied, {b} there")
        if problems:
            raise CommandError("Row counts differ after the copy:\n  " + "\n  ".join(problems))
        rows = sum(totals.values())
        self.stdout.write(self.style.SUCCESS(
            f"Done: {rows:,} rows in {len(totals)} tables copied in {time.monotonic() - started:.0f}s. "
            "Now copy the media/ and protected_media/ folders to the server's disk."))

    # ------------------------------------------------------------------
    def _empty(self, conn, models):
        tables = [m._meta.db_table for m in models if m._meta.db_table != "django_migrations"]
        with conn.cursor() as cur:
            if conn.vendor == "postgresql":
                quoted = ", ".join(conn.ops.quote_name(t) for t in tables)
                cur.execute(f"TRUNCATE {quoted} CASCADE")
            elif conn.vendor == "sqlite":
                cur.execute("PRAGMA defer_foreign_keys = ON")
                for t in tables:
                    cur.execute(f"DELETE FROM {conn.ops.quote_name(t)}")
            else:  # MySQL
                cur.execute("SET FOREIGN_KEY_CHECKS = 0")
                for t in tables:
                    cur.execute(f"DELETE FROM {conn.ops.quote_name(t)}")

    def _copy(self, model):
        fields = [f for f in model._meta.concrete_fields]
        names = [f.attname for f in fields]
        # Keep the original timestamps: auto_now / auto_now_add would stamp "now".
        stamped = [f for f in fields if getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False)]
        saved = [(f, f.auto_now, f.auto_now_add) for f in stamped]
        for f in stamped:
            f.auto_now = f.auto_now_add = False
        try:
            return self._copy_rows(model, names)
        finally:
            for f, now, add in saved:
                f.auto_now, f.auto_now_add = now, add

    def _copy_rows(self, model, names):
        qs = _manager(model).using("default").order_by("pk").values_list(*names)
        # SQLite does not enforce text lengths; Postgres does. Trim (and report)
        # any value longer than its column so one old row cannot stop the copy.
        limits = {f.attname: f.max_length for f in model._meta.concrete_fields
                  if getattr(f, "max_length", None) and f.get_internal_type() in
                  ("CharField", "SlugField", "EmailField", "URLField", "FileField", "ImageField")}
        batch, n = [], 0
        for row in qs.iterator(chunk_size=CHUNK):
            values = dict(zip(names, row))
            for name, limit in limits.items():
                v = values.get(name)
                if isinstance(v, str) and len(v) > limit:
                    self.stdout.write(self.style.WARNING(
                        f"   note: {model._meta.label}.{name} (id {values.get(model._meta.pk.attname)}) "
                        f"trimmed to {limit} characters"))
                    values[name] = v[:limit]
            obj = model(**values)
            batch.append(obj)
            if len(batch) >= CHUNK:
                _manager(model).using(TARGET).bulk_create(batch, batch_size=CHUNK)
                n += len(batch)
                batch = []
        if batch:
            _manager(model).using(TARGET).bulk_create(batch, batch_size=CHUNK)
            n += len(batch)
        if n:
            self.stdout.write(f"   {model._meta.label:<55} {n:>8,}")
        return n

    def _reset_sequences(self, conn, models):
        from django.core.management.color import no_style

        sql = conn.ops.sequence_reset_sql(no_style(), models)
        if sql:
            with conn.cursor() as cur:
                for statement in sql:
                    cur.execute(statement)
