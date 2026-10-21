"""SOP-01 onboarding services (Master Plan §5.4, §5.7) — system code.

The state machine is the seeded ``onboarding`` flow (data). This module only
supplies what the flow's effects need and the checks around a decision:

    draft --submit--> submitted --auto--> clearance (parallel gate)
        ├─ legal_review ──────────┐
        ├─ finance_review ────────┤ clear      any branch: return → draft
        ├─ academic_planning_rev. ┤──────────▶ clearance_join --auto--> institutional_fee_due
        └─ physical_planning_rev. ┘            (effect: create the institutional fee obligation)
    institutional_fee_due --payment_claimed--> payment_pending_confirmation
        --payment_confirmed--> fee_confirmed --auto--> programme_selection
"""
# scoped-by-caller
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core import audit
from institutions import services as inst_services
from institutions.models import Institution, InstitutionDocument
from qa_core import flow, references, timeline
from qa_core.departments import qa_code_of

from .models import Application, ApplicationCycle, DepartmentalClearance

logger = logging.getLogger(__name__)
FLOW = "onboarding"
BRANCHES = ("legal_review", "finance_review", "academic_planning_review", "physical_planning_review")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def instance_of(app):
    return flow.instance_for(app, FLOW)


def current_application(inst):
    return (Application.unscoped.filter(institution=inst).exclude(status="withdrawn")
            .order_by("-created_at").first())


def open_cycle():
    cycles = ApplicationCycle.objects.filter(applies_to_onboarding=True)
    if not cycles.exists():
        return None, True  # no cycles configured: always open
    for cycle in cycles.order_by("-opens_at"):
        if cycle.accepting():
            return cycle, True
    return None, False


def branch_department(app, branch_code):
    from staff.models import Department

    instance = instance_of(app)
    st = flow.stage(instance, branch_code) if instance else None
    code = getattr(st, "actor_scope", "") if st else ""
    return Department.objects.filter(code=code).first() if code else None


def clearances_for_round(app, round_no=None):
    round_no = round_no or app.round
    return list(DepartmentalClearance.unscoped.filter(application=app, round=round_no)
                .select_related("department", "decided_by").order_by("branch_code"))


def public_status(app):
    return flow.public_label(instance_of(app)) or app.status


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------
@transaction.atomic
def start_application(inst, *, user=None):
    """The institution's open application, created (draft) on first use."""
    existing = current_application(inst)
    if existing is not None:
        return existing
    if inst.status not in (Institution.PROPOSED, Institution.UNDER_REVIEW):
        raise ValidationError("Only proposed institutions apply through onboarding (SOP-01).")
    cycle, accepting = open_cycle()
    if not accepting:
        raise ValidationError("Applications are closed at the moment. The next window will be announced.")
    app = Application.unscoped.create(reference=references.issue("application", inst=inst.code), institution=inst,
                                      cycle=cycle, status="draft",
                                      routed_department=inst_services.proposed_department())
    flow.start(FLOW, app, actor=user)
    timeline.record(inst, "application.started", subject=app, actor=user, visibility="public",
                    public_text=f"Onboarding application {app.reference} opened.")
    return app


def profile_gaps(inst):
    """Things the institution must fill in before submitting."""
    gaps = []
    if not inst.ownership_id:
        gaps.append("Ownership")
    if not inst.institution_type_id:
        gaps.append("Institution type")
    if not inst.state_id or not inst.lga:
        gaps.append("State and LGA")
    contacts = {c.kind for c in inst_services.contacts_of(inst)}
    if "official_email" not in contacts:
        gaps.append("Official e-mail")
    if "phone" not in contacts:
        gaps.append("Phone number")
    if "head_of_institution" not in contacts:
        gaps.append("Head of institution")
    if not inst_services.addresses_of(inst):
        gaps.append("Address")
    return gaps


def submission_blockers(app):
    """Why `app` cannot be submitted now ([] = it can)."""
    blockers = []
    if app.status != "draft":
        return ["The application is not a draft."]
    inst = app.institution
    if inst.status == Institution.DELISTED or not inst.portal_enabled:
        blockers.append("Portal access is switched off for this institution.")
    gaps = profile_gaps(inst)
    if gaps:
        blockers.append("Complete your profile: " + ", ".join(gaps) + ".")
    summary = inst_services.checklist_summary(inst_services.checklist(inst))
    if summary["missing"]:
        names = ", ".join(r["kind"].name for r in summary["missing"])
        blockers.append(f"Upload the required documents: {names}.")
    if inst_services.active_hold(app):
        blockers.append("The application is on hold.")
    _cycle, accepting = open_cycle()
    if not accepting:
        blockers.append("Applications are closed at the moment.")
    return blockers


