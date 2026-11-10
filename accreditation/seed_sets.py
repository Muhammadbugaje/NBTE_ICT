"""seed_nbte data sets for v2.1 Phase F onboarding (F.13–F.16).

* ``qa.onboarding_flow_v2``  onboarding FlowDefinition version 2 (pay to submit, per-document
                             review) + the notification templates and rules it uses. Existing
                             applications stay on version 1.
* ``demo.onboarding_v2``     (--with-demo) three demo institutions: one still in setup, one ready
                             for the Chief Executive to "Declare and pay", one submitted
                             automatically with documents in every review state.

Idempotent.
"""
from core.seeding import seed_set

DEMO_PASSWORD = "NBTE-Demo#2026"

TEMPLATES = [  # code, channel, subject, body
    ("onboarding.submitted_automatically", "in_app", "Application {{ reference }} submitted",
     "Your payment is confirmed and your application was submitted automatically. NBTE departments are now "
     "reviewing your documents. You do not need to do anything else."),
    ("onboarding.submitted_automatically", "email", "NBTE: application {{ reference }} submitted",
     "<p>Your payment is confirmed and application {{ reference }} was submitted automatically.</p>"
     "<p>NBTE departments are reviewing your documents. If one needs a change you will be told which, and you "
     "re-upload just that file.</p>"),
    ("onboarding.documents_submitted", "in_app", "Application {{ reference }} submitted",
     "The Chief Executive submitted your documents after Finance confirmed payment. NBTE departments are now "
     "reviewing them."),
    ("onboarding.documents_submitted", "email", "NBTE: application {{ reference }} submitted",
     "<p>The Chief Executive submitted application {{ reference }} after Finance confirmed payment.</p>"
     "<p>NBTE departments are reviewing your documents. If one needs a change, you will be told which file to "
     "upload again.</p>"),
    ("onboarding.documents_cleared", "in_app", "All documents accepted",
     "NBTE accepted every document on {{ reference }}. Next: choose the programmes you will offer."),
    ("document_review.awaiting_assignment", "in_app", "{{ count }} document(s) await an officer",
     "{{ institution }} ({{ reference }}): {{ count }} document(s) for {{ department }} need an officer."),
    ("document_review.assigned", "in_app", "Document to analyse: {{ document }}",
     "Analyse {{ document }} of {{ institution }} and record your recommendation."),
    ("document_review.recommended", "in_app", "Recommendation ready: {{ document }}",
     "The officer recommended a decision on {{ document }} of {{ institution }}. It awaits you."),
    ("document_review.sent_back", "in_app", "Look again: {{ document }}",
     "Your director sent {{ document }} back to you: {{ remark }}"),
    ("document_review.auto_assigned", "in_app", "Auto-assigned by rotation: {{ document }}",
     "The rotation picked {{ officer }} to analyse {{ document }} of {{ institution }} ({{ reference }}). "
     "You may reassign in one click."),
    ("onboarding.physical_verification", "in_app", "Physical verification: {{ institution }}",
     "{{ institution }} proposed {{ count }} programmes on {{ reference }}. Assign an ICT officer for physical "
     "verification."),
    ("inspection.visit_scheduled", "in_app", "QA visit scheduled: {{ institution }}",
     "You are on the QA visit to {{ institution }} ({{ reference }}): {{ dates }}. Check your calendar and "
     "attendance duty."),
    ("programmes.added_master", "in_app", "Programme added: {{ institution }}",
     "{{ institution }} ({{ reference }}) added {{ programme }} from the master list. Its professional body "
     "has been told; NBTE follows the programme."),
]
RULES = [  # event, recipient kind, template code, channel
    ("onboarding.submitted_automatically", "institution_users", "onboarding.submitted_automatically", "in_app"),
    ("onboarding.submitted_automatically", "institution_users", "onboarding.submitted_automatically", "email"),
    ("onboarding.documents_submitted", "institution_users", "onboarding.documents_submitted", "in_app"),
    ("onboarding.documents_submitted", "institution_users", "onboarding.documents_submitted", "email"),
    ("onboarding.documents_cleared", "institution_users", "onboarding.documents_cleared", "in_app"),
    ("document_review.awaiting_assignment", "role", "document_review.awaiting_assignment", "in_app"),
    ("document_review.assigned", "role", "document_review.assigned", "in_app"),
    ("document_review.recommended", "role", "document_review.recommended", "in_app"),
    ("document_review.sent_back", "role", "document_review.sent_back", "in_app"),
    ("document_review.auto_assigned", "role", "document_review.auto_assigned", "in_app"),
    ("onboarding.physical_verification", "role", "onboarding.physical_verification", "in_app"),
    ("inspection.visit_scheduled", "role", "inspection.visit_scheduled", "in_app"),
    ("programmes.added_master", "role", "programmes.added_master", "in_app"),
]


