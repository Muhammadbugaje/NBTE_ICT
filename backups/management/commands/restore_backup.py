"""python manage.py restore_backup --drill <id>
python manage.py restore_backup <id> --confirm <label> [--db-only | --media-only]
python manage.py restore_backup --from-dir <folder> --confirm <label> [...]

--drill: verify the checksums, restore the dump into a scratch database, run
`manage.py check` and a smoke test of key table counts, record a RestoreDrill
and clean up. The live system is never touched.

Without --drill this is the REAL restore: it overwrites the live database and
file roots (the current ones are kept aside as *.before-restore-<time>). It is
a superuser-only runbook step (BACKUPS.md, "Restore runbook"), never a button:
you must name an active superuser with --as and repeat the backup's label with
--confirm. Stop the web server and Celery workers first.

--from-dir restores from a folder holding manifest.json and the artefacts
(e.g. copied off a USB drive or downloaded from S3) — for when the database
that lists the runs is itself gone.
"""
from django.core.management.base import BaseCommand, CommandError

from backups import engine
from backups.models import BackupRun, RestoreDrill


class Command(BaseCommand):
    help = "Run a restore drill (--drill <id>) or, for superusers, a real restore."

    def add_arguments(self, parser):
        parser.add_argument("run_id", nargs="?", type=int, help="BackupRun id (real restore).")
        parser.add_argument("--drill", type=int, metavar="ID", help="Run a restore drill of this BackupRun.")
        parser.add_argument("--from-dir", help="Restore from a folder holding manifest.json and the artefacts.")
        parser.add_argument("--confirm", help="The backup's label, typed again, to confirm a real restore.")
        parser.add_argument("--as", dest="as_user", help="E-mail of the active superuser doing the restore.")
        parser.add_argument("--db-only", action="store_true", help="Restore only the database.")
        parser.add_argument("--media-only", action="store_true", help="Restore only the file roots.")
        parser.add_argument("--skip-check", action="store_true",
                            help="Drill only: skip the `manage.py check` step (faster).")

    def handle(self, *args, **opts):
        if opts["drill"]:
            return self._drill(opts)
        return self._restore(opts)

    def _drill(self, opts):
        run = BackupRun.objects.filter(pk=opts["drill"]).first()
        if run is None:
            raise CommandError(f"No backup run with id {opts['drill']}.")
        try:
            drill = engine.start_drill(run, None)
        except (engine.AlreadyRunning, engine.BackupFailed) as exc:
            raise CommandError(str(exc))
        drill = engine.perform_drill(drill.pk, run_django_check=not opts["skip_check"])
        self.stdout.write(drill.report)
        if drill.result != RestoreDrill.PASSED:
            raise CommandError(f"Restore drill {drill.pk} FAILED.")
        self.stdout.write(self.style.SUCCESS(f"Restore drill {drill.pk} passed."))

    def _restore(self, opts):
        from accounts.models import User

        if opts["db_only"] and opts["media_only"]:
            raise CommandError("Use --db-only or --media-only, not both.")
        user = User.objects.filter(email__iexact=(opts["as_user"] or "").strip(), is_superuser=True,
                                   is_active=True).first() if opts["as_user"] else None
        run = None
        if opts["from_dir"]:
            import json
            from pathlib import Path

            try:
                label = json.loads((Path(opts["from_dir"]) / engine.MANIFEST_NAME).read_text())["label"]
            except Exception as exc:
                raise CommandError(f"Cannot read {engine.MANIFEST_NAME} in {opts['from_dir']}: {exc}")
        else:
            if not opts["run_id"]:
                raise CommandError("Give a BackupRun id, --from-dir <folder>, or --drill <id>.")
            run = BackupRun.objects.filter(pk=opts["run_id"]).first()
            if run is None:
                raise CommandError(f"No backup run with id {opts['run_id']}.")
            label = run.label
            if user is None:
                raise CommandError("A real restore is for superusers only: pass --as <superuser e-mail>.")
        if opts["confirm"] != label:
            raise CommandError(f"This overwrites the live data. Repeat the backup label to confirm: "
                               f"--confirm {label}")
        self.stdout.write(self.style.WARNING(f"Restoring {label} — stop the web server and workers first."))
        engine.restore(run=run, from_dir=opts["from_dir"], include_db=not opts["media_only"],
                       include_media=not opts["db_only"], log=self.stdout.write)
        self.stdout.write(self.style.SUCCESS("Restore finished. Run `python manage.py migrate` and "
                                             "`python manage.py check`, then start the services."))
