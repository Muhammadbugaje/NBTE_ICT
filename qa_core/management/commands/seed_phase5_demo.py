"""seed_phase5_demo — the Phase 5 demo: inspection, resource persons, reports.

    python manage.py seed_phase5_demo                    # case at stage 12, visit recorded, reports in draft
    python manage.py seed_phase5_demo --leave-visit-open # … stage 12 but the Team Lead still records the visit
    python manage.py seed_phase5_demo --reset-passwords

What it does (idempotent — safe to run again):
1. runs ``seed_startup`` (Phase 5 flows, settings, permissions, policies);
2. creates the Phase 4 demo accounts plus the Phase 5 ones (Team Lead,
   Division Head, Deputy Director, a second QA officer), the designations the
   §9 rules read, eight resource persons (each illustrates one §9 rule; two
   have portal accounts), the QA personnel and the backfilled history of the
   "annual cap reached" person;
3. drives the Phase 4 demo case to stage 8 (visit dates), maps the two
   eligible assessors through the finder rules (booked, then confirmed by a
   second officer), then on to stage 12 — the ES visit approval creates the
   inspection, the Pay Officer releases the funds;
4. records the visit as the Team Lead (unless --leave-visit-open), so the
   post-visit reports wait in draft for the Team Lead; the engineering
   assessor submits findings on the portal;
5. prints every account with its password and what to do next.

Refused when DEBUG is false unless ``--i-understand-production``.
"""
import contextlib
import io
import logging
import secrets

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

STAGE_REPORT = 12
RP_ILLUSTRATES = {
    "rp_eng": "eligible — engineering (mapped to EEE; portal account)",
    "rp_sci": "eligible — science/computing (mapped to Computer Science; portal account)",
    "rp_sci2": "eligible — science/computing (ranked by fewest visits)",
    "rp_nominated": "status: nominated, waiting for a vetter",
    "rp_coi": "conflict of interest — works at the demo institution",
    "rp_lowrank": "rank not in required_designations → 'No longer eligible' badge",
    "rp_health": "discipline: health only",
    "rp_capped": "annual cap reached (backfilled visits this year)",
}