def install_notifications():
    from qa_core.models import NotificationRule, NotificationTemplate

    made = 0
    for code, channel, subject, body in TEMPLATES:
        _t, c = NotificationTemplate.objects.get_or_create(code=code, channel=channel,
                                                           defaults={"subject": subject, "body": body,
                                                                     "description": "v2.1 onboarding (F.15/F.16)"})
        made += int(c)
    for event, kind, tcode, channel in RULES:
        t = NotificationTemplate.objects.get(code=tcode, channel=channel)
        _r, c = NotificationRule.objects.get_or_create(event_code=event, recipient_kind=kind, template=t)
        made += int(c)
    return made


@seed_set("qa.onboarding_flow_v2", group="qa", depends=("settings", "roles"), order=570, phase="F")
def seed_onboarding_flow_v2(ctx):
    from . import onboarding_v2

    _fd, created = onboarding_v2.install_flow_v2()
    made = install_notifications()
    if created:
        ctx.res.created += 1
        ctx.note("onboarding flow version 2 published (new applications only)")
    if made:
        ctx.res.created += made
    if not created and not made:
        ctx.res.unchanged += 1


@seed_set("qa.onboarding_flow_v3", group="qa", depends=("qa.onboarding_flow_v2",), order=571, phase="F")
def seed_onboarding_flow_v3(ctx):
    from . import onboarding_v3

    _fd, created = onboarding_v3.install_flow_v3()
    from . import onboarding_v4

    _fd4, created4 = onboarding_v4.install_flow_v4()   # v2.3: approval in principle
    created = created or created4
    if created:
        ctx.res.created += 1
        ctx.note("onboarding flow version 3 published (new applications only)")
    else:
        ctx.res.unchanged += 1


@seed_set("qa.templates_v24", group="qa", depends=("qa_core.startup",), order=575, phase="v2.4")
def seed_templates_v24(ctx):
    """Complete, published SSQ templates for every QA type and the two visit
    report templates (NBTE/P/4A programme evaluation, NBTE/QA/ICT/VVT/001),
    from accreditation/template_library.py. Seed-owned templates publish a
    new version when their system schema changes; owner-edited versions are
    preserved, and SSQs already opened keep their version."""
    from . import template_library

    out = template_library.install(log=ctx.note)
    ctx.res.created += len(out["created"]) + len(out["types_created"])
    if not out["created"] and not out["types_created"]:
        ctx.res.unchanged += 1
    for code, problem in out["errors"].items():
        ctx.warn(f"{code}: {problem}")


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
DEMO_DIRECTORS = [  # department code, e-mail, first, last
    ("legal", "legal.director@demo.nbte.local", "Rakiya", "Bello"),
    ("aspd", "aspd.director@demo.nbte.local", "Kabiru", "Adamu"),
    ("ppd", "ppd.director@demo.nbte.local", "Sani", "Abdullahi"),
    # NTIRD clears onboarding programmes and handles proposed institutions; without
    # a director the demo dead-ends under need-to-know (nobody involved can act).
    ("ntird", "ntird.director@demo.nbte.local", "Zainab", "Lawal"),
]
DEMO_ANALYSTS = [  # department code, e-mail, first, last (a second officer so rotation has a choice)
    ("legal", "legal.officer2@demo.nbte.local", "Halima", "Sani"),
    ("ppd", "planning.physical2@demo.nbte.local", "Musa", "Ibrahim"),
    ("aspd", "planning.academic2@demo.nbte.local", "Grace", "Okoro"),
    ("ntird", "ntird.officer@demo.nbte.local", "Usman", "Danjuma"),
    ("ntird", "ntird.officer2@demo.nbte.local", "Esther", "John"),
]
DEMO_INSTITUTIONS = [  # code, name, CE e-mail, ICT e-mail, stage
    ("DOBSU", "Demo Setup Polytechnic", "ce.setup@demo-poly.local", "ict.setup@demo-poly.local", "setup"),
    ("DOBPAY", "Demo Pay-to-Submit Polytechnic", "ce.pay@demo-poly.local", "ict.pay@demo-poly.local", "pay"),
    ("DOBREV", "Demo Review Polytechnic", "ce.review@demo-poly.local", "ict.review@demo-poly.local", "review"),
]


