"""Professional-body portal (v2.1 R.5, X43): notifications to a body."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

EVENT_TEXT = {
    "programme_proposed": ("New programme at {inst}", "{prog} was proposed at {inst}."),
    "ssq_submitted": ("SSQ in review: {prog}", "{inst} submitted the SSQ for {prog}. NBTE is reviewing it."),
    "visit_scheduled": ("Visit scheduled: {prog}", "NBTE scheduled a visit to {inst} for {prog}{extra}."),
    "approval_to_commence": ("Approval to commence: {prog}",
                             "NBTE approved {prog} at {inst} to commence. Upload your approval letter."),
    "body_letter_requested": ("Your approval letter is needed: {prog}",
                              "NBTE has approved {prog} at {inst} provisionally. The approval to commence is issued "
                              "as soon as you upload your approval letter."),
}


def body_users(body):
    from django.contrib.auth import get_user_model

    return list(get_user_model().objects.filter(is_active=True, user_type="professional_body",
                                                 professional_body=body))


def _issue_approval_to_commence(programme, user):
    """D-19 (v2.2): the body's letter is in — now issue the approval to commence
    that was held back, and tell the institution and the body."""
    try:
        from accreditation.models import Decision
        from documents.issue import approval_recorded
        from institutions.services import notify_institution

        decision = (Decision.unscoped.filter(programme=programme, stage=Decision.ES, outcome=Decision.APPROVE)
                    .order_by("-applied_at", "-pk").first())
        if decision is not None:
            approval_recorded("qa_case.decision_approved", decision, actor=user, institution=programme.institution,
                              context={"programme": str(programme), "streams": decision.approved_streams,
                                       "commencement": decision.commencement_date.strftime("%d %B %Y")
                                       if decision.commencement_date else ""})
        notify_institution(programme.institution, "programme.decided",
                           {"programme": str(programme), "outcome": "Approval to commence",
                            "detail": "The professional body's letter was received; the approval to commence is "
                                      "now issued."}, url="/letters/")
        notify_body(programme, "approval_to_commence")
    except Exception:
        logger.exception("approval to commence after the body letter failed for programme %s", programme.pk)


def notify_body(programme, event, *, extra=""):
    """Notify the users of the programme's body (never raises)."""
    try:
        from config import registry as config
        from notifications.services import notify_many

        events = list(config.get("qa.pb.notify_events",
                                 ["programme_proposed", "ssq_submitted", "visit_scheduled", "approval_to_commence"])
                      or []) + ["body_letter_requested"]
        body = programme.professional_body if programme.professional_body_id else None
        if body is None or not body.portal_enabled or event not in (events or []) or event not in EVENT_TEXT:
            return []
        title, text = EVENT_TEXT[event]
        ctx = {"inst": programme.institution.name, "prog": str(programme), "extra": extra}
        return notify_many(body_users(body), title=title.format(**ctx), body=text.format(**ctx),
                           url=f"/pb/programmes/{programme.pk}/")
    except Exception:
        logger.exception("PB notification %s for programme %s failed", event, getattr(programme, "pk", None))
        return []


# ===========================================================================
# Portal pages (QA portal host): dashboard, programme detail, letter upload
# ===========================================================================
from django.core.exceptions import PermissionDenied, ValidationError  # noqa: E402
from django.http import Http404  # noqa: E402
from django.shortcuts import render  # noqa: E402
from django.utils import timezone  # noqa: E402
from django.views.decorators.http import require_http_methods  # noqa: E402

STAGE_FILTERS = [("awaiting_letter", "Awaiting your letter"), ("visit", "Visit scheduled"),
                 ("ssq", "SSQ in review"), ("in_progress", "With NBTE"), ("approved", "Approved"),
                 ("proposed", "Proposed")]


def is_pb_user(user):
    return bool(user and user.is_authenticated and getattr(user, "user_type", "") == "professional_body"
                and getattr(user, "professional_body_id", None))


def is_pb_admin(user):
    return is_pb_user(user) and getattr(getattr(user, "role", None), "code", "") == "pb-admin"


def pb_required(view):
    from qa_core.portal_views import portal_login_required

    @portal_login_required
    def wrapped(request, *args, **kwargs):
        from config import registry as config

        if not is_pb_user(request.user) or not request.user.professional_body.is_active \
                or not request.user.professional_body.portal_enabled \
                or not config.get("qa.pb.portal_enabled", True):
            raise Http404("This page is for professional body accounts.")
        return view(request, *args, **kwargs)
    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def body_programmes(body):
    """Programmes under the body (system query, scoped by the body itself)."""
    from .models import Programme

    return (Programme.unscoped.filter(professional_body=body).exclude(status="dropped")
            .select_related("institution", "institution__ownership", "level").order_by("institution__name", "name"))


