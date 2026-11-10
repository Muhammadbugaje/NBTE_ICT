"""SOP-03 screens — display helpers (no stage / flow logic lives here).

Everything the redesigned case pages need on top of ``case_services``:

    STAGE_GUIDE                     plain-words "what is this step / who acts / how"
    journey(case, stepper, links)   the stage list with holder, since, due, done-by
    people(users, ...)              person-picker options (rank, department,
                                    open work, leave flag, suggestion, blocked reason)
    viewer_roles(case, user, ...)   "You are: tracking ICT officer …" chips
    institution_card(case)          address, contacts, map link, programmes
    can_remind / remind             "Remind the holder" (cooldown, notification,
                                    timeline row)

The server-side eligibility and validation stay in ``case_services`` — the
picker only *shows* who is eligible and why someone is not.
"""
from __future__ import annotations

import datetime
from urllib.parse import quote_plus

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from . import case_services as cs

# ---------------------------------------------------------------------------
# Plain-words guide per stage: what · who · how (numbered mini-guide)
# ---------------------------------------------------------------------------
STAGE_GUIDE = {
    "case_opened": {
        "what": "The owning department picks the desk officer who will prepare this case.",
        "who": "Director of the owning department",
        "steps": ["Choose the desk officer (GL {grade_min}–{grade_max}) below and save.",
                  "Send the case to Audit, Finance and ICT so they name their officers."]},
    "assign_audit_officer": {
        "what": "Audit names the officer who will check the visit cost.",
        "who": "Director of Audit",
        "steps": ["Choose the supervising Audit officer and save.", "Confirm with the button below."]},
    "assign_finance_officer": {
        "what": "Finance names the officer who will follow the money for this visit.",
        "who": "Director of Finance",
        "steps": ["Choose the supervising Finance officer and save.", "Confirm with the button below."]},
    "assign_ict_officer": {
        "what": "ICT names the officer(s) who will track this case from start to finish.",
        "who": "Director of ICT",
        "steps": ["Choose {ict_required} tracking ICT officer{ict_plural} and save.", "Confirm with the button below."]},
    "generate_cost": {
        "what": "The desk officer prices the visit from the official rate table.",
        "who": "Desk officer",
        "steps": ["Finish adding and checking every cost line in Step 1 above."]},
    "add_personnel": {
        "what": "The desk officer lists the NBTE staff who will go on the visit.",
        "who": "Desk officer",
        "steps": ["Add each person with their duty in Step 1 above (include one team lead)."]},
    "confirm_personnel": {
        "what": "The Director checks the visit team before Audit sees the cost.",
        "who": "Director of the owning department",
        "steps": ["Review the team listed above.", "Confirm it, or return it with a remark below."]},
    "audit_personnel_cost": {
        "what": "The supervising Audit officer checks every cost line against the team.",
        "who": "Supervising Audit officer",
        "steps": ["Review the cost sheet above.", "Adjust days or rates where needed — each change needs a note.",
                  "Submit the audited cost below."]},
    "confirm_audit_cost": {
        "what": "The Director of Audit confirms the audited cost.",
        "who": "Director of Audit",
        "steps": ["Review the audited cost sheet above.", "Confirm it, or return it to the Audit officer with a remark below."]},
    "es_approve_cost": {
        "what": "The Executive Secretary approves the cost of the visit.",
        "who": "Executive Secretary",
        "steps": ["Review the cost sheet above and the visit team.", "Approve, or return with a remark below."]},
    "add_visit_details": {
        "what": "The desk officer sets the visit dates, team lead and logistics.",
        "who": "Desk officer",
        "steps": ["Enter the dates, team lead and logistics in Step 1 above.", "Save the visit details before submitting below."]},
    "confirm_visit_details": {
        "what": "The Director confirms the visit plan.",
        "who": "Director of the owning department",
        "steps": ["Check the dates, team lead and logistics above.", "Confirm, or return with a remark below."]},
    "es_approve_visit": {
        "what": "The Executive Secretary approves the visit; this requests the fund release.",
        "who": "Executive Secretary",
        "steps": ["Check the visit plan above and the cost.", "Approve the visit (funds are requested automatically)."]},
    "request_fund_release": {
        "what": "Finance releases the approved funds for the visit.",
        "who": "Pay Officer (or the Director of Finance)",
        "steps": ["Record the payment or voucher reference in Step 1 above.", "Save it, then confirm the release below."]},
    "post_visit_report": {
        "what": "Each attendee submits their own findings for the programme they covered. The Team Lead reviews "
                "each contribution, then submits one compiled programme report for endorsement. In parallel, "
                "the assigned desk officer completes the resource-capacity assessment. Absentees are shown but "
                "do not hold up the report. This step opens only after the visit is recorded.",
        "who": "Attendees → Team Lead compilation · desk officer capacity assessment → required approvers",
        "steps": ["Record the visit first (Team Lead) — the reports stay locked until then.",
                  "Each attendee submits their own programme findings from the Reports tab.",
                  "The Team Lead accepts or returns each submission, then compiles one final report per programme.",
                  "The assigned desk officer completes all five resource-capacity assessments from the Capacity tab.",
                  "Approval skips Division Head or Deputy Director stages when already covered by the Team Lead "
                  "or when that intermediate seat has no active assignee. The Director's approval remains explicit."]},
    "post_visit_recommendation": {
        "what": "The assigned desk officer completes the capacity assessment. The owning department Director "
                "reviews and confirms every programme's current assessment, then gives the department's "
                "recommendation.",
        "who": "Assigned case desk officer assesses · owning department Director confirms and recommends",
        "steps": ["The assigned desk officer completes all five resource assessments on the Capacity tab.",
                  "Review and confirm each programme's current capacity assessment on that tab.",
                  "Record a recommendation for every programme, then submit this stage."]},
    "cqac_recommendation": {
        "what": "The CQAC recommends a decision for each programme.",
        "who": "CQAC",
        "steps": ["Record the outcome for each programme above and save.", "Send the recommendation below."]},
    "cqac_chair_approval": {
        "what": "The CQAC Chairman approves the committee's recommendation.",
        "who": "CQAC Chairman",
        "steps": ["Review or change each programme's decision above.", "Approve the stage below."]},
    "es_final_approval": {
        "what": "The Executive Secretary gives the final decision.",
        "who": "Executive Secretary",
        "steps": ["Record the final decision for each programme above.", "Give final approval below."]},
    "programme_auto_update": {
        "what": "The system updates the programme records and issues the outcome letters.",
        "who": "System (automatic)", "steps": []},
}
TAB_LABELS = {"overview": "Overview", "cost": "Cost sheet", "personnel": "Personnel", "visit": "Visit",
              "funds": "Fund release", "reports": "Reports", "decisions": "Decisions"}


