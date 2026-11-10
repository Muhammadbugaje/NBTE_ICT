"""seed_nbte data sets owned by messaging (v2.1 Phase E).

* ``messaging.external_route`` - the Executive Secretary approval route that
  every external / general memo not sent by the ES walks (M3).
* ``demo.phase_e`` - demo only: five demo institutions (so the memo filter
  builder has something to count), a published external memo from the ES to
  all polytechnics, a published general memo, a director's external memo
  waiting for the ES ("Polytechnics with more than 5 programmes") and two
  staff announcements (one pinned, one to acknowledge). Idempotent: every row
  is looked up by a natural key first; demo rows are DemoTag-listed.
"""
from core.seeding import seed_set

DEMO_INSTITUTIONS = [
    # code, name, type, ownership, zone, programmes (accredited first), accredited
    ("DFPB", "Demo Federal Polytechnic, Bauchi", "polytechnic", "federal", "NE", 7, 4),
    ("DSPI", "Demo State Polytechnic, Ilorin", "polytechnic", "state", "NC", 6, 2),
    ("DPOW", "Demo Polytechnic, Owerri", "polytechnic", "private", "SE", 3, 0),
    ("DCNJ", "Demo College of Nursing, Jos", "college-nursing", "state", "NC", 4, 1),
    ("DTCK", "Demo Technical College, Kano", "technical-college", "state", "NW", 2, 0),
]
SUBJECT_ALL_POLYS = "2027 accreditation calendar for polytechnics"
SUBJECT_GENERAL = "NBTE offices closed on Independence Day"
SUBJECT_PENDING = "Submission of programme returns (more than five programmes)"
ANN_PINNED = "Staff durbar: Friday 10:00 in the main hall"
ANN_ACK = "New leave policy: please read and acknowledge"


def _tag(obj):
    try:
        from qa_core.models import DemoTag
        DemoTag.tag(obj)
    except Exception:  # pragma: no cover
        pass


@seed_set("messaging.external_route", group="base", depends=("roles",), order=470, phase="E")
def seed_external_route(ctx):
    from .models import MemoRoute
    from .routing import EXTERNAL_ROUTE_CODE, external_route

    existed = MemoRoute.objects.filter(code=EXTERNAL_ROUTE_CODE).exists()
    external_route()
    if existed:
        ctx.res.skipped += 1
    else:
        ctx.res.created += 1


@seed_set("demo.phase_e", group="demo", depends=("demo", "messaging.external_route"), order=940, phase="E",
          demo_only=True)
def seed_demo_phase_e(ctx):
    from django.utils import timezone

    from accounts.models import User
    from announcements.models import Announcement
    from institutions.models import Institution, InstitutionOwnership, InstitutionType, Programme
    from staff.models import Department

    from . import memos as svc
    from . import routing
    from .models import Memo

    es = User.objects.filter(email="qa.es@demo.nbte.local", is_active=True).first()
    director = User.objects.filter(email="qa.director@demo.nbte.local", is_active=True).first()
    if es is None or director is None:
        ctx.warn("Demo ES or QA director missing: run the demo set first.")
        return
    try:
        from documents import signature
        for who in (es, director):
            if signature.active_for(who) is None:
                signature.save_demo(who)
                ctx.res.created += 1
    except Exception as exc:  # pragma: no cover
        ctx.warn(f"Could not create demo signatures: {exc}")

    dept = (Department.objects.filter(code="dpap").first() or Department.objects.filter(is_active=True)
            .order_by("pk").first())
    for code, name, typ, own, zone, n, acc in DEMO_INSTITUTIONS:
        if Institution.unscoped.filter(code=code).exists():
            ctx.res.skipped += 1
            continue
        inst = Institution.unscoped.create(
            code=code, name=name, handling_department=dept, status="approved", zone=zone,
            institution_type=InstitutionType.objects.filter(code=typ).first(),
            ownership=InstitutionOwnership.objects.filter(code=own).first(),
            approved_at=timezone.now(), origin="nbte")
        _tag(inst)
        for k in range(n):
            p = Programme.unscoped.create(institution=inst, name=f"ND Demo Programme {k + 1}",
                                          status="accredited" if k < acc else "proposed")
            _tag(p)
        ctx.res.created += 1

    def memo(subject, sender, **kw):
        m = Memo.objects.filter(subject=subject, sender=sender).first()
        if m is not None:
            ctx.res.skipped += 1
            return m, False
        m = svc.create_memo(sender=sender, subject=subject, **kw)
        _tag(m)
        ctx.res.created += 1
        return m, True

    body_polys = ("Dear Head of Institution,\n\nThe Board will run its 2027 accreditation exercise for polytechnics "
                  "from February to May 2027. Please upload your self-study questionnaires in the Quality "
                  "Assurance Portal by 15 January 2027 and keep your programme records up to date.\n\n"
                  "Visits will be scheduled after the questionnaires are reviewed. Your desk officer will confirm "
                  "the dates.\n\nThank you for your cooperation.")
    m, made = memo(SUBJECT_ALL_POLYS, es, body=body_polys, visibility=Memo.NO_STAFF, kind=Memo.EXTERNAL,
                   institution_scope=Memo.INST_FILTER, institution_filter={"types": ["polytechnic"]},
                   requires_acknowledgment=True, priority=Memo.HIGH)
    if made:
        svc.publish_memo(m, es)

    m, made = memo(SUBJECT_GENERAL, es, body="All NBTE offices will be closed on Thursday, 1 October, to mark "
                   "Independence Day. Institutions should expect no visits or calls from the Board that day.",
                   visibility=Memo.ALL_STAFF, kind=Memo.GENERAL, institution_scope=Memo.INST_ALL)
    if made:
        svc.publish_memo(m, es)

    m, made = memo(SUBJECT_PENDING, director,
                   body="Dear Head of Institution,\n\nInstitutions running more than five programmes are "
                        "requested to submit their programme returns for the 2026/2027 session, including "
                        "staff lists and enrolment figures, by 30 November 2026.\n\nKindly treat as urgent.",
                   visibility=Memo.NO_STAFF, kind=Memo.EXTERNAL, institution_scope=Memo.INST_FILTER,
                   institution_filter={"types": ["polytechnic"], "min_programmes": 5})
    if made:
        svc.publish_memo(m, director)          # waits for the ES (M3)
        if m.status != Memo.AWAITING:
            ctx.warn(f"Demo external memo {m.reference} is {m.status}, expected awaiting the ES.")
    _ = routing

    now = timezone.now()
    for title, body, kind, pinned, ack in (
            (ANN_PINNED, "All staff are invited to the quarterly durbar with the Executive Secretary on "
                         "Friday at 10:00 in the main hall.", Announcement.NOTICE, True, False),
            (ANN_ACK, "The revised leave policy takes effect on 1 November. Please read it on the staff "
                      "portal and acknowledge.", Announcement.ACTION_NEEDED, False, True)):
        if Announcement.objects.filter(title=title).exists():
            ctx.res.skipped += 1
            continue
        a = Announcement.objects.create(title=title, body=body, kind=kind, pinned=pinned, requires_ack=ack,
                                        audience=Announcement.ALL_STAFF, created_by=es, published_at=now)
        _tag(a)
        ctx.res.created += 1


# Phase E chat demo set (pe-chat branch) lives in its own module.
from .seed_sets_chat import DEMO_CHAT_TITLE, demo_chat_users, ensure_demo_chat_base, seed_demo_phase_e_chat  # noqa: E402,F401
from .workflow_seed import seed_memo_routing  # noqa: E402,F401
