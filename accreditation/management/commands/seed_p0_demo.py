"""seed_p0_demo — demo data for the v2.1 Phase 0 walkthroughs (idempotent).

    python manage.py seed_p0_demo

Creates (all tagged in ``qa_core.DemoTag``; password "NBTE-Demo#2026", DEBUG only):

* ``ntird.director@demo.nbte.local`` — Director, NTIRD (clears onboarding programmes, Q2).
* The four Phase 2 clearance officers (Legal, Finance, Academic Planning,
  Physical Planning) become **ClearanceDelegates** of their departments,
  granted by the demo ES — officers no longer clear by role (O10).
* ``ce@demo-poly.local`` — Chief Executive of Demo Polytechnic (DMP), so DMP
  can still submit QA requests and SSQs (X12).
* **DAPE — Demo Applicant Polytechnic** (proposed): Chief Executive
  ``applicant.e@demo-poly.local`` and Head of ICT ``applicant.e.ict@demo-poly.local``;
  its application is at *programme selection* with two programmes listed
  (Q1 → Q2 walkthrough).
* **DAPD — Demo Returned-Document College** (proposed): Chief Executive
  ``applicant.d@demo-poly.local``; its application is in departmental
  clearance and Legal returned one document (O8/O9 walkthrough).

Phase B moves this into ``seed_nbte --with-demo``.
"""
# scoped-by-caller
from __future__ import annotations

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
DEMO_PASSWORD = "NBTE-Demo#2026"