def guide(stage_code, **fmt):
    g = STAGE_GUIDE.get(stage_code) or {"what": "", "who": "", "steps": []}
    steps = []
    for s in g.get("steps", []):
        try:
            steps.append(s.format(**fmt))
        except (KeyError, IndexError):
            steps.append(s)
    return {"what": g.get("what", ""), "who": g.get("who", ""), "steps": steps}


def since(dt, now=None):
    """'3 days' / '5 hours' / 'a few minutes' — how long something has waited."""
    if not dt:
        return ""
    secs = max(0, int(((now or timezone.now()) - dt).total_seconds()))
    days, hours = secs // 86400, secs // 3600
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''}"
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    mins = secs // 60
    return f"{mins} minute{'s' if mins != 1 else ''}" if mins >= 2 else "a moment"


# ---------------------------------------------------------------------------
# The full journey (stage list) for the cockpit
# ---------------------------------------------------------------------------
def journey(case, stepper, links):
    """``stepper`` rows decorated with who holds / held each stage and since when.

    links: ``case_services.case_links(case)`` (all links, newest first)."""
    now = timezone.now()
    open_by, closed_by = {}, {}
    for link in links:
        if link.closed_at is None:
            open_by.setdefault(link.stage_code, link)
        else:
            closed_by.setdefault(link.stage_code, link)  # newest first → the latest closing
    out = []
    for row in stepper:
        codes = [p["code"] for p in row.get("parts", [])] or [row["code"]]
        step = dict(row)
        step["guide"] = guide(row["code"])
        step["parts"] = []
        for p in (row.get("parts") or [{"code": row["code"], "label": row["label"], "state": row["state"]}]):
            o, c = open_by.get(p["code"]), closed_by.get(p["code"])
            part = dict(p)
            part["holders"] = list(getattr(o, "holders", []) or [])
            part["since"] = since(o.opened_at, now) if o else ""
            part["opened_at"] = o.opened_at if o else None
            part["due_at"] = o.due_at if o else None
            part["overdue"] = bool(o and o.due_at and o.due_at < now)
            part["link"] = o
            part["done_at"] = c.closed_at if c else None
            part["done_by"] = list(getattr(c, "holders", []) or []) if c else []
            part["who"] = guide(p["code"])["who"]
            step["parts"].append(part)
        step["is_parallel"] = len(codes) > 1
        step["overdue"] = any(p["overdue"] for p in step["parts"])
        step["done_at"] = max((p["done_at"] for p in step["parts"] if p["done_at"]), default=None)
        out.append(step)
    return out