def _staff_user(email, first, last, dept, role_code):
    from accounts.models import Role, User
    from qa_core.models import DemoTag

    u = User.objects.filter(email__iexact=email).first()
    if u is None:
        u = User(email=email, first_name=first, last_name=last, user_type="staff", department=dept,
                 role=Role.objects.filter(code=role_code).first(), is_active=True, must_change_password=False)
        u.set_password(DEMO_PASSWORD)
        u.save()
        DemoTag.tag(u)
    return u


def _pdf():
    from django.core.files.uploadedfile import SimpleUploadedFile

    body = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    return SimpleUploadedFile("demo.pdf", body, content_type="application/pdf")


def _make_institution(code, name, ce_email, ict_email, stage, ctx):
    from django.utils import timezone

    from institutions import profile
    from institutions import services as inst_services
    from institutions.models import Institution, InstitutionCategory, InstitutionOwnership, InstitutionType
    from qa_core.models import DemoTag
    from staff.models import StateOfOrigin

    inst = Institution.unscoped.filter(code=code).first()
    if inst is not None:
        return inst, False
    state = StateOfOrigin.objects.filter(name__icontains="Kaduna").first() or StateOfOrigin.objects.first()
    inst = inst_services.create_institution({
        "name": name, "code": code, "ownership": InstitutionOwnership.objects.get(code="private"),
        "institution_type": InstitutionType.objects.get(code="polytechnic"),
        "category": InstitutionCategory.objects.filter(code="polytechnic").first(), "state": state, "lga": "Chikun",
        "year_established": 2024, "cac_rc_number": "RC-DEMO-" + code if stage != "setup" else "",
        "contacts": {"official_email": {"value": f"info@{code.lower()}.demo-poly.local"},
                     "phone": {"value": "0803 000 0000"}},
        "address": {"line1": "1 Demo Road, Kakuri", "town": "Kaduna", "lga": "Chikun", "state": state},
        "admin": {"first_name": "Ibrahim", "last_name": "Musa", "email": ict_email, "phone": "0803 000 1111",
                  "job_title": "Head of ICT", "role": "inst-admin"},
        "chief_executive": {"title": "Dr", "full_name": "Amina Yusuf", "email": ce_email, "phone": "0803 000 2222"},
    }, origin="self", activate_admin=True, admin_password=DEMO_PASSWORD)
    DemoTag.tag(inst)
    from institutions.models import InstitutionUser

    for link in InstitutionUser.unscoped.filter(institution=inst).select_related("user"):
        u = link.user
        u.set_password(DEMO_PASSWORD)
        u.is_active, u.must_change_password = True, False
        u.last_login = u.last_login or timezone.now()
        u.save()
        DemoTag.tag(u)
    if stage != "setup":
        for code_, full, email, phone in (("registrar", "Hauwa Bello", "registrar@" + code.lower() + ".demo-poly.local",
                                           "0803 000 1122"),
                                          ("bursar", "Chinedu Okafor", "bursar@" + code.lower() + ".demo-poly.local",
                                           "0803 000 3344")):
            from institutions.models import ManagementRole

            profile.set_holder(inst, ManagementRole.objects.get(code=code_),
                               {"full_name": full, "email": email, "phone": phone}, actor=None)
        from institutions.models import ManagementRole

        profile.set_holder(inst, ManagementRole.objects.get(code="librarian"),
                           {"full_name": "Fatima Garba", "email": "library@" + code.lower() + ".demo-poly.local",
                            "phone": "0803 000 5566"}, actor=None)
    else:
        from institutions.models import ManagementRole

        profile.set_holder(inst, ManagementRole.objects.get(code="registrar"),
                           {"full_name": "Hauwa Bello", "email": "registrar@dobsu.demo-poly.local",
                            "phone": "0803 000 1122"}, actor=None)
    ctx.res.created += 1
    return inst, True


@seed_set("demo.onboarding_v2", group="demo", depends=("qa.onboarding_flow_v2",), order=970, phase="F",
          demo_only=True)
