"""Phase 2 demo data for ``seed_startup --with-demo`` (Master Plan §15).

Everything created here is tagged in ``qa_core.DemoTag`` (or hangs off a
tagged institution or user) so ``--reset-demo`` removes exactly it:

* Demo Polytechnic, Kaduna (DMP, Private) gets its full profile, contacts,
  address and two programmes; Demo College of Health Technology (DCH, State)
  is added with one programme.
* A demo bank account (DEMO, active) so slips can be issued.
* One paid obligation (confirmed by the demo Director of Finance, with its
  Payment Advice) and one pending obligation (awaiting payment).
* The clearance officers (Legal, Finance, Academic Planning, Physical
  Planning) and the Director of Finance, plus a DCH portal administrator.
* A draft QA request container on DMP, so "Wipe Institution Requests" and
  "Cancel Request" have something to act on.
"""
# scoped-by-caller
from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.utils import timezone

from . import seed_data_phase2 as p2
from .departments import qa_department
from .models import DemoTag


def _perm(label):
    app, codename = label.split(".", 1)
    return Permission.objects.filter(content_type__app_label=app, codename=codename).first()


@transaction.atomic
def build_phase2_demo(stats, make_user):
    from accounts.models import User
    from institutions import services as inst_services
    from institutions.models import (
        Institution, InstitutionCategory, InstitutionOwnership, InstitutionRequest, InstitutionRequestType,
        InstitutionType, InstitutionUser, Programme, ProgrammeCategory, ProgrammeLevel,
    )
    from payments import services as pay
    from payments.models import BankAccount, PaymentObligation, PaymentVerification
    from staff.models import StateOfOrigin

    result = {"institutions": 0, "programmes": 0, "obligations": 0, "users": 0}

    def state(name):
        return StateOfOrigin.objects.filter(name__iexact=name).first() or \
            StateOfOrigin.objects.filter(name__icontains=name).first()

    # --- users ---------------------------------------------------------------
    for email, first, last, user_type, dept, role, groups in p2.DEMO_USERS:
        _u, created = make_user(stats, email, first, last, user_type, dept, role, groups)
        result["users"] += created
    group_name, description, perms, members = p2.DEMO_WALKTHROUGH_GROUP
    group, g_created = Group.objects.get_or_create(name=group_name)
    if g_created:
        DemoTag.tag(group)
        from accounts.models import GroupProfile

        GroupProfile.objects.get_or_create(group=group, defaults={"description": description})
        group.permissions.set([p for p in (_perm(label) for label in perms) if p is not None])
    for email in members:
        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            user.groups.add(group)

    # --- bank account -----------------------------------------------------------
    bank = BankAccount.objects.filter(account_number=p2.DEMO_BANK["account_number"],
                                      bank_name=p2.DEMO_BANK["bank_name"]).first()
    if bank is None:
        bank = BankAccount.objects.create(**p2.DEMO_BANK)
        DemoTag.tag(bank)
        BankAccount.objects.exclude(pk=bank.pk).update(is_default=False)

    # --- institutions --------------------------------------------------------------
    dmp = Institution.unscoped.filter(code="DMP").first()
    if dmp is not None and not dmp.ownership_id:
        prof = p2.DEMO_INSTITUTION_PROFILE
        dmp.ownership = InstitutionOwnership.objects.filter(code=prof["ownership"]).first()
        dmp.institution_type = InstitutionType.objects.filter(code=prof["type"]).first()
        dmp.category = InstitutionCategory.objects.filter(code=prof["category"]).first()
        dmp.state = state(prof["state"])
        dmp.lga = prof["lga"]
        dmp.year_established = prof["year_established"]
        dmp.cac_rc_number = prof["cac_rc_number"]
        dmp.website = prof["website"]
        dmp.recompute_zone()
        dmp.approved_at = timezone.now()
        dmp.save()
        inst_services.lifecycle(dmp)
        for kind, value, name in (("official_email", "registrar@demo-poly.local", ""), ("phone", "08030000001", ""),
                                  ("head_of_institution", "", "Dr. Musa Aliyu (Rector)"),
                                  ("registrar", "registrar@demo-poly.local", "Mrs. Grace Bako")):
            inst_services.set_contact(dmp, kind, value, name)
        inst_services.set_address(dmp, "permanent_site", line1="Km 4 Kaduna–Zaria Road", town="Kaduna",
                                  lga=prof["lga"], state=dmp.state)
        admin = User.objects.filter(email__iexact="admin@demo-poly.local").first()
        if admin is not None and not InstitutionUser.unscoped.filter(user=admin).exists():
            InstitutionUser.unscoped.create(institution=dmp, user=admin, institution_role=admin.role,
                                            is_primary_contact=True, job_title="Institutional Administrator",
                                            invited_at=timezone.now())
    spec = p2.DEMO_SECOND_INSTITUTION
    dch = Institution.unscoped.filter(code=spec["code"]).first()
    if dch is None:
        dept = qa_department(spec["department"])
        dch = Institution.unscoped.create(
            code=spec["code"], name=spec["name"], handling_department=dept, status=spec["status"],
            ownership=InstitutionOwnership.objects.filter(code=spec["ownership"]).first(),
            institution_type=InstitutionType.objects.filter(code=spec["type"]).first(),
            category=InstitutionCategory.objects.filter(code=spec["category"]).first(),
            state=state(spec["state"]), lga=spec["lga"], origin="legacy", approved_at=timezone.now())
        dch.recompute_zone()
        dch.save(update_fields=["zone"])
        DemoTag.tag(dch)
        inst_services.lifecycle(dch)
        inst_services.set_contact(dch, "official_email", "provost@demo-health.local")
        inst_services.set_contact(dch, "head_of_institution", "", "Dr. Hadiza Umar (Provost)")
        inst_services.set_address(dch, "permanent_site", line1="Bosso Road", town="Minna", lga=spec["lga"],
                                  state=dch.state)
        result["institutions"] += 1
        _u, created = make_user(stats, "admin@demo-health.local", "Kemi", "Admin (Demo Health)", "institution",
                                None, "inst-admin", [], institution=dch)
        result["users"] += created
        if created:
            InstitutionUser.unscoped.create(institution=dch, user=_u, institution_role=_u.role,
                                            is_primary_contact=True, invited_at=timezone.now())

    # --- programmes ---------------------------------------------------------------
    for code, name, level, category, status, streams in p2.DEMO_PROGRAMMES:
        inst = Institution.unscoped.filter(code=code).first()
        lvl = ProgrammeLevel.objects.filter(code=level).first()
        if inst is None or lvl is None:
            continue
        if not Programme.unscoped.filter(institution=inst, name=name, level=lvl).exists():
            today = timezone.localdate()
            Programme.unscoped.create(institution=inst, name=name, level=lvl, status=status,
                                      category=ProgrammeCategory.objects.filter(name=category).first(),
                                      approved_streams=streams, commencement_date=today.replace(year=today.year - 2),
                                      expiry_date=today.replace(year=today.year + 3))
            result["programmes"] += 1
    result["programme_total"] = Programme.unscoped.filter(institution__code__in=("DMP", "DCH")).count()

    # --- a draft request container ---------------------------------------------------
    qa_type = InstitutionRequestType.objects.filter(code="qa").first()
    if dmp is not None and qa_type and not InstitutionRequest.unscoped.filter(institution=dmp).exists():
        InstitutionRequest.unscoped.create(institution=dmp, request_type=qa_type, status="draft",
                                           title="Reaccreditation (draft, demo)", reference="DEMO-QA-DRAFT")

    # --- one paid and one pending obligation -------------------------------------------
    if dmp is not None and not PaymentObligation.unscoped.filter(institution=dmp).exists():
        director = User.objects.filter(email__iexact="finance.director@demo.nbte.local").first()
        officer = User.objects.filter(email__iexact="qa.finance@demo.nbte.local").first()
        paid = pay.issue_obligation(dmp, "programme_processing", actor=officer,
                                    description="Programme processing fee — 2 programmes (demo, paid)")
        if paid.status == "awaiting_payment" and director is not None and officer is not None:
            from django.core.files.base import ContentFile

            receipt = ContentFile(b"%PDF-1.4\n% demo teller\n", name="demo-teller.pdf")
            pay.submit_proof(paid, user=User.objects.get(email__iexact="admin@demo-poly.local"), upload=receipt,
                             payer_name="Demo Polytechnic", paid_on=timezone.localdate(),
                             amount_claimed=paid.amount, bank_name="Demo Bank", teller_number="TLR-DEMO-0001")
            item = PaymentVerification.unscoped.filter(obligation=paid, is_open=True).first()
            # Provider evidence and bank proofs both go directly to the Finance Director.
            director_ok = pay.is_final_approver(director)
            if item is not None and director_ok and pay.ready_for_final(item)[0]:
                pay.final_decision(item, user=director, decision="confirmed", reason="Demo confirmation")
        result["obligations"] += 1
        pay.issue_obligation(dmp, "institutional_processing", actor=officer,
                             description="Institutional processing fee (demo, pending)")
        result["obligations"] += 1
    result["obligation_total"] = PaymentObligation.unscoped.filter(institution__code__in=("DMP", "DCH")).count()
    return result