def progress_summary(stepper):
    """{done, total, pct, current: [rows]} for the hero meter and the progress strip.

    One count everywhere: the 17 SOP-03 stages. "Case opened" is stage 0, the
    start marker — shown with a check once passed, but not counted — so the
    hero ("stage 9 of 17") and the strip ("8 of 17 stages done") agree."""
    total = stepper[-1]["no"] if stepper else 17
    done = sum(1 for r in stepper if r["state"] == "done" and r["no"])
    return {"done": done, "total": total, "pct": int(round(100 * done / total)) if total else 0,
            "current": [r for r in stepper if r["state"] == "current"], "last_no": total}


# ---------------------------------------------------------------------------
# Person picker options
# ---------------------------------------------------------------------------
def _open_tasks_by_user(user_ids):
    from django.db.models import Count

    from tasks.models import Task, TaskAssignee

    rows = (TaskAssignee.objects.filter(user_id__in=user_ids, task__state__in=Task.OPEN_STATES)
            .exclude(state__in=(TaskAssignee.DONE, TaskAssignee.DECLINED))
            .values("user_id").annotate(n=Count("task", distinct=True)))
    return {r["user_id"]: r["n"] for r in rows}


def _open_cases_by_user(user_ids):
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Count

    from qa_core.models import QATaskLink

    from .models import QACase

    ct = ContentType.objects.get_for_model(QACase)
    rows = (QATaskLink.objects.filter(content_type=ct, closed_at__isnull=True,
                                      task__assignee_rows__user_id__in=user_ids)
            .values("task__assignee_rows__user_id").annotate(n=Count("object_id", distinct=True)))
    return {r["task__assignee_rows__user_id"]: r["n"] for r in rows}


def _leave_by_user(user_ids, days=14):
    """{user_id: (start, end)} for approved leave overlapping the next ``days``."""
    try:
        from leave.models import LeaveApplication
        from leave.services import APPROVED_REQUEST_STATUSES
    except Exception:  # noqa: BLE001 — the leave module is optional here
        return {}
    today = timezone.localdate()
    out = {}
    try:
        qs = (LeaveApplication.objects.filter(staff__user_id__in=user_ids, request__status__in=APPROVED_REQUEST_STATUSES,
                                              start_date__lte=today + datetime.timedelta(days=days),
                                              end_date__gte=today)
              .values_list("staff__user_id", "start_date", "end_date").order_by("start_date"))
        for uid, start, end in qs:
            out.setdefault(uid, (start, end))
    except Exception:  # noqa: BLE001 — never break a form over the leave flag
        return {}
    return out