def seed_demo_onboarding_v2(ctx):
    from django.conf import settings
    from django.db import transaction

    from accounts.models import User
    from accreditation import onboarding_v2, services as acc
    from assignments import rotation
    from assignments.models_rotation import RotationPool
    from institutions import document_review, services as inst_services
    from institutions.models import DocumentReview
    from qa_core import flow
    from staff.models import Department

    # Directors for the reviewing departments whose seat is empty (demo only).
    for dcode, email, first, last in DEMO_DIRECTORS:
        dept = Department.objects.filter(code=dcode).first()
        if dept is None:
            ctx.warn(f"No department {dcode}")
            continue
        if dept.director_id is None:
            u = _staff_user(email, first, last, dept, "director")
            Department.objects.filter(pk=dept.pk).update(director=u)
            from qa_core.departments import clear_cache
            clear_cache()
            ctx.note(f"{dept.name}: demo director {email}")
            from documents import signature  # X23: directors sign what they approve (generated, fictional)

            u = type(u).objects.get(pk=u.pk)
            if signature.signature_required(u) and signature.active_for(u) is None:
                signature.save_demo(u)
    for dcode, email, first, last in DEMO_ANALYSTS:
        dept = Department.objects.filter(code=dcode).first()
        if dept is not None:
            _staff_user(email, first, last, dept, "officer")
    for pool in RotationPool.objects.filter(kind="document_analysis"):
        rotation.sync_members(pool)
    for pool in RotationPool.objects.filter(kind__in=("ssq_review", "visit_panel"), department__code="ntird"):
        rotation.sync_members(pool)

    insts = {}
    for code, name, ce, ict, stage in DEMO_INSTITUTIONS:
        insts[stage], _c = _make_institution(code, name, ce, ict, stage, ctx)

    def upload_all(inst):
        ce_user = User.objects.get(email=[d for d in DEMO_INSTITUTIONS if d[0] == inst.code][0][3])
        for row in inst_services.checklist(inst):
            if row["document"] is None and row["mandatory"]:
                inst_services.upload_document(inst, row["kind"], _pdf(), user=ce_user)

    # "pay": profile and documents complete; the CE can Declare and pay.
    pay = insts["pay"]
    if acc.current_application(pay) is None:
        upload_all(pay)
        acc.start_application(pay, user=None)
        ctx.res.created += 1
    # "review": declared, fee confirmed, submitted automatically, reviews in every state.
    rev = insts["review"]
    if acc.current_application(rev) is None:
        with transaction.atomic():
            upload_all(rev)
            app = acc.start_application(rev, user=None)
            ce_user = User.objects.get(email="ce.review@demo-poly.local")
            onboarding_v2.declare_and_pay(app, user=ce_user, declaration=True)
            inst_ = acc.instance_of(app)
            flow.transition(inst_, "payment_confirmed", None, metadata={"demo": True})
            onboarding_v2.auto_submit(app, reason="demo")
        plan = {  # document kind → what happens (design 23 states)
            "cac": "returned", "c-of-o": "awaiting", "physical-master-plan": "recommended_return",
            "bank-guarantee": "recommended_accept", "academic-master-plan": "awaiting", "need-survey": "assigned",
        }
        for r in DocumentReview.unscoped.filter(application=app).select_related("department", "document__kind"):
            step = plan.get(r.document.kind.code, "awaiting")
            director = document_review.director_of(r.department)
            officers = [u for u in document_review.candidates(r) if (u.email or "").endswith("@demo.nbte.local")]
            if step == "awaiting" or director is None or not officers:
                continue
            officer = officers[0]
            document_review.assign(r, director=director, officer=officer,
                                   reason="Demo data: first demo officer in the list")
            if step == "returned":
                document_review.recommend(r, analyst=officer, recommendation="return",
                                          remark="The certificate copy is not certified by the CAC.")
                document_review.decide(r, director=director, decision="return_to_institution",
                                       remark="Please upload a CAC-certified true copy of the certificate.")
            elif step == "recommended_return":
                document_review.recommend(r, analyst=officer, recommendation="return",
                                          remark="Plan is to scale but not signed or stamped by a registered town "
                                                 "planner. Suggest returning for the signed copy.")
            elif step == "recommended_accept":
                document_review.recommend(r, analyst=officer, recommendation="accept",
                                          remark="Bank guarantee matches the required amount.")
        ctx.res.created += 1
    ctx.note("Demo onboarding v2: DOBSU (setup), DOBPAY (declare and pay), DOBREV (document review)")
    del settings
