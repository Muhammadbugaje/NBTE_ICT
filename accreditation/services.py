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

from django.db.models import Q

from .models import Application, ApplicationCycle, ClearanceDelegate, DepartmentalClearance

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
    if getattr(app, "status", "") == "ssq_submitted":
        # The application ends at "ssq_submitted"; the institution approval
        # moves on without it — say where it really is (portal-safe text).
        from .onboarding_ssq import approval_stage

        public = approval_stage(app).get("public")
        if public:
            return public
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
    inst_services.require_chief_executive(user, "the application")  # X12
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
        from .clearance_access import deciders_for

        for dept in notify:
            users = deciders_for(dept)
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
    from .clearance_access import may_decide_for

    # O10 (T-SEC-7): the director of the clearance department, a named
    # ClearanceDelegate, the ES or a superuser — never any officer.
    ok, why = may_decide_for(user, clearance.department)
    if not ok:
        return False, why
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
# Re-upload of a returned document during review (O8, O9 — first slice of X17)
# ---------------------------------------------------------------------------
UNDER_REVIEW_STATUSES = ("submitted", "clearance")


def reviewing_department_for(inst, kind):
    for req in inst_services.requirements_for(inst):
        if req.document_kind_id == kind.pk:
            return req.reviewing_department
    return None


def reupload_allowed(app, row):
    """The institution may upload this checklist row now (O8): a returned
    document always; anything else only while the application is a draft."""
    if row["state"] == "returned":
        return True
    return app is None or app.status == "draft"


def on_document_reuploaded(doc, previous, *, user):
    """``doc`` replaces ``previous`` (which a department returned). While the
    application is in departmental clearance, put it back in the reviewing
    department's queue only (the other departments are not touched), tell that
    department's director (and the officer who returned it), and tell the
    institution what happens next. Returns the department, or None when the
    application is not under review (nothing to route)."""
    inst = doc.institution
    app = current_application(inst)
    if app is not None and app.status == "document_review":
        # v2.1 X17: per-document review — back to the same department, director and analyst.
        from institutions import document_review

        return document_review.on_document_reuploaded(doc, previous, user=user, application=app)
    if app is None or app.status not in UNDER_REVIEW_STATUSES:
        return None
    dept = reviewing_department_for(inst, doc.kind)
    if dept is None and previous.reviewed_by_id:
        dept = getattr(previous.reviewed_by, "department", None)
    if dept is None:
        return None
    if doc.application_id != app.pk:
        doc.application = app
        doc.save(update_fields=["application", "updated_at"])
    now = timezone.now()
    clearance = (DepartmentalClearance.unscoped.select_for_update()
                 .filter(application=app, round=app.round, department=dept).order_by("-id").first())
    if clearance is not None and clearance.status == DepartmentalClearance.PENDING:
        clearance.resubmitted_at = now
        clearance.resubmissions = (clearance.resubmissions or 0) + 1
        clearance.save(update_fields=["resubmitted_at", "resubmissions", "updated_at"])
    timeline.record(inst, "document.resubmitted", subject=doc, actor=user, visibility="public",
                    public_text=f"{doc.kind.name} re-uploaded. Sent back to {dept.name} for review. "
                                "You do not need to resubmit your application.",
                    internal_text=f"Back in the {dept.name} clearance queue (round {app.round}).")
    from .clearance_access import department_directors

    recipients = {u.pk: u for u in department_directors(dept)}
    reviewer = previous.reviewed_by
    if reviewer is not None and reviewer.is_active and getattr(reviewer, "user_type", "") == "staff" \
            and reviewer.department_id == dept.pk:
        recipients.setdefault(reviewer.pk, reviewer)
    staff_ctx = {"reference": app.reference, "institution": inst.name, "document": doc.kind.name,
                 "department": dept.name, "version": doc.version}

    def _send():
        inst_services.notify_role("onboarding.document_resubmitted", staff_ctx, list(recipients.values()),
                                  url=f"/qa/applications/{app.pk}/")
        inst_services.notify_institution(inst, "onboarding.document_resubmitted_institution",
                                         {"reference": app.reference, "document": doc.kind.name,
                                          "department": dept.name}, url="/application/")
    transaction.on_commit(_send)
    return dept


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
        if app.status == "document_upload" and instance.flow_version.version >= 3:
            text = "Your institutional and processing fees are confirmed. Upload the required documents, then ask "
            text += "the Chief Executive to submit them for departmental review."
        elif app.status == "fee_confirmed" and instance.flow_version.version == 2:
            text = "Your processing fee is confirmed. Your application is being submitted automatically."
        else:
            text = "Your fee is confirmed. Next: tell NBTE which programmes you will offer."
        timeline.record(app.institution, "application.fee_confirmed", subject=app, visibility="public",
                        public_text=text)
        if app.status == "ssq_in_progress" and instance.flow_version.version >= 3:
            from . import onboarding_ssq

            # The fee is confirmed whatever happens next: a failure to open the
            # self-studies (e.g. no published template) must not undo the
            # payment step. The portal and the staff page then offer a retry.
            try:
                onboarding_ssq.open_for_application(app)
            except Exception as exc:  # noqa: BLE001 — open_for_application is atomic (savepoint)
                logger.exception("self-studies not opened for %s", app.reference)
                timeline.record(app.institution, "onboarding.ssq_open_failed", subject=app, visibility="internal",
                                internal_text="Programme fee confirmed but the self-studies did not open: "
                                              + " ".join(getattr(exc, "messages", None) or [str(exc)]))


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
    position = {"draft": 0, "document_upload": 0, "submitted": 1, "clearance": 1, "document_review": 1,
                "institutional_fee_due": 2, "fee_due": 2, "cleared_fee_due": 2, "cleared_payment_pending": 2,
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
    from assignments import access
    if access.assignment_mode():
        # v2.1 X3/X4: full viewers see every department's queue; everyone else
        # only their department's rows they lead / are delegated / assigned.
        if access.full_viewer_everywhere(user):
            if dept_code:
                qs = qs.filter(department__code=dept_code)
        else:
            mine = DepartmentalClearance.objects.for_user(user).values("pk")
            delegated = list(ClearanceDelegate.objects.filter(user=user, active=True)
                             .values_list("department_id", flat=True))
            qs = qs.filter(Q(pk__in=mine) | Q(department_id__in=delegated))
    elif user.is_superuser or user.has_perm("qa_core.view_all_institutions"):
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
        # O9: a re-uploaded document puts its clearance back at the top.
        rows.sort(key=lambda c: (c.resubmitted_at is None, -(c.resubmitted_at.timestamp())
                                 if c.resubmitted_at else 0))
    return rows
