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
import hashlib
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from tasks.services import visible_comments as task_visible_comments  # Phase 0 (H18)
from django.views.decorators.http import require_GET, require_POST

from qa_core.permissions import require, require_or_404
from qa_core.ui import is_htmx, paginate, render_qa, toast

from django.utils import timezone

from . import case_services as cs
from . import case_ui as ui

TABS = [("overview", "Overview"), ("cost", "Cost sheet"), ("personnel", "Personnel"), ("visit", "Visit"),
        ("funds", "Fund release"), ("reports", "Reports"), ("capacity", "Capacity"), ("decisions", "Decisions"),
        ("letters", "Letters"), ("timeline", "Timeline"), ("tasks", "Tasks")]


def can_view_capacity(user):
    return bool(user.is_superuser or user.has_perm("accreditation.view_capacity_assessment"))


def can_run_capacity(user, case=None):
    return bool(case is not None and case.desk_officer_id == getattr(user, "pk", None)
                and set(cs.current_stages(case)).intersection(
                    {"post_visit_report", "post_visit_recommendation"})
                and (user.is_superuser or user.has_perm("accreditation.run_capacity_assessment"))
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
               ("funds", "Fund release (11)"), ("decision", "Reports, capacity & decisions (12–17)")]


@require(cs.can_view_cases)
@require_GET
def queue(request):
    kind = request.GET.get("kind", "")
    q = (request.GET.get("q") or "").strip()
    everything = cs.decorate_stage_links(cs.my_stage_links(request.user))
    from django.utils import timezone

    soon = timezone.now() + datetime.timedelta(hours=48)
    counts = {"all": len(everything),
              "overdue": sum(1 for l in everything if l.overdue),
              "soon": sum(1 for l in everything if not l.overdue and l.due_at and l.due_at <= soon),
              **{k: sum(1 for l in everything if l.stage_code in codes) for k, codes in cs.MY_STAGE_GROUPS.items()}}
    links = [l for l in everything if kind not in cs.MY_STAGE_GROUPS or l.stage_code in cs.MY_STAGE_GROUPS[kind]]
    if q:
        ql = q.lower()
        links = [l for l in links if ql in (l.case.institution.name or "").lower() or ql in (l.case.reference or "").lower()
                 or ql in (l.stage_label or "").lower() or ql in (getattr(l.task, "reference", "") or "").lower()]
    for link in links:
        link.due_soon = bool(not link.overdue and link.due_at and link.due_at <= soon)
    page = paginate(request, links)
    kinds = [(v, l, counts["all"] if not v else counts.get(v, 0)) for v, l in QUEUE_KINDS]
    ctx = {"page": page, "kind": kind, "kinds": kinds, "q": q, "counts": counts,
           "role": getattr(request.user.role, "name", "")}
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
                        "pinned": list(task_visible_comments(link.task, user).filter(is_pinned=True).select_related("author")
                                       .order_by("-pinned_at")[:3]) if link else [],
                        "focus": row["stage"].code == focus})
    bundle = cs.case_bundle(case)
    can = {a["stage"].code: a["can"] for a in actions}
    editor = cs.cost_editor(case, user)
    ctx = {
        "case": case, "inst": case.institution, "tab": tab, "tabs": TABS, "focus": focus, "current": current,
        "actions": actions, "stepper": _stepper(case, current), "monitor": cs.is_monitor(user),
        "started": cs.instance_of(case) is not None, "editor": editor,
        "costing": _costing_summary(case),
        "rates": cs.rates_for(case), "rates_confirmed": cs._bool("qa.cost.rates_confirmed", False),
        "ict_required": cs.ict_required(case),
        "can_send_back": cs.can_send_back_to_audit(case) and not cs.is_monitor(user),
        "can_start": (not cs.is_monitor(user) and cs.instance_of(case) is None
                      and (user.is_superuser or user.has_perm("accreditation.act_on_qa_stage"))),
        "duties": _duties(), "team_lead_roles": cs.team_lead_roles(),
        "grade_min": cs._int("qa.staff.ssq_grade_min", 7), "grade_max": cs._int("qa.staff.ssq_grade_max", 12),
        **bundle,
    }
    ctx["capacity_task_for_user"] = user in cs.holders(case, "capacity_assessment")
    # SOP-03 redesign: guides, progress, role highlights and person-picker options (accreditation/case_ui.py)
    fmt = {"grade_min": ctx["grade_min"], "grade_max": ctx["grade_max"], "ict_required": ctx["ict_required"],
           "ict_plural": "s" if ctx["ict_required"] != 1 else ""}
    for a in actions:
        a["guide"] = ui.guide(a["stage"].code, **fmt)
        a["tab_label"] = ui.TAB_LABELS.get(a["tab"], a["tab"].capitalize())
        a["since"] = ui.since(getattr(a["link"], "opened_at", None))
        a["overdue"] = bool(a["link"] and a["link"].due_at and a["link"].due_at < timezone.now())
    ctx["progress"] = ui.progress_summary(ctx["stepper"])
    ctx["viewer_roles"] = ui.viewer_roles(case, user, bundle["tracking_ict"])
    ctx["can_remind"] = ui.can_remind(case, user, bundle["tracking_ict"])
    ctx["my_turn"] = [a for a in actions if a["can"]]
    ctx["tracking_ict_ids"] = [u.pk for u in bundle["tracking_ict"]]
    ctx["desk_help"] = (f"Builds the cost sheet, personnel list and visit details. "
                        f"Grade level {ctx['grade_min']}–{ctx['grade_max']} (R-20).")
    ctx["ict_help"] = (f"This case needs {ctx['ict_required']} (R-04: {case.programme_count} programme"
                       f"{'s' if case.programme_count != 1 else ''}). They follow the case from start to finish.")
    if can.get("case_opened"):
        ctx["desk_choices"] = cs.eligible_officers(case, "desk")
        ctx["desk_people"] = ui.people(ctx["desk_choices"], block=ui.grade_block)
    for kind in ("audit", "finance", "ict"):
        if can.get(f"assign_{kind}_officer"):
            ctx[f"{kind}_choices"] = cs.eligible_officers(case, kind)
            ctx[f"{kind}_people"] = ui.people(ctx[f"{kind}_choices"])
    if can.get("add_personnel") or can.get("add_visit_details") or editor:
        ctx["staff_choices"] = cs.staff_choices(case)
    ctx["can_personnel"] = bool(can.get("add_personnel"))
    ctx["can_visit"] = bool(can.get("add_visit_details"))
    if ctx["can_personnel"] and tab == "personnel":
        from . import visit_people

        ctx["personnel_people"] = visit_people.rank(case, ctx["staff_choices"], purpose="personnel")
        ctx["external_people"] = visit_people.external_candidates(case)
        ctx["external_disciplines"] = ", ".join(visit_people.case_disciplines(case).values())
    if ctx["can_visit"] and tab == "visit":
        lead_roles = set(cs.team_lead_roles())
        lead_ids = {entry.staff_id for entry in bundle["entries"]
                    if entry.duty == "team_lead" and entry.status == "approved"}
        ctx["lead_help"] = ("Choose a confirmed team lead from the personnel list. Eligible roles: " + ", ".join(
            r.replace("-", " ").replace("_", " ") for r in cs.team_lead_roles()) + ".")
        from . import visit_people

        ctx["lead_people"] = visit_people.rank(case, [st for st in ctx["staff_choices"]
                                                     if st.pk in lead_ids and
                                                     getattr(getattr(st.user, "role", None), "code", "") in lead_roles],
                                               purpose="lead")
    if editor and tab == "cost":
        ctx.update(_cost_people_ctx(case, ctx["staff_choices"], request.GET.get("rate") or ""))
    if tab == "cost":
        ctx["cost_people_n"] = len({ln.staff_id for ln in (bundle.get("lines") or []) if ln.staff_id})
    if tab in ("personnel", "visit", "overview"):
        from . import visit_people

        ctx["external_members"] = visit_people.external_members(case)
    ctx["can_release"] = bool(can.get("request_fund_release")) and cs.can_release_funds(user, case)
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
            ctx["reassign_people"] = ui.people(ctx["reassign_choices"], suggest=False)
        if tab == "overview":  # the case cockpit
            ctx["journey"] = ui.journey(case, ctx["stepper"], links)
            ctx["inst_card"] = ui.institution_card(case)
            try:
                from inspection import services as insp

                ctx["inspection"] = insp.live_inspection(case)
            except Exception:  # noqa: BLE001 — the cockpit never fails over the visit card
                ctx["inspection"] = None
            if bundle["visit"] is not None and bundle["visit"].planned_start and bundle["visit"].planned_end:
                ctx["visit_days"] = (bundle["visit"].planned_end - bundle["visit"].planned_start).days + 1
    if tab == "visit" and bundle["visit"] is not None and bundle["visit"].planned_start:
        ctx["visit_days"] = (bundle["visit"].planned_end - bundle["visit"].planned_start).days + 1
    if tab in ("reports", "visit"):  # Phase 5: inspection + post-visit reports (stages 12–13)
        from inspection import services as insp
        from inspection.models import PostVisitReport

        ctx["inspection"] = insp.live_inspection(case)
        ctx["reports"] = insp.reports_of(case)
        from . import visit_report

        for r in ctx["reports"]:
            r.layer = insp.LAYER_LABEL.get(insp.LAYER_FOR_STAGE.get(r.status, ""), "")
            r.source_ssq, _source_submission = visit_report.submitted_ssq(r.qa_request_programme)
            r.member_counts = insp.member_report_counts(r)
            r.member_rows = insp.member_report_roster(r, user=user)
            r.member_pending = insp.member_reports_ready(r)
            r.team_lead = r.inspection.team_lead
            r.my_row = next((row for row in r.member_rows
                             if getattr(row.attendee.user, "pk", None) == user.pk), None)
            r.required = max(0, r.member_counts["total"] - r.member_counts["absent"])
            r.received = (r.member_counts["submitted"] + r.member_counts["accepted"]
                          + r.member_counts["skipped"])
            r.still_to_submit = r.member_counts["pending"] + r.member_counts["unlinked"]
            r.awaiting_review = r.member_counts["submitted"]
            r.ready_to_compile = not r.member_pending
            r.progress_pct = int(round((r.received / r.required) * 100)) if r.required else 100
            r.skipped_rows = [row for row in r.member_rows
                              if row.member_report and row.member_report.status == "skipped"]
            can_review_evidence = insp.can_review_cqac_evidence(case, user)
            can_endorse_report = insp.can_endorse(r, user)[0]
            r.can_preview_compiled_pdf = bool(
                (r.status == "deputy_verified" and can_endorse_report)
                or (r.status == "director_endorsed" and can_review_evidence))
        ctx["report_summary"] = {
            "programmes": len(ctx["reports"]),
            "required": sum(r.required for r in ctx["reports"]),
            "received": sum(r.received for r in ctx["reports"]),
            "still": sum(r.still_to_submit for r in ctx["reports"]),
            "awaiting": sum(r.awaiting_review for r in ctx["reports"]),
            "ready": sum(1 for r in ctx["reports"] if r.ready_to_compile and not r.submitted_at),
            "compiled": sum(1 for r in ctx["reports"] if r.submitted_at),
        }
        ctx["can_recommend"] = bool(can.get("post_visit_recommendation"))
        try:  # v2.4 template-driven report forms (display only; accreditation/report_forms.py)
            from . import report_forms

            ctx["report_forms_available"] = report_forms.available()
            for r in ctx["reports"]:
                r.eval_form, r.ict_form = report_forms.forms_of(r)
            ctx["ict_form"] = next((r.ict_form for r in ctx["reports"] if r.ict_form is not None), None)
            ctx["can_view_ict_form"] = bool(
                ctx["inspection"] and (
                    insp.can_edit_ict(ctx["inspection"], user)[0]
                    or insp.can_review_cqac_evidence(case, user)))
            ict_people = insp.ict_assignees(ctx["inspection"]) if ctx["inspection"] else []
            ctx["ict_assigned"] = ", ".join((u.get_full_name() or u.email) for u in ict_people) or "not assigned yet"
        except Exception:  # noqa: BLE001 — never break the tab over the forms card
            ctx["report_forms_available"] = {}
        ctx["recommendations"] = PostVisitReport.RECOMMENDATION_CHOICES
    if tab == "reports":  # v2.6: the professional body's own visit reports
        from .models import BodyVisitReport

        ctx["body_reports"] = list(
            BodyVisitReport.unscoped.filter(case=case)
            .select_related("case", "case__institution", "body", "author", "programme", "member")
            .order_by("-created_at", "-id"))
    ctx["min_visit_date"] = datetime.date.today().isoformat()
    if tab == "capacity":  # Phase 8: the capacity engine (Spec §7)
        ctx.update(_capacity_ctx(request, case))
    if tab == "decisions":  # Phase 8: stages 14–16
        ctx.update(_decisions_ctx(request, case, current))
    if tab == "letters":  # Phase 8: outcome letters (B24)
        from . import letters

        ctx["letters"] = letters.letters_of_case(case)
        ctx["can_regenerate"] = letters.can_regenerate(user) and not cs.is_monitor(user)
    # The case is already permission-checked: its professional-body notices are read in system scope.
    from .models import ProfessionalBodyNotice
    ctx["body_notices"] = list(ProfessionalBodyNotice.unscoped.filter(case=case).select_related("body"))
    if ctx["started"]:
        from . import case_changes as cc

        ctx["change_requests"] = cc.requests_for(case)
        ctx["open_change"] = cc.open_request(case)
        ctx["can_request_change"] = cc.can_request(case, user)[0]
        ctx["can_director_approve"] = cc.is_handling_director(case, user)
        ctx["can_es_approve"] = cc.is_es(user)
        ctx["is_owning_director"] = cc.is_owning_department_director(case, user)
        ctx["can_apply_change"] = cc.can_apply(case, ctx["open_change"], user)[0] if ctx["open_change"] else False
        ctx["earlier_stages"] = cc.earlier_stages(case)
        visit_row = bundle.get("visit")
        ctx["visit_dates_locked"] = bool(visit_row and visit_row.status in ("confirmed", "es_approved"))
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
    can_view = can_view_capacity(user)
    if not can_view:
        can_view = cs.can_act(case, user, "post_visit_recommendation")[0]
    if not can_view:
        from inspection import services as insp

        can_view = insp.can_review_cqac_evidence(case, user)
    if not can_view:
        return {"capacity_denied": True}
    items = []
    rows = dec.rows(case)
    currents, older = capacity.rows_state(rows)
    review_states = capacity.review_states(case, rows, current_by_row=currents)
    for row in rows:
        current = currents.get(row.pk, {})
        prefill = capacity.prefill(row)
        kinds = []
        first_incomplete = False
        for kind in capacity.KINDS:
            assessment = current.get(kind)
            inputs = dict(assessment.inputs) if assessment is not None else prefill[kind]
            fields = [{"key": k, "label": label, "help": help_, "value": inputs.get(k, "")}
                      for k, label, help_ in CAPACITY_FIELDS[kind]]
            rooms = list(inputs.get("rooms") or []) if kind in ROOM_KINDS else []
            if kind in ROOM_KINDS:
                rooms += [{"label": "", "seats": "", "count": ""}]
            kinds.append({"kind": kind, "label": capacity.KIND_LABELS[kind], "assessment": assessment,
                          "fields": fields, "rooms": rooms, "rooms_kind": kind in ROOM_KINDS,
                          "older": older.get(row.pk, {}).get(kind, []),
                          "open_by_default": not assessment and not first_incomplete})
            first_incomplete = first_incomplete or assessment is None
        review_state = review_states[row.pk]
        missing_labels = [capacity.KIND_LABELS[kind] for kind in capacity.KINDS
                          if current.get(kind) is None]
        items.append({"row": row, "kinds": kinds, "overall": capacity.overall(row, current_map=current),
                      "review": review_state["review"], "review_current": review_state["is_current"],
                      "missing_labels": missing_labels,
                      "assessed_count": sum(assessment is not None for assessment in current.values()),
                      "required_count": len(capacity.KINDS)})
    stage_open = bool(set(cs.current_stages(case)).intersection(
        {"post_visit_report", "post_visit_recommendation"}))
    can_review = bool(cs.can_act(case, user, "post_visit_recommendation")[0] and not cs.is_monitor(user))
    missing = capacity.missing_assessments(rows, current_by_row=currents)
    return {"capacity_items": items, "can_run_capacity": can_run_capacity(user, case),
            "can_review_capacity": can_review, "capacity_stage_open": stage_open,
            "capacity_assessor": case.desk_officer, "capacity_missing": missing,
            "capacity_all_assessed": not missing, "capacity_params": capacity.parameters()}


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
        can_review_evidence = False
        if stage_code in ("cqac_recommendation", "cqac_chair_approval", "es_final_approval"):
            from inspection import services as insp

            can_review_evidence = insp.can_review_cqac_evidence(case, user)
        ctx.update({"decision_rows": dec.board(case, stage_code, user), "can_decide": ok and not cs.is_monitor(user),
                    "decide_why": why, "decision_stage_label": dict(Decision.STAGE_CHOICES)[stage],
                    "decision_problems": dec.problems(case, stage_code),
                    "batch_enabled": dec.batch_enabled() and stage_code == "cqac_chair_approval",
                    "decision_review_only": bool(can_review_evidence and not ok)})
        if stage_code == "cqac_recommendation":
            ctx["cqac_recommendation_mode"] = dec.recommendation_mode()
            ctx["cqac_panel_members"] = dec.cqac_panel_members(case)
        if can_review_evidence:
            from inspection import services as insp

            from . import visit_report
            from . import report_forms

            for item in ctx["decision_rows"]:
                item["source_ssq"], _submission = visit_report.submitted_ssq(item["row"])
                item["can_preview_compiled_pdf"] = bool(
                    item["report"] and (
                        (item["report"].status == "deputy_verified" and
                         insp.can_endorse(item["report"], user)[0])
                        or (item["report"].status == "director_endorsed"
                            and insp.can_review_cqac_evidence(case, user))))
            ctx["decision_evidence"] = True
            ctx["decision_ict_report_id"] = next(
                (item["report"].pk for item in ctx["decision_rows"] if item["report"]), None)
            ctx["decision_ict_available"] = report_forms.available().get("ict_verification", False)
            ctx["decision_evaluation_available"] = report_forms.available().get("programme_evaluation", False)
    return ctx