def body_institution_ids(user):
    if not is_pb_user(user):
        return set()
    return set(body_programmes(user.professional_body).values_list("institution_id", flat=True))


def stage_of(programme, today=None):
    """(code, public label, visit date or None). Public labels only (X6)."""
    from accreditation.models import QACase, QARequestProgramme
    from inspection.models import Inspection

    today = today or timezone.localdate()
    body = programme.professional_body
    if programme.status == "approved_pending_body_letter" or (
            programme.awaiting_body_letter
            and programme.status in ("approval_to_commence", "accredited", "interim")):
        return "awaiting_letter", f"Approved, awaiting {body.acronym or body.name} letter", None
    if programme.status in ("approval_to_commence", "accredited", "interim"):
        return "approved", programme.get_status_display(), None
    row = (QARequestProgramme.unscoped.filter(programme=programme, status=QARequestProgramme.ACTIVE)
           .select_related("qa_request").order_by("-qa_request__created_at").first())
    if row is not None:
        case = QACase.unscoped.filter(qa_request=row.qa_request).first()
        if case is not None:
            visit = (Inspection.unscoped.filter(case=case, scheduled_end__gte=today)
                     .order_by("scheduled_start").first())
            if visit is not None:
                return "visit", f"Visit {visit.scheduled_start:%d %b %Y}", visit.scheduled_start
        from accreditation.models import SSQ

        if SSQ.unscoped.filter(qa_request_programme=row, is_current=True,
                               status__in=("submitted", "accepted", "analysed")).exists():
            return "ssq", "SSQ in review", None
        return "in_progress", "With NBTE", None
    if programme.status == "proposed":
        return "proposed", "Proposed", None
    return programme.status, programme.get_status_display(), None


def dashboard_rows(body):
    from .models import BodyLetter

    today = timezone.localdate()
    counts = {}
    for pid in BodyLetter.unscoped.filter(body=body).values_list("programme_id", flat=True):
        counts[pid] = counts.get(pid, 0) + 1
    rows = []
    for p in body_programmes(body):
        code, label, visit = stage_of(p, today)
        rows.append({"programme": p, "stage": code, "label": label, "visit": visit,
                     "letters": counts.get(p.pk, 0)})
    return rows


def body_institution_rows(body):
    """v2.6: the institutions running the body's programmes, grouped like the
    dashboard rows — one row per institution with its programmes and stages."""
    by_inst = {}
    for p in body_programmes(body):
        by_inst.setdefault(p.institution, []).append(p)
    return [{"institution": inst, "programmes": progs} for inst, progs in sorted(by_inst.items(),
                                                                                key=lambda kv: kv[0].name)]


def _programme_choice_rows(body):
    """For the request form: the institution/level choices scoped to what the
    body regulates (used by the NBTE-side form too)."""
    from .models import ProgrammeCategory, ProgrammeLevel

    levels = list(ProgrammeLevel.objects.all().order_by("pk"))
    categories = list(ProgrammeCategory.objects.all().order_by("name"))
    return {"levels": levels, "categories": categories}


def body_visit_rows(body):
    """v2.6: the inspections (visits) touching the body's programmes — one row
    per scheduled inspection on a case whose request covers a programme of the
    body. Ordered soonest first."""
    from accreditation.models import QACase, QARequestProgramme
    from inspection.models import Inspection

    case_ids = set(QARequestProgramme.unscoped.filter(programme__professional_body=body,
                                                      status=QARequestProgramme.ACTIVE)
                   .values_list("qa_request_id", flat=True))
    if not case_ids:
        return Inspection.unscoped.none()
    return (Inspection.unscoped.filter(case__qa_request_id__in=case_ids, case__isnull=False)
            .exclude(scheduled_start=None).select_related("case", "case__institution", "case__qa_request")
            .order_by("scheduled_start", "id"))


@pb_required
def pb_dashboard(request):
    from qa_core.portal_views import _shell_context

    body = request.user.professional_body
    rows = dashboard_rows(body)
    f = request.GET
    shown = rows
    if f.get("stage"):
        shown = [r for r in shown if r["stage"] == f["stage"]]
    if f.get("level"):
        shown = [r for r in shown if r["programme"].level_id and r["programme"].level.code == f["level"]]
    q = (f.get("q") or "").strip().lower()
    if q:
        shown = [r for r in shown if q in r["programme"].name.lower() or q in r["programme"].institution.name.lower()]
    stats = {"programmes": len(rows), "institutions": len({r["programme"].institution_id for r in rows}),
             "visits": sum(1 for r in rows if r["stage"] == "visit"),
             "letters": sum(1 for r in rows if r["stage"] == "awaiting_letter")}
    visits = sorted((r for r in rows if r["visit"]), key=lambda r: r["visit"])[:5]
    ctx = _shell_context(request, "dashboard", body=body, rows=shown, stats=stats, visits=visits, filters=f,
                         stage_filters=STAGE_FILTERS, is_admin=is_pb_admin(request.user),
                         levels=sorted({(r["programme"].level.code, r["programme"].level.code.upper())
                                        for r in rows if r["programme"].level_id}))
    from qa_core.ui import is_htmx

    if is_htmx(request) and request.headers.get("HX-Target") == "pb-rows":
        return render(request, "qa_portal/pb/_rows.html", ctx)
    return render(request, "qa_portal/pb/dashboard.html", ctx)


