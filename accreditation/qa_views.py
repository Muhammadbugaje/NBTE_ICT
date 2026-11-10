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
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core import flow
from qa_core.permissions import require, require_or_404
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import qa_services as svc
from . import ssq as engine
from .models import (BodyRequirementCheck, Evidence, QACase, QARequest, QARequestProgramme, QAType, Query, SSQ,
                     Submission)


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def can_view_qa_requests(user):
    return (user.is_authenticated and getattr(user, "user_type", "staff") == "staff"
            and (user.is_superuser or any(user.has_perm(p) for p in (
                "accreditation.view_qa_case", "accreditation.review_ssq", "accreditation.raise_query",
                "accreditation.act_on_qa_stage", "accreditation.clear_qa_programmes",
                "qa_core.view_all_institutions"))))


def can_view_ssq_queue(user):
    if can_view_qa_requests(user):
        return True
    from assignments.access import can_access_any_ssq

    if can_access_any_ssq(user):
        return True
    from . import ssq_routing  # the routing tracker (ES, Director of ICT, supervisors, department leads)

    return ssq_routing.can_track_any(user)


def _msg(exc):
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


def _request_or_404(request, pk):
    req = (QARequest.objects.for_user(request.user).select_related("institution__handling_department", "qa_type",
                                                                    "cycle").filter(pk=pk).first())
    if req is None:
        case = QACase.unscoped.filter(qa_request_id=pk).select_related(
            "institution", "owning_department",
        ).first()
        if case is not None:
            from inspection.services import can_review_cqac_evidence

            if can_review_cqac_evidence(case, request.user):
                req = (QARequest.unscoped.select_related("institution__handling_department", "qa_type", "cycle")
                       .filter(pk=pk).first())
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
            from . import ssq_routing  # NBTE side: the internal stage and holder

            current.who = ssq_routing.holder(current, with_supervisors=False)
            current.public = current.who["label"]
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


def _now_page(request, req):
    """Passed a stage on this request: reload straight onto the request as it
    is now, instead of leaving the reader on the page they just acted on."""
    url = reverse("qa_req:detail", args=[req.pk])
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


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
        return _now_page(request, req)  # returned: land on the request as it is now (back in draft)
    return _now_page(request, req)  # cleared: land on the request as it is now (the QA fee is up next)


@require_or_404(can_view_qa_requests)
@require_POST
def request_retry_fee(request, pk):
    req = _request_or_404(request, pk)
    try:
        svc.retry_fee(req, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _detail_response(request, req, error=_msg(exc))
    return _now_page(request, req)


# ---------------------------------------------------------------------------
# SSQ review
# ---------------------------------------------------------------------------
SSQ_FILTERS = [("submitted", "At NBTE"), ("unassigned", "Needs an officer"), ("assessing", "Under assessment"),
               ("approval", "Awaiting approval"), ("overdue", "Overdue"), ("accepted", "Approved"),
               ("returned", "Returned"), ("draft", "With the institution"), ("all", "All")]
STAGE_FILTERS = {"unassigned": ("awaiting_assignment",), "assessing": ("offered", "under_assessment"),
                 "approval": ("awaiting_approval",)}


def _levels():
    from institutions.models import ProgrammeLevel

    return ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin")


def _visible_ssqs(user):
    """Every SSQ the user may track (routing metadata), each marked ``can_open``
    when the strict X1 list lets the user open its answers."""
    from . import ssq_routing

    return ssq_routing.tracked_queryset(user).select_related(
        "qa_request_programme__qa_request__institution__handling_department",
        "qa_request_programme__qa_request__qa_type", "qa_request_programme__programme__level", "reviewed_by")


def _queue_context(request, params=None):
    from . import ssq_routing

    user = request.user
    f = params if params is not None else request.GET
    status = f.get("status", "submitted") or "submitted"
    base = _visible_ssqs(user)
    qs = base.order_by("submitted_at", "pk")
    if status == "draft":
        qs = qs.filter(is_current=True, status__in=SSQ.EDITABLE_STATUSES)
    elif status in ("submitted", "unassigned", "assessing", "approval", "overdue"):
        qs = qs.filter(status="submitted", is_current=True)
    elif status != "all":
        qs = qs.filter(status=status)
    if f.get("type"):
        qs = qs.filter(qa_request_programme__qa_request__qa_type__code=f["type"])
    if f.get("level"):
        qs = qs.filter(qa_request_programme__programme__level__code=f["level"])
    if f.get("dept"):
        qs = qs.filter(qa_request_programme__qa_request__institution__handling_department__code=f["dept"])
    q = (f.get("q") or "").strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(qa_request_programme__qa_request__reference__icontains=q)
                       | Q(qa_request_programme__qa_request__institution__name__icontains=q)
                       | Q(qa_request_programme__programme__name__icontains=q))
    if user.is_superuser or ssq_routing.tracks_everything(user):
        openable = None
    else:
        openable = set(SSQ.objects.for_user(user).values_list("pk", flat=True))
    from django.utils import timezone

    now = timezone.now()
    rows = list(qs[:500])
    for s in rows:
        s.who = ssq_routing.holder(s, now=now, with_supervisors=False)
        s.public = s.who["label"]
        s.can_open = openable is None or s.pk in openable
        s.can_nudge = ssq_routing.can_nudge(user, s)
        s.waiting_days = (now - s.submitted_at).days if s.submitted_at else None
    if status in STAGE_FILTERS:
        rows = [s for s in rows if s.who["stage"] in STAGE_FILTERS[status]]
    elif status == "overdue":
        rows = [s for s in rows if s.who["overdue"]]
    # Waiting longest first, overdue on top.
    if status not in ("accepted", "returned", "all", "draft"):
        rows.sort(key=lambda s: (not s.who["overdue"], -(s.who["days"] or 0)))
    page = paginate(request, rows)
    # Stats over everything at NBTE in scope (independent of the filters).
    stats = {"waiting": 0, "unassigned": 0, "assessing": 0, "approval": 0, "overdue": 0}
    for s in base.filter(status="submitted", is_current=True).select_related(
            "qa_request_programme__qa_request__institution__handling_department")[:1000]:
        info = ssq_routing.holder(s, now=now, with_supervisors=False)
        stats["waiting"] += 1
        for key, stages in STAGE_FILTERS.items():
            if info["stage"] in stages:
                stats[key] += 1
        stats["overdue"] += int(info["overdue"])
    from staff.models import Department

    dept_ids = set(base.values_list("qa_request_programme__qa_request__institution__handling_department_id",
                                    flat=True))
    return {"page": page, "filters": f, "status": status, "statuses": SSQ_FILTERS,
            "types": QAType.objects.order_by("sort_order"), "levels": _levels(),
            "departments": Department.objects.filter(pk__in=[d for d in dept_ids if d]).order_by("name"),
            "counts": stats, "sla_days": ssq_routing._int("qa.ssq.stage_sla_days", 5),
            "tracks_all": ssq_routing.tracks_everything(user)}


