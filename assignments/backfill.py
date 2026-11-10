"""P.1 back-fill: WorkAssignment rows from the assignment-like fields that
already exist on QA records (spec_v21 G1.b / G1.c migration 2).

Runs from the data migration ``assignments/0004_backfill`` (historical
models) and from ``manage.py backfill_work_assignments`` (dry run + report).
Idempotent: a row is only created when no WorkAssignment of the same kind,
subject and assignee exists. Never deletes or changes existing rows. Every
row is created with ``assigned_by=None`` (the system) and ``meta.backfill``
naming its source field.

Status rule: open record -> ``in_progress``; closed record -> ``completed``.
"""
from __future__ import annotations

from collections import OrderedDict

CASE_CLOSED_STATUSES = {"closed", "cancelled", "withdrawn", "rejected"}
INSTITUTION_CLOSED = {"closed", "withdrawn", "rejected", "deregistered"}


def _get(apps, label):
    if apps is None:
        from django.apps import apps as live
        return live.get_model(label)
    return apps.get_model(label)


def _visit_dates(insp):
    """meta["from"] / meta["to"] (ISO dates): the convention the rotation
    engine's availability reads for visit assignments."""
    out = {}
    if insp.scheduled_start:
        out["from"] = insp.scheduled_start.isoformat()
    if insp.scheduled_end:
        out["to"] = insp.scheduled_end.isoformat()
    return out