def _programme_or_404(request, pk):
    p = body_programmes(request.user.professional_body).filter(pk=pk).first()
    if p is None:
        raise Http404
    return p


@pb_required
def pb_programme(request, pk, *, error="", status=200):
    from qa_core.portal_views import _shell_context

    p = _programme_or_404(request, pk)
    code, label, visit = stage_of(p)
    from .models import BodyLetter

    letters = BodyLetter.unscoped.filter(programme=p, body=request.user.professional_body).select_related(
        "uploaded_by")
    ctx = _shell_context(request, "dashboard", p=p, stage=label, stage_code=code, visit=visit, letters=letters,
                         is_admin=is_pb_admin(request.user), kinds=BodyLetter.KIND_CHOICES, error=error)
    return render(request, "qa_portal/pb/programme.html", ctx, status=status)


def upload_letter(programme, *, body, user, upload, kind="approval_letter", reference="", issued_on=None,
                  visible_to_institution=True, letter=None):
    """Service: store a body letter — a fresh row, or (when ``letter`` is the
    body's own approval recorded earlier) the official copy onto that record.
    An approval letter clears ``awaiting_body_letter`` and tells the
    institution and the handling department."""
    from qa_core import timeline
    from qa_core.files import validate_upload

    from .models import BodyLetter

    if programme.professional_body_id != body.pk:
        raise PermissionDenied("This programme is not under your body.")
    validate_upload(upload, extensions=("pdf", "jpg", "jpeg", "png"), max_mb=10, label="letter")
    if letter is not None:
        if letter.body_id != body.pk or letter.programme_id != programme.pk:
            raise PermissionDenied("That letter record is not yours.")
        letter.file = upload
        letter.save()
        _notify_letter(letter, recorded=False)
        return letter
    letter = BodyLetter(body=body, programme=programme, kind=kind, reference=reference[:80], issued_on=issued_on,
                        uploaded_by=user, visible_to_institution=visible_to_institution)
    letter.file = upload
    letter.save()
    _after_body_letter(programme, body, user, letter, recorded=False)
    return letter


def record_approval(programme, *, body, user, reference="", issued_on=None, note=""):
    """The body approves the institution to commence the programme — recorded
    at once (the awaiting-release machinery fires exactly as when the letter
    file is uploaded); the signed official copy is uploaded afterwards onto
    the same record."""
    from qa_core import timeline

    from .models import BodyLetter

    if programme.professional_body_id != body.pk:
        raise PermissionDenied("This programme is not under your body.")
    letter = BodyLetter(body=body, programme=programme, kind="approval_letter", reference=(reference or "")[:80],
                        issued_on=issued_on, uploaded_by=user, visible_to_institution=True, notes=note or "")
    letter.save()
    _after_body_letter(programme, body, user, letter, recorded=True)
    return letter


def _after_body_letter(programme, body, user, letter, *, recorded):
    """Everything that follows a letter becoming real: release the programme,
    the timeline entry and the notifications."""
    from qa_core import timeline

    if letter.kind == "approval_letter" and programme.awaiting_body_letter:
        programme.awaiting_body_letter = False
        fields = ["awaiting_body_letter", "updated_at"]
        # D-19: the body's letter releases the programme from the waiting status.
        released = programme.status == "approved_pending_body_letter"
        if released:
            programme.status = (programme.approval_type.programme_status
                                if programme.approval_type_id else "approval_to_commence")
            fields.append("status")
        programme.save(update_fields=fields)
        if released:
            _issue_approval_to_commence(programme, user)
    verb = f"recorded its approval to commence {programme}" if recorded \
        else f"uploaded its {letter.get_kind_display().lower()} for {programme}"
    timeline.record(programme.institution, "programme.body_letter", subject=programme, actor=user,
                    visibility="public" if letter.visible_to_institution else "internal",
                    public_text=f"{body.acronym or body.name} {verb}."
                                + (" The official copy follows." if recorded else ""))
    _notify_letter(letter, recorded=recorded)