APPROVE_LABELS = cs.APPROVE_LABELS  # Phase 9: moved to case_services


def _duties():
    from .models import PersonnelEntry

    return PersonnelEntry.DUTY_CHOICES


def _reassign_choices():
    import org
    from accounts.models import User

    from accounts.audience import nbte_staff_users_q

    return list(User.objects.filter(nbte_staff_users_q()).exclude(
        org.role_q("ict-qa-monitor"))
        .select_related("role", "department").order_by("department__name", "first_name")[:400])


@require_or_404(cs.can_view_cases)
@require_GET
def detail(request, pk):
    case = _case_or_404(request, pk)
    if set(cs.current_stages(case)).intersection({"post_visit_report", "post_visit_recommendation"}):
        from . import capacity

        capacity.ensure_assessment_task(case)
    return render_qa(request, "accreditation/case/detail.html", _detail_ctx(request, case),
                     {"qa-panel": "accreditation/case/_detail_panel.html"})


def _live_token(parts):
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


@require_or_404(cs.can_view_cases)
@require_GET
def live(request, pk):
    """Cheap version stamp for the SOP-03 case panel (polled, never cached).

    It covers the case itself, its flow instance/events, its task links and
    the stage data shown on the panel (cost sheet, personnel, visit,
    decisions, controlled changes). The client swaps the panel only when
    this stamp moves, and only when the viewer is not editing.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Max

    from qa_core.models import FlowEvent, QATaskLink

    from .models import (CaseChangeRequest, CostLine, CostSheet, Decision, PersonnelEntry,
                         VisitDetail, VisitExternalMember)

    case = _case_or_404(request, pk)
    inst = cs.instance_of(case)
    parts = [case.updated_at, case.status, case.started_at, case.completed_at]
    if inst is not None:
        parts += [inst.updated_at, inst.completed_at, tuple(inst.current_stages or ())]
        parts.append(FlowEvent.objects.filter(instance=inst).aggregate(latest=Max("at"))["latest"])
    parts.append(QATaskLink.objects.filter(
        content_type=ContentType.objects.get_for_model(case), object_id=case.pk,
    ).aggregate(latest=Max("updated_at"))["latest"])
    # Stage data the panel renders. Each is indexed by case; keeping them
    # here means an edit by another officer refreshes the panel at once.
    for model, lookup in (
        (CostSheet, {"case": case}),
        (CostLine, {"sheet__case": case}),
        (PersonnelEntry, {"personnel_list__case": case}),
        (VisitDetail, {"case": case}),
        (VisitExternalMember, {"case": case}),
        (Decision, {"case": case}),
        (CaseChangeRequest, {"case": case}),
    ):
        parts.append(model.unscoped.filter(**lookup).aggregate(latest=Max("updated_at"))["latest"])
    token = _live_token(parts)
    if request.GET.get("v") == token:
        return JsonResponse({"v": token, "unchanged": True})
    return JsonResponse({
        "v": token,
        "status": case.status,
        "stage": (inst.current_stages[0] if inst is not None and inst.current_stages else ""),
        "updated": case.updated_at.isoformat() if case.updated_at else None,
    })



def body_report_file(request, pk, report_id):
    """v2.6: download the professional body's reported file on this case —
    only once the body has approved and sent the report to NBTE."""
    from qa_core.portal_views import portal_file_response

    from .models import BodyVisitReport

    case = _case_or_404(request, pk)
    report = (BodyVisitReport.unscoped.filter(pk=report_id, case=case).select_related("case", "case__institution")
              .first())
    if report is None:
        raise Http404
    if report.status != report.WITH_NBTE:
        raise Http404("The professional body has not sent this report to NBTE yet.")
    if not report.file or not report.can_download(request.user):
        raise Http404
    return portal_file_response(request, report, channel="case")


def _cost_people_ctx(case, staff_choices, rate_pk=""):
    """The cost sheet's Person picker, ranked for the chosen rate (accreditation/visit_people.py)."""
    from . import visit_people

    rate = cs.rate_by_pk(case, rate_pk) if rate_pk else None
    rows = visit_people.rank(case, staff_choices, rate=rate, purpose="cost")
    return {"cost_people": rows, "cost_rate": rate, "cost_people_counts": visit_people.summary(rows),
            "cost_rule_text": visit_people.describe(case, rate),
            "cost_external_only": bool(rate is not None and visit_people.rule_for(rate).get("external_only"))}