# ===========================================================================
# Phase 3 demo additions (QA requests and the SSQ)
# ===========================================================================
@transaction.atomic
def build_phase3_demo(stats, make_user):
    """Make Demo Polytechnic ready for the SOP-02 walkthrough:

    * an academic-department sub-account (the department return path);
    * the two DMP programmes now expire inside the reassessment window
      (4 and 5 months away), and an *Institutional Administration* programme;
    * accepted organogram, CAC and C-of-O documents (they prefill the SSQ) and
      one document still awaiting review (it must never prefill);
    * the QA request fee amounts for the demo (confirmed, so a bill is issued;
      the demo Director can then waive it).
    """
    import datetime

    from django.core.files.base import ContentFile

    from config.models import ConfigEntry
    from institutions.models import DocumentKind, Institution, InstitutionDocument, InstitutionUser, Programme
    from institutions.models import ProgrammeLevel

    from . import seed_data_phase3 as p3

    result = {"users": 0, "programmes": 0, "documents": 0, "settings": 0}
    dmp = Institution.unscoped.filter(code="DMP").first()
    if dmp is None:
        return result
    email, first, last, user_type, dept, role, groups = p3.DEMO_ACADEMIC_USER
    user, created = make_user(stats, email, first, last, user_type, dept, role, groups, institution=dmp)
    result["users"] += created
    if created:
        InstitutionUser.unscoped.create(institution=dmp, user=user, institution_role=user.role,
                                        invited_at=timezone.now())

    today = timezone.localdate()
    for name, months in (("Computer Science", 4), ("Electrical/Electronic Engineering Technology", 5)):
        prog = Programme.unscoped.filter(institution=dmp, name=name).first()
        target = today + datetime.timedelta(days=30 * months)
        if prog is not None and (prog.expiry_date is None or prog.expiry_date > target):
            prog.expiry_date = target
            prog.save(update_fields=["expiry_date", "updated_at"])
            result["programmes"] += 1
    if not Programme.unscoped.filter(institution=dmp, is_institutional_admin=True).exists():
        Programme.unscoped.create(institution=dmp, name=p3.INSTITUTIONAL_ADMIN_PROGRAMME,
                                  level=ProgrammeLevel.objects.filter(code="nd").first(), status="accredited",
                                  is_institutional_admin=True, commencement_date=today.replace(year=today.year - 4),
                                  expiry_date=today + datetime.timedelta(days=150))
        result["programmes"] += 1

    admin = User_for("admin@demo-poly.local")
    director = User_for("qa.director@demo.nbte.local")
    for kind_code, status in p3.DEMO_DOCUMENTS:
        kind = DocumentKind.objects.filter(code=kind_code).first()
        if kind is None or InstitutionDocument.unscoped.filter(institution=dmp, kind=kind).exists():
            continue
        doc = InstitutionDocument(institution=dmp, kind=kind, status=status, uploaded_by=admin)
        doc.file = ContentFile(b"%PDF-1.4\n% demo " + kind_code.encode() + b"\n%%EOF\n", name=f"demo-{kind_code}.pdf")
        if status == InstitutionDocument.ACCEPTED:
            doc.accepted_by, doc.accepted_at = director, timezone.now()
            doc.reviewed_by, doc.reviewed_at = director, timezone.now()
        doc.save()
        result["documents"] += 1

    for key, value in p3.DEMO_REVIEW_FEE.items():
        entry = ConfigEntry.objects.filter(key=key, scope="global").first()
        if entry is not None and entry.value != value and entry.value in ("0", "false", "0.00", ""):
            entry.value = value
            entry.save()
            result["settings"] += 1
    if result["settings"]:
        stats.notes.append("Demo: the QA request fee was set to ₦50,000 + ₦150,000 per programme and confirmed "
                           "(qa.fee.qa_request.*) so the SOP-02 walkthrough issues a bill. Set the real amounts "
                           "before go-live.")
    return result


def User_for(email):  # noqa: N802 - small helper
    from accounts.models import User

    return User.objects.filter(email__iexact=email).first()