def _notify_letter(letter, recorded=False):
    from django.contrib.auth import get_user_model

    from notifications.services import notify_many

    p = letter.programme
    inst = p.institution
    User = get_user_model()
    if recorded:
        title = f"{letter.body.acronym or letter.body.name} approval recorded: {p}"
        text = f"{letter.body.name} approves {p} at {inst.name} to commence. The official letter copy follows."
    else:
        title = f"{letter.body.acronym or letter.body.name} letter uploaded: {p}"
        text = f"{letter.get_kind_display()} for {p} at {inst.name}."
    staff = []
    if inst.handling_department_id:
        staff = list(User.objects.filter(is_active=True, user_type="staff", department_id=inst.handling_department_id,
                                         role__code__in=("director", "officer")))
    notify_many(staff, title=title, body=text, url=f"/qa/institutions/{inst.pk}/")
    if letter.visible_to_institution:
        notify_many(list(User.objects.filter(is_active=True, user_type="institution", institution=inst)),
                    title=title, body=text, url=f"/programmes/{p.pk}/")


@pb_required
def pb_letter_upload(request, pk):
    import datetime

    if request.method != "POST":
        raise Http404
    p = _programme_or_404(request, pk)
    if not is_pb_admin(request.user):
        raise PermissionDenied("Only the body's administrator uploads letters.")
    upload = request.FILES.get("file")
    issued = None
    if request.POST.get("issued_on"):
        try:
            issued = datetime.date.fromisoformat(request.POST["issued_on"])
        except ValueError:
            return pb_programme(request, pk, error="Enter the date as YYYY-MM-DD.", status=422)
    kind = request.POST.get("kind") or "approval_letter"
    from .models import BodyLetter

    if kind not in dict(BodyLetter.KIND_CHOICES):
        kind = "other"
    if upload is None:
        return pb_programme(request, pk, error="Choose the signed PDF.", status=422)
    # A recorded-but-unfiled approval gets its official copy onto the same
    # record, never a duplicate entry.
    target = None
    if kind == "approval_letter":
        target = (BodyLetter.unscoped.filter(programme=p, body=request.user.professional_body,
                                             kind="approval_letter", file="").first())
    try:
        upload_letter(p, body=request.user.professional_body, user=request.user, upload=upload, kind=kind,
                      reference=request.POST.get("reference", ""), issued_on=issued, letter=target)
    except ValidationError as exc:
        return pb_programme(request, pk, error=" ".join(exc.messages), status=422)
    from qa_core.ui import toast

    return toast(pb_programme(request, pk), "success", "Letter uploaded", "NBTE and the institution can see it now.")


@pb_required
@require_http_methods(["POST"])
def pb_programme_approve(request, pk):
    """v2.6: the body approves the institution to commence this programme —
    recorded at once; the signed official copy is uploaded afterwards onto the
    same letter record (no second entry)."""
    import datetime

    from qa_core.ui import toast

    from .models import BodyLetter

    if not is_pb_admin(request.user):
        raise PermissionDenied("Only the body's administrator records its approval.")
    p = _programme_or_404(request, pk)
    open_record = (BodyLetter.unscoped.filter(programme=p, body=request.user.professional_body,
                                              kind="approval_letter", file="").first())
    if open_record is not None:
        return pb_programme(request, pk, status=422,
                            error="The body's approval is already recorded here — upload the signed official "
                                  "copy below.")
    issued = None
    if request.POST.get("issued_on"):
        try:
            issued = datetime.date.fromisoformat(request.POST["issued_on"])
        except ValueError:
            return pb_programme(request, pk, error="Enter the date as YYYY-MM-DD.", status=422)
    try:
        record_approval(p, body=request.user.professional_body, user=request.user,
                        reference=request.POST.get("reference", ""), issued_on=issued,
                        note=request.POST.get("note", ""))
    except ValidationError as exc:
        return pb_programme(request, pk, error=" ".join(exc.messages), status=422)
    return toast(pb_programme(request, pk), "success", "Approval recorded",
                 "NBTE and the institution are told; upload the official copy when it is signed.")


@pb_required
def pb_letter_download(request, pk):
    from qa_core.portal_views import portal_file_response

    from .models import BodyLetter

    letter = BodyLetter.unscoped.filter(pk=pk, body=request.user.professional_body).first()
    if letter is None or not letter.file or not letter.can_download(request.user):
        raise Http404
    return portal_file_response(request, letter, channel="pb-portal")


