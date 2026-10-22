"""Internal SOP-03 screens (Phase 4), namespace "qa_case", mounted at /qa/cases/.

    Case Board (cases by stage group, filters) · My stage work (ES / Pay
    Officer / officer queues) · Case detail with tabs: overview (stage
    actions, desk officer and supervising staff), cost sheet editor,
    personnel, visit, fund release, timeline, tasks · task-link drift reports.

Cases are read through ``case_services.visible_cases(user)`` (institution
scope plus the cross-department SOP-03 roles); object routes answer 404 out
of scope (``require_or_404``). The ICT monitor sees everything and can write
nothing (R-13): every POST here refuses the monitor with 403 before it does
anything. Every screen works as a full page and as an HTMX partial for
#qa-panel (§20), with toasts for results.
"""
from __future__ import annotations

import datetime
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from qa_core.permissions import require, require_or_404
from qa_core.ui import paginate, render_qa, toast

from . import case_services as cs

TABS = [("overview", "Overview"), ("cost", "Cost sheet"), ("personnel", "Personnel"), ("visit", "Visit"),
        ("funds", "Fund release"), ("reports", "Reports"), ("capacity", "Capacity"), ("decisions", "Decisions"),
        ("letters", "Letters"), ("timeline", "Timeline"), ("tasks", "Tasks")]


def can_view_capacity(user):
    return bool(user.is_superuser or user.has_perm("accreditation.view_capacity_assessment"))


def can_run_capacity(user):
    return bool((user.is_superuser or user.has_perm("accreditation.run_capacity_assessment"))
                and not cs.is_monitor(user))


def _msg(exc):
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