@transaction.atomic
def submit(app, *, user, declaration=False):
    app = Application.unscoped.select_for_update().get(pk=app.pk)
    if not declaration:
        raise ValidationError("Confirm the declaration that the information and documents are true.")
    blockers = submission_blockers(app)
    if blockers:
        raise ValidationError(blockers)
    app.declaration_accepted = True
    app.save(update_fields=["declaration_accepted", "updated_at"])
    flow.transition(instance_of(app), "submit", user)
    app.refresh_from_db()
    return app


@transaction.atomic
def withdraw(app, *, user, remark):
    if app.status != "draft":
        raise ValidationError("Only a draft application can be withdrawn.")
    flow.transition(instance_of(app), "withdraw", user, remark=remark)
    app.refresh_from_db()
    timeline.record(app.institution, "application.withdrawn", subject=app, actor=user, visibility="public",
                    public_text=f"Application {app.reference} was withdrawn.", internal_text=remark)
    return app


# ---------------------------------------------------------------------------
# Flow effects (registered in accreditation.effects)
# ---------------------------------------------------------------------------
def on_submitted(ctx):
    app = ctx.subject
    now = timezone.now()
    app.round = (app.round or 0) + 1
    app.submitted_at = now
    app.submitted_by = ctx.actor if getattr(ctx.actor, "pk", None) else None
    app.return_remark = ""
    app.returned_by_department = None
    app.save(update_fields=["round", "submitted_at", "submitted_by", "return_remark", "returned_by_department",
                            "updated_at"])
    # Every live document is part of this submission.
    InstitutionDocument.unscoped.filter(institution=app.institution,
                                        status__in=(InstitutionDocument.SUBMITTED, InstitutionDocument.ACCEPTED)
                                        ).update(application=app)
    inst = app.institution
    if inst.status == Institution.PROPOSED:
        inst_services.lifecycle_move(inst, "submit_for_review", ctx.actor)
    timeline.record(inst, "application.submitted", subject=app, actor=ctx.actor, visibility="public",
                    public_text=f"Application {app.reference} submitted for departmental review "
                                f"(round {app.round}).")


def on_open_clearance(ctx):
    """One DepartmentalClearance per branch of the gate, for this round."""
    app = ctx.subject
    instance = ctx.instance
    gate = flow.stage(instance, "clearance")
    branches = [t.to_stage for t in gate.outgoing.select_related("to_stage").order_by("sort_order")]
    from staff.models import Department

    notify = []
    for branch in branches:
        dept = Department.objects.filter(code=branch.actor_scope).first() if branch.actor_scope else None
        if dept is None:
            raise ValidationError(f"The department for '{branch.label}' ({branch.actor_scope or 'not set'}) "
                                  "does not exist. Check Settings → Workflows.")
        DepartmentalClearance.unscoped.create(application=app, department=dept, branch_code=branch.code,
                                              round=app.round)
        notify.append(dept)

    def _send():
        for dept in notify:
            users = inst_services.staff_in_department(dept, perm="accreditation.decide_departmental_clearance")
            inst_services.notify_role("onboarding.submitted", {"reference": app.reference,
                                                               "institution": app.institution.name},
                                      users, url=f"/qa/applications/clearance/?dept={dept.code}")
    transaction.on_commit(_send)


def on_returned(ctx):
    """A branch returned the application: close the round and tell the institution."""
    app = ctx.subject
    branch = ctx.transition.from_stage.code
    now = timezone.now()
    clearance = (DepartmentalClearance.unscoped.filter(application=app, branch_code=branch, round=app.round)
                 .first())
    dept = clearance.department if clearance else None
    DepartmentalClearance.unscoped.filter(application=app, round=app.round, status=DepartmentalClearance.PENDING) \
        .exclude(branch_code=branch).update(status=DepartmentalClearance.CLOSED, updated_at=now)
    app.returned_at = now
    app.return_remark = ctx.remark
    app.returned_by_department = dept
    app.declaration_accepted = False
    app.save(update_fields=["returned_at", "return_remark", "returned_by_department", "declaration_accepted",
                            "updated_at"])
    inst = app.institution
    if inst.status == Institution.UNDER_REVIEW:
        inst_services.lifecycle_move(inst, "return_to_proposed", ctx.actor, remark=ctx.remark)
    who = dept.name if dept else "NBTE"
    timeline.record(inst, "application.returned", subject=app, actor=ctx.actor, visibility="public",
                    public_text=f"{who} returned your application: {ctx.remark}")
    transaction.on_commit(lambda: inst_services.notify_institution(
        inst, "onboarding.returned", {"reference": app.reference, "department": who, "remark": ctx.remark},
        url="/application/"))