@require_or_404(cs.can_view_cases)
@require_GET
def cost_people(request, pk):
    """HTMX: re-rank the cost sheet's Person picker when the rate changes."""
    case = _case_or_404(request, pk)
    if not cs.cost_editor(case, request.user):
        raise PermissionDenied("The cost sheet is not open for you to edit.")
    ctx = {"case": case, **_cost_people_ctx(case, cs.staff_choices(case), request.GET.get("rate") or "")}
    return render(request, "accreditation/case/_cost_person.html", ctx)


def _costing_summary(case):
    try:
        from . import visit_costing

        return visit_costing.summary(case)
    except Exception:
        return None


def _respond(request, case, *, error="", ok=("", ""), tab=None, status=None):
    case.refresh_from_db()
    response = render(request, "accreditation/case/_detail_panel.html",
                      _detail_ctx(request, case, tab=tab or request.POST.get("tab") or None, error=error),
                      status=status or (422 if error else 200))
    if error:
        return toast(response, "error", "Not done", error)
    return toast(response, "success", *ok)


def _now_url(case, tab=None):
    """The case page's URL as it is right now: first current stage focused (§20 deep link)."""
    stages = cs.stage_objects(case)
    if stages:
        code = stages[0].code
        tab = tab or cs.DATA_STAGES.get(code, "overview")
        return f"{reverse('qa_case:detail', args=[case.pk])}?tab={tab}&stage={code}"
    return f"{reverse('qa_case:detail', args=[case.pk])}?tab=overview"


