"""Portal door: QA requests (SOP-02), the SSQ, queries, the programme board,
programmes and outcome letters — JSON shapes of what
``accreditation.qa_portal_views``, ``accreditation.programme_portal_views``
and ``institutions.programme_views`` show. Business rules stay in
``accreditation.qa_services`` / ``accreditation.ssq``."""
import datetime

from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from .portal import estimate_payload, file_dict, humanize, obligation_row, ref, user_name


# ---------------------------------------------------------------------------
# Lookups (accreditation.qa_portal_views._req_or_404 / _ssq_or_404 / _row_or_404)
# ---------------------------------------------------------------------------
def request_or_404(user, pk):
    from accreditation.models import QARequest

    req = QARequest.objects.for_user(user).select_related("qa_type", "institution").filter(pk=pk).first()
    if req is None:
        raise Http404
    return req


def ssq_or_404(user, req, ssq_pk):
    from accreditation.models import SSQ

    ssq = (SSQ.objects.for_user(user)
           .select_related("template", "qa_request_programme__programme__level",
                           "qa_request_programme__programme__category", "assigned_department_user")
           .filter(pk=ssq_pk, qa_request_programme__qa_request=req).first())
    if ssq is None:
        raise Http404
    return ssq


def row_or_404(user, req, row_pk):
    from accreditation.models import QARequestProgramme

    row = (QARequestProgramme.objects.for_user(user).select_related("programme")
           .filter(pk=row_pk, qa_request=req).first())
    if row is None:
        raise Http404
    return row


def manage_or_404(user, inst):
    """qa_portal_views._manage_or_404 (only administrators change requests)."""
    from accreditation import qa_services as svc

    if not svc.can_manage_requests(user, inst):
        raise Http404


# ---------------------------------------------------------------------------
# QA requests
# ---------------------------------------------------------------------------
def requests_queryset(user, params):
    """accreditation.qa_portal_views.requests_list's query and filters."""
    from accreditation.models import QARequest

    qs = QARequest.objects.for_user(user).select_related("qa_type").order_by("-created_at")
    show = params.get("show", "open")
    if show == "open":
        qs = qs.exclude(status__in=("cancelled", "decided"))
    elif show == "closed":
        qs = qs.filter(status__in=("cancelled", "decided"))
    if params.get("type"):
        qs = qs.filter(qa_type__code=params["type"])
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(request_programmes__programme__name__icontains=q)).distinct()
    if params.get("level"):
        qs = qs.filter(request_programmes__programme__level__code=params["level"],
                       request_programmes__status="active").distinct()
    return qs


def type_dict(t):
    return {"code": t.code, "name": t.name}


def request_row(user, req):
    from accreditation import qa_services as svc
    from accreditation.models import QARequestProgramme

    progs = (QARequestProgramme.objects.for_user(user).filter(qa_request=req, status="active")
             .select_related("programme__level"))
    return {"id": req.pk, "reference": req.reference, "qa_type": type_dict(req.qa_type), "status": req.status,
            "public_status": str(svc.public_status(req)), "created_at": req.created_at,
            "submitted_at": req.submitted_at,
            "programmes": [{"id": r.programme_id, "name": r.programme.name,
                            "level": r.programme.level.code if r.programme.level_id else ""} for r in progs]}


def new_request_payload(user, inst, qa_type=None):
    """accreditation.qa_portal_views._new_context: the types (with why one is
    unavailable) and, for a chosen type, each programme's eligibility."""
    from accreditation import qa_services as svc
    from accreditation.models import QAType

    types = []
    for t in QAType.objects.filter(is_active=True).order_by("sort_order"):
        ok, why = svc.type_availability(inst, t)
        types.append({"code": t.code, "name": t.name, "description": t.description or "", "available": ok,
                      "reason": why, "asks_streams": bool(t.asks_streams), "max_programmes": t.max_programmes})
    programmes = []
    if qa_type is not None:
        for row in svc.eligible_programmes(inst, qa_type):
            p = row["programme"]
            programmes.append({"id": p.pk, "name": p.name, "level": ref(p.level) if p.level_id else None,
                               "status": p.status, "status_label": p.get_status_display(),
                               "expiry_date": p.expiry_date, "eligible": row["eligible"], "reason": row["reason"],
                               "streams": svc.default_streams(p, qa_type)})
    from institutions.models import Institution

    return {"can_create": bool(svc.can_manage_requests(user, inst) and inst.status == Institution.APPROVED),
            "types": types, "qa_type": type_dict(qa_type) if qa_type is not None else None,
            "programmes": programmes, "window_months": svc.window_months(), "max_streams": svc.max_streams()}