# ---------------------------------------------------------------------------
# Departmental decisions
# ---------------------------------------------------------------------------
def can_decide(clearance, user):
    """(allowed, reason)."""
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return False, "Only NBTE staff decide clearances."
    if not user.has_perm("accreditation.decide_departmental_clearance"):
        return False, "You do not hold accreditation.decide_departmental_clearance."
    if not user.is_superuser and user.department_id != clearance.department_id:
        return False, f"Only {clearance.department.name} can decide this clearance."
    if clearance.status != DepartmentalClearance.PENDING:
        return False, f"This clearance is already {clearance.get_status_display().lower()}."
    if clearance.round != clearance.application.round:
        return False, "This clearance belongs to an earlier round."
    instance = instance_of(clearance.application)
    if instance is None or clearance.branch_code not in instance.current_stages:
        return False, "The application is not waiting for this department."
    if instance.paused:
        return False, "The application is on hold."
    return True, ""


def documents_for(clearance):
    """The application's documents this department reviews (by DocumentRequirement)."""
    app = clearance.application
    rows = [r for r in inst_services.checklist(app.institution)
            if r["department"] is not None and r["department"].pk == clearance.department_id]
    return rows


@transaction.atomic
def decide(clearance, *, user, decision, remark="", accept_documents=True, impersonating=False):
    clearance = DepartmentalClearance.unscoped.select_for_update().select_related("application", "department") \
        .get(pk=clearance.pk)
    if impersonating:
        raise PermissionDenied("You cannot decide while impersonating.")
    ok, why = can_decide(clearance, user)
    if not ok:
        raise PermissionDenied(why)
    app = clearance.application
    remark = (remark or "").strip()
    if decision == "clear":
        for row in documents_for(clearance):
            doc = row["document"]
            if not row["mandatory"]:
                continue
            if doc is None or row["state"] in ("missing", "returned", "expired"):
                raise ValidationError(f"{row['kind'].name} is not ready — return the application instead.")
            if doc.status == InstitutionDocument.SUBMITTED:
                if not accept_documents:
                    raise ValidationError(f"Review {row['kind'].name} before clearing.")
                inst_services.review_document(doc, "accept", user=user)
        flow.transition(instance_of(app), "clear", user, remark=remark, from_stage=clearance.branch_code)
        clearance.status = DepartmentalClearance.CLEARED
    elif decision == "return":
        if len(remark) < 3:
            raise ValidationError("Write the remark the institution will see.")
        flow.transition(instance_of(app), "return", user, remark=remark, from_stage=clearance.branch_code)
        clearance.status = DepartmentalClearance.RETURNED
    else:
        raise ValidationError("Choose clear or return.")
    clearance.remark = remark
    clearance.decided_by = user
    clearance.decided_at = timezone.now()
    clearance.save(update_fields=["status", "remark", "decided_by", "decided_at", "updated_at"])
    audit.log(audit._action("state_change"), clearance, actor=user, changes={"status": {"new": clearance.status}},
              note=f"{qa_code_of(clearance.department)} {decision}")
    if decision == "clear":
        timeline.record(app.institution, "clearance.cleared", subject=app, actor=user, visibility="public",
                        public_text=f"{clearance.department.name} cleared your application.")
    app.refresh_from_db()
    if app.status == "institutional_fee_due":
        transaction.on_commit(lambda: inst_services.notify_institution(
            app.institution, "onboarding.cleared", {"reference": app.reference}, url="/payments/"))
    return clearance


# ---------------------------------------------------------------------------
# Payments drive the fee stages (signal receivers in accreditation.effects)
# ---------------------------------------------------------------------------
def _app_for(obligation):
    if obligation.source_type_id is None:
        return None
    if obligation.source_type.model_class() is not Application:
        return None
    return Application.unscoped.filter(pk=obligation.source_id).first()