def _now_page(request, case, tab=None):
    """After passing a stage, send the reader straight to the case as it is now
    — the browser re-loads onto the new step instead of leaving them on the
    one they just finished (same contract as institutions._after_initiate)."""
    url = _now_url(case, tab)
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


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
@require_GET
def source_ssq(request, pk, ssq_id):
    """v2.7: the institution's submitted SSQ, read-only, for the people going
    on the visit (and anyone who may open the case). It is the reference the
    post-visit report's declared figures come from."""
    case = cs.visible_cases(request.user).filter(pk=pk).select_related(
        "qa_request", "institution").first()
    if case is None:
        raise Http404
    from .models import SSQ, Submission

    ssq = (SSQ.unscoped.filter(pk=ssq_id, qa_request_programme__qa_request=case.qa_request)
           .select_related("qa_request_programme__programme__level", "template", "supersedes").first())
    if ssq is None:
        raise Http404
    submission = (Submission.unscoped.filter(ssq=ssq).select_related("submitted_by")
                  .order_by("-version").first())
    if submission is None:
        raise Http404
    from .models import Evidence

    evidence_by_field = {}
    for evidence in (Evidence.unscoped.filter(ssq=ssq).exclude(status="removed")
                     .select_related("reused_from__kind")):
        evidence_by_field.setdefault(evidence.field_key, []).append(evidence)
    sections = submission.rendered_snapshot
    ctx = {"case": case, "ssq": ssq, "submission": submission, "sections": sections,
           "programme": ssq.qa_request_programme.programme, "inst": case.institution,
           "evidence_by_field": evidence_by_field, "portal": False, "back": request.GET.get("back", "")}
    return render(request, "accreditation/case/source_ssq.html", ctx)