def run(apps=None, *, dry_run=False):
    """Create the back-fill rows. Returns an ordered dict
    ``{source: {"found": n, "created": n, "existing": n, "skipped": n}}``."""
    from django.utils import timezone

    WA = _get(apps, "assignments.WorkAssignment")
    CT = _get(apps, "contenttypes.ContentType")
    User = _get(apps, "accounts.User")
    report = OrderedDict()
    now = timezone.now()
    ct_cache = {}
    planned = set()

    def ct_for(model):
        key = model._meta.label_lower
        if key not in ct_cache:
            ct_cache[key], _ = CT.objects.get_or_create(app_label=model._meta.app_label,
                                                        model=model._meta.model_name)
        return ct_cache[key]

    user_dept = dict(User.objects.values_list("pk", "department_id"))
    user_active = dict(User.objects.values_list("pk", "is_active"))

    def add(source, *, institution_id, kind, subject, assignee_id, department_id=None, is_open=True,
            role="", started=None, finished=None, meta=None):
        row = report.setdefault(source, {"found": 0, "created": 0, "existing": 0, "skipped": 0})
        row["found"] += 1
        if not assignee_id or not institution_id or assignee_id not in user_dept:
            row["skipped"] += 1
            return
        ct = ct_for(type(subject))
        key = (kind, ct.pk, str(subject.pk), assignee_id)
        exists = key in planned or WA.objects.filter(kind=kind, subject_ct=ct, subject_id=str(subject.pk),
                                                     assignee_id=assignee_id).exists()
        if exists:
            row["existing"] += 1
            return
        row["created"] += 1
        planned.add(key)
        if dry_run:
            return
        status = "in_progress" if (is_open and user_active.get(assignee_id, False)) else "completed"
        WA.objects.create(
            institution_id=institution_id, kind=kind, subject_ct=ct, subject_id=str(subject.pk),
            department_id=department_id or user_dept.get(assignee_id), assignee_id=assignee_id,
            role_on_work=role, status=status, assigned_by=None,
            assigned_at=started or now, accepted_at=started or now,
            completed_at=None if status == "in_progress" else (finished or now),
            closed_reason="" if status == "in_progress" else "completed",
            meta={"backfill": source, **(meta or {})})

    # -- QA cases (SOP-03) ------------------------------------------------
    QACase = _get(apps, "accreditation.QACase")
    for case in QACase._base_manager.all():
        is_open = case.status not in CASE_CLOSED_STATUSES
        dept = case.owning_department_id
        add("QACase.desk_officer", institution_id=case.institution_id, kind="case_desk", subject=case,
            assignee_id=case.desk_officer_id, department_id=dept, is_open=is_open, role="analyst",
            started=case.created_at)
        add("QACase.supervising_audit_officer", institution_id=case.institution_id, kind="case_supervision_audit",
            subject=case, assignee_id=case.supervising_audit_officer_id, is_open=is_open, role="supervisor",
            started=case.created_at)
        add("QACase.supervising_finance_officer", institution_id=case.institution_id,
            kind="case_supervision_finance", subject=case, assignee_id=case.supervising_finance_officer_id,
            is_open=is_open, role="supervisor", started=case.created_at)

    # -- ICT supervision: one oversight row per institution and officer ------
    Institution = _get(apps, "institutions.Institution")
    seen = set()
    for case in QACase._base_manager.all().order_by("-created_at"):
        inst = Institution._base_manager.get(pk=case.institution_id)
        for uid in case.tracking_ict_officers.values_list("pk", flat=True):
            if (inst.pk, uid) in seen:
                continue
            seen.add((inst.pk, uid))
            add("QACase.tracking_ict_officers", institution_id=inst.pk, kind="oversight_ict", subject=inst,
                assignee_id=uid, is_open=inst.status not in INSTITUTION_CLOSED, role="supervisor",
                started=case.created_at, meta={"case": case.pk})

    # -- Institution desk officer --------------------------------------------
    for inst in Institution._base_manager.exclude(desk_officer__isnull=True):
        add("Institution.desk_officer", institution_id=inst.pk, kind="case_desk", subject=inst,
            assignee_id=inst.desk_officer_id, department_id=inst.handling_department_id,
            is_open=inst.status not in INSTITUTION_CLOSED, role="analyst")

    # -- SSQ reviews ---------------------------------------------------------
    SSQRA = _get(apps, "inspection.SSQReviewAssignment")
    for ra in SSQRA._base_manager.select_related("ssq__qa_request_programme__qa_request"):
        req = ra.ssq.qa_request_programme.qa_request
        add("SSQReviewAssignment.reviewer", institution_id=req.institution_id, kind="ssq_review", subject=ra.ssq,
            assignee_id=ra.reviewer_id, is_open=ra.completed_at is None, role="reviewer",
            started=ra.assigned_at, finished=ra.completed_at)
    SSQ = _get(apps, "accreditation.SSQ")
    for ssq in SSQ._base_manager.exclude(assigned_department_user__isnull=True).select_related(
            "qa_request_programme__qa_request"):
        req = ssq.qa_request_programme.qa_request
        add("SSQ.assigned_department_user", institution_id=req.institution_id, kind="ssq_review", subject=ssq,
            assignee_id=ssq.assigned_department_user_id,
            is_open=ssq.is_current and ssq.status not in ("approved", "rejected", "closed", "superseded"),
            role="analyst", started=ssq.created_at)

    # -- Visits: mappings, team members, team leads ---------------------------
    QAMapping = _get(apps, "inspection.QAMapping")
    for m in QAMapping._base_manager.select_related("case", "qa_personnel__staff", "resource_person"):
        uid = None
        if m.qa_personnel_id:
            uid = m.qa_personnel.staff.user_id
        elif m.resource_person_id:
            uid = m.resource_person.user_id
        add("QAMapping (duty)", institution_id=m.case.institution_id, kind="visit_panel", subject=m.case,
            assignee_id=uid, is_open=m.status in ("proposed", "active"), role="member", started=m.created_at,
            meta={"duty": m.duty, "mapping": m.pk, "from": m.from_date.isoformat(), "to": m.to_date.isoformat()})
    Inspection = _get(apps, "inspection.Inspection")
    for insp in Inspection._base_manager.select_related("case"):
        is_open = insp.closed_at is None
        add("Inspection.team_lead", institution_id=insp.case.institution_id, kind="visit_lead", subject=insp,
            assignee_id=insp.team_lead_id, is_open=is_open, role="lead", started=insp.created_at,
            finished=insp.closed_at, meta=_visit_dates(insp))
    Team = _get(apps, "inspection.InspectionTeamMember")
    for tm in Team._base_manager.select_related("inspection__case", "staff"):
        if not tm.staff_id:
            continue
        insp = tm.inspection
        add("InspectionTeamMember.staff", institution_id=insp.case.institution_id, kind="visit_panel",
            subject=insp, assignee_id=tm.staff.user_id, is_open=insp.closed_at is None, role="member",
            started=tm.created_at, finished=insp.closed_at, meta=_visit_dates(insp))

    # -- Complaints ----------------------------------------------------------
    Complaint = _get(apps, "complaints.Complaint")
    Stage = _get(apps, "complaints.ComplaintStage")
    for c in Complaint._base_manager.exclude(institution__isnull=True):
        add("Complaint.current_assignee", institution_id=c.institution_id, kind="complaint_task", subject=c,
            assignee_id=c.current_assignee_id, department_id=c.current_department_id,
            is_open=c.closed_at is None, role="analyst", started=c.created_at, finished=c.closed_at)
    for st in Stage.objects.exclude(assigned_to__isnull=True).select_related("complaint"):
        c = st.complaint
        if not c.institution_id:
            continue
        add("ComplaintStage.assigned_to", institution_id=c.institution_id, kind="complaint_task", subject=c,
            assignee_id=st.assigned_to_id, department_id=st.assigned_department_id,
            is_open=st.left_at is None and c.closed_at is None, role="analyst", started=st.entered_at,
            finished=st.left_at)

    # -- Onboarding clearances (decided) -------------------------------------
    Clearance = _get(apps, "accreditation.DepartmentalClearance")
    for cl in Clearance._base_manager.exclude(decided_by__isnull=True).select_related("application"):
        add("DepartmentalClearance.decided_by", institution_id=cl.application.institution_id, kind="clearance",
            subject=cl, assignee_id=cl.decided_by_id, department_id=cl.department_id, is_open=False,
            role="decider", started=cl.opened_at, finished=cl.decided_at)
    PReview = _get(apps, "accreditation.OnboardingProgrammeReview")
    for pr in PReview._base_manager.exclude(decided_by__isnull=True).select_related("application"):
        add("OnboardingProgrammeReview.decided_by", institution_id=pr.application.institution_id,
            kind="programme_clearance", subject=pr, assignee_id=pr.decided_by_id, department_id=pr.department_id,
            is_open=False, role="decider", started=pr.created_at, finished=pr.decided_at)
    return report


def render_report(report, *, dry_run, when=None):
    from django.utils import timezone
    when = when or timezone.now()
    total = {k: sum(r[k] for r in report.values()) for k in ("found", "created", "existing", "skipped")}
    lines = [
        "# WorkAssignment back-fill report (P.1, X1)",
        "",
        f"Run: {when:%Y-%m-%d %H:%M} · mode: {'DRY RUN (nothing written)' if dry_run else 'APPLIED'}",
        "",
        "Every row is created by the system (`assigned_by` empty) with `meta.backfill` naming its source field. "
        "Open records become `in_progress`, closed records `completed`. Rows that already exist "
        "(same kind, subject and person) are left alone; records with no person or no institution are skipped. "
        "Nothing is deleted. Counts only: no names.",
        "",
        "| Source field | Found | Created | Already there | Skipped |",
        "|---|---:|---:|---:|---:|",
    ]
    for source, r in report.items():
        lines.append(f"| `{source}` | {r['found']} | {r['created']} | {r['existing']} | {r['skipped']} |")
    lines.append(f"| **Total** | **{total['found']}** | **{total['created']}** | **{total['existing']}** | "
                 f"**{total['skipped']}** |")
    lines.append("")
    return "\n".join(lines)