class Command(BaseCommand):
    help = "Create the Phase 5 demo (inspection, resource persons, post-visit reports) and print credentials."

    def add_arguments(self, parser):
        parser.add_argument("--reset-passwords", action="store_true",
                            help="Give every demo account a new password (printed once).")
        parser.add_argument("--leave-visit-open", action="store_true",
                            help="Stop at stage 12 with the inspection still scheduled (record it yourself).")
        parser.add_argument("--skip-startup", action="store_true", help="Do not run seed_startup first.")
        parser.add_argument("--i-understand-production", action="store_true",
                            help="Allow running when DEBUG is false.")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["i_understand_production"]:
            raise CommandError("seed_phase5_demo creates demo accounts; it is refused when DEBUG is false. "
                               "Add --i-understand-production if you really mean it.")
        if not opts["skip_startup"]:
            call_command("seed_startup", stdout=io.StringIO(), stderr=io.StringIO())
            self.stdout.write("seed_startup: done (Phase 5 flows, settings, permissions, policies).")
        force_change = not settings.DEBUG
        noisy = [logging.getLogger(n) for n in ("notifications.engine", "tasks.services", "tasks")]
        levels = [lg.level for lg in noisy]
        for lg in noisy:
            lg.setLevel(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = self._build(opts, force_change)
        finally:
            for lg, level in zip(noisy, levels):
                lg.setLevel(level)
        self._print(*result, force_change=force_change)

    # ------------------------------------------------------------------
    def _build(self, opts, force_change):
        from accreditation import case_demo
        from accreditation import case_services as cs
        from accreditation.models import QACase
        from inspection import demo

        with transaction.atomic():
            demo.ensure_designations()
            inst = case_demo.ensure_institution()
            people, creds = case_demo.ensure_accounts(reset_passwords=opts["reset_passwords"],
                                                      force_change=force_change)
            rps = demo.ensure_resource_persons(people, inst)
            demo.ensure_qa_personnel(people)
            demo.backfill_capped(rps["rp_capped"])
            creds += self._rp_passwords(rps, opts["reset_passwords"], force_change)

            case = case_demo.demo_case()
            created = case is None
            if case is None:
                case = case_demo.create_case(people, inst)
            # Phase 4 cases created before Phase 5 lack the endorsement-layer pins.
            pins = dict(case.stage_actors or {})
            for key, user in case_demo.demo_pins(people).items():
                if user is not None and key not in pins:
                    pins[key] = user.pk
            if pins != (case.stage_actors or {}):
                QACase.unscoped.filter(pk=case.pk).update(stage_actors=pins)
                case.refresh_from_db()

            note = ""
            if case_demo.stage_no(case) < 10:
                with cs.pinned_actors(case_demo.demo_pins(people)):
                    case = case_demo.advance(case, people, 9)  # visit dates entered (stage 8 done)
                    demo.map_demo_people(case, people, rps)
                    case = case_demo.advance(case, people, STAGE_REPORT)
            elif case_demo.stage_no(case) > STAGE_REPORT:
                note = ("The demo case is already past stage 12. To replay Phase 5 from the start, run "
                        "`python manage.py seed_startup --reset-demo` and then this command again.")
            if case_demo.stage_no(case) == STAGE_REPORT and not opts["leave_visit_open"]:
                demo.complete_visit(case)
                demo.submit_demo_findings(case, rps)
        return case, people, rps, creds, created, note

    def _rp_passwords(self, rps, reset, force_change):
        out = []
        for key in ("rp_eng", "rp_sci"):
            rp = rps.get(key)
            user = getattr(rp, "user", None)
            if user is None:
                continue
            password = None
            if reset or not user.has_usable_password():
                password = secrets.token_urlsafe(9)
                user.set_password(password)
                user.must_change_password = force_change
                user.save(update_fields=["password", "must_change_password"])
            out.append((key, user.email, password, "resource-person", "QA portal"))
        return out

    # ------------------------------------------------------------------
    def _print(self, case, people, rps, creds, created, note, *, force_change):
        from accreditation import case_services as cs
        from inspection import services

        w = self.stdout.write
        w(self.style.SUCCESS("NBTE QA — Phase 5 demo ready"))
        w(f"  Case {case.reference} for {case.institution.name} — {'created' if created else 'existing'}.")
        for stage in cs.stage_objects(case):
            no = f"stage {stage.sop_stage_no}" if stage.sop_stage_no else stage.code
            w(f"  Now at {no}: {stage.label}")
        inspection = services.live_inspection(case)
        if inspection is not None:
            w(f"  Inspection {inspection.reference}: {inspection.status}, "
              f"team lead {inspection.team_lead.email if inspection.team_lead else '—'}")
            for report in services.reports_of(case):
                holders = ", ".join(u.email for u in services.report_holders(report)[0]) or "—"
                w(f"    Report — {report.qa_request_programme.programme}: {report.status} → {holders}")
        if note:
            w(self.style.WARNING("  " + note))
        w("")
        w("  Resource persons (each illustrates a §9 rule in the Availability Finder):")
        for key, rp in rps.items():
            w(f"    {key:<14}{rp.full_name:<32}{rp.get_status_display():<11}{RP_ILLUSTRATES.get(key, '')}")
        w("")
        w("  Demo accounts (sign in with the e-mail address):")
        w(f"    {'who':<18}{'e-mail':<40}{'password':<16}role")
        for key, email, password, role, dept in creds:
            w(f"    {key:<18}{email:<40}{(password or '(unchanged)'):<16}{role}{(' · ' + dept) if dept else ''}")
        if any(not c[2] for c in creds):
            w("    (unchanged) = the account already existed; run with --reset-passwords to get new passwords.")
        if force_change:
            w(self.style.WARNING("  Every new password must be changed at first sign-in."))
        w("")
        w("  Next (PHASE5_WALKTHROUGH.docx): sign in as the Team Lead → Inspection → Reports → write and submit;")
        w("  then the Division Head endorses, the Deputy Director returns with a remark, the Team Lead resubmits.")
        w("  Internal: http://localhost:8000/qa/inspection/inspections/   Portal: the QA portal host, /engagements/")
