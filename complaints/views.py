"""Complaints — internal screens (Master Plan §11, §13; PHASES.md Phase 7).

    Queue (Assigned to me · My department · Escalated to me · All I can see)
    Complaint (conversation, stage actions, SLA clock, escalation, holds)
    SLA dashboard (+ CSV report)

Complaints are read only through ``services.visible_to`` (the complaint
visibility rules); object routes answer 404 for anything else (§4 layer 3).
Every page is a full page and an HTMX partial (§20).
"""
from __future__ import annotations

import csv
import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from qa_core.permissions import require, require_or_404
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import services, sla
from .models import ComplaintLayer, ComplaintStage, Escalation

TABS = [("mine", "Assigned to me"), ("department", "My department"), ("escalated", "Escalated to me"),
        ("all", "All I can see")]
SLA_FILTERS = [("", "Any deadline"), ("breached", "Overdue"), ("near", "Past half-way"), ("on_track", "On track"),
               ("paused", "Waiting for the raiser")]
STATUS_FILTERS = [("open", "Open"), ("resolved", "Resolved, awaiting confirmation"), ("closed", "Closed"),
                  ("all", "Every status")]


def can_open(user):
    return services.is_handler(user) or (user.is_authenticated and (
        user.has_perm("complaints.escalate_complaint") or user.has_perm("complaints.view_internal_messages")))


def can_report(user):
    return user.is_authenticated and getattr(user, "user_type", "staff") == "staff" and (
        user.is_superuser or user.has_perm("complaints.view_complaint_reports"))


