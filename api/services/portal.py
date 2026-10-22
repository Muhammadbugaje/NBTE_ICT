"""Portal door (institutions and resource persons): shared helpers and the
institution-area payloads (institution profile, users, documents,
application, timeline).

Everything is read the way the QA portal pages read it — the same
``Model.objects.for_user(user)`` scoping and the same helpers in
``institutions.portal_views`` / ``institutions.services`` /
``accreditation.services``. This module only shapes the result as JSON.
"""
import datetime

from django.http import Http404
from django.urls import reverse
from django.utils import timezone

# Files the portal serves (qa_core.portal_views.PORTAL_FILE_MODELS); the
# generic files/<label>/<pk>/ endpoint accepts exactly these labels.
from qa_core.portal_views import PORTAL_FILE_MODELS  # noqa: F401  (re-exported)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def money(value):
    """("1200000.00", "₦1,200,000.00") — the contract's money pair."""
    from payments.money import naira, to_money

    if value in (None, ""):
        value = 0
    return f"{to_money(value):.2f}", naira(value)


def local_date(value):
    """A date (or the local date of a datetime), or None."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return timezone.localdate(value) if timezone.is_aware(value) else value.date()
    return value


def route_of(url):
    """Portal page path -> app route ("/payments/5/" -> "/payments/5")."""
    if not url:
        return ""
    return url.rstrip("/") or "/"


def ref(obj, *, code=True):
    """{"id", "code", "name"} for a lookup row (or None)."""
    if obj is None:
        return None
    return {"id": obj.pk, "code": (getattr(obj, "code", "") or "") if code else "",
            "name": str(getattr(obj, "name", obj))}


def file_url(obj):
    """The API download path of a protected file row (files/<label>/<pk>/)."""
    return reverse("api:portal_file", args=[obj._meta.label_lower, obj.pk])


def file_dict(obj, url=None):
    """A downloadable file: name, size and the API path that serves it."""
    if obj is None or not getattr(obj, "file", None):
        return None
    name = getattr(obj, "original_name", "") or obj.file.name.rsplit("/", 1)[-1]
    size = getattr(obj, "size_bytes", None)
    return {"id": obj.pk, "name": name, "size_bytes": size or 0,
            "size_label": getattr(obj, "size_label", "") or "", "download_url": url or file_url(obj)}


def user_name(user):
    """Same as the portal's ``who`` filter."""
    if not user:
        return ""
    return (user.get_full_name() or "").strip() or getattr(user, "email", "") or ""


def humanize(value):
    from qa_core.templatetags.qa_tags import humanize_code

    return humanize_code(value)


# ---------------------------------------------------------------------------
# Who may use which page (institution_required / rp_required)
# ---------------------------------------------------------------------------
def institution_or_404(user):
    """The signed-in institution account's institution (the portal's
    ``institution_required``: resource persons and orphans get 404)."""
    from qa_core.portal_views import _institution_for

    if getattr(user, "user_type", "") != "institution":
        raise Http404
    inst = _institution_for(user)
    if inst is None:
        raise Http404
    return inst


def full_institution(request):
    """institutions.portal_views._inst (with the lookups the profile shows);
    404 for anyone but an institution account."""
    from institutions.portal_views import _inst

    institution_or_404(request.user)
    inst = _inst(request)
    if inst is None:
        raise Http404
    return inst


def rp_or_404(user):
    """inspection.portal_views.rp_required: the resource-person record."""
    from inspection import services

    rp = services.rp_of(user)
    if rp is None:
        raise Http404
    return rp


# ---------------------------------------------------------------------------
# Institution profile
# ---------------------------------------------------------------------------
def institution_payload(request, inst):
    from institutions import portal_views as pv
    from institutions import services

    editable = pv._profile_editable(request, inst)
    contacts = [{"kind": c.kind, "kind_label": c.get_kind_display(), "name": c.name, "value": c.value}
                for c in services.contacts_of(inst)]
    addresses = [{"kind": a.kind, "kind_label": a.get_kind_display(), "line1": a.line1, "town": a.town,
                  "lga": a.lga, "state": a.state.name if a.state_id else "", "one_line": a.one_line}
                 for a in services.addresses_of(inst)]
    edit = None
    if editable:
        initial = pv._profile_initial(inst)
        edit = {k: initial.get(k) for k in PROFILE_FIELDS}
        for key in ("lga", "cac_rc_number", "website", "official_email", "phone", "head_name", "registrar_name",
                    "bursar_name", "address_line1", "address_town", "name"):
            edit[key] = edit.get(key) or ""
    dept = inst.handling_department
    return {
        "id": inst.pk, "code": inst.code, "name": inst.name, "status": inst.status,
        "status_label": inst.status_label,
        "ownership": ref(inst.ownership), "institution_type": ref(inst.institution_type),
        "category": ref(inst.category), "state": ref(inst.state, code=False) if inst.state_id else None,
        "lga": inst.lga or "", "zone": inst.zone or "", "year_established": inst.year_established,
        "cac_rc_number": inst.cac_rc_number or "", "website": inst.website or "",
        "handling_department": {"id": dept.pk, "name": dept.name} if dept is not None else None,
        "contacts": contacts, "addresses": addresses,
        "portal_enabled": bool(inst.portal_enabled),
        "editable": editable,
        "can_manage_users": pv._can_manage_users(request),
        "edit": edit,
    }