def payment_claimed(obligation):
    app = _app_for(obligation)
    instance = instance_of(app) if app else None
    if instance is not None and flow.can(instance, "payment_claimed"):
        flow.transition(instance, "payment_claimed", None, metadata={"obligation": obligation.reference})


def payment_rejected(obligation):
    app = _app_for(obligation)
    instance = instance_of(app) if app else None
    if instance is not None and flow.can(instance, "payment_rejected"):
        flow.transition(instance, "payment_rejected", None, metadata={"obligation": obligation.reference})


def payment_settled(obligation):
    app = _app_for(obligation)
    instance = instance_of(app) if app else None
    if instance is not None and flow.can(instance, "payment_confirmed"):
        flow.transition(instance, "payment_confirmed", None, metadata={"obligation": obligation.reference})
        app.refresh_from_db()
        timeline.record(app.institution, "application.fee_confirmed", subject=app, visibility="public",
                        public_text="Your fee is confirmed. Next: tell NBTE which programmes you will offer.")


def stage_board(app):
    """Ordered milestones for the portal tracker: [(code, label, state)]."""
    instance = instance_of(app)
    current = set(instance.current_stages) if instance else set()
    passed = {e.to_stage for e in flow.history(instance)} if instance else set()
    milestones = [
        ("draft", "Draft & documents"),
        ("clearance", "Departmental clearance"),
        ("institutional_fee_due", "Institutional fee"),
        ("fee_confirmed", "Fee confirmed by Finance"),
        ("programme_selection", "Programme selection"),
        ("programme_clearance", "Programme clearance"),
        ("ssq_in_progress", "Questionnaire (SSQ)"),
    ]
    order = [m[0] for m in milestones]
    status = app.status
    position = {"draft": 0, "submitted": 1, "clearance": 1, "institutional_fee_due": 2,
                "payment_pending_confirmation": 2, "fee_confirmed": 3, "programme_selection": 4,
                "programme_clearance": 5, "programme_fee_due": 5, "programme_payment_pending": 5,
                "programme_fee_confirmed": 5, "ssq_in_progress": 6, "ssq_submitted": 7}.get(status, 0)
    if current & set(BRANCHES):
        position = 1
    out = []
    for i, (code, label) in enumerate(milestones):
        if i < position:
            state = "done"
        elif i == position:
            state = "current" if status not in ("ssq_submitted",) else "done"
        else:
            state = "todo"
        out.append({"code": code, "label": label, "state": state})
    del order, passed
    return out


# ---------------------------------------------------------------------------
# Lookups for the internal views (authorisation is decided by the caller:
# department match or institution scope)
# ---------------------------------------------------------------------------
def clearance_application_ids(department_id):
    return set(DepartmentalClearance.unscoped.filter(department_id=department_id)
               .values_list("application_id", flat=True))


def load_application(pk):
    return (Application.unscoped.select_related("institution", "institution__ownership", "institution__category",
                                                "institution__institution_type", "institution__state",
                                                "institution__handling_department", "returned_by_department")
            .filter(pk=pk).first())


def applications_by_ids(ids):
    return list(Application.unscoped.filter(pk__in=ids)
                .select_related("institution", "institution__ownership", "returned_by_department")
                .order_by("-submitted_at", "-created_at"))


def load_clearance(pk):
    return DepartmentalClearance.unscoped.select_related("application", "department").filter(pk=pk).first()


def load_document(pk):
    return InstitutionDocument.unscoped.select_related("kind", "institution").filter(pk=pk).first()


def clearance_departments(user):
    """Departments the user may filter the queue by."""
    from staff.models import Department

    if user.is_superuser or user.has_perm("qa_core.view_all_institutions"):
        ids = set(DepartmentalClearance.unscoped.values_list("department_id", flat=True))
        return list(Department.objects.filter(pk__in=ids).order_by("name"))
    return [user.department] if user.department_id else []


def clearance_rows(user, *, status="pending", dept_code=""):
    qs = DepartmentalClearance.unscoped.select_related("application", "application__institution",
                                                       "application__institution__ownership", "department",
                                                       "decided_by")
    if user.is_superuser or user.has_perm("qa_core.view_all_institutions"):
        if dept_code:
            qs = qs.filter(department__code=dept_code)
    elif user.department_id:
        qs = qs.filter(department_id=user.department_id)
    else:
        return []
    if status and status != "all":
        qs = qs.filter(status=status)
    rows = list(qs.order_by("opened_at"))
    if status == "pending":
        rows = [c for c in rows if c.round == c.application.round]
    return rows