def _photo_url(staff):
    try:
        return staff.photo.url if staff is not None and staff.photo else ""
    except Exception:  # noqa: BLE001
        return ""


def people(persons, *, staff_mode=False, block=None, suggest=True, note=None):
    """Picker options for ``persons`` (Users, or Staff with ``staff_mode``).

    block(person) -> "" or a short reason the person cannot be chosen (shown,
    and the option is disabled). note(person) -> extra short text."""
    from django.template.defaultfilters import date as date_filter

    from accounts.audience import is_nbte_staff

    from . import case_services as _cs

    # One request can render five pickers (desk/audit/finance/ICT + personnel),
    # and each picker costs a handful of grouped queries; the result rows are
    # read-only context, so the same list only has to be built once per
    # request. Any task/assignment/case-write bumps the request memo.
    if getattr(_cs._MEMO_STATE, "in_request", False):
        ids = tuple(getattr(getattr(p, "user", None), "pk", None) for p in persons) if staff_mode \
            else tuple(getattr(p, "pk", None) for p in persons)
        key = ("_ui_people", staff_mode, ids, bool(block), suggest,
               id(note) if callable(note) else note, id(block) if callable(block) else block)
        store = _cs._case_memo()
        if key in store:
            return store[key]
        rows = _people_rows(persons, staff_mode=staff_mode, block=block, suggest=suggest, note=note)
        store[key] = rows
        return rows
    return _people_rows(persons, staff_mode=staff_mode, block=block, suggest=suggest, note=note)


def _people_rows(persons, *, staff_mode=False, block=None, suggest=True, note=None):
    from django.template.defaultfilters import date as date_filter

    from accounts.audience import is_nbte_staff

    # Only NBTE staff ever appear in a picker (institution / portal / system accounts never do).
    persons = [p for p in persons if p is not None and is_nbte_staff(p.user if staff_mode else p)]
    users = [(p.user if staff_mode else p) for p in persons]
    ids = [u.pk for u in users if u is not None]
    try:  # batch the per-row staff/designation/department/role lookups (was ~100 queries per picker)
        from django.db.models import prefetch_related_objects

        if staff_mode:
            prefetch_related_objects([p for p in persons if p is not None], "designation", "department")
        else:
            prefetch_related_objects([u for u in users if u is not None],
                                     "staff_profile__designation", "staff_profile__department", "role", "department")
    except Exception:  # noqa: BLE001 - a prefetch miss must never break a picker
        pass
    tasks = _open_tasks_by_user(ids) if ids else {}
    cases = _open_cases_by_user(ids) if ids else {}
    leave = _leave_by_user(ids) if ids else {}
    today = timezone.localdate()
    out = []
    for person, user in zip(persons, users):
        staff = person if staff_mode else getattr(user, "staff_profile", None)
        if staff_mode:
            name = (f"{getattr(person, 'first_name', '')} {getattr(person, 'last_name', '')}".strip()
                    or str(person).strip())
        else:
            name = (user.get_full_name() or "").strip() or user.email
        parts = [w for w in name.replace("@", " ").split() if w]
        designation = getattr(getattr(staff, "designation", None), "name", "") if staff is not None else ""
        role = getattr(getattr(user, "role", None), "name", "") if user is not None else ""
        grade = (getattr(staff, "grade_level", "") or "") if staff is not None else ""
        dept = getattr(getattr(staff, "department", None), "name", "") if staff is not None else ""
        if not dept and user is not None:
            dept = getattr(getattr(user, "department", None), "name", "") or ""
        away = ""
        lv = leave.get(getattr(user, "pk", None))
        if lv:
            start, end = lv
            away = (f"On leave until {date_filter(end, 'j M')}" if start <= today
                    else f"Leave from {date_filter(start, 'j M')}")
        uid = getattr(user, "pk", None)
        out.append({
            "pk": person.pk, "name": name, "initials": "".join(w[0] for w in parts[:2]).upper() or "?",
            "rank": designation or role, "role": role, "grade": grade, "dept": dept,
            "photo": _photo_url(staff), "tasks": tasks.get(uid, 0), "cases": cases.get(uid, 0),
            "away": away, "blocked": (block(person) if block else "") or "",
            "note": (note(person) if note else "") or "", "suggested": False,
            "email": getattr(user, "email", "") if user is not None else "",
            "staff_no": getattr(staff, "employee_no", "") if staff is not None else "",
        })
    if suggest:
        free = [o for o in out if not o["blocked"] and not o["away"]]
        if len(free) > 1:
            best = min(free, key=lambda o: (o["cases"], o["tasks"], o["name"]))
            best["suggested"] = True
    # A status for the picker's groups and filter (visit_people.rank sets a richer one).
    busy_cap = cs._int("qa.people.busy_open_tasks", 8)
    for o in out:
        o["status"] = ("locked" if o["blocked"] else "away" if o["away"] else
                       "busy" if busy_cap and o["tasks"] >= busy_cap else
                       "recommended" if o["suggested"] else "available")
        o["reasons"] = ([("bad", o["blocked"])] if o["blocked"] else []) + (
            [("warn", o["away"])] if o["away"] else []) + (
            [("warn", f"{o['tasks']} open tasks (cap {busy_cap})")] if o["status"] == "busy" else [])
    return out


