"""python manage.py seed_nbte  - one command that seeds a fresh NBTE Digital
database (v2.1 X51; docs/master_plan_v2_1/specs/seed_command_spec.md).

    seed_nbte [--only SETS] [--skip SETS] [--no-deps] [--dry-run] [--with-demo]
              [--reset-demo] [--i-understand-production] [--update-unedited]
              [--strict] [--list] [--format text|json] [--data-dir PATH]

Idempotent: a second run reports no changes. Never overwrites an edit. Data
sets are registered by apps in <app>/seed_sets.py (core.seeding).

Deprecation shim (one release): `seed_nbte --with-lgas` / `--quiet` (the old
staff command, now `seed_staff_reference`) prints a note and runs the geo and
org sets.
"""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core import seeding

EXIT_FAILED, EXIT_STRICT = 1, 2


class Command(BaseCommand):
    help = "Seed reference data, settings and (optionally) demo data. Idempotent; never overwrites edits."

    def add_arguments(self, parser):
        parser.add_argument("--only", default="", help="Comma list of set names or groups "
                                                       "(base, geo, org, hr, qa, finance, workflow, demo).")
        parser.add_argument("--skip", default="", help="Comma list of sets or groups to leave out.")
        parser.add_argument("--no-deps", action="store_true", help="Run exactly the named sets.")
        parser.add_argument("--dry-run", action="store_true", help="Run in a transaction that is rolled back.")
        parser.add_argument("--with-demo", action="store_true", help="Also create the tagged demo data.")
        parser.add_argument("--reset-demo", action="store_true",
                            help="Delete exactly the DemoTag-listed rows (DEBUG only), then stop (or continue with --with-demo).")
        parser.add_argument("--i-understand-production", action="store_true")
        parser.add_argument("--update-unedited", action="store_true",
                            help="Refresh seeded rows nobody has edited since seeding.")
        parser.add_argument("--strict", action="store_true", help="Warnings count as failures (exit 2).")
        parser.add_argument("--list", action="store_true", help="List the sets, dependencies and files; check the files.")
        parser.add_argument("--format", choices=("text", "json"), default="text")
        parser.add_argument("--data-dir", default="", help="Read data files from another folder.")
        # deprecated flags of the old staff command (shim)
        parser.add_argument("--with-lgas", action="store_true", help=("Deprecated: the old staff command is "
                                                                       "seed_staff_reference."))
        parser.add_argument("--quiet", action="store_true", help="Deprecated (seed_staff_reference).")

    def handle(self, *args, **o):
        self.fmt = o["format"]
        if o["with_lgas"] or o["quiet"]:
            new_flags = ("only", "skip", "no_deps", "dry_run", "with_demo", "reset_demo", "update_unedited", "strict",
                         "list")
            if not any(o[k] for k in new_flags):
                self.stdout.write("seed_nbte --with-lgas/--quiet is deprecated: use `python manage.py "
                                  "seed_staff_reference` for the old staff reference seed. Running the geo and org "
                                  "sets of the new seed_nbte instead.")
                o["only"] = "geo,org"
        if o["list"]:
            return self.list_sets(o)
        if o["reset_demo"]:
            if not settings.DEBUG and not o["i_understand_production"]:
                raise CommandError("--reset-demo is refused when DEBUG is false. Add --i-understand-production "
                                   "if you really mean it.")
            from qa_core.management.commands.seed_startup import LegacyStartupCommand

            LegacyStartupCommand(stdout=self.stdout, stderr=self.stderr)._reset_demo(dry_run=o["dry_run"])
            if not o["with_demo"]:
                return
        only = [x for x in o["only"].split(",") if x.strip()] or None
        skip = [x for x in o["skip"].split(",") if x.strip()] or None
        try:
            results = seeding.run(only=only, skip=skip, no_deps=o["no_deps"], dry_run=o["dry_run"],
                                  update_unedited=o["update_unedited"], with_demo=o["with_demo"],
                                  data_dir=o["data_dir"] or None, verbosity=o["verbosity"], stdout=self.stdout)
        except KeyError as exc:
            raise CommandError(f"Unknown set or group: {exc}. Use --list to see them.")
        self.report(results, dry_run=o["dry_run"])
        failed = any(r.status in ("failed", "dependency-failed") for _n, r in results)
        warned = any(r.warnings for _n, r in results)
        if failed:
            raise CommandError("At least one data set failed (see above).", returncode=EXIT_FAILED)
        if o["strict"] and warned:
            raise CommandError("--strict: warnings present (see above).", returncode=EXIT_STRICT)

    # ------------------------------------------------------------------
    def list_sets(self, o):
        problems = seeding.validate_files(o["data_dir"] or None)
        rows = []
        for s in sorted(seeding.REGISTRY.values(), key=lambda s: (s.order, s.name)):
            rows.append({"name": s.name, "groups": list(s.group), "depends": list(s.depends), "files": list(s.files),
                         "version": s.version, "phase": s.phase, "demo_only": s.demo_only})
        if self.fmt == "json":
            self.stdout.write(json.dumps({"sets": rows, "file_problems": problems}, indent=1))
        else:
            self.stdout.write(f"{'Set':<24}{'Groups':<10}{'Depends on':<50}Files")
            self.stdout.write("-" * 100)
            for r in rows:
                self.stdout.write(f"{r['name']:<24}{','.join(r['groups']):<10}{', '.join(r['depends']) or '-':<50}"
                                  f"{', '.join(r['files']) or '(code)'}")
            self.stdout.write("")
            if problems:
                for rel, errs in problems.items():
                    self.stdout.write(self.style.ERROR(f"  {rel}: " + "; ".join(errs[:5])))
            else:
                self.stdout.write(self.style.SUCCESS("All data files are valid."))
        if problems:
            raise CommandError("Data files have problems (see above).", returncode=EXIT_FAILED)

    def report(self, results, *, dry_run):
        if self.fmt == "json":
            self.stdout.write(json.dumps({
                "dry_run": dry_run, "at": timezone.now().isoformat(),
                "sets": {n: r.as_dict() for n, r in results},
                "totals": seeding.totals(results).as_dict(),
                "changes": sum(r.changes for _n, r in results)}, indent=1, default=str))
            return
        w = self.stdout.write
        w(f"NBTE Digital seed — {timezone.localtime():%Y-%m-%d %H:%M} (dry run: {'yes' if dry_run else 'no'})")
        w(f"{'Set':<26}{'Created':>8}{'Filled':>8}{'Unchanged':>11}{'Edited(kept)':>14}{'Skipped':>9}{'Warnings':>10}")
        for name, r in results:
            label = name + (" (dependency)" if r.as_dependency else "")
            if r.status == "failed":
                w(self.style.ERROR(f"{label:<26}  FAILED: {r.error}"))
                continue
            if r.status in ("skipped", "dependency-failed"):
                w(f"{label:<26}  {r.status}" + (f" — {'; '.join(map(str, r.warnings))}" if r.warnings else ""))
                continue
            extra = ("   (" + "; ".join(" ".join(map(str, x)) if isinstance(x, (tuple, list)) else str(x) for x in (r.warnings + r.notes))[:90] + ")") if (r.warnings or r.notes) else ""
            w(f"{label:<26}{r.created:>8}{r.filled + r.updated_unedited:>8}{r.unchanged:>11}{r.kept_edited:>14}"
              f"{r.skipped:>9}{len(r.warnings):>10}{extra}")
        t = seeding.totals(results)
        w("-" * 86)
        w(f"{'Total':<26}{t.created:>8}{t.filled + t.updated_unedited:>8}{t.unchanged:>11}{t.kept_edited:>14}"
          f"{t.skipped:>9}{len(t.warnings):>10}")
        changes = sum(r.changes for _n, r in results)
        if dry_run:
            w(self.style.WARNING("Dry run: nothing was written."))
        elif not changes:
            w(self.style.SUCCESS("No changes — everything is already up to date."))
        else:
            w(self.style.SUCCESS(f"{changes} change(s) applied."))
        w("Next: confirm placeholders in Settings (fees, grade levels, zonal e-mails, bank list); "
          "run with --strict before go-live.")