def create_body_user(body, *, email, first_name, last_name="", job_title="", role_code="pb-admin",
                     primary_contact=False, actor=None, request=None, send_link=True):
    """Create a portal account for the body (registrar = pb-admin) and e-mail a
    one-time set-password link (never a password). The membership row
    (``ProfessionalBodyMember``) keeps the body's own list of people in order."""
    from django.contrib.auth import get_user_model
    from .models import ProfessionalBodyMember

    from . import pb_people

    User = get_user_model()
    email = (email or "").strip().lower()
    if not email:
        raise ValidationError("Enter an e-mail address.")
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("An account with this e-mail already exists.")
    if role_code not in pb_people.ROLES:
        raise ValidationError("Choose a valid role for a person of the body.")
    if primary_contact:  # one No.1 at a time
        ProfessionalBodyMember.objects.filter(body=body, is_primary_contact=True).update(is_primary_contact=False)
    user = User(email=email, first_name=(first_name or "").strip()[:150], last_name=(last_name or "").strip()[:150],
                user_type="professional_body", professional_body=body,
                role=pb_people.role_for(role_code), must_change_password=True, is_active=True)
    user.set_unusable_password()
    user.save()
    ProfessionalBodyMember.objects.create(body=body, user=user, job_title=(job_title or "").strip()[:120],
                                          is_primary_contact=bool(primary_contact), invited_at=timezone.now(),
                                          invited_by=actor)
    if send_link:
        pb_people._send_set_password(user, request=request)
    return user


def resend_set_password_link(member, *, request=None):
    """A fresh set-password link for a body account (cooldown-guarded)."""
    import hashlib

    from django.core.cache import cache
    from django.utils import timezone

    from . import pb_people

    key = "pb:pwcool:" + hashlib.sha256(str(member.user_id).encode()).hexdigest()[:20]
    if cache.get(key):
        raise ValidationError("A link was sent to this person recently — check their inbox first.")
    if not member.user.is_active:
        raise ValidationError("This account is not active; activate it first.")
    pb_people._send_set_password(member.user, request=request)
    cache.set(key, 1, 5 * 60)
    member.credentials_last_sent_at = timezone.now()
    member.save(update_fields=["credentials_last_sent_at", "updated_at"])




# ===========================================================================
# v2.6: the body as a proper portal client — its people, visits and reports,
# the institutions whose programmes it regulates, and programme requests.
# ===========================================================================
PB_TABS = [("", "Programmes"), ("institutions", "Institutions"), ("visits", "Visits"),
           ("reports", "Reports"), ("people", "People"), ("requests", "Requests")]


def _tab_list():
    """The portal tabs as template-ready rows (url names included)."""
    return [{"key": k, "label": label, "name": f"qa_portal:pb_{k}" if k else "qa_portal:pb_dashboard"}
            for k, label in PB_TABS]


def _body_ctx(request, section, **extra):
    from qa_core.portal_views import _shell_context

    ctx = _shell_context(request, f"pb_{section}" if section else "pb_dashboard",
                         body=request.user.professional_body, is_admin=is_pb_admin(request.user),
                         pb_tabs=_tab_list(), section_key=section, tab=section, **extra)
    return ctx


# ---------------------------------------------------------------------------
# People: the body's own accounts (registrar adds them; NBTE can too)
# ---------------------------------------------------------------------------
@pb_required
def pb_people(request, *, error="", ok="", status=200):
    from . import pb_people as people

    body = request.user.professional_body
    ctx = _body_ctx(request, "people", members=list(people.members_of(body, active=False)),
                    can_manage=is_pb_admin(request.user), error=error, ok=ok, roles=people.ROLE_CHOICES)
    return render(request, "qa_portal/pb/people.html", ctx, status=status)


@pb_required
@require_http_methods(["POST"])
def pb_person_new(request):
    from qa_core.ui import toast

    from . import pb_people as service

    if not is_pb_admin(request.user):
        raise PermissionDenied("Only the body's administrator adds its people.")
    post = request.POST
    try:
        service.add_member(request.user.professional_body, email=post.get("email", ""),
                           full_name=post.get("full_name", ""), job_title=post.get("job_title", ""),
                           role_code=post.get("role") or "pb-rep", invited_by=request.user,
                           primary_contact=post.get("primary_contact") == "on", request=request)
    except ValidationError as exc:
        return pb_people(request, error=" ".join(exc.messages), status=422)
    from qa_core import timeline

    timeline.record(request.user.professional_body, "portal.body_person_added", subject=request.user.professional_body,
                    actor=request.user, visibility="internal",
                    internal_text=f"A portal account for a person of the body was created "
                                  f"({(post.get('email') or '').strip().lower()}).")
    return toast(pb_people(request), "success", "Person added",
                 "A set-password link was e-mailed to them.")