@require_or_404(cs.can_view_cases)
@require_GET
def source_ssq_pdf(request, pk, ssq_id):
    """Preview or download the exact submitted institutional SSQ snapshot."""
    from django.utils.text import slugify
    from core import audit
    from qa_core.pdf_report import Report, pdf_response

    case = cs.visible_cases(request.user).filter(pk=pk).select_related(
        "qa_request__institution", "institution").first()
    if case is None:
        raise Http404
    from .models import SSQ, Evidence, Submission

    ssq = (SSQ.unscoped.filter(pk=ssq_id, qa_request_programme__qa_request=case.qa_request)
           .select_related("qa_request_programme__programme__level", "template").first())
    if ssq is None:
        raise Http404
    submission = (Submission.unscoped.filter(ssq=ssq).select_related("submitted_by")
                  .order_by("-version").first())
    if submission is None:
        raise Http404
    programme = ssq.qa_request_programme.programme
    evidence = list(Evidence.unscoped.filter(ssq=ssq).exclude(status="removed")
                    .select_related("reused_from__kind").order_by("field_key", "created_at"))
    evidence_by_field = {}
    for row in evidence:
        evidence_by_field.setdefault(row.field_key, []).append(row)
    rep = Report(f"Institutional self-study · {programme}", subject=case.institution.name,
                 generated_by=request.user, cover=False)
    rep.h1("Submitted institutional self-study")
    rep.para("This PDF reflects the immutable submission snapshot. Uploaded supporting files are listed by name "
             "and remain available from the full SSQ page.")
    rep.kv([("Institution", case.institution.name), ("Institution code", case.institution.code),
            ("Programme", programme.name), ("Case", case.reference), ("SSQ version", ssq.version),
            ("Template", ssq.template.name), ("Submission version", submission.version),
            ("Submitted by", submission.submitted_by or "—"), ("Submitted on", submission.submitted_at),
            ("Snapshot checksum", submission.checksum)])
    for section in submission.rendered_snapshot or []:
        rep.h2(section.get("title", "SSQ section"))
        for field in section.get("fields", []):
            key, label = field.get("key", ""), field.get("label", "Item")
            if field.get("type") == "catalogue":
                rep.table(["Item", "Required", "Available", "Unit"],
                          [[row.get("name", ""), row.get("required", ""), row.get("quantity", "") or "—",
                            row.get("unit", "")] for row in field.get("rows", [])],
                          widths=[0.48, 0.16, 0.18, 0.18], empty="No catalogue items.")
            elif field.get("type") == "table":
                columns = field.get("columns", [])
                rows = [[f"Row {index}",
                         "; ".join(f"{column.get('label', column.get('key', ''))}: "
                                   f"{row.get(column.get('key'), '') or '—'}" for column in columns)]
                        for index, row in enumerate(field.get("rows", []), 1)]
                rep.table(["Entry", "Details"], rows, widths=[0.14, 0.86], empty="No rows.")
            else:
                rep.kv([(label, field.get("value", "") or "—")], cols=1)
            files = evidence_by_field.get(key, [])
            if files:
                rep.kv([("Supporting files", ", ".join(item.label for item in files))], cols=1)
    audit.log("other", case, actor=request.user,
              note=f"previewed or downloaded SSQ PDF {ssq.pk} (case {case.reference})")
    response = pdf_response(
        f"{slugify(programme.name)[:50] or 'programme'}-ssq-v{ssq.version}.pdf", rep.build(),
        inline=request.GET.get("download") != "1")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def act(request, case):
    stage = request.POST.get("stage", "")
    action = request.POST.get("action", "approve")
    cs.act(case, user=request.user, stage_code=stage, action=action, remark=request.POST.get("remark", ""))
    case.refresh_from_db()
    # Passed the stage: reload straight onto the case as it is now (the next
    # step), never leave the reader on the step they just finished.
    return _now_page(request, case)


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
    return _now_page(request, case)


