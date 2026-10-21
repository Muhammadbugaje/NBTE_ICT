"""Internal SOP-02 screens (Phase 3), namespace "qa_req", mounted at /qa/requests/.

    QA requests: list (filters) · detail (programmes, clearance, fee, SSQs, queries, history)
    SSQ review queue · SSQ review (the immutable submission) · accept / return
    Raise query (on a request, an SSQ or a piece of evidence) · queries list · close

Everything is read through ``Model.objects.for_user(user)`` (Master Plan §4);
object routes answer 404 out of scope (``require_or_404``). Every screen
works as a full page and as an HTMX partial for #qa-panel (§20).
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core import flow
from qa_core.permissions import require, require_or_404
from qa_core.ui import paginate, render_qa, toast

from . import qa_services as svc
from . import ssq as engine
from .models import QACase, QARequest, QARequestProgramme, QAType, Query, SSQ, Evidence, Submission


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def can_view_qa_requests(user):
    return (user.is_authenticated and getattr(user, "user_type", "staff") == "staff"
            and (user.is_superuser or any(user.has_perm(p) for p in (
                "accreditation.view_qa_case", "accreditation.review_ssq", "accreditation.raise_query",
                "accreditation.clear_qa_programmes", "qa_core.view_all_institutions"))))


def _msg(exc):
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


def _request_or_404(request, pk):
    req = (QARequest.objects.for_user(request.user).select_related("institution__handling_department", "qa_type",
                                                                    "cycle").filter(pk=pk).first())
    if req is None:
        raise Http404
    return req


def _ssq_or_404(request, pk):
    ssq = (SSQ.objects.for_user(request.user)
           .select_related("qa_request_programme__qa_request__institution", "qa_request_programme__programme__level",
                           "template", "qa_request_programme__qa_request__qa_type").filter(pk=pk).first())
    if ssq is None:
        raise Http404
    return ssq


def _query_or_404(request, pk):
    query = Query.objects.for_user(request.user).select_related("institution", "raised_by").filter(pk=pk).first()
    if query is None:
        raise Http404
    return query


# ---------------------------------------------------------------------------
# QA requests
# ---------------------------------------------------------------------------
STATUS_GROUPS = [
    ("", "All stages"), ("draft", "Draft (institution)"), ("clearance", "Programme clearance"),
    ("fee", "Fee stage"), ("ssq", "SSQ in progress"), ("case", "SSQ submitted / in workflow"),
    ("closed", "Decided or cancelled"),
]
GROUPS = {"draft": ("draft",), "clearance": ("submitted", "programme_clearance"),
          "fee": ("fee_due", "payment_pending_confirmation", "fee_confirmed"), "ssq": ("ssq_in_progress",),
          "case": ("ssq_submitted", "in_workflow"), "closed": ("decided", "cancelled")}


@require(can_view_qa_requests)
@require_GET
def request_list(request):
    user = request.user
    qs = QARequest.objects.for_user(user).select_related("institution", "qa_type").order_by("-created_at")
    f = request.GET
    group = f.get("stage", "")
    if group in GROUPS:
        qs = qs.filter(status__in=GROUPS[group])
    if f.get("type"):
        qs = qs.filter(qa_type__code=f["type"])
    q = (f.get("q") or "").strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(reference__icontains=q) | Q(institution__name__icontains=q) | Q(institution__code__iexact=q))
    if f.get("fee_note") == "yes":
        qs = qs.exclude(fee_note="")
    page = paginate(request, qs)
    for r in page["rows"]:
        r.public = svc.public_status(r)
        r.count = QARequestProgramme.objects.for_user(user).filter(qa_request=r, status="active").count()
    base = QARequest.objects.for_user(user)
    ctx = {"page": page, "filters": f, "stages": STATUS_GROUPS, "types": QAType.objects.order_by("sort_order"),
           "counts": {"total": base.count(), "clearance": base.filter(status="programme_clearance").count(),
                      "ssq_waiting": SSQ.objects.for_user(user).filter(status="submitted", is_current=True).count(),
                      "open_queries": Query.objects.for_user(user).filter(status__in=("open", "answered")).count()}}
    return render_qa(request, "accreditation/qa/list.html", ctx,
                     {"qa-panel": "accreditation/qa/_list_panel.html", "qa-rows": "accreditation/qa/_rows.html"})


def _detail_context(request, req, **extra):
    user = request.user
    rows = list(QARequestProgramme.objects.for_user(user).filter(qa_request=req)
                .select_related("programme__level", "programme__category", "replaced_by__programme")
                .order_by("status", "programme__name"))
    ssqs = []
    for row in rows:
        if row.status != "active":
            continue
        current = SSQ.objects.for_user(user).filter(qa_request_programme=row, is_current=True).select_related(
            "template").first()
        if current is not None:
            counts = svc.evidence_counts(current)
            progress = engine.section_progress(current.template, current.data or {}, counts)
            current.done = sum(p["filled"] for p in progress)
            current.total = sum(p["total"] for p in progress)
            current.public = svc.ssq_public_status(current)
            ssqs.append(current)
    from django.contrib.contenttypes.models import ContentType

    from payments.models import PaymentObligation
    from payments.services import stage_label

    obligations = list(PaymentObligation.objects.for_user(user).filter(
        source_type=ContentType.objects.get_for_model(QARequest), source_id=str(req.pk)).order_by("-created_at"))
    for ob in obligations:
        ob.stage = stage_label(ob)
    instance = svc.request_instance(req)
    can_clear, why_not = svc.can_clear(req, user)
    ctx = {
        "req": req, "inst": req.institution, "rows": rows, "ssqs": ssqs, "obligations": obligations,
        "public_status": svc.public_status(req), "history": flow.history(instance),
        "queries": list(Query.objects.for_user(user).filter(qa_request=req).order_by("-created_at")),
        "case": QACase.objects.for_user(user).filter(qa_request=req).first(),
        "can_clear": can_clear, "clear_reason": why_not,
        "can_query": user.has_perm("accreditation.raise_query"),
        "r25": svc.cancellable_stage_labels(req), "is_cancellable": svc.is_cancellable(req),
        "can_retry_fee": req.status == "fee_due" and bool(req.fee_note) and not svc.open_obligations(req)
        and (user.is_superuser or user.has_perm("payments.view_obligation") or user.has_perm("accreditation.view_qa_case")),
    }
    ctx.update(extra)
    return ctx


@require_or_404(can_view_qa_requests)
@require_GET
def request_detail(request, pk):
    req = _request_or_404(request, pk)
    return render_qa(request, "accreditation/qa/detail.html", _detail_context(request, req),
                     {"qa-panel": "accreditation/qa/_detail_panel.html"})


def _detail_response(request, req, *, error="", ok=("", "")):
    req.refresh_from_db()
    response = render(request, "accreditation/qa/_detail_panel.html", _detail_context(request, req, error=error),
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not done", error)
    return toast(response, "success", *ok)


@require_or_404(can_view_qa_requests)
@require_POST
def request_clear(request, pk):
    req = _request_or_404(request, pk)
    decision = request.POST.get("decision", "clear")
    remark = request.POST.get("remark", "").strip()
    try:
        if decision == "return":
            svc.return_programmes(req, user=request.user, remark=remark)
        else:
            svc.clear_programmes(req, user=request.user, remark=remark)
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    if decision == "return":
        return _detail_response(request, req, ok=("Returned to the institution", "It is back in draft."))
    return _detail_response(request, req, ok=("Programmes cleared", "The QA fee was issued (or is waiting for "
                                                                    "its amount)."))


@require_or_404(can_view_qa_requests)
@require_POST
def request_retry_fee(request, pk):
    req = _request_or_404(request, pk)
    try:
        svc.retry_fee(req, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    return _detail_response(request, req, ok=("Fee issued", "The institution can now pay it."))


# ---------------------------------------------------------------------------
# SSQ review
# ---------------------------------------------------------------------------
SSQ_FILTERS = [("submitted", "Waiting for review"), ("accepted", "Accepted"), ("returned", "Returned"),
               ("draft", "Still with the institution"), ("all", "All")]


def _levels():
    from institutions.models import ProgrammeLevel

    return ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin")


@require(can_view_qa_requests)
@require_GET
def ssq_queue(request):
    user = request.user
    status = request.GET.get("status", "submitted")
    qs = (SSQ.objects.for_user(user).select_related("qa_request_programme__qa_request__institution",
                                                    "qa_request_programme__qa_request__qa_type",
                                                    "qa_request_programme__programme__level")
          .order_by("submitted_at", "pk"))
    if status == "draft":
        qs = qs.filter(is_current=True, status__in=SSQ.EDITABLE_STATUSES)
    elif status != "all":
        qs = qs.filter(status=status)
    if request.GET.get("type"):
        qs = qs.filter(qa_request_programme__qa_request__qa_type__code=request.GET["type"])
    if request.GET.get("level"):
        qs = qs.filter(qa_request_programme__programme__level__code=request.GET["level"])
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(qa_request_programme__qa_request__reference__icontains=q)
                       | Q(qa_request_programme__qa_request__institution__name__icontains=q)
                       | Q(qa_request_programme__programme__name__icontains=q))
    page = paginate(request, qs)
    from django.utils import timezone

    for s in page["rows"]:
        s.public = svc.ssq_public_status(s)
        s.waiting_days = (timezone.now() - s.submitted_at).days if s.submitted_at else None
    ctx = {"page": page, "filters": request.GET, "status": status, "statuses": SSQ_FILTERS,
           "types": QAType.objects.order_by("sort_order"),
           "levels": _levels(),
           "counts": {"waiting": SSQ.objects.for_user(user).filter(status="submitted", is_current=True).count()}}
    return render_qa(request, "accreditation/qa/ssq_queue.html", ctx,
                     {"qa-panel": "accreditation/qa/_ssq_queue_panel.html",
                      "qa-rows": "accreditation/qa/_ssq_rows.html"})


def _ssq_context(request, ssq, **extra):
    user = request.user
    submission = Submission.objects.for_user(user).filter(ssq=ssq).order_by("-version").first()
    if submission is not None:
        sections = submission.rendered_snapshot
    else:
        sections = engine.render_snapshot(ssq.template, ssq.data or {}, programme=ssq.qa_request_programme.programme)
    evidence = list(Evidence.objects.for_user(user).filter(ssq=ssq).exclude(status="removed")
                    .select_related("reused_from__kind"))
    by_field = {}
    for ev in evidence:
        by_field.setdefault(ev.field_key, []).append(ev)
    versions = list(SSQ.objects.for_user(user).filter(qa_request_programme=ssq.qa_request_programme)
                    .order_by("-version"))
    ok, why = svc.can_review(ssq, user)
    req = ssq.qa_request
    ctx = {"ssq": ssq, "req": req, "inst": req.institution, "programme": ssq.qa_request_programme.programme,
           "submission": submission, "sections": sections, "evidence_by_field": by_field, "versions": versions,
           "can_review": ok, "review_reason": why, "public": svc.ssq_public_status(ssq),
           "history": flow.history(svc.ssq_instance(ssq)), "can_query": user.has_perm("accreditation.raise_query"),
           "queries": list(Query.objects.for_user(user).filter(qa_request=req).order_by("-created_at")),
           "checksum_ok": submission.verify() if submission else None}
    ctx.update(extra)
    return ctx


@require_or_404(can_view_qa_requests)
@require_GET
def ssq_review(request, pk):
    ssq = _ssq_or_404(request, pk)
    return render_qa(request, "accreditation/qa/ssq_review.html", _ssq_context(request, ssq),
                     {"qa-panel": "accreditation/qa/_ssq_review_panel.html"})


@require_or_404(can_view_qa_requests)
@require_POST
def ssq_decide(request, pk):
    ssq = _ssq_or_404(request, pk)
    decision = request.POST.get("decision", "")
    remark = request.POST.get("remark", "").strip()
    error = ""
    try:
        if decision == "accept":
            svc.accept_ssq(ssq, user=request.user, remark=remark)
        elif decision == "return":
            svc.return_ssq(ssq, user=request.user, remark=remark)
        else:
            raise ValidationError("Choose accept or return.")
    except (ValidationError, PermissionDenied) as exc:
        error = _msg(exc)
    ssq.refresh_from_db()
    response = render(request, "accreditation/qa/_ssq_review_panel.html",
                      _ssq_context(request, ssq, error=error, remark=remark if error else ""),
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not done", error)
    if decision == "accept":
        return toast(response, "success", "SSQ accepted")
    return toast(response, "success", "SSQ returned", "The institution revises it as a new version.")


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def _query_subject(request, kind, pk):
    if kind == "request":
        return _request_or_404(request, pk)
    if kind == "ssq":
        return _ssq_or_404(request, pk)
    if kind == "evidence":
        ev = Evidence.objects.for_user(request.user).select_related("ssq").filter(pk=pk).first()
        if ev is None:
            raise Http404
        return ev
    raise Http404


@require_or_404(can_view_qa_requests)
@require_http_methods(["GET", "POST"])
def query_raise(request, kind, pk):
    """The Raise-query form (GET) and its submission (POST) for a request, an
    SSQ or a piece of evidence."""
    subject = _query_subject(request, kind, pk)
    import config

    ctx = {"subject": subject, "kind": kind, "subject_label": svc.query_subject_label(subject),
           "default_days": config.get("qa.query.default_due_days", 14), "values": request.POST}
    if request.method == "POST":
        try:
            query = svc.raise_query(subject, user=request.user, title=request.POST.get("title", ""),
                                    body=request.POST.get("body", ""), due_days=request.POST.get("due_days"))
        except (ValidationError, PermissionDenied) as exc:
            response = render(request, "accreditation/qa/_query_form.html", dict(ctx, error=_msg(exc)), status=422)
            return toast(response, "error", "Query not sent", _msg(exc))
        response = render(request, "accreditation/qa/_query_sent.html", dict(ctx, query=query))
        return toast(response, "success", f"Query {query.reference} sent",
                     "The institution was notified and can respond on the portal.")
    if not request.user.has_perm("accreditation.raise_query"):
        raise Http404
    return render(request, "accreditation/qa/_query_form.html", ctx)


@require(can_view_qa_requests)
@require_GET
def query_list(request):
    user = request.user
    status = request.GET.get("status", "active")
    qs = Query.objects.for_user(user).select_related("institution", "raised_by", "qa_request").order_by("-created_at")
    if status == "active":
        qs = qs.filter(status__in=("open", "answered"))
    elif status in ("open", "answered", "closed"):
        qs = qs.filter(status=status)
    if request.GET.get("mine") == "1":
        qs = qs.filter(raised_by=user)
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(reference__icontains=q) | Q(title__icontains=q) | Q(institution__name__icontains=q))
    page = paginate(request, qs)
    ctx = {"page": page, "filters": request.GET, "status": status,
           "counts": {"answered": Query.objects.for_user(user).filter(status="answered").count()}}
    return render_qa(request, "accreditation/qa/queries.html", ctx,
                     {"qa-panel": "accreditation/qa/_queries_panel.html",
                      "qa-rows": "accreditation/qa/_query_rows.html"})


def _query_context(request, query, **extra):
    from .models import Response

    ctx = {"query": query, "responses": list(Response.objects.for_user(request.user).filter(query=query)
                                             .select_related("responded_by")),
           "subject": svc.query_subject(query), "can_close": request.user.has_perm("accreditation.raise_query")}
    ctx.update(extra)
    return ctx


@require_or_404(can_view_qa_requests)
@require_GET
def query_detail(request, pk):
    query = _query_or_404(request, pk)
    return render_qa(request, "accreditation/qa/query_detail.html", _query_context(request, query),
                     {"qa-panel": "accreditation/qa/_query_detail_panel.html"})


@require_or_404(can_view_qa_requests)
@require_POST
def query_close(request, pk):
    query = _query_or_404(request, pk)
    error = ""
    try:
        svc.close_query(query, user=request.user, remark=request.POST.get("remark", ""))
    except (ValidationError, PermissionDenied) as exc:
        error = _msg(exc)
    query.refresh_from_db()
    response = render(request, "accreditation/qa/_query_detail_panel.html", _query_context(request, query, error=error),
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not closed", error)
    return toast(response, "success", f"{query.reference} closed")