def grade_block(user):
    """R-20 shown in the picker (case_services._check_grade still decides)."""
    try:
        cs._check_grade(user)
    except ValidationError:
        lo, hi = cs._int("qa.staff.ssq_grade_min", 7), cs._int("qa.staff.ssq_grade_max", 12)
        grade = cs._grade(user)
        return f"GL {grade} — needs GL {lo}–{hi}" if grade is not None else "No grade level on record"
    return ""


# ---------------------------------------------------------------------------
# Who is looking: role highlights
# ---------------------------------------------------------------------------
def viewer_roles(case, user, tracking_ict=()):
    roles = []
    if user is None or not getattr(user, "is_authenticated", False):
        return roles
    if user in tracking_ict:
        roles.append("Tracking ICT officer")
    try:
        from assignments import access

        if access.is_ict_supervisor(user, case.institution) and "Tracking ICT officer" not in roles:
            roles.append("ICT supervisor")
    except Exception:  # noqa: BLE001
        pass
    if case.desk_officer_id == user.pk:
        roles.append("Desk officer")
    if case.supervising_audit_officer_id == user.pk:
        roles.append("Supervising Audit officer")
    if case.supervising_finance_officer_id == user.pk:
        roles.append("Supervising Finance officer")
    return roles


def is_tracker(case, user, tracking_ict=()):
    """Tracking ICT officer on the case, or the institution's ICT supervisor."""
    return bool({"Tracking ICT officer", "ICT supervisor"} & set(viewer_roles(case, user, tracking_ict)))


# ---------------------------------------------------------------------------
# Institution card
# ---------------------------------------------------------------------------
def map_url(address=None, inst=None):
    gps = (getattr(address, "gps", "") or "").replace(" ", "")
    if gps:
        return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(gps)
    text = getattr(address, "one_line", "") or ""
    if inst is not None:
        text = f"{inst.name}, {text}" if text else inst.name
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(text) if text else ""


def institution_card(case):
    from institutions import services as isvc

    from . import decisions as dec

    inst = case.institution
    try:
        addresses = isvc.addresses_of(inst)
    except Exception:  # noqa: BLE001
        addresses = []
    try:
        contacts = isvc.contacts_of(inst)
    except Exception:  # noqa: BLE001
        contacts = []
    for c in contacts:
        v = (c.value or "").strip()
        c.href = f"mailto:{v}" if "@" in v else (f"tel:{v.replace(' ', '')}" if v and any(ch.isdigit() for ch in v)
                                                 else "")
    for a in addresses:
        a.map_url = map_url(a, inst)
    try:
        programmes = dec.rows(case)
    except Exception:  # noqa: BLE001
        programmes = []
    return {"addresses": addresses, "contacts": contacts, "programmes": programmes,
            "map_url": addresses[0].map_url if addresses else map_url(None, inst)}