@pb_required
@require_http_methods(["POST"])
def pb_person_primary(request, pk):
    from qa_core.ui import toast
    from .models import ProfessionalBodyMember

    from . import pb_people as service

    if not is_pb_admin(request.user):
        raise PermissionDenied
    member = ProfessionalBodyMember.objects.filter(pk=pk, body=request.user.professional_body).first()
    if member is None:
        raise Http404
    service.set_primary(request.user.professional_body, member.user)
    return toast(pb_people(request), "success", "No.1 updated",
                 f"{member.name} is now the body's primary contact (the registrar).")


@pb_required
@require_http_methods(["POST"])
def pb_person_resend(request, pk):
    from qa_core.ui import toast
    from .models import ProfessionalBodyMember

    from . import pb_portal

    if not is_pb_admin(request.user):
        raise PermissionDenied
    member = ProfessionalBodyMember.objects.filter(pk=pk, body=request.user.professional_body).first()
    if member is None:
        raise Http404
    try:
        pb_portal.resend_set_password_link(member, request=request)
    except ValidationError as exc:
        return pb_people(request, error=" ".join(exc.messages), status=422)
    return toast(pb_people(request), "success", "Link sent",
                 f"A fresh set-password link went to {member.user.email}.")


@pb_required
@require_http_methods(["POST"])
def pb_person_toggle(request, pk):
    from qa_core.ui import toast
    from .models import ProfessionalBodyMember

    from . import pb_people as service

    if not is_pb_admin(request.user):
        raise PermissionDenied
    member = ProfessionalBodyMember.objects.filter(pk=pk, body=request.user.professional_body).first()
    if member is None:
        raise Http404
    if member.is_active_link:
        service.deactivate_member(member)
        title, body_ = "Person removed", f"{member.name} no longer signs in for {member.body}."
    else:
        service.reactivate_member(member)
        title, body_ = "Person back on", f"{member.name}'s account is live again; a set-password link was sent."
    return toast(pb_people(request), "success", title, body_)


# ---------------------------------------------------------------------------
# Visits: what is coming for the body and who represents it
# ---------------------------------------------------------------------------
@pb_required
def pb_visits(request, *, error="", status=200):
    from accreditation.models import VisitInvitation
    from accreditation.pb_reports import cases_awaiting_a_report

    from . import pb_people as service

    body = request.user.professional_body
    today = timezone.localdate()
    rows = list(body_visit_rows(body))
    past = [r for r in rows if r.scheduled_start and r.scheduled_start < today]
    invitations = list(VisitInvitation.unscoped.filter(body=body, status__in=VisitInvitation.OPEN)
                       .select_related("case", "case__institution", "letter").order_by("visit_start"))
    # per upcoming visit: the body's person of record (or a picker to choose one)
    visit_rows = []
    for inspection in [r for r in rows if r.scheduled_start and r.scheduled_start >= today]:
        case = inspection.case
        rep = service.representative_for_case(case, body)
        if body_reports_of(case):
            continue  # the body's report on this visit already exists
        visit_rows.append({"inspection": inspection, "case": case, "rep": rep,
                           "choices": list(service.assignable_members(body))})
    ctx = _body_ctx(request, "visits", upcoming=visit_rows, past=past, invitations=invitations,
                    can_assign=is_pb_admin(request.user),
                    awaiting=cases_awaiting_a_report(request.user), error=error)
    return render(request, "qa_portal/pb/visits.html", ctx, status=status)


def body_reports_of(case):
    from accreditation.models import BodyVisitReport

    return BodyVisitReport.unscoped.filter(case=case).exists()


@pb_required
@require_http_methods(["POST"])
def pb_assign_rep(request, case_pk):
    from django.contrib.auth import get_user_model
    from django.shortcuts import get_object_or_404
    from qa_core.ui import toast
    from accreditation.models import QACase

    from . import pb_people as service

    if not is_pb_admin(request.user):
        raise PermissionDenied("Only the body's administrator assigns its visit representative.")
    body = request.user.professional_body
    case = QACase.unscoped.filter(pk=case_pk).select_related("institution").first()
    inspection_ok = any(inspection.case_id == case_pk for inspection in body_visit_rows(body))
    if case is None or not inspection_ok:
        raise Http404
    member_user = get_object_or_404(get_user_model(), pk=(request.POST.get("member") or "0"))
    try:
        row = service.assign_representative(case, body, request.user, member_user,
                                            remark=request.POST.get("remark", ""))
    except ValidationError as exc:
        return pb_visits(request, error=" ".join(exc.messages), status=422)
    name = (row.user.get_full_name() or row.user.email).strip()
    return toast(pb_visits(request), "success", "Representative assigned",
                 f"{name} stands for {body.acronym or body.name} on this visit. NBTE has been told.")