def request_estimate(user, inst, qa_type, ids, asked):
    """accreditation.qa_portal_views.request_estimate (live fee)."""
    from django.core.exceptions import ValidationError

    from accreditation import qa_services as svc
    from institutions.models import Programme

    programmes = list(Programme.objects.for_user(user).filter(institution=inst, pk__in=ids))
    pairs, problems = [], []
    for p in programmes:
        try:
            n = svc._streams_for(p, qa_type, asked.get(p.pk)) if qa_type is not None else 1
        except ValidationError as exc:
            problems.append(" ".join(exc.messages))
            n = svc.default_streams(p, qa_type)
        pairs.append((p, n))
    est = svc.fee_estimate(inst, qa_type, pairs) if (qa_type is not None and pairs) else None
    return {"estimate": estimate_payload(est), "problems": problems, "count": len(pairs)}


def _ssq_brief(user, ssq):
    from accreditation import qa_services as svc
    from accreditation import ssq as engine

    progress = engine.section_progress(ssq.template, ssq.data or {}, svc.evidence_counts(ssq))
    return {"id": ssq.pk, "version": ssq.version, "status": ssq.status,
            "public_status": str(svc.ssq_public_status(ssq)),
            "done": sum(p["filled"] for p in progress), "total": sum(p["total"] for p in progress),
            "review_remark": ssq.review_remark or "", "last_saved_at": ssq.last_saved_at}


def next_step(req, ssqs, obligations):
    """A one-line "what now" for the request (the detail page's callouts)."""
    status = req.status
    if status == "draft":
        return "Check the programmes and streams, then submit the request."
    if status == "programme_clearance":
        return "NBTE is clearing the new programme(s); the QA fee follows."
    if status in ("fee_due", "payment_pending_confirmation"):
        if any(o["status"] in ("awaiting_payment", "rejected", "partially_paid") for o in obligations):
            return "Pay the QA fee on the Payments page."
        if req.fee_note:
            return "NBTE will issue the QA fee shortly."
        return "Your payment is being confirmed by NBTE Finance."
    if status == "ssq_in_progress":
        if any(s["status"] == "returned" for s in ssqs):
            return "NBTE returned an SSQ. Start a new version and fix what was asked."
        return "Complete and submit the self-study questionnaire (SSQ) for each programme."
    if status in ("ssq_submitted", "in_workflow"):
        return "NBTE is reviewing your submission."
    if status == "decided":
        return "NBTE has decided. Outcome letters are under Outcome letters."
    if status == "cancelled":
        return "This request was cancelled."
    return ""