# The fields of institutions.portal_views.PortalProfileForm (PATCH institution/).
PROFILE_FIELDS = ("name", "ownership", "institution_type", "category", "state", "lga", "year_established",
                  "cac_rc_number", "website", "official_email", "phone", "head_name", "registrar_name",
                  "bursar_name", "address_line1", "address_town")


def profile_options(state_id=None):
    """Choices for the profile form (the website's selects and LGA list)."""
    from institutions import services
    from institutions.portal_views import PortalProfileForm
    from staff.models import StateOfOrigin

    form = PortalProfileForm()
    state = StateOfOrigin.objects.filter(pk=state_id).first() if state_id else None
    return {
        "ownerships": [ref(o) for o in form.fields["ownership"].queryset],
        "institution_types": [ref(t) for t in form.fields["institution_type"].queryset],
        "categories": [dict(ref(c), institution_type_id=c.institution_type_id)
                       for c in form.fields["category"].queryset],
        "states": [{"id": s.pk, "name": s.name} for s in form.fields["state"].queryset],
        "lgas": list(services.lgas_for(state)) if state is not None else [],
    }


def members_payload(request, inst):
    from institutions import portal_views as pv
    from institutions import services

    from .me import initials

    members = []
    for m in services.members_of(inst):
        members.append({"id": m.pk, "user_id": m.user_id, "full_name": user_name(m.user),
                        "initials": initials(m.user),
                        "role": m.institution_role.name if m.institution_role_id else "",
                        "job_title": m.job_title or "", "is_primary_contact": bool(m.is_primary_contact),
                        "invited": not m.user.has_usable_password()})
    can_manage = pv._can_manage_users(request)
    roles = [{"value": v, "label": str(label)} for v, label in pv.InviteUserForm.base_fields["role"].choices]
    return {"can_manage": can_manage, "results": members, "invite_roles": roles if can_manage else []}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def document_row(request, inst, row):
    from institutions import portal_views as pv
    from qa_core.templatetags.qa_tags import doc_state_label

    row = pv._decorate_row(request, inst, row)
    kind, doc, dept = row["kind"], row["document"], row["department"]
    return {
        "kind": {"id": kind.pk, "code": kind.code, "name": kind.name, "description": kind.description or ""},
        "mandatory": bool(row["mandatory"]), "state": row["state"], "state_label": doc_state_label(row["state"]),
        "department": {"code": dept.code, "name": dept.name} if dept is not None else None,
        "document": document_dict(doc) if doc is not None else None,
        "can_upload": bool(row["can_upload"]), "max_mb": row["max_mb"], "extensions": list(kind.extensions()),
    }


def document_dict(doc):
    return {"id": doc.pk, "version": doc.version, "status": doc.status, "uploaded_at": doc.created_at,
            "review_remark": doc.review_remark or "", "valid_until": doc.valid_until,
            "file": file_dict(doc, reverse("api:portal_document_download", args=[doc.pk]))}


def summary_payload(rows):
    from institutions import services

    s = services.checklist_summary(rows)
    return {"mandatory": s["mandatory"], "ready": s["ready"], "complete": s["complete"], "percent": s["percent"],
            "missing": [{"kind_id": r["kind"].pk, "name": r["kind"].name, "state": r["state"]}
                        for r in s["missing"]]}


def filter_document_rows(rows, params):
    """institutions.portal_views.documents' filters (state, dept, need, q)."""
    state = params.get("state", "")
    dept = params.get("dept", "")
    need = params.get("need", "")
    q = (params.get("q") or "").strip().lower()
    return [r for r in rows if (not state or r["state"] == state)
            and (not dept or (r["department"] and r["department"].code == dept))
            and (not need or (need == "required") == r["mandatory"])
            and (not q or q in r["kind"].name.lower())]


# ---------------------------------------------------------------------------
# Application (SOP-01)
# ---------------------------------------------------------------------------
def obligation_row(ob):
    amount, display = money(ob.amount)
    balance, balance_display = money(ob.balance)
    return {"id": ob.pk, "reference": ob.reference, "description": ob.description, "amount": amount,
            "amount_display": display, "balance": balance, "balance_display": balance_display,
            "due_date": local_date(ob.due_at), "status": ob.status, "route": f"/payments/{ob.pk}"}