class Command(BaseCommand):
    help = "Demo data for the v2.1 Phase 0 walkthroughs (programme submission, clearance delegates, re-upload)."

    def add_arguments(self, parser):
        parser.add_argument("--i-understand-production", action="store_true")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["i_understand_production"]:
            raise CommandError("Demo data with a shared password is for development only.")
        with transaction.atomic():
            notes = self._run()
        for line in notes:
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS("Phase 0 demo ready."))

    # ------------------------------------------------------------------
    def _user(self, email, first, last, role, *, dept=None, institution=None, notes):
        from accounts.models import Role, User
        from qa_core.departments import qa_department
        from qa_core.models import DemoTag

        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            return user
        user = User(email=email, first_name=first, last_name=last, role=Role.objects.filter(code=role).first(),
                    user_type="institution" if institution is not None else "staff", institution=institution,
                    department=qa_department(dept) if dept else None, is_active=True, must_change_password=False)
        user.set_password(DEMO_PASSWORD)
        user.save()
        DemoTag.tag(user)
        if institution is not None:
            from institutions.models import InstitutionUser

            InstitutionUser.unscoped.get_or_create(user=user, defaults={
                "institution": institution, "institution_role": user.role, "is_primary_contact": False,
                "job_title": "Chief Executive" if role == "inst-chief-executive" else "Head of ICT"})
        notes.append(f"created {email} ({role})")
        return user

    def _proposed(self, code, name, *, ce, ict=None, notes):
        from accounts.models import User
        from institutions import services as inst_services
        from institutions.models import (
            Institution, InstitutionCategory, InstitutionOwnership, InstitutionType, InstitutionUser,
        )
        from qa_core.models import DemoTag
        from staff.models import StateOfOrigin

        inst = Institution.unscoped.filter(code=code).first()
        if inst is not None:
            return inst, False
        state = StateOfOrigin.objects.filter(name__iexact="KADUNA").first()
        lga = (inst_services.lgas_for(state) or ["Kaduna North"])[0]
        inst = inst_services.create_institution({
            "name": name, "code": code, "ownership": InstitutionOwnership.objects.filter(code="private").first(),
            "institution_type": InstitutionType.objects.filter(code="polytechnic").first(),
            "category": InstitutionCategory.objects.filter(code="polytechnic").first(), "state": state, "lga": lga,
            "year_established": 2024, "cac_rc_number": "RC DEMO", "website": "",
            "contacts": {"official_email": {"value": f"info@{code.lower()}.demo-poly.local"},
                         "phone": {"value": "08030000000"},
                         "head_of_institution": {"name": f"{ce[1]} {ce[2]}", "value": ""}},
            "address": {"line1": "Demo Road", "town": "Kaduna", "lga": lga, "state": state},
            "admin": {"first_name": ce[1], "last_name": ce[2], "email": ce[0], "job_title": "Rector",
                      "role": "inst-chief-executive"},
        }, origin="self", activate_admin=True, admin_password=DEMO_PASSWORD)
        DemoTag.tag(inst)
        DemoTag.tag(User.objects.get(email__iexact=ce[0]))
        notes.append(f"created proposed institution {code} with Chief Executive {ce[0]}")
        if ict:
            self._user(ict[0], ict[1], ict[2], "inst-admin", institution=inst, notes=notes)
            InstitutionUser.unscoped.filter(user__email__iexact=ict[0]).update(job_title="Head of ICT")
        return inst, True

    def _upload_all(self, inst, user, app):
        from institutions import services as inst_services

        for row in inst_services.checklist(inst):
            if row["state"] in ("missing", "returned", "expired"):
                inst_services.upload_document(inst, row["kind"], SimpleUploadedFile(
                    f"{row['kind'].code}.pdf", PDF, content_type="application/pdf"), user=user, application=app)

    def _run(self):
        from accounts.models import User
        from accreditation import clearance_access, services
        from accreditation.models import Application
        from institutions import services as inst_services
        from institutions.models import Institution, ProgrammeLevel, ProgrammeCategory
        from qa_core import flow
        from qa_core.departments import qa_department

        notes = []
        # --- staff: NTIRD director and clearance delegates (O10, Q2) ------------
        self._user("ntird.director@demo.nbte.local", "Nkechi", "Director NTIRD (Demo)", "director", dept="ntird",
                   notes=notes)
        es = User.objects.filter(email__iexact="qa.es@demo.nbte.local").first() \
            or User.objects.filter(role__code="executive-secretary", is_active=True).first()
        for email, dept_code in (("legal.officer@demo.nbte.local", "legal"),
                                 ("finance.clearance@demo.nbte.local", "finance"),
                                 ("planning.academic@demo.nbte.local", "academic-planning"),
                                 ("planning.physical@demo.nbte.local", "physical-planning")):
            officer = User.objects.filter(email__iexact=email).first()
            dept = qa_department(dept_code)
            if officer is None or dept is None or es is None:
                continue
            if officer.department_id != dept.pk:
                continue
            if not clearance_access.live_delegations(dept, officer).exists():
                clearance_access.add_delegate(dept, officer, granted_by=es, note="Demo delegation (Phase 0)")
                notes.append(f"delegated {dept.name} clearance to {email}")

        # --- DMP Chief Executive (X12) ------------------------------------------
        dmp = Institution.unscoped.filter(code="DMP").first()
        if dmp is not None:
            self._user("ce@demo-poly.local", "Musa", "Aliyu (Rector, Demo Poly)", "inst-chief-executive",
                       institution=dmp, notes=notes)

        # --- DAPE: application at programme selection (Q1/Q2) -------------------
        ce = ("applicant.e@demo-poly.local", "Esther", "Rector (Demo Applicant)")
        inst, created = self._proposed("DAPE", "Demo Applicant Polytechnic", ce=ce,
                                       ict=("applicant.e.ict@demo-poly.local", "Emeka", "Head of ICT (Demo)"),
                                       notes=notes)
        if created:
            user = User.objects.get(email__iexact=ce[0])
            app = services.start_application(inst, user=user)
            self._upload_all(inst, user, app)
            instance = services.instance_of(app)
            flow.force_stage(instance, "programme_selection", None,
                             remark="Demo: documents cleared and institutional fee confirmed (fast-forward).")
            nd = ProgrammeLevel.objects.filter(code="nd").first()
            for name, cat, streams in (("Computer Science", "Computing & Information Technology", 2),
                                       ("Electrical/Electronic Engineering Technology", "Engineering Technology", 1)):
                inst_services.add_proposed_programme(inst, name, nd, ProgrammeCategory.objects.filter(name=cat)
                                                     .first(), application=app, user=user, streams=streams)
            notes.append("DAPE application is at programme selection with two programmes")

        # --- DAPD: application in clearance with one returned document (O8/O9) --
        ce = ("applicant.d@demo-poly.local", "Dauda", "Provost (Demo College)")
        inst, created = self._proposed("DAPD", "Demo Returned-Document College", ce=ce, notes=notes)
        if created:
            user = User.objects.get(email__iexact=ce[0])
            app = services.start_application(inst, user=user)
            self._upload_all(inst, user, app)
            services.submit(app, user=user, declaration=True)
            app = Application.unscoped.get(pk=app.pk)
            legal = qa_department("legal")
            reviewer = User.objects.filter(email__iexact="legal.officer@demo.nbte.local").first()
            row = next((r for r in inst_services.checklist(inst)
                        if r["department"] is not None and legal is not None and r["department"].pk == legal.pk
                        and r["document"] is not None), None)
            if row is not None and reviewer is not None:
                inst_services.review_document(row["document"], "return", user=reviewer,
                                              remark="The copy is not certified. Upload a certified true copy.")
                notes.append(f"DAPD: Legal returned {row['kind'].name}")
        # --- DMP: an open SSQ, for the institution SSQ view without "Required" (X35) --
        if dmp is not None:
            self._dmp_ssq(dmp, notes)
        return notes

    def _dmp_ssq(self, dmp, notes):
        from accounts.models import User
        from accreditation import qa_services as svc
        from accreditation.models import QARequest, QAType
        from payments import services as pay
        from qa_core.models import DemoTag

        if QARequest.unscoped.filter(institution=dmp).exclude(status__in=("cancelled", "draft")).exists():
            return
        ce = User.objects.filter(email__iexact="ce@demo-poly.local").first()
        from payments.services import finance_director
        fd = finance_director()  # Phase B: the Finance director seat
        if fd is not None and not fd.email.lower().endswith("@demo.nbte.local"):
            fd = None
        if ce is None or fd is None:
            return
        for qa_type in QAType.objects.filter(is_active=True).order_by("sort_order"):
            programmes = [r["programme"] for r in svc.eligible_programmes(dmp, qa_type) if r["eligible"]]
            if not programmes:
                continue
            try:
                req = svc.create_request(dmp, qa_type, programmes[:1], user=ce)
                svc.submit(req, user=ce, declaration=True)
            except Exception as exc:  # noqa: BLE001 - demo only: try the next type
                notes.append(f"DMP {qa_type.code}: not created ({exc})")
                continue
            for ob in svc.open_obligations(req):
                if ob.status == "issued":
                    pay.publish(ob, actor=fd)
                    ob.refresh_from_db()
                if ob.status == "awaiting_payment":
                    pay.waive(ob, actor=fd, reason="Phase 0 demo: fee waived")
                    ob.refresh_from_db()
                    svc.payment_settled(ob)
            DemoTag.tag(req)
            notes.append(f"DMP {qa_type.code} request {req.reference}: SSQ open")
            return