ASSIGN_STAGES = {"case_opened": "desk", "assign_audit_officer": "audit",
                 "assign_finance_officer": "finance", "assign_ict_officer": "ict"}


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def assign(request, case):
    """One click: save the chosen person(s) for an assigning stage and complete
    that stage (no separate "Save" step). Both happen in one transaction, so a
    refused approval leaves the choice unsaved."""
    from django.db import transaction

    stage = request.POST.get("stage", "")
    kind = ASSIGN_STAGES.get(stage)
    if kind is None:
        raise Http404
    with transaction.atomic():
        if kind == "desk":
            officer = _user(request.POST.get("officer"))
            if officer is None:
                raise ValidationError("Choose the desk officer.")
            cs.set_desk_officer(case, user=request.user, officer=officer)
            names = [officer]
        else:
            names = [_user(pk) for pk in request.POST.getlist("officers") if pk]
            if not names or any(o is None for o in names):
                raise ValidationError("Choose the officer to assign.")
            cs.set_supervisor(case, user=request.user, kind=kind, officers=names)
        cs.act(case, user=request.user, stage_code=stage, action="approve", remark=request.POST.get("remark", ""))
    case.refresh_from_db()
    return _now_page(request, case, tab="overview")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def desk_officer(request, case):
    officer = _user(request.POST.get("officer"))
    cs.set_desk_officer(case, user=request.user, officer=officer)
    return _now_page(request, case, tab="overview")


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
    if rate is None:
        raise ValidationError("Choose a rate (role) from the rate table.")
    warnings = []
    if staff is not None:
        from . import visit_people

        warnings = visit_people.check(case, staff, rate=rate, purpose="cost")["warnings"]
    line = cs.add_cost_line(case, user=request.user, rate=rate, days=p.get("days", "1"),
                            role_on_exercise=p.get("role_on_exercise", ""), staff=staff)
    if cs.cost_editor(case, request.user) == "audit" and p.get("note"):
        line.audit_note = p["note"][:255]
        line.save(update_fields=["audit_note"])
    name = f"{staff.first_name} {staff.last_name}".strip() if staff is not None else ""
    who = f" for {name}" if staff is not None else ""
    response = _respond(request, case, ok=("Line added", f"{line.rate.role}{who}: ₦{line.amount:,.2f}. "
                                                         f"New total ₦{line.sheet.total:,.2f}."), tab="cost")
    if warnings:
        response = toast(response, "warning", f"Line added — but {name} may not be free",
                         "; ".join(warnings) + f". New total ₦{line.sheet.total:,.2f}.")
    return response


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def cost_calc(request, case):
    """v2.2 X39: price the visit from the state's rates (rebuilds calculator lines)."""
    from . import visit_costing

    lines = visit_costing.build_from_rates(case, user=request.user, nights=request.POST.get("nights") or None)
    sheet = cs.current_sheet(case)
    return _respond(request, case, ok=("Cost calculated from the state's rates",
                                       f"{len(lines)} line{'s' if len(lines) != 1 else ''} priced. "
                                       f"Total ₦{(sheet.total if sheet else 0):,.2f}."), tab="cost")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def cost_actual(request, case):
    """v2.2 X39: record what the visit actually cost."""
    from . import visit_costing

    visit_costing.record_actual(case, user=request.user, amount=request.POST.get("amount", ""),
                                note=request.POST.get("note", ""))
    return _respond(request, case, ok=("Actual spend recorded", ""), tab="cost")


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
        label = line.role_on_exercise
        cs.remove_cost_line(case, line, user=request.user, note=p.get("note", ""))
        sheet = cs.current_sheet(case)
        return _respond(request, case, ok=("Line removed", f"“{label}” is off the sheet. "
                                                           f"New total ₦{(sheet.total if sheet else 0):,.2f}."), tab="cost")
    rate = cs.rate_by_pk(case, p.get("rate")) if p.get("rate") else None
    line = cs.update_cost_line(case, line, user=request.user, days=p.get("days"), rate=rate, note=p.get("note", ""))
    return _respond(request, case, ok=("Line updated", f"{line.role_on_exercise}: ₦{line.amount:,.2f}. "
                                                       f"New total ₦{line.sheet.total:,.2f}."), tab="cost")


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


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def personnel_external_add(request, case):
    """An external participant (resource person / professional-body rep) on the visit team."""
    from . import visit_people

    member = visit_people.add_external(case, user=request.user, value=request.POST.get("external", ""),
                                       remark=request.POST.get("remark", ""))
    return _respond(request, case, ok=("Added to the visit team", f"{member.name} — {member.label}"),
                    tab="personnel")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def personnel_external_remove(request, case, member_id):
    from . import visit_people
    from .models import VisitExternalMember

    member = VisitExternalMember.unscoped.filter(case=case, pk=member_id).first()
    if member is None:
        raise Http404
    name = member.name
    visit_people.remove_external(case, member, user=request.user)
    return _respond(request, case, ok=("Removed", name), tab="personnel")


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