# ---------------------------------------------------------------------------
# Remind the holder of a stage
# ---------------------------------------------------------------------------
REMIND_EVENT = "reminded"


def _remind_code(stage_code):
    """qa_case.<stage>.reminded (the timeline shows the last part: "Reminded")."""
    return f"qa_case.{stage_code}.{REMIND_EVENT}"[:80]


def cooldown_minutes():
    return cs._int("qa.case.nudge_cooldown_minutes", 60)


def can_remind(case, user, tracking_ict=()):
    """ES, super admin, the owning department's Director, the Director of ICT,
    tracking ICT officers and the institution's ICT supervisor — never the ICT
    monitor (R-13, read-only)."""
    if user is None or not getattr(user, "is_authenticated", False) or cs.is_monitor(user):
        return False
    if getattr(user, "user_type", "staff") != "staff":
        return False
    if user.is_superuser:
        return True
    try:
        import org

        if org.is_es(user) or org.is_super_admin(user):
            return True
        if case.owning_department_id and org.is_director(user, case.owning_department):
            return True
        from qa_core.departments import qa_department

        ict = qa_department("ict")
        if ict is not None and org.is_director(user, ict):
            return True
    except Exception:  # noqa: BLE001
        pass
    return is_tracker(case, user, tracking_ict)


def last_reminder(case, stage_code):
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import TimelineEvent

    from .models import QACase

    return (TimelineEvent.unscoped.filter(content_type=ContentType.objects.get_for_model(QACase),
                                          object_id=str(case.pk), event_code=_remind_code(stage_code))
            .order_by("-at").first())


def remind(case, stage_code, *, by, message="", tracking_ict=()):
    """Notify whoever holds ``stage_code``; returns the people reminded."""
    if not can_remind(case, by, tracking_ict):
        raise PermissionDenied("You cannot send reminders on this case.")
    if stage_code not in cs.current_stages(case):
        raise ValidationError("The case is no longer at this stage — refresh the page.")
    people_ = [u for u in cs.holders(case, stage_code) if u is not None and u.pk != by.pk]
    if not people_:
        raise ValidationError("Nobody else holds this stage right now — there is no one to remind.")
    now = timezone.now()
    last = last_reminder(case, stage_code)
    cool = datetime.timedelta(minutes=cooldown_minutes())
    if last is not None and now - last.at < cool:
        ago = max(1, int((now - last.at).total_seconds() // 60))
        wait = max(1, int((cool - (now - last.at)).total_seconds() // 60))
        raise ValidationError(f"A reminder was sent {ago} minute(s) ago. You can send another in {wait} minute(s).")
    link = cs.open_link(case, stage_code)
    label = cs.stage_label(stage_code)
    held = since(getattr(link, "opened_at", None), now)
    name = (by.get_full_name() or by.email)
    message = (message or "").strip()[:500]
    body = (f"{case.institution.name} ({case.reference}) is waiting at “{label}”"
            + (f" — with you for {held}" if held else "") + ". Please act on it or return it with a remark."
            + (f" Note from {name}: {message}" if message else ""))
    from notifications.models import Notification
    from notifications.services import notify

    for u in people_:
        notify(u, title=f"Reminder: {label}", body=body, url=f"/qa/cases/{case.pk}/?stage={stage_code}",
               category=getattr(Notification, "QA", Notification.SYSTEM),
               level=getattr(Notification, "WARNING", Notification.INFO), actor=by,
               metadata={"case": case.pk, "stage": stage_code, "kind": "qa_case.nudge"})
    from qa_core import timeline

    timeline.record(case.institution, _remind_code(stage_code), subject=case, actor=by, visibility="internal",
                    internal_text=f"{name} reminded {', '.join(u.get_full_name() or u.email for u in people_)} "
                                  f"about “{label}”." + (f" “{message}”" if message else ""))
    return people_