# ---------------------------------------------------------------------------
# Reports: write → registrar approves/returns → send to NBTE
# ---------------------------------------------------------------------------
@pb_required
def pb_reports(request, *, error="", status=200):
    from accreditation import pb_reports as service

    rows = list(service.my_reports(request.user))
    mine = [r for r in rows if r.author_id == request.user.pk]
    review = [r for r in rows if r.author_id != request.user.pk] if is_pb_admin(request.user) else []
    ctx = _body_ctx(request, "reports", rows=rows, mine=mine, review=review,
                    draft_cases=service.cases_awaiting_a_report(request.user),
                    can_review=is_pb_admin(request.user), error=error)
    return render(request, "qa_portal/pb/reports.html", ctx, status=status)


@pb_required
@require_http_methods(["POST"])
def pb_report_new(request):
    from qa_core.ui import toast
    from accreditation.models import QACase
    from accreditation import pb_reports as service

    case = QACase.unscoped.filter(pk=request.POST.get("case") or 0).first()
    if case is None:
        raise Http404
    try:
        report = service.create_report(request.user, case, title=request.POST.get("title", ""),
                                       summary=request.POST.get("summary", ""),
                                       content=request.POST.get("content", ""),
                                       recommendation=request.POST.get("recommendation") or None,
                                       remark=request.POST.get("remark", ""),
                                       upload=request.FILES.get("file"))
    except (ValidationError, PermissionDenied) as exc:
        return pb_reports(request, error=" ".join(getattr(exc, "messages", None) or [str(exc)]), status=422)
    return toast(pb_report(request, report.pk), "success", "Report drafted",
                 "Save it as you go, then submit it to the registrar.")


def _report_or_404(request, pk):
    from accreditation.models import BodyVisitReport

    row = (BodyVisitReport.unscoped.filter(pk=pk)
           .filter(models_case_filter(request.user)).select_related("case", "case__institution", "body", "author",
                                                                   "programme", "member").first())
    if row is None:
        raise Http404
    return row


def models_case_filter(user):
    """A body account sees only its body's reports."""
    from django.db.models import Q

    from accreditation.models import BodyVisitReport

    body_id = getattr(user, "professional_body_id", None)
    allowed = Q(body_id=body_id) if body_id else Q(pk=None)
    return allowed


@pb_required
def pb_report_file(request, pk):
    """v2.6: download the report's attachment (QAFileMixin storage)."""
    from qa_core.portal_views import portal_file_response

    report = _report_or_404(request, pk)
    if not report.file or not report.can_download(request.user):
        raise Http404
    return portal_file_response(request, report, channel="pb-portal")


@pb_required
def pb_report(request, pk, *, error="", status=200):
    from accreditation import pb_reports as service
    from qa_core.portal_views import _shell_context

    report = _report_or_404(request, pk)
    body = request.user.professional_body
    reviewing = service.can_review(request.user, report) and report.is_reviewable()
    editable = service.can_edit(request.user, report)
    ctx = _shell_context(request, "pb_reports", body=body, report=report,
                         is_admin=is_pb_admin(request.user), reviewing=reviewing, editable=editable,
                         pb_tabs=_tab_list(), section_key="reports", tab="reports", error=error,
                         recommendations=type(report).RECOMMENDATION_CHOICES)
    return render(request, "qa_portal/pb/report.html", ctx, status=status)


@pb_required
@require_http_methods(["POST"])
def pb_report_save(request, pk):
    from qa_core.ui import toast
    from accreditation import pb_reports as service

    report = _report_or_404(request, pk)
    try:
        service.update_report(request.user, report, title=request.POST.get("title"),
                              summary=request.POST.get("summary"), content=request.POST.get("content"),
                              recommendation=request.POST.get("recommendation") or None,
                              remark=request.POST.get("remark"), upload=request.FILES.get("file"))
    except (ValidationError, PermissionDenied) as exc:
        return pb_report(request, pk, error=" ".join(getattr(exc, "messages", None) or [str(exc)]), status=422)
    toast_or_reload = toast(pb_report(request, pk), "success", "Saved",
                            "Your report is up to date.")
    return toast_or_reload


@pb_required
@require_http_methods(["POST"])
def pb_report_submit(request, pk):
    from qa_core.ui import toast
    from accreditation import pb_reports as service

    report = _report_or_404(request, pk)
    try:
        service.submit_report(request.user, report)
    except (ValidationError, PermissionDenied) as exc:
        return pb_report(request, pk, error=" ".join(getattr(exc, "messages", None) or [str(exc)]), status=422)
    return toast(pb_report(request, pk), "success", "Sent to the registrar",
                 "You will be told when they have reviewed it.")