# ---------------------------------------------------------------------------
# v2.7: controlled changes (visit dates · rollback), Director → ES
# ---------------------------------------------------------------------------
@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def change_request(request, case):
    from . import case_changes as cc
    from .models import CaseChangeRequest

    p = request.POST
    kind = p.get("kind", "")
    if kind == CaseChangeRequest.VISIT_DATE:
        change = cc.request_visit_date(case, user=request.user, start=_date(p.get("start")), end=_date(p.get("end")),
                                       reason=p.get("reason", ""))
        next_step = "Sent to the Executive Secretary for approval." if change.status == "pending_es" else \
            "Sent to the handling Director for approval."
        return _respond(request, case, ok=("Change requested", next_step),
                        tab="visit")
    if kind == CaseChangeRequest.ROLLBACK:
        change = cc.request_rollback(case, user=request.user, target_stage=p.get("target_stage", ""),
                                     reason=p.get("reason", ""))
        next_step = "Sent to the Executive Secretary for approval." if change.status == "pending_es" else \
            "Sent to the handling Director for approval."
        return _respond(request, case, ok=("Rollback requested", next_step),
                        tab="overview")
    raise ValidationError("Choose what to change.")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def change_decide(request, case, req_id):
    from . import case_changes as cc
    from .models import CaseChangeRequest

    req = CaseChangeRequest.unscoped.filter(case=case, pk=req_id).first()
    if req is None:
        raise Http404
    decision = request.POST.get("decision", "")
    cc.decide(req, user=request.user, decision=decision, remark=request.POST.get("remark", ""))
    tab = "visit" if req.kind == CaseChangeRequest.VISIT_DATE else "overview"
    message = {"approve": "Approved.", "reject": "Rejected.", "return": "Returned for revision."}.get(decision)
    return _respond(request, case, ok=("Recorded", message or "Decision recorded."), tab=tab)


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def change_apply(request, case, req_id):
    from . import case_changes as cc
    from .models import CaseChangeRequest

    req = CaseChangeRequest.unscoped.filter(case=case, pk=req_id).first()
    if req is None:
        raise Http404
    cc.apply(req, user=request.user)
    tab = "visit" if req.kind == CaseChangeRequest.VISIT_DATE else "overview"
    return _respond(request, case, ok=("Applied", "The case is updated."), tab=tab)


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def change_cancel(request, case, req_id):
    from . import case_changes as cc
    from .models import CaseChangeRequest

    req = CaseChangeRequest.unscoped.filter(case=case, pk=req_id).first()
    if req is None:
        raise Http404
    cc.cancel(req, user=request.user)
    tab = "visit" if req.kind == CaseChangeRequest.VISIT_DATE else "overview"
    return _respond(request, case, ok=("Cancelled", "The request is closed."), tab=tab)


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
# Remind whoever holds a stage (case cockpit; ICT supervisor, directors, ES)
# ===========================================================================
@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def remind(request, case, stage):
    tracking = list(case.tracking_ict_officers.all())
    people = ui.remind(case, stage, by=request.user, message=request.POST.get("message", ""), tracking_ict=tracking)
    return _respond(request, case, tab=request.POST.get("tab") or "overview",
                    ok=("Reminder sent", "Sent to " + ", ".join(u.get_full_name() or u.email for u in people)
                        + f". You can remind again in {ui.cooldown_minutes()} minutes."))


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

    if not can_run_capacity(request.user, case):
        raise PermissionDenied("Only the assigned desk officer may assess capacity while the Director's "
                               "post-visit recommendation stage is open.")
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
def capacity_review(request, case, row_id):
    from . import capacity
    from . import decisions as dec

    row = next((r for r in dec.rows(case) if r.pk == row_id), None)
    if row is None:
        raise Http404
    review = capacity.confirm_review(case, row, user=request.user)
    return _respond(request, case, tab="capacity",
                    ok=("Capacity review confirmed",
                        f"{row.programme}: reviewed by {review.reviewed_by.get_full_name() or review.reviewed_by.email}."))


@require_or_404(cs.can_view_cases)
@require_POST
@writer
@_guarded
def decisions_save(request, case):
    from . import decisions as dec

    stage_code = request.POST.get("stage", "")
    saved = dec.record_from_post(case, stage_code, request.POST, user=request.user)
    if stage_code == "cqac_recommendation" and dec.recommendation_mode() == "individual":
        return _respond(request, case, tab="decisions",
                        ok=("CQAC votes saved",
                            f"Your vote was saved for {len(saved)} programme(s). Every assigned panelist must "
                            "vote before the majority recommendation can be finalized."))
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
