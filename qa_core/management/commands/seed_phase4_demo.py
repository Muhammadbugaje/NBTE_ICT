"""seed_phase4_demo — a ready-to-drive SOP-03 case (Phase 4).

    python manage.py seed_phase4_demo                 # case at stage 2 (generate cost)
    python manage.py seed_phase4_demo --stage 3       # … or advanced to stage 3, 5, 11 …
    python manage.py seed_phase4_demo --reset-passwords
    python manage.py seed_phase4_demo --new-case      # another case (a fresh demo request)

What it does (idempotent — safe to run again):
1. runs ``seed_startup`` (flows, settings, the rate table, task categories …);
2. creates the Phase 4 demo accounts (each with a staff profile and grade
   level) and the demo institution *Kaduna Demo Polytechnic (Phase 4)*;
3. creates a QA request for its two programmes, waives the fee, submits both
   SSQs — which creates the QA case and starts SOP-03 — and drives the case to
   the requested stage through the real services, so every stage creates and
   closes real Tasks in the Tasks app;
4. prints every account with its password (new accounts, or all with
   ``--reset-passwords``), the case, its stage and who holds the task.

Refused when DEBUG is false unless ``--i-understand-production``.
"""
import io

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create (or advance) the Phase 4 demo QA case and print the demo credentials."

    def add_arguments(self, parser):
        parser.add_argument("--stage", type=int, default=2,
                            help="Advance the demo case to this SOP-03 stage number (0–18; default 2).")
        parser.add_argument("--reset-passwords", action="store_true",
                            help="Give every demo account a new password (printed once).")
        parser.add_argument("--new-case", action="store_true",
                            help="Create another demo case even if one exists.")
        parser.add_argument("--skip-startup", action="store_true", help="Do not run seed_startup first.")
        parser.add_argument("--i-understand-production", action="store_true",
                            help="Allow running when DEBUG is false.")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["i_understand_production"]:
            raise CommandError("seed_phase4_demo creates demo accounts; it is refused when DEBUG is false. "
                               "Add --i-understand-production if you really mean it.")
        if not 0 <= opts["stage"] <= 18:
            raise CommandError("--stage must be between 0 and 18.")
        if not opts["skip_startup"]:
            buffer = io.StringIO()
            call_command("seed_startup", stdout=buffer, stderr=io.StringIO())
            self.stdout.write("seed_startup: done (flows, settings, rate table, task categories).")

        force_change = not settings.DEBUG
        import contextlib
        import logging
        import sys

        quiet = io.StringIO()
        noisy = [logging.getLogger(n) for n in ("notifications.engine", "tasks.services", "tasks")]
        levels = [lg.level for lg in noisy]
        for lg in noisy:
            lg.setLevel(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(io.StringIO()):
                case, people, creds, created = self._build(opts, force_change)
        finally:
            for lg, level in zip(noisy, levels):
                lg.setLevel(level)
        self._print(case, people, creds, created, force_change)

    def _build(self, opts, force_change):
        from accreditation import case_demo

        with transaction.atomic():
            inst = case_demo.ensure_institution()
            people, creds = case_demo.ensure_accounts(reset_passwords=opts["reset_passwords"],
                                                      force_change=force_change)
            case = None if opts["new_case"] else case_demo.demo_case()
            created = False
            if case is None:
                from django.core.exceptions import ValidationError

                try:
                    case = case_demo.create_case(people, inst)
                except ValidationError as exc:
                    raise CommandError(
                        "Could not open a new demo case: " + "; ".join(exc.messages) +
                        " Run without --new-case to keep using the existing demo case.") from exc
                created = True
            case = case_demo.advance(case, people, opts["stage"])
        return case, people, creds, created

    def _print(self, case, people, creds, created, force_change):
        from accreditation import case_services as cs

        w = self.stdout.write
        w(self.style.SUCCESS("NBTE QA — Phase 4 demo ready"))
        w(f"  Case {case.reference} for {case.institution.name} ({case.programme_count} programmes) — "
          f"{'created' if created else 'already existed'}.")
        stages = cs.stage_objects(case)
        for stage in stages:
            holders = ", ".join(u.email for u in cs.holders(case, stage.code)) or "—"
            if stage.kind == "end":
                w(f"  The case has completed SOP-03 ({stage.label}); no open tasks.")
                continue
            no = f"stage {stage.sop_stage_no}" if stage.sop_stage_no else "case opened"
            w(f"  Now at {no}: {stage.label} → task for {holders}")
        w("  Internal system: http://localhost:8000/qa/cases/   (Tasks: http://localhost:8000/tasks/assigned/)")
        w("")
        w("  Demo accounts (sign in with the e-mail address):")
        w(f"    {'who':<18}{'e-mail':<38}{'password':<16}role")
        for key, email, password, role, dept in creds:
            shown = password or "(unchanged)"
            w(f"    {key:<18}{email:<38}{shown:<16}{role}{(' · ' + dept) if dept else ''}")
        if any(not c[2] for c in creds):
            w("    (unchanged) = the account already existed; run with --reset-passwords to get new passwords.")
        if force_change:
            w(self.style.WARNING("  Every new password must be changed at first sign-in."))
        else:
            w("  DEBUG is on, so these demo accounts are not forced to change their password.")
        w("  Remove the demo with: python manage.py seed_startup --reset-demo")