def request_detail_payload(user, req):
    """accreditation.qa_portal_views._detail_context, as JSON."""
    from django.contrib.contenttypes.models import ContentType

    from accreditation import qa_portal_views as qv
    from accreditation import qa_services as svc
    from accreditation.models import SSQ, QARequest, QARequestProgramme, Query
    from payments.models import PaymentObligation

    inst = req.institution
    rows = list(QARequestProgramme.objects.for_user(user).filter(qa_request=req)
                .select_related("programme__level", "replaced_by__programme").order_by("status", "programme__name"))
    active_ids = {r.programme_id for r in rows if r.status == "active"}
    candidates = []
    if svc.can_change_programmes(req, "add_programme") or svc.can_change_programmes(req, "replace_programme"):
        for item in svc.eligible_programmes(inst, req.qa_type):
            if item["programme"].pk not in active_ids and item["eligible"]:
                p = item["programme"]
                candidates.append({"id": p.pk, "name": p.name, "level": p.level.code if p.level_id else ""})
    programmes = []
    for row in rows:
        ssq = None
        if row.status == "active":
            current = SSQ.objects.for_user(user).filter(qa_request_programme=row, is_current=True).first()
            if current is not None:
                ssq = _ssq_brief(user, current)
        programmes.append({
            "row_id": row.pk, "programme": {"id": row.programme_id, "name": row.programme.name,
                                            "level": row.programme.level.code if row.programme.level_id else ""},
            "status": row.status, "requested_streams": row.requested_streams,
            "is_new_programme": bool(row.is_new_programme), "clearance_status": row.clearance_status or "",
            "clearance_remark": row.clearance_remark or "", "dropped_reason": row.dropped_reason or "",
            "replaced_by": row.replaced_by.programme.name if row.replaced_by_id else "", "ssq": ssq})
    obligations = [obligation_row(o) for o in PaymentObligation.objects.for_user(user).filter(
        source_type=ContentType.objects.get_for_model(QARequest), source_id=str(req.pk)).order_by("-created_at")]
    manage = svc.can_manage_requests(user, inst)
    ssqs = [p["ssq"] for p in programmes if p["ssq"]]
    estimate = None
    if req.status == "draft":
        estimate = estimate_payload(svc.fee_estimate(inst, req.qa_type, [(r.programme, r.requested_streams)
                                                                         for r in rows if r.status == "active"]))
    return {
        "id": req.pk, "reference": req.reference, "qa_type": type_dict(req.qa_type), "status": req.status,
        "public_status": str(svc.public_status(req)), "created_at": req.created_at,
        "submitted_at": req.submitted_at, "fee_note": req.fee_note or "", "cancel_reason": req.cancel_reason or "",
        "stages": qv._board(req), "programmes": programmes, "candidates": candidates, "fees": obligations,
        "queries": [{"id": q.pk, "reference": q.reference, "title": q.title, "status": q.status,
                     "due_at": q.due_at} for q in Query.objects.for_user(user).filter(qa_request=req)
                    .order_by("-created_at")],
        "estimate": estimate, "next_step": next_step(req, ssqs, obligations),
        "can_manage": manage,
        "can_add": bool(manage and svc.can_change_programmes(req, "add_programme")),
        "can_drop": bool(manage and svc.can_change_programmes(req, "drop_programme")),
        "can_replace": bool(manage and svc.can_change_programmes(req, "replace_programme")),
        "can_cancel": bool(manage and svc.is_cancellable(req)),
        "can_submit": bool(manage and req.status == "draft" and _is_ce(user)),
        "cancellable_stages": [str(s) for s in svc.cancellable_stage_labels(req)],
        "max_streams": svc.max_streams(),
    }


# ---------------------------------------------------------------------------
# SSQ (the workspace: every section at once, for offline drafting)
# ---------------------------------------------------------------------------
def _evidence_dict(ev):
    return {"id": ev.pk, "name": ev.display_name, "status": ev.status,
            "reused_from": ({"id": ev.reused_from_id, "kind": ev.reused_from.kind.name,
                             "version": ev.reused_from.version} if ev.reused_from_id else None),
            "file": file_dict(ev) if ev.file else (file_dict(ev.reused_from) if ev.reused_from_id else None),
            "uploaded_at": ev.created_at}


def _field_payload(field, value, programme, *, prefilled, edited, evidence, reusable):
    from accreditation import ssq as engine

    out = {"key": field["key"], "type": field["type"], "label": field.get("label", field["key"]),
           "required": bool(field.get("required")), "help": field.get("help", "") or "",
           "placeholder": field.get("placeholder", "") or "", "width": field.get("width", "full") or "full",
           "validation": field.get("validation") or {},
           "options": [{"value": v, "label": lab} for v, lab in engine.option_pairs(field)],
           "value": value, "prefilled": prefilled, "edited": edited,
           "multiple": bool(field.get("multiple")), "document_kind": field.get("document_kind") or "",
           "columns": None, "min_rows": None, "max_rows": None, "items": None,
           "evidence": None, "reusable_documents": None}
    if field["type"] == "table":
        out["columns"] = [{"key": c["key"], "type": c.get("type", "text"), "label": c.get("label", c["key"]),
                           "options": [{"value": v, "label": lab} for v, lab in engine.option_pairs(c)]}
                          for c in field.get("columns") or []]
        rules = field.get("validation") or {}
        out["min_rows"] = int(field.get("min_rows") or rules.get("min_rows") or 0)
        out["max_rows"] = int(field.get("max_rows") or rules.get("max_rows") or 200)
        out["value"] = list(value or [])
    if field["type"] == "catalogue":
        qty = value or {}
        # X35: no required quantity for the institution.
        out["items"] = [{"id": item.pk, "name": item.name, "unit": getattr(item, "unit", "") or "",
                         "quantity": qty.get(str(item.pk), "")}
                        for item in engine.catalogue_items(field, programme)]
        out["value"] = dict(qty)
    if field["type"] == "multi_choice":
        out["value"] = list(value or [])
    if field["type"] == "file":
        out["value"] = None
        out["evidence"] = [_evidence_dict(ev) for ev in evidence]
        out["reusable_documents"] = [{"id": d.pk, "kind": d.kind.name, "version": d.version,
                                      "name": d.display_name} for d in reusable]
    return out