def _msg(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            return " ".join(m for msgs in exc.message_dict.values() for m in msgs)
        return " ".join(exc.messages)
    return str(exc) or "Not allowed."


# ===========================================================================
# Queue
# ===========================================================================
# Phase 9: the queue logic lives in services (shared with the mobile API).
def _base(request, tab):
    return services.queue_base(request.user, tab)


_status_filter = services.status_filter
decorate = services.decorate


def _queue_rows(request, tab):
    f = request.GET
    return services.queue_rows(request.user, tab, status=f.get("status") or "open", layer=f.get("layer", ""),
                               priority=f.get("priority", ""), q=f.get("q", ""), sla_filter=f.get("sla", ""))


def _counts(request):
    return services.queue_counts(request.user)


def _default_tab(request, counts):
    return services.default_tab(request.user, counts)


@require(can_open)
@require_GET
def queue(request):
    counts = _counts(request)
    tab = request.GET.get("tab") or _default_tab(request, counts)
    if tab not in dict(TABS):
        tab = "all"
    page = paginate(request, _queue_rows(request, tab))
    ctx = {"page": page, "tab": tab, "tabs": TABS, "counts": counts, "filters": request.GET,
           "layers": ComplaintLayer.objects.filter(is_active=True, parent__isnull=True),
           "priorities": ComplaintLayer._meta.get_field("default_priority").choices,
           "sla_filters": SLA_FILTERS, "status_filters": STATUS_FILTERS,
           "can_report": can_report(request.user)}
    return render_qa(request, "complaints/queue.html", ctx, {"qa-panel": "complaints/_queue_panel.html",
                                                             "qa-rows": "complaints/_queue_rows.html"})


# ===========================================================================
# One complaint
# ===========================================================================
def _complaint_or_404(request, pk):
    complaint = services.get_visible(request.user, pk)
    if complaint is None:
        raise Http404("No such complaint.")
    return complaint


def _detail_ctx(request, complaint, **extra):
    from accounts.models import User
    from staff.models import Department

    user = request.user
    rights = services.detail_rights(user, complaint)  # Phase 9: shared with the API
    internal = rights["internal"]
    messages = services.messages_for(user, complaint, internal=internal)
    stages = list(complaint.stages.select_related("assigned_department", "assigned_to", "routed_by")
                  .prefetch_related("escalations__escalated_to").order_by("entered_at", "id"))
    current = next((s for s in reversed(stages) if s.left_at is None), None)
    can_act, why = rights["can_act"], rights["act_why"]
    actions = rights["actions"]
    refer_targets = []
    if any(t.action_code == "refer" for t in actions):
        refer_targets = list(Department.objects.order_by("name"))
    ctx = {
        "c": complaint, "messages_list": list(messages), "internal": internal, "stages": stages, "current": current,
        "sla": services.sla_state(current), "actions": actions, "can_act": can_act, "act_why": why,
        "refer_departments": refer_targets,
        "refer_people": list(User.objects.filter(is_active=True, user_type="staff",
                                                 department_id=user.department_id).order_by("first_name")[:200])
        if refer_targets and user.department_id else [],
        "can_escalate": rights["can_escalate"], "can_hold": rights["can_hold"],
        # v2.1 X9–X11: assign (director), read-only watchers
        "officers": rights["officers"], "read_only": rights["read_only"], "watch_reason": rights["watch_reason"],
        "is_director": rights["is_director"], "is_officer": rights["is_officer"],
        "watchers": list(complaint.watchers.all()),
        "escalations": list(Escalation.objects.filter(complaint_stage__complaint=complaint)
                            .select_related("escalated_to", "by", "complaint_stage").order_by("at")),
        "can_write": internal,
        "related_url": _related_url(complaint),
        "answers": [(f.get("label") or f.get("key"), complaint.answers.get(f.get("key")))
                    for f in (complaint.layer.custom_fields or []) if complaint.answers.get(f.get("key"))],
    }
    ctx.update(extra)
    return ctx


def _related_url(complaint):
    model = complaint.related_type.model if complaint.related_type_id else ""
    pk = complaint.related_id
    return {"paymentobligation": f"/qa/finance/obligations/{pk}/", "qacase": f"/qa/cases/{pk}/",
            "qarequest": f"/qa/requests/{pk}/", "inspection": f"/qa/inspection/inspections/{pk}/"}.get(model, "")


def _render_detail(request, complaint, status=200, **extra):
    return render_qa(request, "complaints/detail.html", _detail_ctx(request, complaint, **extra),
                     {"qa-panel": "complaints/_detail_panel.html"}, status=status)


@require_or_404(can_open)
@require_GET
def detail(request, pk):
    return _render_detail(request, _complaint_or_404(request, pk))


def _after(request, complaint, title, body=""):
    complaint.refresh_from_db()
    response = render_qa(request, "complaints/detail.html", _detail_ctx(request, complaint),
                         {"qa-panel": "complaints/_detail_panel.html"})
    if not is_htmx(request):
        from django.shortcuts import redirect

        return redirect("qa_complaints:detail", complaint.pk)
    return toast(response, "success", title, body)


def _failed(request, complaint, exc, title="Not done"):
    response = render_qa(request, "complaints/detail.html", _detail_ctx(request, complaint, error=_msg(exc)),
                         {"qa-panel": "complaints/_detail_panel.html"}, status=422)
    return toast(response, "error", title, _msg(exc))


@require_POST
@require_or_404(can_open)
def act(request, pk, action):
    from accounts.models import User
    from staff.models import Department

    complaint = _complaint_or_404(request, pk)
    p = request.POST
    try:
        dept = Department.objects.filter(pk=p.get("refer_department") or 0).first()
        person = User.objects.filter(pk=p.get("refer_user") or 0, is_active=True, user_type="staff").first()
        # X10: the officer is re-validated by the service (own department only).
        officer = User.objects.filter(pk=p.get("officer") or 0).first() if action == "assign" else None
        services.act(complaint, action, request.user, remark=p.get("remark", ""), refer_department=dept,
                     refer_user=person, officer=officer)
    except (ValidationError, PermissionDenied) as exc:
        return _failed(request, complaint, exc)
    complaint.refresh_from_db()
    return _after(request, complaint, "Done", f"{complaint.reference} is now: {complaint.status.replace('_', ' ')}")


@require_POST
@require_or_404(can_open)
def message(request, pk):
    complaint = _complaint_or_404(request, pk)
    try:
        visibility = "public" if request.POST.get("visibility") == "public" else "internal"
        services.add_message(complaint, request.user, request.POST.get("body", ""), visibility=visibility,
                             files=request.FILES.getlist("files"))
    except (ValidationError, PermissionDenied) as exc:
        return _failed(request, complaint, exc, "Message not sent")
    label = "Reply sent to the raiser" if request.POST.get("visibility") == "public" else "Internal note added"
    return _after(request, complaint, label)


@require_POST
@require_or_404(can_open)
def escalate(request, pk):
    from accounts.models import User

    complaint = _complaint_or_404(request, pk)
    try:
        to_user = User.objects.filter(pk=request.POST.get("to_user") or 0, is_active=True, user_type="staff").first()
        rows = services.escalate_manually(complaint, request.user, reason=request.POST.get("reason", ""),
                                          to_user=to_user)
    except (ValidationError, PermissionDenied) as exc:
        return _failed(request, complaint, exc, "Not escalated")
    names = ", ".join(r.escalated_to.get_full_name() or r.escalated_to.email for r in rows if r.escalated_to)
    return _after(request, complaint, "Escalated", f"{names} is now watching {complaint.reference}.")


@require_POST
@require_or_404(can_open)
def hold(request, pk):
    complaint = _complaint_or_404(request, pk)
    try:
        services.place_hold(complaint, request.user, request.POST.get("reason", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _failed(request, complaint, exc, "No hold placed")
    return _after(request, complaint, "Hold placed", complaint.related_label)


# ===========================================================================
# SLA dashboard and report
# ===========================================================================
def dashboard_data(user, days=90, now=None):
    now = now or timezone.now()
    since = now - datetime.timedelta(days=days)
    seen = services.visible_to(user)
    open_qs = seen.filter(closed_at__isnull=True)
    rows = decorate(list(open_qs.select_related("layer", "current_department")))
    states = {k: 0 for k in ("on_track", "near", "breached", "paused", "none")}
    for c in rows:
        states[c.sla.get("state", "none")] = states.get(c.sla.get("state", "none"), 0) + 1
    left = ComplaintStage.objects.filter(complaint__in=seen, left_at__gte=since, due_at__isnull=False)
    left_total = left.count()
    on_time = left.filter(breached_at__isnull=True).count()
    resolved = list(seen.filter(resolved_at__gte=since).values_list("created_at", "resolved_at"))
    avg_hours = (sum((r - c).total_seconds() for c, r in resolved) / len(resolved) / 3600) if resolved else None
    by_layer = list(seen.filter(created_at__gte=since).values("layer__name")
                    .annotate(total=Count("id"), open=Count("id", filter=Q(closed_at__isnull=True)))
                    .order_by("-total")[:12])
    breached_by_layer = dict(ComplaintStage.objects.filter(complaint__in=seen, entered_at__gte=since,
                                                           breached_at__isnull=False)
                             .values_list("complaint__layer__name").annotate(n=Count("complaint", distinct=True)))
    for row in by_layer:
        row["breached"] = breached_by_layer.get(row["layer__name"], 0)
        row["pct"] = round(100 * row["breached"] / row["total"]) if row["total"] else 0
    by_dept = list(open_qs.values("current_department__name").annotate(n=Count("id")).order_by("-n")[:10])
    top = max([d["n"] for d in by_dept] or [1])
    for d in by_dept:
        d["w"] = int(100 * d["n"] / top)
    levels = dict(Escalation.objects.filter(complaint_stage__complaint__in=seen, at__gte=since)
                  .values_list("level").annotate(n=Count("id")))
    weeks = []
    start = timezone.localdate(now) - datetime.timedelta(days=7 * 11 + timezone.localdate(now).weekday())
    raised = seen.filter(created_at__date__gte=start)
    for i in range(12):
        a = start + datetime.timedelta(days=7 * i)
        b = a + datetime.timedelta(days=7)
        weeks.append({"label": a.strftime("%d %b"),
                      "raised": raised.filter(created_at__date__gte=a, created_at__date__lt=b).count(),
                      "resolved": seen.filter(resolved_at__date__gte=a, resolved_at__date__lt=b).count()})
    return {
        "open": len(rows), "states": states, "on_time_pct": round(100 * on_time / left_total) if left_total else None,
        "stages_measured": left_total, "avg_hours": avg_hours, "resolved": len(resolved),
        "by_layer": by_layer, "by_dept": by_dept, "levels": [(lv, levels.get(lv, 0)) for lv in (1, 2, 3)],
        "weeks": weeks, "days": days,
        "overdue": [c for c in rows if c.sla.get("state") == "breached"][:8],
    }


@require(can_report)
@require_GET
def dashboard(request):
    try:
        days = max(7, min(365, int(request.GET.get("days") or 90)))
    except ValueError:
        days = 90
    data = dashboard_data(request.user, days)
    chart = {"labels": [w["label"] for w in data["weeks"]], "raised": [w["raised"] for w in data["weeks"]],
             "resolved": [w["resolved"] for w in data["weeks"]]}
    return render_qa(request, "complaints/dashboard.html", {"d": data, "chart": chart, "days": days},
                     {"qa-panel": "complaints/_dashboard_panel.html"})


@require(can_report)
@require_GET
def report_csv(request):
    rows = decorate(list(services.visible_to(request.user)
                         .select_related("layer", "institution", "current_department", "raised_by")
                         .order_by("-created_at")[:5000]))
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="complaints-report.csv"'
    out = csv.writer(response)
    out.writerow(["Reference", "Raised", "Layer", "Priority", "Institution", "Raised by", "Status", "Department",
                  "Deadline state", "Resolved", "Closed", "Reopened"])
    for c in rows:
        out.writerow([c.reference, timezone.localtime(c.created_at).strftime("%Y-%m-%d %H:%M"), c.layer.name,
                      c.get_priority_display(), getattr(c.institution, "name", ""), c.raised_by.email,
                      c.status, getattr(c.current_department, "name", ""), c.sla.get("state", ""),
                      timezone.localtime(c.resolved_at).strftime("%Y-%m-%d %H:%M") if c.resolved_at else "",
                      timezone.localtime(c.closed_at).strftime("%Y-%m-%d %H:%M") if c.closed_at else "",
                      c.reopened_count])
    return response