def estimate_payload(est):
    """payments.services.fee_estimate result as JSON (or None)."""
    if not est:
        return None
    total = None
    total_display = ""
    if est.get("total") is not None:
        total, total_display = money(est["total"])
    lines = []
    for label, amount in est.get("lines") or []:
        a, d = money(amount)
        lines.append({"label": str(label), "amount": a, "amount_display": d})
    return {"total": total, "total_display": total_display, "lines": lines, "error": str(est.get("error") or ""),
            "blocked": bool(est.get("blocked"))}


def application_payload(request, inst):
    """accreditation.portal_views._context, as JSON."""
    from accreditation import portal_views as apv
    from accreditation import services
    from accreditation.models import DepartmentalClearance
    from accreditation.qa_services import max_streams
    from institutions import services as inst_services
    from institutions.models import Programme

    app = apv._app(request, inst)
    rows = inst_services.checklist(inst)
    impersonating = bool(getattr(request, "is_impersonating", False))
    clearances = []
    if app is not None and app.round:
        for c in (DepartmentalClearance.objects.for_user(request.user)
                  .filter(application=app, round=app.round).select_related("department").order_by("branch_code")):
            clearances.append({"department": c.department.name if c.department_id else "", "status": c.status,
                               "status_label": c.get_status_display(), "decided_at": c.decided_at,
                               "opened_at": c.opened_at,
                               "remark": c.remark if c.status == "returned" else ""})
    programmes = list(Programme.objects.for_user(request.user).filter(institution=inst)
                      .select_related("level", "category").order_by("name"))
    selecting = app is not None and app.status == "programme_selection" and not impersonating
    blockers = services.submission_blockers(app) if app is not None and app.status == "draft" else []
    hold = inst_services.active_hold(app) if app is not None else None
    board = services.stage_board(app) if app is not None else []
    options = None
    if selecting:
        form = apv.ProgrammeForm()
        options = {"levels": [ref(lv) for lv in form.fields["level"].queryset],
                   "categories": [ref(c, code=False) for c in form.fields["category"].queryset],
                   "max_streams": max_streams()}
    return {
        "institution": {"id": inst.pk, "code": inst.code, "name": inst.name, "status": inst.status},
        "can_start": app is None and inst.status in ("proposed", "under_review") and not impersonating,
        "application": None if app is None else {
            "id": app.pk, "reference": app.reference, "status": app.status,
            "public_status": str(services.public_status(app)), "round": app.round,
            "cycle": app.cycle.name if app.cycle_id else "", "submitted_at": app.submitted_at,
            "return_remark": app.return_remark or "",
        },
        "stages": board,
        "hold": {"reason": str(getattr(hold, "reason", "") or "")} if hold else None,
        "gaps": services.profile_gaps(inst) if app is None or app.status == "draft" else [],
        "blockers": blockers,
        "can_submit": bool(app is not None and app.status == "draft" and not blockers and not impersonating),
        "documents": summary_payload(rows),
        "clearances": clearances,
        "fees": [obligation_row(ob) for ob in apv._obligations(request, app)],
        "programmes": [programme_brief(p) for p in programmes],
        "estimate": estimate_payload(apv._programme_estimate(inst, programmes)),
        "selecting_programmes": selecting,
        "programme_options": options,
        "handled_by": inst.handling_department.name if inst.handling_department_id else "NBTE",
    }


def programme_brief(p):
    return {"id": p.pk, "name": p.name, "level": ref(p.level) if p.level_id else None,
            "category": p.category.name if p.category_id else "", "status": p.status,
            "status_label": p.get_status_display(), "requested_streams": p.requested_streams,
            "approved_streams": p.approved_streams}


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------
def timeline_queryset(user, params):
    """institutions.portal_views.timeline's query and filters."""
    from django.db.models import Q

    from institutions.portal_views import PERIODS
    from qa_core.models import TimelineEvent

    qs = TimelineEvent.objects.for_user(user).filter(visibility="public").select_related("actor")
    kind = params.get("kind", "")
    if kind == "application":
        qs = qs.filter(Q(event_code__startswith="application") | Q(event_code__startswith="clearance"))
    elif kind == "payment":
        qs = qs.filter(event_code__startswith="payment")
    elif kind == "document":
        qs = qs.filter(event_code__startswith="document")
    elif kind == "institution":
        qs = qs.filter(Q(event_code__startswith="institution") | Q(event_code__startswith="contact")
                       | Q(event_code__startswith="address") | Q(event_code__startswith="portal"))
    period = params.get("period", "")
    if period in PERIODS:
        qs = qs.filter(at__gte=timezone.now() - datetime.timedelta(days=PERIODS[period]))
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(public_text__icontains=q)
    by = params.get("by", "")
    if by == "you":
        qs = qs.filter(actor__user_type="institution")
    elif by == "nbte":
        qs = qs.exclude(actor__user_type="institution")
    return qs.order_by("-at", "-id")


def timeline_row(e):
    return {"id": e.pk, "at": e.at, "text": e.public_text, "event_code": e.event_code,
            "by": "you" if getattr(e.actor, "user_type", "") == "institution" else "nbte"}
