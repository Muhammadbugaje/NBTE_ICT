"""The rotation queue: real work waiting for an officer in a department.

The rotation page never invents work. It lists the jobs that exist and have
nobody on them yet, in the departments the user runs, and hands each one to
the service that owns it:

    document_analysis   DocumentReview awaiting an analyst      institutions.document_review.assign
    ssq_review          submitted SSQ with no open review work  accreditation.ssq_assign.assign
    oversight_ict       proposed / under-review institution     work.assign (ICT department)
                        with no ICT supervisor
    onboarding_ssq      submitted onboarding programme          accreditation.onboarding_linear.assign
                        self-study with no officer yet

    departments_for(user)        the departments whose queue ``user`` may run
                                 (director: his own; ES / super admin: all)
    waiting(departments)         [Group] — the jobs per category, oldest first
    get_job(user, key)           one job, only while it waits in the user's scope
    candidates(job)              (rows, suggested) — the department's staff, rotation order
    assign(job, by=, chosen=, reason=)
                                 server-side checks, then the owning service

Visit panels are composed on the visit itself (several people, external
resource persons) and are not queued here.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

DOCS, SSQ, OVERSIGHT = "document_analysis", "ssq_review", "oversight_ict"
ONBOARDING = "onboarding_ssq"
ORDER = (DOCS, ONBOARDING, SSQ, OVERSIGHT)
CATEGORIES = {
    DOCS: {"code": DOCS, "label": "Document review", "short": "Docs", "chip": "docs", "icon": "file",
           "blurb": "Institution documents waiting for an analyst."},
    SSQ: {"code": SSQ, "label": "SSQ assessment", "short": "SSQ", "chip": "ssq", "icon": "sheet",
          "blurb": "Submitted SSQs waiting for a reviewing officer."},
    ONBOARDING: {"code": ONBOARDING, "label": "Onboarding self-study", "short": "Onboarding", "chip": "ssq",
                 "icon": "sheet", "blurb": "Programme self-studies of new institutions waiting for an assessing "
                                           "officer."},
    OVERSIGHT: {"code": OVERSIGHT, "label": "ICT oversight assignment", "short": "Oversight", "chip": "over",
                "icon": "shield", "blurb": "New institutions waiting for an ICT supervisor."},
}
PER_CATEGORY = 50
OVERSIGHT_STATUSES = ("proposed", "under_review")


# ---------------------------------------------------------------------------
# Who
# ---------------------------------------------------------------------------
def is_top(user):
    from . import work
    return bool(getattr(user, "is_authenticated", False) and work.is_top(user))


def can_open(user):
    """The Director (head of a department) and the ES / super admin. Nobody else."""
    import org
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if getattr(user, "user_type", "staff") != "staff" and not user.is_superuser:
        return False
    return bool(is_top(user) or org.directed_department(user) is not None)


def departments_for(user):
    """Departments whose queue ``user`` runs: the one he directs; all for ES / super admin."""
    import org
    from staff.models import Department
    if not can_open(user):
        return []
    if is_top(user):
        return list(Department.objects.order_by("name"))
    d = org.directed_department(user)
    return [d] if d is not None else []


def ict_department():
    from qa_core.departments import qa_department
    return qa_department("ict")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@dataclass
class Job:
    category: str
    obj: object
    department: object
    institution: object
    title: str
    detail: str
    since: datetime.datetime | None
    url: str = ""

    @property
    def pk(self):
        return self.obj.pk

    @property
    def key(self):
        return f"{self.category}:{self.obj.pk}"

    @property
    def meta(self):
        return CATEGORIES[self.category]

    @property
    def waiting_days(self):
        if not self.since:
            return None
        return max(0, (timezone.now() - self.since).days)


@dataclass
class Group:
    meta: dict
    jobs: list = field(default_factory=list)
    count: int = 0
    head: Job | None = None
    head_rows: list = field(default_factory=list)
    head_suggested: dict | None = None


def _ids(departments):
    return None if departments is None else [getattr(d, "pk", d) for d in departments]


def _documents(dept_ids, pk=None):
    from institutions.models import DocumentReview
    qs = (DocumentReview.unscoped.filter(status=DocumentReview.AWAITING)
          .select_related("document", "document__kind", "document__institution", "department")
          .order_by("created_at", "pk"))
    if dept_ids is not None:
        qs = qs.filter(department_id__in=dept_ids)
    if pk is not None:
        qs = qs.filter(pk=pk)
    out = []
    for r in qs[:PER_CATEGORY] if pk is None else qs[:1]:
        inst = r.document.institution
        kind = getattr(r.document.kind, "name", "Document")
        out.append(Job(DOCS, r, r.department, inst, kind, inst.name + (f" · round {r.round}" if r.round > 1 else ""),
                       r.created_at, f"/qa/applications/documents/{r.pk}/"))
    return out


def _open_subject_ids(kind, model):
    from django.contrib.contenttypes.models import ContentType

    from .models import WorkAssignment
    ct = ContentType.objects.get_for_model(model, for_concrete_model=False)
    raw = (WorkAssignment.objects.filter(kind=kind, status__in=WorkAssignment.OPEN, subject_ct=ct)
           .values_list("subject_id", flat=True))
    return [int(x) for x in raw if str(x).isdigit()]


def _ssqs(dept_ids, pk=None):
    from accreditation.models import SSQ as SSQModel
    qs = (SSQModel.unscoped.filter(status="submitted", is_current=True)
          .exclude(pk__in=_open_subject_ids(SSQ, SSQModel))
          .select_related("qa_request_programme__programme", "qa_request_programme__qa_request__institution",
                          "qa_request_programme__qa_request__institution__handling_department")
          .order_by("submitted_at", "created_at", "pk"))
    if dept_ids is not None:
        qs = qs.filter(qa_request_programme__qa_request__institution__handling_department_id__in=dept_ids)
    else:
        qs = qs.filter(qa_request_programme__qa_request__institution__handling_department__isnull=False)
    if pk is not None:
        qs = qs.filter(pk=pk)
    out = []
    for s in qs[:PER_CATEGORY] if pk is None else qs[:1]:
        row = s.qa_request_programme
        inst = row.qa_request.institution
        prog = getattr(row.programme, "name", "") or "Programme"
        out.append(Job(SSQ, s, inst.handling_department, inst, f"SSQ · {prog}",
                       f"{inst.name} · {row.qa_request.reference}", s.submitted_at or s.created_at,
                       f"/qa/requests/ssq/{s.pk}/"))
    return out


def _oversight(dept_ids, pk=None):
    from institutions.models import Institution

    from .models import WorkAssignment
    ict = ict_department()
    if ict is None or (dept_ids is not None and ict.pk not in dept_ids):
        return []
    taken = (WorkAssignment.objects.filter(kind=OVERSIGHT, status__in=WorkAssignment.OPEN)
             .values_list("institution_id", flat=True))
    qs = (Institution.unscoped.filter(status__in=OVERSIGHT_STATUSES).exclude(pk__in=taken)
          .order_by("created_at", "pk"))
    if pk is not None:
        qs = qs.filter(pk=pk)
    out = []
    for inst in qs[:PER_CATEGORY] if pk is None else qs[:1]:
        out.append(Job(OVERSIGHT, inst, ict, inst, inst.name,
                       f"{inst.code} · {(inst.status or '').replace('_', ' ').capitalize()} — needs an ICT supervisor",
                       inst.created_at, f"/qa/institutions/{inst.code}/?tab=overview"))
    return out


def _onboarding(dept_ids, pk=None):
    from accreditation import onboarding_linear
    out = []
    for study, dept in onboarding_linear.waiting_for_assignment(dept_ids, pk=pk)[:PER_CATEGORY]:
        inst = study.application.institution
        out.append(Job(ONBOARDING, study, dept, inst, f"Self-study · {study.programme}",
                       f"{inst.name} · {study.application.reference}", study.submitted_at or study.created_at,
                       onboarding_linear.study_url(study)))
    return out


COLLECTORS = {DOCS: _documents, SSQ: _ssqs, OVERSIGHT: _oversight, ONBOARDING: _onboarding}


def waiting(departments):
    """[Group] for every category that has work waiting in ``departments``."""
    ids = _ids(departments)
    groups = []
    for code in ORDER:
        jobs = COLLECTORS[code](ids)
        if jobs:
            groups.append(Group(meta=CATEGORIES[code], jobs=jobs, count=len(jobs), head=jobs[0]))
    return groups


def waiting_by_department(departments):
    """{department pk: number of jobs waiting} (for the ES's department picker)."""
    out = {}
    for code in ORDER:
        for j in COLLECTORS[code](_ids(departments)):
            out[j.department.pk] = out.get(j.department.pk, 0) + 1
    return out


def parse_key(key):
    cat, _, pk = str(key or "").partition(":")
    if cat not in COLLECTORS or not pk.isdigit():
        return None, None
    return cat, int(pk)


def get_job(user, key):
    """The job, only while it is waiting for an officer in one of the user's departments."""
    cat, pk = parse_key(key)
    if cat is None:
        return None
    depts = departments_for(user)
    if not depts:
        return None
    found = COLLECTORS[cat](_ids(depts), pk=pk)
    return found[0] if found else None


# ---------------------------------------------------------------------------
# Who can take it (the department's own staff, rotation order)
# ---------------------------------------------------------------------------
def department_staff(dept):
    import org
    from accounts.models import User
    from accounts.audience import nbte_staff_users_q
    qs = (User.objects.filter(nbte_staff_users_q())
          .filter(Q(department=dept) | Q(staff_profile__department=dept))
          .exclude(role__code__in=(org.ES_ROLE, "super-admin")).select_related("role").distinct())
    seat = org.director_of(dept)
    if seat is not None:
        qs = qs.exclude(pk=seat.pk)
    return list(qs.order_by("first_name", "last_name", "pk"))


def suggestion(job):
    """The rotation's ranked suggestion for this job (None when the department has no pool)."""
    from .models_rotation import RotationPool
    try:
        if job.category == DOCS:
            from institutions import document_review
            return document_review.suggestion(job.obj)
        if job.category == SSQ:
            from accreditation import ssq_assign
            return ssq_assign.suggestion(job.obj)
        if job.category == ONBOARDING:
            from accreditation import onboarding_linear
            return onboarding_linear.suggestion(job.obj)
        from . import rotation
        return rotation.suggest_ict_supervisor(job.obj, record=False)
    except RotationPool.DoesNotExist:
        return None


def _initials(name):
    parts = [p for p in name.replace(".", " ").split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def _staff_for(job):
    """The job's department staff, shared with the other jobs of the page."""
    from . import rotation_load as load
    b = load.active()
    if b is not None:
        return b.department_staff(job.department, department_staff)
    return department_staff(job.department)


def candidates(job):
    """(rows, suggested): every active staff member of the job's department —
    the rotation's eligible members ranked first, then staff outside the pool
    (lightest load first), then the people a rule blocks. ``suggested`` is the
    next person in rotation (or, with no pool, the lightest load)."""
    from . import rotation_load as load
    staff = _staff_for(job)
    by_pk = {u.pk: u for u in staff}
    s = suggestion(job)
    rows, seen = [], set()

    def row(u, *, eligible, in_pool, reasons=(), notes=(), warnings=(), summary="", open_n=None):
        name = u.get_full_name() or u.email
        return {"user": u, "user_id": u.pk, "name": name, "initials": _initials(name), "eligible": eligible,
                "in_pool": in_pool, "reasons": list(reasons), "notes": list(notes), "warnings": list(warnings),
                "summary": summary, "open": open_n if open_n is not None else load.open_load_all(u),
                "role": getattr(u.role, "name", "") or "Staff", "suggested": False}

    blocked = []
    if s is not None:
        for c in s.eligible:
            if c.user.pk in by_pk:
                rows.append(row(by_pk[c.user.pk], eligible=True, in_pool=True, notes=c.notes, warnings=c.warnings,
                                summary=c.summary, open_n=c.open))
                seen.add(c.user.pk)
        for c in s.blocked:
            if c.user.pk in by_pk:
                blocked.append(row(by_pk[c.user.pk], eligible=False, in_pool=True, reasons=c.reasons,
                                   summary=c.summary, open_n=c.open))
                seen.add(c.user.pk)
    rest = [row(u, eligible=True, in_pool=False, notes=["Not in the rotation pool"])
            for u in staff if u.pk not in seen]
    rest.sort(key=lambda r: (r["open"], r["name"]))
    suggested = rows[0] if rows else None
    if suggested is None and rest:
        suggested = rest[0]
        suggested["notes"] = ["Lightest load in the department (no rotation pool for this work yet)"]
    if suggested is not None:
        suggested["suggested"] = True
    return rows + rest + blocked, suggested


# ---------------------------------------------------------------------------
# Assign
# ---------------------------------------------------------------------------
def assign(job, *, by, chosen, reason=""):
    """Give ``job`` to ``chosen`` (a user pk). Every check runs here: the job
    must still be waiting in ``by``'s departments (callers resolve it with
    ``get_job``), the person must be active staff of that department, a
    blocked person needs the override permission and a reason, and choosing
    anyone but the next in rotation needs a reason. Returns the assignee."""
    from . import rotation
    if job is None:
        raise ValidationError("This work is no longer waiting for assignment in your department.")
    if not (is_top(by) or job.department in departments_for(by)):
        raise PermissionDenied("Only the director of this department or the ES assigns this work.")
    try:
        chosen_id = int(getattr(chosen, "pk", chosen) or 0)
    except (TypeError, ValueError):
        chosen_id = 0
    rows, suggested = candidates(job)
    row = next((r for r in rows if r["user_id"] == chosen_id), None)
    if row is None:
        raise ValidationError(f"Choose a member of staff of {job.department.name}.")
    reason = (reason or "").strip()
    if not row["eligible"]:
        if not rotation.can_override_constraints(by):
            raise ValidationError(f"{row['name']} can't take this work: " + "; ".join(row["reasons"]))
        if len(reason) < 5:
            raise ValidationError("Say why the rotation rule is overridden.")
    if suggested is not None and chosen_id != suggested["user_id"] and len(reason) < 5:
        raise ValidationError(f"Say why you chose someone other than {suggested['name']} (next in rotation).")
    officer = row["user"]
    if job.category == DOCS:
        from institutions import document_review
        document_review.assign(job.obj, director=by, officer=officer, reason=reason)
    elif job.category == SSQ:
        from accreditation import ssq_assign
        ssq_assign.assign(job.obj, by=by, officer=officer, reason=reason)
    elif job.category == ONBOARDING:
        from accreditation import onboarding_linear
        onboarding_linear.assign(job.obj, by=by, officer=officer, reason=reason)
    else:
        _assign_oversight(job.obj, by=by, officer=officer, reason=reason)
    return officer


@transaction.atomic
def _assign_oversight(inst, *, by, officer, reason=""):
    """The ICT supervisor of a new institution (X19), as the institution page does it."""
    from institutions.models import Institution

    from . import rotation, work
    from .models import WorkAssignment
    from .models_rotation import RotationPool
    inst = Institution.unscoped.select_for_update().get(pk=inst.pk)
    if inst.status not in OVERSIGHT_STATUSES:
        raise ValidationError("This institution no longer needs an ICT supervisor.")
    if WorkAssignment.objects.filter(institution=inst, kind=OVERSIGHT, status__in=WorkAssignment.OPEN).exists():
        raise ValidationError("This institution already has an ICT supervisor.")
    ict = ict_department()
    try:
        s = rotation.suggest_ict_supervisor(inst, record=True)
    except RotationPool.DoesNotExist:
        s = None
    decided = False
    if s is not None and s.decision is not None and any(c.get("user_id") == officer.pk
                                                        for c in s.decision.candidates or []):
        rotation.record_decision(s.decision, by=by, chosen=officer, override_reason=reason)
        decided = True
    wa = work.assign(inst, WorkAssignment.OVERSIGHT_ICT, officer, subject=inst, department=ict, assigned_by=by,
                     role_on_work="supervisor", meta={"registration_oversight": True, "via": "rotation"})
    if decided:
        s.decision.refresh_from_db()
        if s.decision.work_assignment_id is None:
            rotation.link_work(s.decision, wa)
    try:
        from qa_core import task_bridge
        task_bridge.close_for_stage(inst, "oversight_ict", reason="supervisor appointed", actor=by)
    except Exception:  # the task list must never block the appointment
        logger.exception("Could not close the oversight task of %s", inst.pk)
    return wa