@require(can_view_ssq_queue)
@require_GET
def ssq_queue(request):
    return render_qa(request, "accreditation/qa/ssq_queue.html", _queue_context(request),
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
    from . import shortfall  # v2.1 R.2 (X36)

    ctx["shortfall"] = getattr(submission, "shortfall", None) if submission is not None else None
    if ctx["shortfall"] is None and submission is not None:
        ctx["shortfall"] = shortfall.record(submission)  # submissions made before Phase R
    if ctx["shortfall"] is not None:
        ctx["shortfall_suggested"] = shortfall.SUGGESTED_LABELS.get(ctx["shortfall"].suggested, "")
    from . import ssq_assign  # v2.1 Part 2.2: strict assignment

    # v2.1 R.6 (X37b): professional-body requirements and the reviewer's results.
    body_requirements = svc.requirements_for(ssq)
    requirement_checks = svc.requirement_checks(ssq)
    ctx["body_requirements"] = body_requirements
    ctx["requirement_checks"] = requirement_checks
    ctx["requirement_rows"] = [(r, requirement_checks.get(r.pk)) for r in body_requirements]
    ctx["requirement_result_choices"] = BodyRequirementCheck.RESULT_CHOICES
    ctx["can_record_requirements"] = bool(ok)
    ctx["assignment"] = ssq_assign.current_assignment(ssq)
    ctx["can_assign"] = ssq_assign.can_assign(user, ssq) and ctx["assignment"] is None and \
        ssq.status == "submitted" and ssq.is_current
    if ctx["can_assign"]:
        s = ssq_assign.suggestion(ssq)
        ctx["assign_suggested"] = s.suggested.user if s is not None and s.suggested is not None else None
        ctx["assign_candidates"] = ssq_assign.candidates(ssq)
        ctx["assign_cap"] = ssq_assign.cap()
    # Who is handling this SSQ (routing, holder, approver, time at stage) and
    # what this user may do about it.
    from . import ssq_routing

    ctx["who"] = ssq_routing.holder(ssq)
    ctx["can_nudge"] = ssq_routing.can_nudge(user, ssq)
    ctx["can_submit_assessment"] = ssq_routing.can_submit_assessment(user, ssq)
    ctx["can_send_back"] = ssq_routing.can_send_back(user, ssq)
    ctx["officer_final_decision"] = ssq_routing.officer_final_decision()
    ctx["recommendations"] = list(ssq_routing.RECOMMENDATIONS.items())
    ctx.update(extra)
    return ctx


def _panel_response(request, ssq, *, error="", ok=("", ""), **extra):
    """Re-render the SSQ panel after an action, with an inline message and a toast."""
    ssq.refresh_from_db()
    ctx = _ssq_context(request, ssq, action_error=error, action_ok=ok[0] if not error else "",
                       action_ok_body=ok[1] if not error else "", **extra)
    response = render(request, "accreditation/qa/_ssq_review_panel.html", ctx, status=422 if error else 200)
    if error:
        return toast(response, "error", "Not done", error)
    return toast(response, "success", *ok)


@require_or_404(can_view_ssq_queue)
@require_GET
def ssq_review(request, pk):
    ssq = _ssq_or_404(request, pk)
    return render_qa(request, "accreditation/qa/ssq_review.html", _ssq_context(request, ssq),
                     {"qa-panel": "accreditation/qa/_ssq_review_panel.html"})


@require_or_404(can_view_ssq_queue)
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
    if decision == "accept":
        ok = ("SSQ approved", "The institution sees it as approved; the review work is closed.")
    else:
        ok = ("SSQ returned", "The institution revises it as a new version.")
    return _panel_response(request, ssq, error=error, ok=ok, remark=remark if error else "")


@require_or_404(can_view_ssq_queue)
@require_POST
def ssq_requirements(request, pk):
    """v2.1 R.6 (X37b): save the reviewer's result for each professional-body requirement."""
    ssq = _ssq_or_404(request, pk)
    results = {}
    for key, value in request.POST.items():
        if not key.startswith("req_"):
            continue
        try:
            requirement_pk = int(key[4:])
        except (TypeError, ValueError):
            continue
        results[requirement_pk] = {"result": value,
                                   "remark": request.POST.get(f"req_remark_{requirement_pk}", "")}
    error = ""
    try:
        svc.save_requirement_checks(ssq, user=request.user, results=results)
    except (ValidationError, PermissionDenied) as exc:
        error = _msg(exc)
    return _panel_response(request, ssq, error=error, ok=("Requirement checks saved", ""))


@require_or_404(can_view_ssq_queue)
@require_POST
def ssq_assign_view(request, pk):
    """v2.1 Part 2.2: the director (or ES) assigns the SSQ to one officer of the department."""
    from accounts.models import User

    from . import ssq_assign

    ssq = _ssq_or_404(request, pk)
    officer = User.objects.filter(pk=request.POST.get("officer") or 0, is_active=True).first()
    error = ""
    try:
        ssq_assign.assign(ssq, by=request.user, officer=officer,
                          accept_suggestion=request.POST.get("accept_suggestion") == "1",
                          reason=request.POST.get("reason", ""))
    except (ValidationError, PermissionDenied) as exc:
        error = _msg(exc)
    if error:
        return _panel_response(request, ssq, error=error, assign_error=error)
    wa = ssq_assign.current_assignment(ssq)
    return _panel_response(request, ssq, ok=(
        f"Assigned to {wa.assignee.get_full_name() or wa.assignee.email}",
        "They were notified and must accept it before they can open it."))


def _tracked_or_404(request, pk):
    from . import ssq_routing

    ssq = ssq_routing.tracked_queryset(request.user).select_related(
        "qa_request_programme__qa_request__institution", "qa_request_programme__programme").filter(pk=pk).first()
    if ssq is None:
        raise Http404
    return ssq


@require_or_404(can_view_ssq_queue)
@require_POST
def ssq_nudge(request, pk):
    """"Send reminder": notify whoever holds the SSQ now (director, officer or approver)."""
    from . import ssq_routing

    ssq = _tracked_or_404(request, pk)
    error, ok = "", ("", "")
    try:
        people = ssq_routing.nudge(ssq, by=request.user, message=request.POST.get("message", ""))
        ok = ("Reminder sent", "Sent to " + ", ".join(u.get_full_name() or u.email for u in people) + ".")
    except (ValidationError, PermissionDenied) as exc:
        error = _msg(exc)
    if request.POST.get("from") == "detail" and ssq_routing.can_open(request.user, ssq):
        return _panel_response(request, _ssq_or_404(request, pk), error=error, ok=ok)
    params = request.POST.copy()
    response = render(request, "accreditation/qa/_ssq_queue_panel.html",
                      dict(_queue_context(request, params), action_error=error, action_ok=ok[0],
                           action_ok_body=ok[1]), status=422 if error else 200)
    if error:
        return toast(response, "error", "Reminder not sent", error)
    return toast(response, "success", *ok)


@require_or_404(can_view_ssq_queue)
@require_POST
def ssq_assessment(request, pk):
    """The assigned officer submits the assessment (recommendation) to the
    approver; the approver may send it back to the officer."""
    from . import ssq_routing

    ssq = _ssq_or_404(request, pk)
    action = request.POST.get("action", "submit")
    remark = request.POST.get("remark", "")
    error, ok = "", ("", "")
    try:
        if action == "send_back":
            ssq_routing.send_back_to_officer(ssq, by=request.user, remark=remark)
            ok = ("Sent back to the officer", "They were notified with your note.")
        else:
            ssq_routing.submit_assessment(ssq, officer=request.user,
                                          recommendation=request.POST.get("recommendation", ""), remark=remark)
            who = ssq_routing.approver(ssq)
            ok = ("Assessment submitted", f"{(who.get_full_name() or who.email) if who else 'The approver'} "
                                          "was notified to approve it.")
    except (ValidationError, PermissionDenied) as exc:
        error = _msg(exc)
    return _panel_response(request, ssq, error=error, ok=ok, assessment_remark=remark if error else "")


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