def writer(view):
    """POST routes: the ICT monitor is read-only (R-13)."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if cs.is_monitor(request.user):
            raise PermissionDenied("The ICT monitor has read-only access to QA cases (R-13).")
        return view(request, *args, **kwargs)
    return wrapped


def _case_or_404(request, pk):
    case = (cs.visible_cases(request.user)
            .select_related("institution", "owning_department", "qa_request__qa_type", "desk_officer",
                            "supervising_audit_officer", "supervising_finance_officer")
            .filter(pk=pk).first())
    if case is None:
        raise Http404
    return case


def _date(raw):
    try:
        return datetime.date.fromisoformat((raw or "").strip())
    except ValueError:
        return None


def _user(pk):
    from accounts.models import User

    try:
        return User.objects.filter(pk=int(pk), is_active=True).select_related("role", "department").first()
    except (TypeError, ValueError):
        return None


# ===========================================================================
# Case Board
# ===========================================================================
@require(cs.can_view_cases)
@require_GET
def board(request):
    user = request.user
    f = request.GET
    qs = cs.filter_cases(cs.visible_cases(user), q=f.get("q"), department=f.get("department"),
                         flag=f.get("flag"))
    cases = cs.decorate_cases(list(qs[:600]))
    mine_only = f.get("mine") == "1"
    columns = []
    for key, label, codes in cs.STAGE_GROUPS:
        columns.append({"key": key, "label": label, "codes": codes, "cards": []})
    not_started = {"key": "new", "label": "Not started", "codes": ("",), "cards": []}
    for case in cases:
        if mine_only and user not in case.holders:
            continue
        for col in columns:
            if case.status in col["codes"]:
                col["cards"].append(case)
                break
        else:
            not_started["cards"].append(case)
    if not_started["cards"]:
        columns.insert(0, not_started)
    from staff.models import Department

    ctx = {"columns": columns, "filters": f, "total": sum(len(c["cards"]) for c in columns),
           "departments": Department.objects.filter(pk__in=cs.visible_cases(user).values("owning_department"))
           .order_by("name"),
           "monitor": cs.is_monitor(user), "queue_count": len(cs.my_stage_links(user)),
           "can_batch": can_batch(user) and not cs.is_monitor(user)}
    return render_qa(request, "accreditation/case/board.html", ctx,
                     {"qa-panel": "accreditation/case/_board_panel.html",
                      "case-board": "accreditation/case/_board_columns.html"})


# ===========================================================================
# My stage work (ES cost / visit queues, Pay Officer queue, any holder)
# ===========================================================================
QUEUE_KINDS = [("", "All my stages"), ("cost", "Cost & personnel (2–7)"), ("visit", "Visit (8–10)"),
               ("funds", "Fund release (11)")]


@require(cs.can_view_cases)
@require_GET
def queue(request):
    kind = request.GET.get("kind", "")
    links = cs.decorate_stage_links(cs.my_stage_links(request.user, kind=kind))
    page = paginate(request, links)
    ctx = {"page": page, "kind": kind, "kinds": QUEUE_KINDS, "role": getattr(request.user.role, "name", "")}
    return render_qa(request, "accreditation/case/queue.html", ctx,
                     {"qa-panel": "accreditation/case/_queue_panel.html"})


# ===========================================================================
# Case detail
# ===========================================================================
_stepper = cs.stepper  # Phase 9: moved to case_services (shared with the API)


def _detail_ctx(request, case, *, tab=None, focus="", **extra):
    user = request.user
    current = cs.stage_objects(case)
    focus = focus or request.GET.get("stage", "") or request.POST.get("stage", "")
    codes = [s.code for s in current]
    if focus not in codes:
        focus = codes[0] if codes else ""
    tab = tab or request.GET.get("tab") or cs.DATA_STAGES.get(focus, "overview")
    if tab not in dict(TABS):
        tab = "overview"
    actions = []
    for row in cs.stage_actions(case, user, current):
        link = row["link"]
        actions.append({**row, "tab": cs.DATA_STAGES.get(row["stage"].code, "overview"),
                        "pinned": list(link.task.comments.filter(is_pinned=True).select_related("author")
                                       .order_by("-pinned_at")[:3]) if link else [],
                        "focus": row["stage"].code == focus})
    bundle = cs.case_bundle(case)
    can = {a["stage"].code: a["can"] for a in actions}
    editor = cs.cost_editor(case, user)
    ctx = {
        "case": case, "inst": case.institution, "tab": tab, "tabs": TABS, "focus": focus, "current": current,
        "actions": actions, "stepper": _stepper(case, current), "monitor": cs.is_monitor(user),
        "started": cs.instance_of(case) is not None, "editor": editor,
        "rates": cs.rates_for(case), "rates_confirmed": cs._bool("qa.cost.rates_confirmed", False),
        "ict_required": cs.ict_required(case),
        "can_send_back": cs.can_send_back_to_audit(case) and not cs.is_monitor(user),
        "can_start": (not cs.is_monitor(user) and cs.instance_of(case) is None
                      and (user.is_superuser or user.has_perm("accreditation.act_on_qa_stage"))),
        "duties": _duties(), "team_lead_roles": cs.team_lead_roles(),
        "grade_min": cs._int("qa.staff.ssq_grade_min", 7), "grade_max": cs._int("qa.staff.ssq_grade_max", 12),
        **bundle,
    }
    if can.get("case_opened"):
        ctx["desk_choices"] = cs.eligible_officers(case, "desk")
    for kind in ("audit", "finance", "ict"):
        if can.get(f"assign_{kind}_officer"):
            ctx[f"{kind}_choices"] = cs.eligible_officers(case, kind)
    if can.get("add_personnel") or can.get("add_visit_details") or editor:
        ctx["staff_choices"] = cs.staff_choices(case)
    ctx["can_personnel"] = bool(can.get("add_personnel"))
    ctx["can_visit"] = bool(can.get("add_visit_details"))
    ctx["can_release"] = bool(can.get("request_fund_release")) and (
        user.is_superuser or user.has_perm("accreditation.release_funds"))
    if tab == "timeline":
        ctx["timeline"] = cs.case_timeline(case, user)
    if tab in ("tasks", "overview"):
        links = cs.case_links(case)
        from qa_core.task_bridge import may_reassign, qa_url

        for link in links:
            link.url = qa_url(link)
            link.can_reassign = (link.closed_at is None and not cs.is_monitor(user)
                                 and may_reassign(user, link.task)[0])
            link.stage_label = cs.stage_label(link.stage_code)
        ctx["links"] = links
        if any(link.can_reassign for link in links):
            ctx["reassign_choices"] = _reassign_choices()
    if tab == "visit" and bundle["visit"] is not None and bundle["visit"].planned_start:
        ctx["visit_days"] = (bundle["visit"].planned_end - bundle["visit"].planned_start).days + 1
    if tab in ("reports", "visit"):  # Phase 5: inspection + post-visit reports (stages 12–13)
        from inspection import services as insp
        from inspection.models import PostVisitReport

        ctx["inspection"] = insp.live_inspection(case)
        ctx["reports"] = insp.reports_of(case)
        for r in ctx["reports"]:
            r.layer = insp.LAYER_LABEL.get(insp.LAYER_FOR_STAGE.get(r.status, ""), "")
        ctx["can_recommend"] = bool(can.get("post_visit_recommendation"))
        ctx["recommendations"] = PostVisitReport.RECOMMENDATION_CHOICES
    ctx["min_visit_date"] = datetime.date.today().isoformat()
    if tab == "capacity":  # Phase 8: the capacity engine (Spec §7)
        ctx.update(_capacity_ctx(request, case))
    if tab == "decisions":  # Phase 8: stages 14–16
        ctx.update(_decisions_ctx(request, case, current))
    if tab == "letters":  # Phase 8: outcome letters (B24)
        from . import letters

        ctx["letters"] = letters.letters_of_case(case)
        ctx["can_regenerate"] = letters.can_regenerate(user) and not cs.is_monitor(user)
    ctx.update(extra)
    return ctx


# ===========================================================================
# Phase 8: capacity, decisions and letters tabs
# ===========================================================================
CAPACITY_FIELDS = {  # kind → [(input key, label, help)] besides the room table
    "classroom": [("approved_streams", "Streams already approved", "0 for a new programme."),
                  ("requested_streams", "Streams asked for", ""),
                  ("contact_hours_week", "Weekly contact hours of one stream", "Classroom hours a week."),
                  ("other_load_week", "Weekly hours other programmes use these rooms", "")],
    "laboratory": [("approved_streams", "Streams already approved", "0 for a new programme."),
                   ("requested_streams", "Streams asked for", ""),
                   ("contact_hours_week", "Weekly practical hours of one stream", ""),
                   ("other_load_week", "Weekly hours other programmes use these rooms", "")],
    "library": [("requested_streams", "Streams asked for", ""), ("students_per_stream", "Students per stream", ""),
                ("seats", "Library seats available to the programme", ""),
                ("titles", "Programme-specific titles", "")],
    "teaching_staff": [("requested_streams", "Streams asked for", ""),
                       ("students_per_stream", "Students per stream", ""),
                       ("full_time_lecturers", "Full-time lecturers", ""),
                       ("admin_post_lecturers", "Of whom hold an administrative post", ""),
                       ("contact_hours_week", "Weekly teaching hours of one stream", ""),
                       ("counted_staff", "Lecturers counted for the ratio", "HND: Lecturer I and above.")],
    "support_staff": [("requested_streams", "Streams asked for", ""),
                      ("students_per_stream", "Students per stream", ""),
                      ("support_staff", "Technologists and technicians", "")],
}
ROOM_KINDS = ("classroom", "laboratory")


def _capacity_ctx(request, case):
    from . import capacity
    from . import decisions as dec

    user = request.user
    if not can_view_capacity(user):
        return {"capacity_denied": True}
    items = []
    for row in dec.rows(case):
        current = capacity.current(row)
        prefill = capacity.prefill(row)
        kinds = []
        for kind in capacity.KINDS:
            assessment = current[kind]
            inputs = dict(assessment.inputs) if assessment is not None else prefill[kind]
            fields = [{"key": k, "label": label, "help": help_, "value": inputs.get(k, "")}
                      for k, label, help_ in CAPACITY_FIELDS[kind]]
            rooms = list(inputs.get("rooms") or []) if kind in ROOM_KINDS else []
            if kind in ROOM_KINDS:
                rooms += [{"label": "", "seats": "", "count": ""}] * 2
            kinds.append({"kind": kind, "label": capacity.KIND_LABELS[kind], "assessment": assessment,
                          "fields": fields, "rooms": rooms, "rooms_kind": kind in ROOM_KINDS,
                          "older": capacity.older_versions(row, kind)})
        items.append({"row": row, "kinds": kinds, "overall": capacity.overall(row)})
    return {"capacity_items": items, "can_run_capacity": can_run_capacity(user),
            "capacity_params": capacity.parameters()}


def _decisions_ctx(request, case, current):
    from . import decisions as dec
    from .models import Decision

    user = request.user
    codes = [s.code for s in current]
    stage_code = next((c for c in codes if dec.is_decision_stage(c)), "")
    ctx = {"decision_stage": stage_code, "approval_types": dec.active_types(),
           "all_decisions": dec.all_decisions(case)}
    if stage_code:
        stage = dec.STAGE_OF_FLOW[stage_code]
        ok, why = dec.can_decide(case, user, stage)
        ctx.update({"decision_rows": dec.board(case, stage_code, user), "can_decide": ok and not cs.is_monitor(user),
                    "decide_why": why, "decision_stage_label": dict(Decision.STAGE_CHOICES)[stage],
                    "decision_problems": dec.problems(case, stage_code),
                    "batch_enabled": dec.batch_enabled() and stage_code == "cqac_chair_approval"})
    return ctx


APPROVE_LABELS = cs.APPROVE_LABELS  # Phase 9: moved to case_services


def _duties():
    from .models import PersonnelEntry

    return PersonnelEntry.DUTY_CHOICES


def _reassign_choices():
    from accounts.models import User

    return list(User.objects.filter(is_active=True, user_type="staff").exclude(
        role__code__in=("ict-qa-monitor",)).exclude(email__iexact="qa-system@nbte.local")
        .select_related("role", "department").order_by("department__name", "first_name")[:400])


@require_or_404(cs.can_view_cases)
@require_GET
def detail(request, pk):
    case = _case_or_404(request, pk)
    return render_qa(request, "accreditation/case/detail.html", _detail_ctx(request, case),
                     {"qa-panel": "accreditation/case/_detail_panel.html"})


def _respond(request, case, *, error="", ok=("", ""), tab=None, status=None):
    case.refresh_from_db()
    response = render(request, "accreditation/case/_detail_panel.html",
                      _detail_ctx(request, case, tab=tab or request.POST.get("tab") or None, error=error),
                      status=status or (422 if error else 200))
    if error:
        return toast(response, "error", "Not done", error)
    return toast(response, "success", *ok)


def _guarded(fn):
    """Run a service call; turn ValidationError / PermissionDenied into the
    error panel (403 for permission, 422 for validation)."""
    @wraps(fn)
    def wrapped(request, pk, *args, **kwargs):
        case = _case_or_404(request, pk)
        try:
            return fn(request, case, *args, **kwargs)
        except PermissionDenied as exc:
            return _respond(request, case, error=_msg(exc), status=403)
        except ValidationError as exc:
            return _respond(request, case, error=_msg(exc))
    return wrapped


# ===========================================================================
# Stage actions
# ===========================================================================
@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def act(request, case):
    stage = request.POST.get("stage", "")
    action = request.POST.get("action", "approve")
    before = {s.label for s in cs.stage_objects(case)}
    cs.act(case, user=request.user, stage_code=stage, action=action, remark=request.POST.get("remark", ""))
    case.refresh_from_db()
    now = ", ".join(s.label for s in cs.stage_objects(case)) or "closed"
    if action == "return":
        title = "Returned"
    else:
        title = "Done" if before else "Approved"
    return _respond(request, case, ok=(title, f"The case is now at: {now}. Its task was closed and the next "
                                              "one opened."), tab="overview")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def start(request, case):
    if cs.instance_of(case) is not None:
        raise ValidationError("SOP-03 has already started for this case.")
    if not (request.user.is_superuser or request.user.has_perm("accreditation.act_on_qa_stage")):
        raise PermissionDenied("You do not hold accreditation.act_on_qa_stage.")
    cs.start_case(case, actor=request.user)
    return _respond(request, case, ok=("SOP-03 started", "The owning department's Director has the first task."))


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def desk_officer(request, case):
    officer = _user(request.POST.get("officer"))
    cs.set_desk_officer(case, user=request.user, officer=officer)
    return _respond(request, case, ok=("Desk officer chosen", f"{officer.get_full_name() or officer.email}."),
                    tab="overview")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def supervisor(request, case, kind):
    if kind not in ("audit", "finance", "ict"):
        raise Http404
    officers = [_user(pk) for pk in request.POST.getlist("officers") if pk]
    if any(o is None for o in officers):
        raise ValidationError("Choose active officers.")
    cs.set_supervisor(case, user=request.user, kind=kind, officers=officers)
    return _respond(request, case, ok=("Saved", ", ".join(o.get_full_name() or o.email for o in officers)),
                    tab="overview")


# ===========================================================================
# Cost sheet
# ===========================================================================
@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def cost_add(request, case):
    p = request.POST
    rate = cs.rate_by_pk(case, p.get("rate"))
    staff = cs.staff_by_pk(p.get("staff")) if p.get("staff") else None
    line = cs.add_cost_line(case, user=request.user, rate=rate, days=p.get("days", "1"),
                            role_on_exercise=p.get("role_on_exercise", ""), staff=staff)
    if cs.cost_editor(case, request.user) == "audit" and p.get("note"):
        line.audit_note = p["note"][:255]
        line.save(update_fields=["audit_note"])
    return _respond(request, case, ok=("Line added", f"{line.rate.role}: ₦{line.amount:,.2f}"), tab="cost")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def cost_line(request, case, line_id):
    line = cs.cost_line_of(case, line_id)
    if line is None:
        raise Http404
    p = request.POST
    if p.get("op") == "remove":
        cs.remove_cost_line(case, line, user=request.user, note=p.get("note", ""))
        return _respond(request, case, ok=("Line removed", ""), tab="cost")
    rate = cs.rate_by_pk(case, p.get("rate")) if p.get("rate") else None
    line = cs.update_cost_line(case, line, user=request.user, days=p.get("days"), rate=rate, note=p.get("note", ""))
    return _respond(request, case, ok=("Line updated", f"₦{line.amount:,.2f}"), tab="cost")


# ===========================================================================
# Personnel
# ===========================================================================
@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def personnel_add(request, case):
    staff = cs.staff_by_pk(request.POST.get("staff"))
    entry = cs.add_personnel(case, user=request.user, staff=staff, duty=request.POST.get("duty", "member"),
                             remark=request.POST.get("remark", ""))
    return _respond(request, case, ok=("Added", f"{entry.staff} ({entry.get_duty_display()})"), tab="personnel")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def personnel_remove(request, case, entry_id):
    entry = cs.personnel_entry_of(case, entry_id)
    if entry is None:
        raise Http404
    cs.remove_personnel(case, entry, user=request.user)
    return _respond(request, case, ok=("Removed", str(entry.staff)), tab="personnel")


# ===========================================================================
# Visit and fund release
# ===========================================================================
@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def visit(request, case):
    p = request.POST
    team_lead = cs.staff_by_pk(p.get("team_lead")) if p.get("team_lead") else None
    cs.save_visit(case, user=request.user, planned_start=_date(p.get("planned_start")),
                  planned_end=_date(p.get("planned_end")), team_lead=team_lead, notes=p.get("notes", ""))
    return _respond(request, case, ok=("Visit details saved", "Submit them with the stage action when ready."),
                    tab="visit")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def recommend(request, case, report_id):
    """Stage 13: the Director's recommendation for one programme's report."""
    from inspection import services as insp

    report = next((r for r in insp.reports_of(case) if r.pk == report_id), None)
    if report is None:
        raise Http404
    insp.recommend(report, user=request.user, recommendation=request.POST.get("recommendation", ""),
                   note=request.POST.get("note", ""))
    return _respond(request, case, ok=("Recommendation saved", str(report.qa_request_programme.programme)),
                    tab="reports")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def release(request, case):
    cs.record_release(case, user=request.user, reference=request.POST.get("reference", ""),
                      remark=request.POST.get("remark", ""))
    return _respond(request, case, ok=("Release recorded", "Confirm the release with the stage action."),
                    tab="funds")


# ===========================================================================
# Reassign a stage task (§8 step 7)
# ===========================================================================
@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def reassign(request, case, link_id):
    from qa_core.task_bridge import reassign_stage_task

    link = next((l for l in cs.case_links(case, open_only=True) if l.pk == link_id), None)
    if link is None:
        raise Http404
    to_user = _user(request.POST.get("to_user"))
    from_user = _user(request.POST.get("from_user")) if request.POST.get("from_user") else (
        link.holders[0] if link.holders else None)
    reassign_stage_task(link, actor=request.user, from_user=from_user, to_user=to_user)
    return _respond(request, case, ok=("Reassigned", f"{link.task.reference} → "
                                                     f"{to_user.get_full_name() or to_user.email}"), tab="tasks")


# ===========================================================================
# Task-link drift reports (nightly reconciliation, §8 step 5)
# ===========================================================================
def can_view_drift(user):
    return cs.can_view_cases(user) and (user.is_superuser or cs.is_monitor(user)
                                         or user.has_perm("qa_core.view_oversight_board")
                                         or user.has_perm("qa_core.reassign_qa_task"))


@require(can_view_drift)
@require_GET
def drift(request):
    from qa_core.models import TaskLinkDriftReport

    page = paginate(request, TaskLinkDriftReport.objects.order_by("-run_at"))
    return render_qa(request, "accreditation/case/drift.html",
                     {"page": page, "can_run": request.user.is_superuser or request.user.has_perm(
                         "qa_core.reassign_qa_task"), "monitor": cs.is_monitor(request.user),
                      **_drift_summary()},
                     {"qa-panel": "accreditation/case/_drift_panel.html"})


def _drift_summary():
    """Phase 8 UI: the latest run, the latest real repair and totals for the hero."""
    from qa_core.models import TaskLinkDriftReport

    runs = TaskLinkDriftReport.objects.order_by("-run_at")
    latest = runs.first()
    last_repair = runs.filter(dry_run=False).first()
    return {"latest": latest, "last_repair": last_repair, "runs_total": runs.count(),
            "runs_with_drift": runs.filter(Q(opened__gt=0) | Q(closed__gt=0)).count()}


@require(can_view_drift)
@require_POST
@writer
def drift_run(request):
    from qa_core.models import TaskLinkDriftReport
    from qa_core.task_bridge import reconcile

    if not (request.user.is_superuser or request.user.has_perm("qa_core.reassign_qa_task")):
        raise PermissionDenied("Running the reconciliation needs qa_core.reassign_qa_task.")
    dry = request.POST.get("dry_run") == "1"
    report = reconcile(dry_run=dry)
    page = paginate(request, TaskLinkDriftReport.objects.order_by("-run_at"))
    response = render(request, "accreditation/case/_drift_panel.html",
                      {"page": page, "can_run": True, "monitor": False, **_drift_summary()})
    return toast(response, "success", "Reconciliation " + ("checked (dry run)" if dry else "run"),
                 f"{report.checked} link(s) checked · {report.opened} opened · {report.closed} closed.")



# ===========================================================================
# Phase 8: capacity runs, decisions, letters and the Chairman's batch page
# ===========================================================================
def _capacity_inputs(post, kind):
    inputs = {key: (post.get(key) or "").strip() for key, _l, _h in CAPACITY_FIELDS[kind]}
    if kind in ROOM_KINDS:
        rooms = []
        for i in range(60):
            seats, count = post.get(f"rooms-{i}-seats"), post.get(f"rooms-{i}-count")
            if seats is None and count is None:
                continue
            if (seats or "").strip() or (count or "").strip():
                rooms.append({"label": (post.get(f"rooms-{i}-label") or "").strip()[:120],
                              "seats": (seats or "0").strip(), "count": (count or "1").strip()})
        inputs["rooms"] = rooms
    return inputs


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def capacity_run(request, case, row_id, kind):
    from . import capacity
    from . import decisions as dec

    if not can_run_capacity(request.user):
        raise PermissionDenied("Running a capacity assessment needs accreditation.run_capacity_assessment.")
    row = next((r for r in dec.rows(case) if r.pk == row_id), None)
    if row is None or kind not in capacity.KINDS:
        raise Http404
    assessment = capacity.run(row, kind, _capacity_inputs(request.POST, kind), user=request.user)
    res = assessment.result
    return _respond(request, case, tab="capacity",
                    ok=(f"{capacity.KIND_LABELS[kind]}: {res['adequacy']}",
                        f"{row.programme}: {res['supportable_streams']} stream(s) supportable, "
                        f"{res['requested_streams']} asked for (version {assessment.version})."))


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def decisions_save(request, case):
    from . import decisions as dec

    stage_code = request.POST.get("stage", "")
    saved = dec.record_from_post(case, stage_code, request.POST, user=request.user)
    return _respond(request, case, tab="decisions",
                    ok=("Decisions saved", f"{len(saved)} programme(s). Approve the stage when every programme "
                                           "is decided."))


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def letter_regenerate(request, case, letter_id):
    from . import letters

    letter = next((l for l in letters.letters_of_case(case) if l.pk == letter_id), None)
    if letter is None:
        raise Http404
    new = letters.regenerate(letter, user=request.user, reason=request.POST.get("reason", ""))
    return _respond(request, case, tab="letters", ok=("Letter regenerated", f"{letter.reference} → {new.reference}"))


def can_batch(user):
    return cs.can_view_cases(user) and (user.is_superuser or user.has_perm("accreditation.batch_decide"))


@require(can_batch)
def batch(request):
    """B23: the CQAC Chairman approves CQAC recommendations in a batch, one
    programme category at a time."""
    from . import decisions as dec

    error, result = "", None
    if request.method == "POST":
        if cs.is_monitor(request.user):
            raise PermissionDenied
        raw = request.POST.get("category", "")
        category_id = int(raw) if raw.isdigit() and raw != "0" else None
        try:
            key, decided, moved = dec.batch_approve(request.user, request.POST.getlist("rows"),
                                                    category_id=category_id)
            result = {"key": key, "decided": decided, "moved": moved}
        except (ValidationError, PermissionDenied) as exc:
            error = _msg(exc)
    groups = dec.batch_candidates(request.user)
    ctx = {"groups": groups, "enabled": dec.batch_enabled(), "error": error, "result": result}
    response = render_qa(request, "accreditation/case/batch.html", ctx,
                         {"qa-panel": "accreditation/case/_batch_panel.html"})
    if request.method == "POST":
        if error:
            response.status_code = 422
            return toast(response, "error", "Not approved", error)
        return toast(response, "success", f"Batch {result['key']} approved",
                     f"{len(result['decided'])} programme(s); {len(result['moved'])} case(s) sent to the ES.")
    return response