def ssq_payload(user, req, ssq):
    """accreditation.qa_portal_views._ws_context for every section."""
    from accreditation import qa_services as svc
    from accreditation import ssq as engine
    from accreditation.models import SSQ, Evidence, Submission

    template = ssq.template
    sections = template.sections()
    programme = ssq.qa_request_programme.programme
    counts = svc.evidence_counts(ssq)
    progress = {p["section"]["key"]: p for p in engine.section_progress(template, ssq.data or {}, counts)}
    editable = svc.editable_sections(ssq, user)
    meta = ssq.meta or {}
    edited = set(meta.get("edited", []))
    prefilled = set(meta.get("prefilled", [])) - edited
    by_field = {}
    for ev in (Evidence.objects.for_user(user).filter(ssq=ssq).exclude(status="removed")
               .select_related("reused_from__kind")):
        by_field.setdefault(ev.field_key, []).append(ev)
    data = ssq.data or {}
    out_sections = []
    for section in sections:
        fields = []
        for field in section.get("fields", []):
            reusable = svc.reusable_documents(ssq, field) if field["type"] == "file" else []
            fields.append(_field_payload(field, data.get(field["key"]), programme,
                                         prefilled=field["key"] in prefilled, edited=field["key"] in edited,
                                         evidence=by_field.get(field["key"], []), reusable=reusable))
        p = progress.get(section["key"], {"filled": 0, "total": 0, "complete": True})
        out_sections.append({"key": section["key"], "title": section.get("title", section["key"]),
                             "description": section.get("description", "") or "",
                             "department": bool(section.get("department", True)),
                             "editable": section["key"] in editable,
                             "progress": {"filled": p["filled"], "total": p["total"], "complete": p["complete"]},
                             "saved_at": (meta.get("sections", {}).get(section["key"]) or {}).get("saved_at"),
                             "fields": fields})
    problems = svc.submission_problems(ssq) if ssq.is_current else {}
    role = getattr(getattr(user, "role", None), "code", "")
    manage = svc.can_manage_requests(user, req.institution)
    is_dept = svc.is_department_user(user)
    submission = Submission.objects.for_user(user).filter(ssq=ssq).order_by("-version").first()
    frozen = not ssq.is_current or ssq.status in ("submitted", "accepted", "returned", "analysed")
    return {
        "id": ssq.pk, "request": {"id": req.pk, "reference": req.reference, "qa_type": type_dict(req.qa_type)},
        "programme": {"id": programme.pk, "name": programme.name,
                      "level": programme.level.code if programme.level_id else ""},
        "template": {"id": template.pk, "name": template.name, "version": template.version},
        "version": ssq.version, "status": ssq.status, "public_status": str(svc.ssq_public_status(ssq)),
        "is_current": bool(ssq.is_current), "review_remark": ssq.review_remark or "",
        "department_remark": ssq.department_remark or "", "assigned_sections": list(ssq.assigned_sections or []),
        "assigned_department_user": (user_name(ssq.assigned_department_user)
                                     if ssq.assigned_department_user_id else ""),
        "last_saved_at": ssq.last_saved_at, "submitted_at": ssq.submitted_at,
        "sections": out_sections,
        "editable_sections": editable,
        "problems": [{"key": k, "message": str(v)} for k, v in problems.items()],
        "can_submit": bool(role in svc.submit_roles() and ssq.status in ("draft", "submitted_by_department")
                           and ssq.is_current and not is_dept),
        "can_revise": bool(manage and not is_dept and ssq.status == "returned" and ssq.is_current),
        "can_send_department": bool(manage and ssq.is_current and ssq.status in ("draft", "submitted_by_department")),
        "can_recall": bool(manage and ssq.is_current and ssq.status == "returned_to_department"),
        "can_department_submit": bool(is_dept and ssq.assigned_department_user_id == user.pk
                                      and ssq.status == "returned_to_department"),
        "department_users": ([{"id": u.pk, "name": user_name(u)} for u in svc.department_users(req.institution)]
                             if manage else []),
        "is_department": is_dept,
        "versions": [{"id": v.pk, "version": v.version, "status": v.status, "is_current": bool(v.is_current)}
                     for v in SSQ.objects.for_user(user).filter(qa_request_programme=ssq.qa_request_programme)
                     .order_by("-version")],
        "submission": ({"version": submission.version, "submitted_at": submission.submitted_at,
                        "snapshot": engine.for_audience(submission.rendered_snapshot, "institution")}
                       if submission is not None and frozen else None),
        "max_mb": _max_mb(),
    }