@pb_required
@require_http_methods(["POST"])
def pb_report_review(request, pk):
    from qa_core.ui import toast
    from accreditation import pb_reports as service

    report = _report_or_404(request, pk)
    decision = request.POST.get("decision", "approve")
    try:
        service.decide(request.user, report, decision, remark=request.POST.get("remark", ""))
    except (ValidationError, PermissionDenied) as exc:
        return pb_report(request, pk, error=" ".join(getattr(exc, "messages", None) or [str(exc)]), status=422)
    if decision == "return":
        return toast(pb_report(request, pk), "success", "Returned",
                     "The representative is told what to correct.")
    return toast(pb_report(request, pk), "success", "Approved",
                 "You can now send it on to NBTE.")


@pb_required
@require_http_methods(["POST"])
def pb_report_send(request, pk):
    from qa_core.ui import toast
    from accreditation import pb_reports as service

    report = _report_or_404(request, pk)
    try:
        service.send_to_nbte(request.user, report, final_upload=request.FILES.get("file"))
    except (ValidationError, PermissionDenied) as exc:
        return pb_report(request, pk, error=" ".join(getattr(exc, "messages", None) or [str(exc)]), status=422)
    return toast(pb_report(request, pk), "success", "Sent to NBTE",
                 "The case team sees your approved report on the case's Reports tab.")


# ---------------------------------------------------------------------------
# Institutions: who runs the body's programmes, and where each one stands
# ---------------------------------------------------------------------------
@pb_required
def pb_institutions(request):
    by_inst = {}
    for p in body_programmes(request.user.professional_body):
        code, label, visit = stage_of(p)
        by_inst.setdefault(p.institution, []).append({"programme": p, "stage": code, "label": label})
    rows = [{"institution": inst, "programmes": progs}
            for inst, progs in sorted(by_inst.items(), key=lambda kv: kv[0].name.lower())]
    ctx = _body_ctx(request, "institutions", rows=rows)
    return render(request, "qa_portal/pb/institutions.html", ctx)


# ---------------------------------------------------------------------------
# Requests: ask NBTE to put a programme under the body
# ---------------------------------------------------------------------------
@pb_required
def pb_requests(request, *, error="", status=200):
    from .models import BodyProgrammeRequest, Institution

    body = request.user.professional_body
    rows = (BodyProgrammeRequest.objects.filter(body=body)
            .select_related("institution", "level", "category", "decided_by", "requested_by")
            .order_by("-created_at", "-id"))
    institutions = (Institution.unscoped.filter(is_active=True).order_by("name"))
    ctx = _body_ctx(request, "requests", rows=rows, groups=_programme_choice_rows(body),
                    institutions=institutions,
                    can_submit=is_pb_admin(request.user), statuses=BodyProgrammeRequest.STATUS_CHOICES,
                    error=error)
    return render(request, "qa_portal/pb/requests.html", ctx, status=status)


@pb_required
@require_http_methods(["POST"])
def pb_request_new(request):
    from django.shortcuts import redirect
    from qa_core.ui import toast

    from . import pb_requests as service
    from .models import Institution, ProgrammeCategory, ProgrammeLevel

    if not is_pb_admin(request.user):
        raise PermissionDenied("Only the body's administrator sends programme requests.")
    body = request.user.professional_body
    institution = Institution.unscoped.filter(pk=request.POST.get("institution") or 0).first()
    level = ProgrammeLevel.objects.filter(pk=request.POST.get("level") or 0).first()
    category = ProgrammeCategory.objects.filter(pk=request.POST.get("category") or 0).first()
    try:
        row = service.submit(body, user=request.user, institution=institution,
                             programme_name=request.POST.get("programme_name", ""),
                             level=level, category=category,
                             rationale=request.POST.get("rationale", ""))
    except ValidationError as exc:
        return pb_requests(request, error=" ".join(exc.messages), status=422)
    return toast(redirect("/pb/requests/"), "success", "Request sent",
                 f"NBTE has been asked to put “{row.programme_name}” under {body.acronym or body.name}. "
                 "You will be told when it is decided.")


@pb_required
@require_http_methods(["POST"])
def pb_request_cancel(request, pk):
    from django.shortcuts import redirect
    from qa_core.ui import toast

    from . import pb_requests as service
    from .models import BodyProgrammeRequest

    if not is_pb_admin(request.user):
        raise PermissionDenied("Only the body's administrator withdraws requests.")
    row = BodyProgrammeRequest.objects.filter(pk=pk, body=request.user.professional_body).first()
    if row is None:
        raise Http404
    try:
        service.withdraw(row, user=request.user)
    except ValidationError as exc:
        return pb_requests(request, error=" ".join(exc.messages), status=422)
    return toast(redirect("/pb/requests/"), "success", "Request withdrawn")