def _max_mb():
    import config

    try:
        return int(config.get("qa.documents.max_upload_mb", 10))
    except (TypeError, ValueError):
        return 10


def section_of(ssq, key):
    for section in ssq.template.sections():
        if any(f["key"] == key for f in section.get("fields", [])):
            return section["key"]
    return None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def queries_queryset(user, params):
    """accreditation.qa_portal_views.queries' query and filters."""
    from accreditation.models import Query

    qs = Query.objects.for_user(user).select_related("qa_request").order_by("-created_at")
    status = params.get("status", "active")
    if status == "active":
        qs = qs.filter(status__in=("open", "answered"))
    elif status in ("open", "answered", "closed"):
        qs = qs.filter(status=status)
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(title__icontains=q) | Q(subject_label__icontains=q)
                       | Q(qa_request__reference__icontains=q))
    about = params.get("about", "")
    if about == "request":
        qs = qs.filter(qa_request__isnull=False)
    elif about == "other":
        qs = qs.filter(qa_request__isnull=True)
    due = params.get("due", "")
    now = timezone.now()
    if due == "overdue":
        qs = qs.filter(status="open", due_at__lt=now)
    elif due == "week":
        qs = qs.filter(status="open", due_at__gte=now, due_at__lt=now + datetime.timedelta(days=7))
    return qs


def query_row(q):
    return {"id": q.pk, "reference": q.reference, "title": q.title, "subject_label": q.subject_label or "",
            "status": q.status, "status_label": q.get_status_display(), "due_at": q.due_at,
            "is_overdue": bool(q.is_overdue), "created_at": q.created_at,
            "qa_request": ({"id": q.qa_request_id, "reference": q.qa_request.reference}
                           if q.qa_request_id else None)}


def query_detail_payload(user, query):
    from accreditation.models import Response

    messages = [{"kind": "query", "id": query.pk, "author": "NBTE", "from_nbte": True, "body": query.body,
                 "at": query.created_at, "attachment": None}]
    for r in Response.objects.for_user(user).filter(query=query).select_related("responded_by").order_by("at", "pk"):
        messages.append({"kind": "response", "id": r.pk, "author": user_name(r.responded_by), "from_nbte": False,
                         "body": r.body, "at": r.at or r.created_at, "attachment": file_dict(r)})
    out = query_row(query)
    out.update({"body": query.body, "close_remark": query.close_remark or "", "closed_at": query.closed_at,
                "messages": messages, "can_respond": query.status != query.CLOSED, "max_mb": _max_mb()})
    return out


# ---------------------------------------------------------------------------
# Programmes
# ---------------------------------------------------------------------------
def letter_dict(letter):
    copies = []
    for c in letter.copies.all():
        copies.append({"id": c.pk, "kind": c.kind, "kind_label": c.get_kind_display(), "name": c.display_name,
                       "download_url": reverse("api:portal_letter_download", args=[c.pk])})
    decision = letter.decision
    return {"id": letter.pk, "reference": letter.reference, "programme": str(letter.programme),
            "programme_id": letter.programme_id, "is_current": bool(letter.is_current),
            "generated_at": letter.generated_at,
            "outcome": getattr(decision, "outcome", "") if decision is not None else "",
            "approval_type": (decision.approval_type.name if decision is not None and
                              getattr(decision, "approval_type_id", None) else ""),
            "expiry_date": getattr(decision, "expiry_date", None) if decision is not None else None,
            "copies": copies}


def board_payload(user, inst):
    """accreditation.programme_portal_views.programmes (status board)."""
    from accreditation import programme_portal_views as ppv
    from accreditation import qa_services as svc
    from accreditation.models import QAType

    cards = []
    for c in ppv.programme_cards(user, inst):
        p = c["programme"]
        cards.append({
            "id": p.pk, "name": p.name, "level": ref(p.level) if p.level_id else None, "status": p.status,
            "status_label": p.get_status_display(),
            "approval_type": p.approval_type.name if p.approval_type_id else "",
            "approved_streams": p.approved_streams, "commencement_date": p.commencement_date,
            "expiry_date": p.expiry_date, "days_left": c["days_left"], "urgency": c["urgency"],
            "window_opens": c["opens"], "window_open": c["window_open"], "can_renew": c["can_renew"],
            "letter": letter_dict(c["letter"]) if c["letter"] is not None else None})
    renewal = QAType.objects.filter(code=ppv.RENEWAL_TYPE).first()
    return {"window_months": svc.window_months(),
            "renewal_type": type_dict(renewal) if renewal is not None else None, "cards": cards}


def programmes_queryset(user, inst, params):
    """institutions.programme_views.portal_programmes' query and filters."""
    from institutions.models import Programme

    qs = (Programme.objects.for_user(user).filter(institution=inst).exclude(status="dropped")
          .select_related("level", "category"))
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(name__icontains=q)
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("level"):
        qs = qs.filter(level__code=params["level"])
    return qs.order_by("name", "level__sort_order")


def programme_rows(programmes):
    from institutions import programme_info

    bodies = programme_info.bodies_map(programmes)
    out = []
    for p in programmes:
        info = programme_info.expiry(p)
        out.append({"id": p.pk, "name": p.name, "level": ref(p.level) if p.level_id else None,
                    "category": p.category.name if p.category_id else "", "status": p.status,
                    "status_label": p.get_status_display(), "approved_streams": p.approved_streams,
                    "commencement_date": p.commencement_date, "expiry_date": p.expiry_date,
                    "days_left": info["days"], "expiry_label": info["label"],
                    "professional_bodies": [b.name for b in bodies.get(p.pk, [])]})
    return out


def programme_detail_payload(user, inst, pk):
    """institutions.programme_views.portal_programme."""
    from django.contrib.contenttypes.models import ContentType

    from accreditation import qa_services
    from accreditation.models import OutcomeLetter, OutcomeLetterCopy, QARequestProgramme
    from institutions import programme_info
    from institutions.models import Programme
    from qa_core.models import TimelineEvent

    programme = (Programme.objects.for_user(user).filter(pk=pk).exclude(status="dropped")
                 .select_related("level", "category", "institution").first())
    if programme is None or programme.institution_id != inst.pk:
        raise Http404
    row = programme_rows([programme])[0]
    rows = (QARequestProgramme.objects.for_user(user).filter(programme=programme)
            .select_related("qa_request", "qa_request__qa_type").order_by("-qa_request__created_at"))
    ct = ContentType.objects.get_for_model(Programme)
    events = (TimelineEvent.objects.for_user(user).filter(visibility="public").filter(
        Q(content_type=ct, object_id=str(programme.pk)) | Q(public_text__icontains=programme.name))
        .order_by("-at", "-id")[:50])
    from django.db.models import Prefetch

    letters = (OutcomeLetter.objects.for_user(user).filter(programme=programme)
               .select_related("decision__approval_type")
               .prefetch_related(Prefetch("copies", queryset=OutcomeLetterCopy.objects.for_user(user)
                                          .order_by("kind"))).order_by("-generated_at"))
    row.update({
        "approval_type": programme_info.approval_type_name(programme),
        "requests": [{"id": r.qa_request_id, "reference": r.qa_request.reference,
                      "qa_type": type_dict(r.qa_request.qa_type), "created_at": r.qa_request.created_at,
                      "public_status": humanize(qa_services.public_status(r.qa_request))} for r in rows],
        "history": [{"at": e.at, "text": e.public_text} for e in events],
        "letters": [letter_dict(letter) for letter in letters],
    })
    return row


def letters_queryset(user):
    """accreditation.programme_portal_views.letters."""
    from accreditation import programme_portal_views as ppv
    from accreditation.models import OutcomeLetter

    return (OutcomeLetter.objects.for_user(user)
            .select_related("programme__level", "decision__approval_type", "case")
            .prefetch_related(ppv._copies(user)).order_by("-generated_at"))


def _is_ce(user):
    """X12 (Phase 0): only the Chief Executive submits to NBTE."""
    from institutions.services import is_chief_executive

    return is_chief_executive(user)
