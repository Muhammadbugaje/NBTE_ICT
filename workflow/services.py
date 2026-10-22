"""Workflow engine — the service layer.

Every business operation on a request goes through here.

Public API:
    generate_reference()                        — allocate REQ-YYYY-NNNNN
    raise_request(...)                          — create and start the chain
    resolve_approver(step, request)             — who should act now
    approve_step(request, user, comment)        — approve and advance
    assign_staff(request, user, staff, comment) — assign, then advance
    mark_fulfilled(request, user, comment)      — assignee marks done, advances
    confirm_handover(request, user, comment)    — requester closes the request
    return_step(request, user, comment)         — back one step (peer return)
    return_to_requester(request, user, comment) — back to requester for revision
    resubmit(request, user, subject, body)      — requester sends back to the
                                                  approver who returned it
    reject_step(request, user, comment)         — terminate
    cancel_request(request, user, comment)      — requester cancels
    delete_request(request, user, reason)       — soft delete with audit trail
    add_comment(...), add_attachment(...)
    log_activity(...)
"""
import logging
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from core import audit
from notifications.models import Notification
from notifications.services import notify

from .models import (
    ApprovalChain,
    ApprovalStep,
    Request,
    RequestActivity,
    RequestApproval,
    RequestAttachment,
    RequestComment,
)

logger = logging.getLogger(__name__)

from . import emails as workflow_emails
# ===========================================================================
# Reference allocation
# ===========================================================================
@transaction.atomic
def generate_reference():
    """Allocate the next request reference of the form REQ-YYYY-NNNNN.

    Uses `all_objects` so SOFT-DELETED requests still count toward the
    sequence — otherwise deleting the highest-numbered request causes a
    unique-constraint collision on the next allocation.
    """
    year = date.today().year
    prefix = f"REQ-{year}-"
    last = (
        Request.all_objects
        .select_for_update()
        .filter(reference__startswith=prefix)
        .order_by("-reference")
        .first()
    )
    if last:
        try:
            seq = int(last.reference.rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:05d}"


# ===========================================================================
# Activity log
# ===========================================================================
def log_activity(request, kind, actor=None, message="", metadata=None):
    return RequestActivity.objects.create(
        request=request,
        kind=kind,
        actor=actor,
        message=message or "",
        metadata=metadata or {},
    )


# ===========================================================================
# Approver resolution
# ===========================================================================
def _department_for_scope(step, request):
    from staff.models import Department

    scope = (step.scope or "requester").strip().lower()
    if scope == "requester":
        return request.requester.department
    if scope == "receiving":
        return request.receiving_department
    return Department.objects.filter(code=scope).first()


def _find_user_with_role_in_department(role, department):
    if role is None or department is None:
        return None
    from accounts.models import User

    users = (
        User.objects.filter(is_active=True, role=role, department=department)
        .order_by("pk")
    )
    first = users.first()
    if first is None:
        return None
    if users.count() > 1:
        logger.warning(
            "Multiple users with role=%s in department=%s; using pk=%s",
            role.code, department.code, first.pk,
        )
    return first


def _find_department_head(department):
    if department is None:
        return None
    from accounts.models import User

    # ES sits at the top of every department it belongs to; directors
    # and the rest follow. Including `executive-secretary` here means
    # an ES's own line-head approval step resolves to the ES themselves,
    # which the self-approval guard then skips cleanly.
    for role_code in ("executive-secretary", "director", "deputy-director",
                      "office-head-admin", "office-head-ts", "office-head-dev",
                      "division-head"):
        user = (
            User.objects.filter(
                is_active=True, department=department, role__code=role_code,
            ).order_by("pk").first()
        )
        if user is not None:
            return user
    return None

def resolve_approver(step, request):
    """Resolve who should act at this step, or None if nobody matches."""
    from accounts.models import User

    kind = step.approver_kind

    if kind == ApprovalStep.APPROVER_SPECIFIC:
        return step.approver_user if (step.approver_user and step.approver_user.is_active) else None

    if kind == ApprovalStep.APPROVER_ROLE:
        department = _department_for_scope(step, request)
        return _find_user_with_role_in_department(step.approver_role, department)

    if kind == ApprovalStep.APPROVER_REQUESTER_HEAD:
        return _find_department_head(request.requester.department)

    if kind == ApprovalStep.APPROVER_REQUESTER_DEPUTY:
        if request.requester.department is None:
            return None
        return (
            User.objects.filter(
                is_active=True,
                department=request.requester.department,
                role__code="deputy-director",
            ).order_by("pk").first()
        )

    if kind == ApprovalStep.APPROVER_REQUESTER_DIRECTOR:
        if request.requester.department is None:
            return None
        return (
            User.objects.filter(
                is_active=True,
                department=request.requester.department,
                role__code="director",
            ).order_by("pk").first()
        )

    if kind == ApprovalStep.APPROVER_RECEIVING_DIRECTOR:
        if request.receiving_department is None:
            return None
        return (
            User.objects.filter(
                is_active=True,
                department=request.receiving_department,
                role__code="director",
            ).order_by("pk").first()
        )

    if kind == ApprovalStep.APPROVER_ES:
        from accounts.models import User
        return (
            User.objects.filter(
                is_active=True,
                role__code="executive-secretary",
            )
            .order_by("pk")
            .first()
        )

    return None


# ===========================================================================
# Runtime chain building — cross-department two-phase handoff
# ===========================================================================
def _synthetic_step(name, approver_kind, role_code=None, scope_code="receiving"):
    """Build an extra approval step from a request-type flag.

    Every synthetic step is a mandatory gate (Finance, Audit, HR, ES,
    Records filing) and is therefore marked `is_hierarchical=False` so it
    is never bypassed by rank.
    """
    step = ApprovalStep(
        name=name,
        kind=ApprovalStep.KIND_APPROVAL,
        approver_kind=approver_kind,
        scope=scope_code,
        is_hierarchical=False,
    )
    if role_code:
        from accounts.models import Role
        step.approver_role = Role.objects.filter(code=role_code).first()
    return step


def _extras_for_request_type(request):
    """Build the extra approval steps injected by the request type's flags."""
    rt = request.request_type
    extras = []
    if rt.requires_finance_approval:
        extras.append(_synthetic_step(
            "Finance Director approval", ApprovalStep.APPROVER_ROLE,
            role_code="director", scope_code="finance"))
    if rt.requires_audit_approval:
        extras.append(_synthetic_step(
            "Audit Director approval", ApprovalStep.APPROVER_ROLE,
            role_code="director", scope_code="audit"))
    if rt.requires_hr_approval:
        extras.append(_synthetic_step(
            "HR Director approval", ApprovalStep.APPROVER_ROLE,
            role_code="director", scope_code="hr"))
    if rt.requires_es_approval:
        extras.append(_synthetic_step(
            "Executive Secretary approval", ApprovalStep.APPROVER_ES))
    if rt.requires_records_filing:
        extras.append(_synthetic_step(
            "Records filing", ApprovalStep.APPROVER_ROLE,
            role_code="admin-staff", scope_code="receiving"))
    return extras


def _build_runtime_chain(request):
    """Return (steps, handoff_index).

    Order:
      - Cross-department, different departments:
            requester chain  →  mandatory extras  →  receiving chain
      - Same department (or non-cross-department):
            mandatory extras  →  receiving chain

    Extras (Finance / Audit / HR / ES / Records filing) sit BEFORE the
    receiving chain so they run as pre-approval gates — the receiving
    department's own workflow (its director approval, assignment,
    fulfilment, closure) does not start until every mandatory extra has
    been cleared. This also fixes the bug where extras placed after the
    receiving chain never ran, because the closure step finalises the
    request via `_finalise` rather than advancing the chain.

    `handoff_index` marks the first step that belongs to the receiving
    department. During the extras, the request is still treated as
    pre-handoff (status AWAITING, not AWAITING_RECEIVING).
    """
    receiving_steps = list(
        request.request_type.approval_chain.steps.all().order_by("position")
    )
    extras = _extras_for_request_type(request)

    is_same_dept = (
        request.requester.department_id is not None
        and request.requester.department_id == request.receiving_department_id
    )

    if request.request_type.is_cross_department and not is_same_dept:
        req_chain = request.request_type.requester_chain
        if req_chain is None:
            req_chain = ApprovalChain.objects.filter(code="internal-4-step").first()
        if req_chain is not None:
            req_steps = list(req_chain.steps.all().order_by("position"))
            handoff_index = len(req_steps) + len(extras)
            return req_steps + extras + receiving_steps, handoff_index

    return extras + receiving_steps, None

def build_runtime_steps(request):
    """Convenience wrapper — returns the full runtime chain for a request.

    Used by `_resolve_current_step`, `_position_of_step`, and `resubmit`.
    `_build_runtime_chain` returns (steps, handoff_index); this drops the
    index since those callers don't need it.
    """
    steps, _ = _build_runtime_chain(request)
    return steps


# ===========================================================================
# Step identity
# ===========================================================================
def _step_key(step):
    if step.pk:
        return f"db:{step.pk}"
    return f"syn:{slugify(step.name)}"

def _db_step(step):
    """Return `step` for FK storage only if it exists in the DB.

    Synthetic steps (Finance / Audit / HR / ES / Records filing) are
    built at runtime and have no `pk`. Django refuses to save an FK to an
    unsaved object, so we pass None for those and rely on the
    `_step_key(step)` value already stored in `RequestActivity.metadata`
    and in `Request.metadata["current_step_key"]` to identify them.
    """
    if step is None:
        return None
    return step if step.pk else None

def _current_step_key(request):
    return request.metadata.get("current_step_key")


def _set_current_step(request, step):
    request.metadata = dict(request.metadata or {})
    request.metadata["current_step_key"] = _step_key(step) if step else None
    request.current_step = step if (step and step.pk) else None
    request.current_step_started_at = timezone.now()
    request.save(update_fields=["current_step", "current_step_started_at",
                                "metadata", "updated_at"])


def _resolve_current_step(request):
    key = _current_step_key(request)
    if not key:
        return None
    for step in build_runtime_steps(request):
        if _step_key(step) == key:
            return step
    return None


def _position_of_step(request, step):
    steps = build_runtime_steps(request)
    key = _step_key(step)
    for i, s in enumerate(steps):
        if _step_key(s) == key:
            return i
    return -1

# ---------------------------------------------------------------------------
# Rank helpers
# ---------------------------------------------------------------------------
def _requester_rank(request):
    """Effective seniority of the requester.

    Superusers outrank the whole hierarchy (1000). ES has rank 999 from
    its Role. Everyone else uses their Role.rank, defaulting to 0.
    """
    requester = request.requester
    if requester.is_superuser:
        return 1000
    if requester.role_id:
        return requester.role.rank or 0
    return 0


def _actor_rank(actor):
    if actor is None:
        return 0
    if actor.is_superuser:
        return 1000
    if actor.role_id:
        return actor.role.rank or 0
    return 0


# ---------------------------------------------------------------------------
# Cross-department routing mode (Q3)
# ---------------------------------------------------------------------------
_CROSS_DEPT_MODES = ("full_chain", "rank_aware", "director_direct")


def _cross_dept_routing_mode():
    """Read the cross-department routing mode from the config registry.

      'full_chain'      — the receiving chain always runs in full
                          regardless of the requester's rank. Default.
      'rank_aware'      — the receiving chain is also rank-skipped.
      'director_direct' — directors (rank 100..998) skip the receiving
                          chain's junior steps and route straight to the
                          receiving director. ES and superusers get the
                          full receiving chain (so ES requests still land
                          on the receiving director's desk for assignment).

    Falls back to 'full_chain' if the config app isn't installed or the
    key is missing.
    """
    try:
        from config import get as cfg_get
        value = cfg_get("workflow.cross_dept_routing", "full_chain")
    except Exception:
        return "full_chain"
    value = (value or "full_chain").strip().lower()
    return value if value in _CROSS_DEPT_MODES else "full_chain"


def _receiving_chain_rank_aware(mode, requester_rank):
    """Should the receiving chain skip junior actors for this requester?"""
    if mode == "rank_aware":
        return True
    if mode == "director_direct":
        # Directors and above-director-band get the shortcut. ES (999)
        # and superusers (1000) deliberately do NOT — the user's Q1(b)
        # rule is that ES still goes through the receiving chain.
        return 100 <= requester_rank < 999
    return False

def _status_for_step(step, idx, handoff_index):
    """Determine the request status when entering a step."""
    if step.kind == ApprovalStep.KIND_FULFILLMENT:
        return Request.IN_PROGRESS
    if step.kind == ApprovalStep.KIND_CLOSURE:
        return Request.READY
    if handoff_index is not None and idx >= handoff_index:
        return Request.AWAITING_RECEIVING
    return Request.AWAITING

# ===========================================================================
# Chain advancement
# ===========================================================================
def _advance_to_next_actor(request, from_step_index):
    """Move the chain to the next step whose actor can be resolved.

    Skip rules:
      - Optional steps with no resolvable actor are silently skipped.
      - A step whose resolved actor is the requester is ALWAYS skipped —
        self-approval is never allowed.
      - A hierarchical approval step is skipped when its resolved actor is
        strictly junior to the requester, subject to where the step sits:
          * inside the requester's own chain (same-department, or
            cross-department before the handoff): always rank-skipped;
          * inside the receiving chain: rank-skipped only when the
            `workflow.cross_dept_routing` setting says so. Default is
            'full_chain' — the receiving department's internal governance
            runs untouched by the requester's seniority.
      - Non-hierarchical approval steps (Finance, Audit, HR, ES, Records
        filing) are NEVER rank-skipped — they are mandatory gates.
      - Required steps with no actor park the request and notify admins.
    """
    steps, handoff_index = _build_runtime_chain(request)
    idx = from_step_index + 1

    requester_rank = _requester_rank(request)
    cross_dept_mode = _cross_dept_routing_mode()

    while idx < len(steps):
        step = steps[idx]
        actor = resolve_approver(step, request)

        # --- Skip: optional step with no actor ---
        if actor is None and step.is_optional:
            log_activity(
                request, RequestActivity.STATUS_CHANGED, actor=None,
                message=f"Skipped optional step: {step.name}",
                metadata={"step": _step_key(step), "reason": "no actor"},
            )
            idx += 1
            continue

        # --- Approval-step skips ---
        if actor is not None and step.kind == ApprovalStep.KIND_APPROVAL:

            # 1. Never allow self-approval.
            if actor.pk == request.requester_id:
                log_activity(
                    request, RequestActivity.STATUS_CHANGED, actor=None,
                    message=f"Skipped step: {step.name} (requester is the actor)",
                    metadata={"step": _step_key(step),
                              "reason": "self_approval"},
                )
                idx += 1
                continue

            # 2. Rank-skip, subject to position and configuration.
            if step.is_hierarchical:
                in_requester_chain = (
                    handoff_index is None or idx < handoff_index
                )
                in_receiving_chain = (
                    handoff_index is not None and idx >= handoff_index
                )

                rank_skip_applies = False
                if in_requester_chain:
                    rank_skip_applies = True
                elif in_receiving_chain:
                    rank_skip_applies = _receiving_chain_rank_aware(
                        cross_dept_mode, requester_rank
                    )

                if rank_skip_applies:
                    actor_rank = _actor_rank(actor)
                    if actor_rank < requester_rank:
                        log_activity(
                            request, RequestActivity.STATUS_CHANGED, actor=None,
                            message=(
                                f"Skipped step: {step.name} "
                                f"({actor.get_full_name() or actor.email} "
                                f"is more junior than the requester)"
                            ),
                            metadata={
                                "step": _step_key(step),
                                "reason": "actor_more_junior",
                                "actor_rank": actor_rank,
                                "requester_rank": requester_rank,
                                "in_receiving_chain": in_receiving_chain,
                                "cross_dept_mode": cross_dept_mode,
                            },
                        )
                        idx += 1
                        continue

        # --- Required step with no actor: park ---
        if actor is None:
            _set_current_step(request, step)
            request.status = Request.AWAITING
            request.save(update_fields=["status", "updated_at"])
            log_activity(
                request, RequestActivity.STATUS_CHANGED, actor=None,
                message=f"Waiting for someone to fill the role for step: {step.name}",
                metadata={"step": _step_key(step), "reason": "no actor"},
            )
            _notify_admins_no_actor(request, step)
            return

        # --- We have an actionable actor ---
        _set_current_step(request, step)
        request.status = _status_for_step(step, idx, handoff_index)
        request.save(update_fields=["status", "updated_at"])

        # Handoff notification — fires only on the call that actually
        # crosses from the requester chain into the receiving chain,
        # even if some steps were skipped on the way.
        crossed_handoff = (
            handoff_index is not None
            and idx >= handoff_index
            and from_step_index < handoff_index
        )
        if crossed_handoff:
            log_activity(
                request, RequestActivity.STATUS_CHANGED, actor=None,
                message=f"Routed to {request.receiving_department.name} for action.",
                metadata={"handoff": True},
            )
            _notify_handoff(request)

        _notify_step_actor(request, step, actor)
        # Email the approver too — silently skipped if they have no email.
        try:
            workflow_emails.send_request_awaiting_approval_email(request, actor, step)
        except Exception:
            logger.exception("Workflow approval email failed for %s", request.reference)
        return

    # Chain exhausted — complete.
    _finalise(request, actor=None, note="Chain completed.") 
   
   
def _advance_from(request, step):
    """Advance the chain past `step`."""
    idx = _position_of_step(request, step)
    _advance_to_next_actor(request, from_step_index=idx)


# ===========================================================================
# Raise
# ===========================================================================
@transaction.atomic
def raise_request(*, requester, request_type, subject, body, priority="normal",
                  form_data=None):
    """Create a Request and start the chain.

    `form_data` is the dict of schema-submitted values. If it references an
    asset, an AssetIssue is linked to the request automatically.
    """
    if not request_type.is_active:
        raise ValidationError("This request type is not currently available.")

    request = None
    last_error = None
    for attempt in range(5):
        reference = generate_reference()
        try:
            with transaction.atomic():
                request = Request.objects.create(
                    reference=reference,
                    request_type=request_type,
                    requester=requester,
                    subject=subject.strip(),
                    body=body.strip(),
                    priority=priority,
                    receiving_department=request_type.handling_department,
                    status=Request.AWAITING,
                    form_data=form_data or {},
                )
            break
        except IntegrityError as exc:
            last_error = exc
            continue

    if request is None:
        logger.exception("Could not allocate a reference after 5 attempts: %s", last_error)
        raise ValidationError("Could not allocate a request reference right now. Please try again.")

    log_activity(
        request, RequestActivity.CREATED, actor=requester,
        message=f"Request raised: {request.subject}",
        metadata={"request_type": request_type.code,
                  "has_form_data": bool(form_data)},
    )

    # If the form data names an asset, link it as an AssetIssue.
    if form_data:
        _link_asset_from_form_data(request, requester, form_data)

    _advance_to_next_actor(request, from_step_index=-1)
    return request


# ---------------------------------------------------------------------------
# Asset issue linkage
# ---------------------------------------------------------------------------
def _link_asset_from_form_data(request, requester, form_data):
    """If `form_data` contains an 'asset' key referencing an existing asset,
    create an AssetIssue linked to this request and put the asset into
    the damaged state so the workflow knows it's awaiting repair."""
    asset_tag = form_data.get("asset")
    if not asset_tag:
        return
    try:
        from assets.models import Asset, AssetIssue
    except ImportError:
        return

    asset = Asset.objects.filter(asset_tag=asset_tag).first()
    if asset is None:
        return
    # Defence in depth: the form already limits the choices, but never link
    # (or mark damaged) an asset the requester is not allowed to see.
    from assets.access import can_view_asset
    if not can_view_asset(requester, asset):
        return

    # Avoid duplicates if the request is resubmitted with the same asset.
    if request.asset_issues.filter(asset=asset, resolved_at__isnull=True).exists():
        return

    try:
        AssetIssue.objects.create(
            asset=asset,
            reported_by=requester,
            summary=request.subject[:255],
            description=request.body,
            request=request,
        )
        if asset.state in ("new", "in_service"):
            asset.state = "damaged"
            asset.save(update_fields=["state", "updated_at"])
    except Exception:
        logger.exception("Could not link asset issue for request %s", request.reference)
        

# ===========================================================================
# Actor authorization
# ===========================================================================
_EXTRA_APPROVAL_FLAGS = {
    "finance": "requires_finance_approval",
    "audit": "requires_audit_approval",
    "hr": "requires_hr_approval",
}


def candidate_requests_for(user, queryset=None):
    """Open requests on which `user` could possibly be the acting user.

    Review M17: the sidebar and the action queue used to resolve the
    approver of every open request (several queries each) on every page.
    This narrows the set in ONE database query to requests the user can
    act on under some step kind, using the same rules as `resolve_approver`
    and `_ensure_actor`:

      * fulfilment - the request is assigned to them
      * closure    - they raised it
      * specific   - the current step names them
      * requester/receiving/department-scoped role steps - one of the
        request's departments (or the step's scope) is their department,
        including the Finance / Audit / HR gates
      * superusers and the Executive Secretary are not narrowed (they can
        act anywhere)

    The caller still checks each candidate with `_ensure_actor` /
    `resolve_approver`; this only removes requests that cannot match.
    """
    from django.db.models import Q

    qs = queryset if queryset is not None else Request.objects.all()
    qs = qs.exclude(status__in=Request.CLOSED_STATUSES)
    if user.is_superuser:
        return qs
    role_code = user.role.code if user.role_id else ""
    if role_code == "executive-secretary":
        return qs

    q = (
        Q(assigned_to=user)
        | Q(requester=user)
        | Q(current_step__approver_user=user)
    )
    if user.department_id:
        q |= Q(requester__department_id=user.department_id)
        q |= Q(receiving_department_id=user.department_id)
        code = (user.department.code or "").strip().lower()
        if code:
            q |= Q(current_step__scope__iexact=code)
            flag = _EXTRA_APPROVAL_FLAGS.get(code)
            if flag:
                q |= Q(**{f"request_type__{flag}": True})
    return qs.filter(q)


def _ensure_actor(request, user):
    """Return (step, actor) if `user` is allowed to act on the current step.

    Rules per step kind:
      approval     — resolved approver (or superuser)
      assignment   — resolved approver (or superuser)
      fulfillment  — the assigned staff member only (or superuser)
      closure      — the requester only (or superuser)
    """
    step = _resolve_current_step(request)
    if step is None:
        raise ValidationError("This request has no active step.")

    if step.kind == ApprovalStep.KIND_FULFILLMENT:
        if request.assigned_to_id is None:
            raise ValidationError("No assignee for this step.")
        if request.assigned_to_id != user.pk and not user.is_superuser:
            raise PermissionDenied("Only the assigned staff member can act on this step.")
        return step, request.assigned_to

    if step.kind == ApprovalStep.KIND_CLOSURE:
        if request.requester_id != user.pk and not user.is_superuser:
            raise PermissionDenied("Only the requester can confirm handover.")
        return step, request.requester

    actor = resolve_approver(step, request)
    if actor is None:
        raise PermissionDenied("No one is currently assigned to this step.")
    if actor.pk != user.pk and not user.is_superuser:
        raise PermissionDenied("You are not the acting user for this step.")
    return step, actor


# ===========================================================================
# Approval / assignment / fulfilment / closure
# ===========================================================================
@transaction.atomic
def approve_step(request, user, comment=""):
    step, actor = _ensure_actor(request, user)
    if step.kind == ApprovalStep.KIND_ASSIGNMENT:
        # Review L3: "approving" an assignment step used to record an
        # approval and then do nothing. The step is completed by assigning.
        raise ValidationError("This step is completed by assigning a staff member.")
    now = timezone.now()
    duration = 0
    if request.current_step_started_at:
        duration = int((now - request.current_step_started_at).total_seconds())

    RequestApproval.objects.create(
        request=request, step=_db_step(step),
        actor=user, actor_role=user.role.name if user.role_id else "",
        decision=RequestApproval.APPROVE,
        comment=comment or "",
        duration_seconds=duration,
    )
    log_activity(
        request, RequestActivity.APPROVED, actor=user,
        message=f"Approved step: {step.name}",
        metadata={"step": _step_key(step), "comment": comment or ""},
    )

    if step.kind == ApprovalStep.KIND_ASSIGNMENT:
        # Approval of an assignment step without a chosen assignee: keep
        # status awaiting assignment, do not advance. This lets the acting
        # user use "assign_staff" instead of "approve_step" for this step.
        # We still advanced nothing here on purpose.
        return request

    if step.kind == ApprovalStep.KIND_FULFILLMENT:
        return mark_fulfilled(request, user, comment=comment)

    if step.kind == ApprovalStep.KIND_CLOSURE:
        return confirm_handover(request, user, comment=comment)

    _advance_from(request, step)
    return request


@transaction.atomic
def assign_staff(request, user, staff_user, comment=""):
    """Assign a staff member, record the approval decision, and advance."""
    step = _resolve_current_step(request)
    if step is None or step.kind != ApprovalStep.KIND_ASSIGNMENT:
        raise ValidationError("This request is not awaiting assignment.")
    actor = resolve_approver(step, request)
    if actor is None or (actor.pk != user.pk and not user.is_superuser):
        raise PermissionDenied("You are not the acting user for this step.")

    if staff_user is None or not staff_user.is_active:
        raise ValidationError("Invalid assignee.")

    now = timezone.now()
    duration = 0
    if request.current_step_started_at:
        duration = int((now - request.current_step_started_at).total_seconds())

    request.assigned_to = staff_user
    request.save(update_fields=["assigned_to", "updated_at"])

    # Record the decision.
    RequestApproval.objects.create(
        request=request, step=_db_step(step),
        actor=user, actor_role=user.role.name if user.role_id else "",
        decision=RequestApproval.APPROVE,
        comment=(comment or f"Assigned to {staff_user.get_full_name() or staff_user.email}"),
        duration_seconds=duration,
    )
    log_activity(
        request, RequestActivity.ASSIGNED, actor=user,
        message=f"Assigned to {staff_user.get_full_name() or staff_user.email}",
        metadata={"assigned_to": staff_user.pk, "comment": comment or ""},
    )
    _notify_assignee(request, staff_user, user)

    # Advance the chain past the assignment step.
    _advance_from(request, step)
    return request


@transaction.atomic
def mark_fulfilled(request, user, comment=""):
    """The assignee marks the work as done, then the chain advances."""
    step = _resolve_current_step(request)
    if step is None:
        raise ValidationError("This request has no active step.")
    if request.assigned_to_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the assigned staff member can mark this done.")

    now = timezone.now()
    duration = 0
    if request.current_step_started_at:
        duration = int((now - request.current_step_started_at).total_seconds())

    RequestApproval.objects.create(
        request=request, step=_db_step(step),
        actor=user, actor_role=user.role.name if user.role_id else "",
        decision=RequestApproval.APPROVE,
        comment=comment or "Work marked as done.",
        duration_seconds=duration,
    )
    log_activity(
        request, RequestActivity.FULFILLMENT_MARKED, actor=user,
        message="Work marked as done by the assignee.",
        metadata={"comment": comment or ""},
    )

    _advance_from(request, step)
    return request


@transaction.atomic
def confirm_handover(request, user, comment=""):
    """Requester confirms receipt — completes the request."""
    if request.requester_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the requester can confirm handover.")
    step = _resolve_current_step(request)
    if step is None:
        raise ValidationError("This request has no active step.")

    now = timezone.now()
    duration = 0
    if request.current_step_started_at:
        duration = int((now - request.current_step_started_at).total_seconds())

    RequestApproval.objects.create(
        request=request, step=_db_step(step),
        actor=user, actor_role=user.role.name if user.role_id else "",
        decision=RequestApproval.APPROVE,
        comment=comment or "Handover confirmed by requester.",
        duration_seconds=duration,
    )
    return _finalise(request, actor=user, note=comment or "Handover confirmed.")


# ===========================================================================
# Returns (peer and requester)
# ===========================================================================
@transaction.atomic
def return_step(request, user, comment=""):
    """Send the request back ONE step. If already at the first step, this
    sends it back to the requester (equivalent to `return_to_requester`)."""
    if not (comment or "").strip():
        raise ValidationError("A comment is required when returning a request.")

    step, actor = _ensure_actor(request, user)
    now = timezone.now()
    duration = 0
    if request.current_step_started_at:
        duration = int((now - request.current_step_started_at).total_seconds())

    RequestApproval.objects.create(
        request=request, step=_db_step(step),
        actor=user, actor_role=user.role.name if user.role_id else "",
        decision=RequestApproval.RETURN,
        comment=comment,
        duration_seconds=duration,
    )
    log_activity(
        request, RequestActivity.RETURNED, actor=user,
        message=f"Returned step: {step.name}",
        metadata={"step": _step_key(step), "comment": comment},
    )

    idx = _position_of_step(request, step)
    # Review L3: go back to the nearest earlier step that someone actually
    # acted on. Stepping back one position blindly could land on a step
    # that was skipped, so the chain re-skipped it and returned the request
    # to the same person. No acted step before this one -> requester.
    steps = build_runtime_steps(request)
    acted = _acted_step_keys(request)
    target_idx = None
    for i in range(idx - 1, -1, -1):
        if _step_key(steps[i]) in acted:
            target_idx = i
            break
    if target_idx is None:
        return _send_to_requester(request, user, step, comment)

    _advance_to_next_actor(request, from_step_index=target_idx - 1)
    return request


def _acted_step_keys(request):
    """Step keys on which someone recorded an approval/assignment for
    this request (DB steps via RequestApproval, synthetic ones via the
    APPROVED activity metadata)."""
    keys = {
        f"db:{sid}" for sid in request.approvals.filter(
            decision=RequestApproval.APPROVE, step__isnull=False,
        ).values_list("step_id", flat=True)
    }
    for meta in request.activity.filter(
        kind=RequestActivity.APPROVED,
    ).values_list("metadata", flat=True):
        key = (meta or {}).get("step")
        if key:
            keys.add(key)
    return keys


@transaction.atomic
def return_to_requester(request, user, comment=""):
    """Send the request all the way back to the requester for revision,
    regardless of the current step."""
    if not (comment or "").strip():
        raise ValidationError("A comment is required when returning a request.")

    step, actor = _ensure_actor(request, user)
    now = timezone.now()
    duration = 0
    if request.current_step_started_at:
        duration = int((now - request.current_step_started_at).total_seconds())

    RequestApproval.objects.create(
        request=request, step=_db_step(step),
        actor=user, actor_role=user.role.name if user.role_id else "",
        decision=RequestApproval.RETURN,
        comment=comment,
        duration_seconds=duration,
    )
    log_activity(
        request, RequestActivity.RETURNED, actor=user,
        message=f"Returned to requester from step: {step.name}",
        metadata={"step": _step_key(step), "comment": comment, "to_requester": True},
    )
    return _send_to_requester(request, user, step, comment)


def _send_to_requester(request, actor, from_step, comment):
    request.status = Request.RETURNED
    request.returned_by = actor
    request.returned_from_step = from_step if from_step.pk else None
    request.returned_at = timezone.now()
    request.metadata = dict(request.metadata or {})
    request.metadata["returned_from_step_key"] = _step_key(from_step)
    request.current_step = None
    request.current_step_started_at = None
    request.metadata["current_step_key"] = None
    request.save(update_fields=[
        "status", "returned_by", "returned_from_step", "returned_at",
        "current_step", "current_step_started_at", "metadata", "updated_at",
    ])
    log_activity(
        request, RequestActivity.STATUS_CHANGED, actor=actor,
        message="Request returned to the requester for revision.",
    )
    _notify_requester_returned(request, actor, comment)
    try:
        workflow_emails.send_request_returned_email(request, actor, comment)
    except Exception:
        logger.exception("Workflow return email failed for %s", request.reference)
    return request


@transaction.atomic
def resubmit(request, user, *, subject=None, body=None, comment="", form_data=None):
    """Requester revises and resubmits a returned request."""
    if request.requester_id != user.pk:
        raise PermissionDenied("Only the requester can resubmit this request.")
    if request.status != Request.RETURNED:
        raise ValidationError("Only returned requests can be resubmitted.")

    changes = {}
    if subject is not None and subject.strip() and subject.strip() != request.subject:
        changes["subject"] = {"old": request.subject, "new": subject.strip()}
        request.subject = subject.strip()
    if body is not None and body.strip() and body.strip() != request.body:
        changes["body"] = {"old_len": len(request.body), "new_len": len(body.strip())}
        request.body = body.strip()
    if form_data is not None:
        changes["form_data"] = {"old_keys": list((request.form_data or {}).keys()),
                                "new_keys": list(form_data.keys())}
        request.form_data = form_data

    # Determine where to send it back to.
    from_step_key = request.metadata.get("returned_from_step_key")
    target_step = None
    for s in build_runtime_steps(request):
        if _step_key(s) == from_step_key:
            target_step = s
            break

    steps, handoff_index = _build_runtime_chain(request)
    entered_step = target_step if target_step is not None else steps[0]
    _set_current_step(request, entered_step)

    # Review L3: status follows the step (receiving-department steps are
    # "awaiting receiving", fulfilment is "in progress"), as when the chain
    # first reached it. It used to always be AWAITING.
    request.status = _status_for_step(
        entered_step, _position_of_step(request, entered_step), handoff_index,
    )
    request.returned_by = None
    request.returned_from_step = None
    request.returned_at = None
    request.revision_count += 1
    request.save(update_fields=[
        "subject", "body", "form_data", "status",
        "returned_by", "returned_from_step", "returned_at",
        "revision_count", "updated_at",
    ])

    log_activity(
        request, RequestActivity.STATUS_CHANGED, actor=user,
        message="Request revised and resubmitted by requester.",
        metadata={
            "changes": changes,
            "revision_count": request.revision_count,
            "returned_to_step": from_step_key,
        },
    )

    target_actor = resolve_approver(target_step, request) if target_step else None
    if target_actor is not None:
        _notify_step_actor(request, target_step, target_actor)
        notify(
            recipient=target_actor,
            title=f"Revised: {request.reference}",
            body=f"{request.requester.get_full_name() or request.requester.email} resubmitted with changes.",
            url=f"/workflow/{request.pk}/",
            category=Notification.REQUEST,
            level=Notification.INFO,
            actor=user,
        )
    return request

# ===========================================================================
# Rejection
# ===========================================================================
@transaction.atomic
def reject_step(request, user, comment=""):
    if not (comment or "").strip():
        raise ValidationError("A comment is required when rejecting a request.")
    step, actor = _ensure_actor(request, user)
    now = timezone.now()
    duration = 0
    if request.current_step_started_at:
        duration = int((now - request.current_step_started_at).total_seconds())

    RequestApproval.objects.create(
        request=request, step=_db_step(step),
        actor=user, actor_role=user.role.name if user.role_id else "",
        decision=RequestApproval.REJECT,
        comment=comment,
        duration_seconds=duration,
    )
    log_activity(
        request, RequestActivity.REJECTED, actor=user,
        message=f"Rejected at step: {step.name}",
        metadata={"step": _step_key(step), "comment": comment},
    )

    request.status = Request.REJECTED
    request.closed_at = now
    request.save(update_fields=["status", "closed_at", "updated_at"])
    _notify_requester_rejected(request, user, comment)
    try:
        workflow_emails.send_request_rejected_email(request, user, comment)
    except Exception:
        logger.exception("Workflow rejection email failed for %s", request.reference)
    # Review M16: nothing was repaired, so the asset keeps its damaged state.
    _close_linked_asset_issues(request, user, note="Request rejected.", restore_state=None)
    return request


# ===========================================================================
# Cancellation & deletion
# ===========================================================================
@transaction.atomic
def cancel_request(request, user, comment=""):
    if request.requester_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the requester can cancel this request.")
    if request.is_closed:
        raise ValidationError("This request is already closed.")
    request.status = Request.CANCELLED
    request.closed_at = timezone.now()
    request.save(update_fields=["status", "closed_at", "updated_at"])
    log_activity(
        request, RequestActivity.CANCELLED, actor=user,
        message=comment or "Cancelled by requester.",
    )
    _close_linked_asset_issues(request, user, note="Request cancelled.", restore_state=None)
    return request


@transaction.atomic
def delete_request(request, user, reason=""):
    if not reason or len(reason.strip()) < 5:
        raise ValidationError("A reason is required to delete a request.")
    if request.requester_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the requester or a superuser can delete this request.")
    # Review M16: completed requests are official records, and approved
    # leave is reflected in attendance - requesters may not delete them.
    if not user.is_superuser:
        if request.status == Request.COMPLETED:
            raise ValidationError("Completed requests cannot be deleted.")
        from leave.services import APPROVED_REQUEST_STATUSES
        if (request.request_type.code == "annual-leave"
                and request.status in APPROVED_REQUEST_STATUSES):
            raise ValidationError(
                "Approved leave cannot be deleted. Ask an approver if it must be withdrawn."
            )

    notify_targets = set()
    notify_targets.add(request.requester_id)
    current_step = _resolve_current_step(request)
    if current_step is not None:
        current_actor = resolve_approver(current_step, request)
        if current_actor is not None:
            notify_targets.add(current_actor.pk)
    if request.receiving_department_id:
        from accounts.models import User as _User
        directors = _User.objects.filter(
            is_active=True,
            department_id=request.receiving_department_id,
            role__code="director",
        ).values_list("pk", flat=True)
        notify_targets.update(directors)
    if request.assigned_to_id:
        notify_targets.add(request.assigned_to_id)
    notify_targets.discard(user.pk)

    log_activity(
        request, "deleted", actor=user,
        message=f"Request deleted by {user.get_full_name() or user.email}.",
        metadata={"reason": reason},
    )

    from accounts.models import User as _User
    for uid in notify_targets:
        target = _User.objects.filter(pk=uid, is_active=True).first()
        if target is None:
            continue
        notify(
            recipient=target,
            title=f"Request {request.reference} was deleted",
            body=(f"Deleted by {user.get_full_name() or user.email}. Reason: {reason.strip()}"),
            url="/workflow/",
            category=Notification.REQUEST,
            level=Notification.WARNING,
            actor=user,
            metadata={"deleted_request_id": request.pk, "reference": request.reference, "reason": reason},
        )

    request.delete()
    return request


# ===========================================================================
# Comments, attachments
# ===========================================================================
def add_comment(request, user, body, internal=False):
    # Review M2: internal notes are hidden from the requester, so a
    # requester cannot write one, and the timeline never carries their text.
    if internal and request.requester_id == user.pk:
        internal = False
    comment = RequestComment.objects.create(
        request=request, author=user, body=body.strip(), is_internal=internal,
    )
    log_activity(
        request, RequestActivity.COMMENTED, actor=user,
        message="Internal note added." if internal else body[:200],
        metadata={"internal": internal, "comment_id": comment.pk},
    )
    return comment


def add_attachment(request, user, file, description=""):
    attachment = RequestAttachment.objects.create(
        request=request, uploaded_by=user, file=file,        # ← uploaded_by, not user
        original_name=getattr(file, "name", "") or "",
        description=description,
    )
    log_activity(
        request, RequestActivity.ATTACHED, actor=user,
        message=f"Attached {attachment.original_name}",
        metadata={"attachment_id": attachment.pk},
    )
    return attachment

# ===========================================================================
# Completion
# ===========================================================================
@transaction.atomic
def _finalise(request, actor=None, note=""):
    request.status = Request.COMPLETED
    request.completed_at = timezone.now()
    request.closed_at = timezone.now()
    request.save(update_fields=["status", "completed_at", "closed_at", "updated_at"])
    log_activity(
        request, RequestActivity.COMPLETED, actor=actor,
        message=note or "Request completed.",
    )

    # Resolve any linked asset issues.
    _close_linked_asset_issues(request, actor, note="Request completed.")

    try:
        from .pdf import generate_request_record
        generate_request_record(request)
        log_activity(
            request, RequestActivity.PDF_GENERATED, actor=None,
            message="Record PDF generated.",
        )
    except Exception:
        logger.exception("Could not generate record PDF for %s", request.reference)

    notify(
        recipient=request.requester,
        title=f"Request {request.reference} completed",
        body=request.subject,
        url=f"/workflow/{request.pk}/",
        category=Notification.REQUEST,
        level=Notification.SUCCESS,
        actor=actor,
    )
    try:
        workflow_emails.send_request_completed_email(request)
    except Exception:
        logger.exception("Workflow completion email failed for %s", request.reference)
    return request


def _close_linked_asset_issues(request, actor, note="", restore_state="in_service"):
    """Resolve every open AssetIssue linked to this request and restore the
    asset to `restore_state` (None = leave the asset's state unchanged)."""
    try:
        from assets import services as asset_services
    except ImportError:
        return
    for issue in request.asset_issues.filter(resolved_at__isnull=True):
        try:
            asset_services.resolve_issue(
                issue=issue,
                resolved_by=actor,
                resolution_notes=note,
                restore_state=restore_state,
            )
        except Exception:
            logger.exception("Could not resolve asset issue %s", issue.pk)

# ===========================================================================
# Notifications
# ===========================================================================
def _notify_step_actor(request, step, actor):
    if actor is None:
        return
    notify(
        recipient=actor,
        title=f"Approval needed — {request.reference}",
        body=f"{request.subject} · step: {step.name}",
        url=f"/workflow/{request.pk}/",
        category=Notification.REQUEST,
        level=Notification.WARNING,
        actor=request.requester,
    )


def _notify_handoff(request):
    """Notify the receiving department's director that a request has arrived."""
    if request.receiving_department_id is None:
        return
    from accounts.models import User
    director = (
        User.objects.filter(
            is_active=True,
            department_id=request.receiving_department_id,
            role__code="director",
        ).order_by("pk").first()
    )
    if director is None:
        return
    notify(
        recipient=director,
        title=f"Incoming: {request.reference}",
        body=f"{request.subject} (from {request.requester.get_full_name() or request.requester.email})",
        url=f"/workflow/{request.pk}/",
        category=Notification.REQUEST,
        level=Notification.WARNING,
    )


def _notify_assignee(request, assignee, assigner):
    notify(
        recipient=assignee,
        title=f"You've been assigned — {request.reference}",
        body=request.subject,
        url=f"/workflow/{request.pk}/",
        category=Notification.REQUEST,
        level=Notification.INFO,
        actor=assigner,
    )


def _notify_requester_returned(request, actor, comment):
    notify(
        recipient=request.requester,
        title=f"Returned for revision — {request.reference}",
        body=comment or "Please review and resubmit.",
        url=f"/workflow/{request.pk}/",
        category=Notification.REQUEST,
        level=Notification.WARNING,
        actor=actor,
    )


def _notify_requester_rejected(request, actor, comment):
    notify(
        recipient=request.requester,
        title=f"Rejected — {request.reference}",
        body=comment or "Your request was rejected.",
        url=f"/workflow/{request.pk}/",
        category=Notification.REQUEST,
        level=Notification.ERROR,
        actor=actor,
    )


def _notify_admins_no_actor(request, step):
    from accounts.models import User
    for admin in User.objects.filter(is_active=True, is_superuser=True):
        notify(
            recipient=admin,
            title=f"No one matches a workflow step — {request.reference}",
            body=f"Step '{step.name}' has no acting user. Configure a role or user.",
            url=f"/workflow/{request.pk}/",
            category=Notification.SYSTEM,
            level=Notification.ERROR,
        )
        
        
def _can_delete_content(actor, owner):
    """Shared permission check for deleting a comment or attachment.

    Allowed for:
      - the owner themselves,
      - the director of the owner's department,
      - a superuser.

    Fails closed on any missing party.
    """
    if actor is None or owner is None:
        return False
    if actor.is_superuser:
        return True
    if actor.pk == owner.pk:
        return True
    if (actor.department_id
            and actor.department_id == owner.department_id
            and actor.role_id
            and actor.role.code == "director"):
        return True
    return False

def delete_comment(request, user, comment):
    """Delete a comment. Author, the author's department director, or
    a superuser. Logged before deletion so the audit trail survives."""
    if not _can_delete_content(user, comment.author):
        raise PermissionDenied("You cannot delete this comment.")
    log_activity(
        request, RequestActivity.COMMENTED, actor=user,
        message=f"Comment deleted by {user.get_full_name() or user.email}.",
        metadata={
            "action": "comment_deleted",
            "comment_id": comment.pk,
            "author_id": comment.author_id,
            "body_excerpt": (comment.body or "")[:120],
        },
    )
    comment.delete()


def delete_attachment(request, user, attachment):
    """Delete an attachment. Uploader, the uploader's department director,
    or a superuser. The file is removed from storage too."""
    if not _can_delete_content(user, attachment.uploaded_by):
        raise PermissionDenied("You cannot delete this attachment.")
    log_activity(
        request, RequestActivity.ATTACHED, actor=user,
        message=(f"Attachment '{attachment.original_name}' deleted by "
                 f"{user.get_full_name() or user.email}."),
        metadata={
            "action": "attachment_deleted",
            "attachment_id": attachment.pk,
            "uploader_id": attachment.uploaded_by_id,
            "original_name": attachment.original_name,
        },
    )
    try:
        attachment.file.delete(save=False)
    except Exception:
        logger.exception("Could not delete file for attachment %s", attachment.pk)
    attachment.delete()        
        
        
        

# ===========================================================================
# Phase 9 API: the raise / resubmit sequences of workflow.views (annual-leave
# validation + leave sync around the service call), shared with the mobile
# API so both paths run the same checks.
# ===========================================================================
LEAVE_TYPE_CODE = "annual-leave"


def raise_request_checked(*, requester, request_type, subject, body, priority, form_data):
    if request_type.code == LEAVE_TYPE_CODE:
        from leave.services import validate_application
        validate_application(requester, form_data)
    with transaction.atomic():
        req = raise_request(requester=requester, request_type=request_type, subject=subject,
                            body=body, priority=priority, form_data=form_data)
        if request_type.code == LEAVE_TYPE_CODE:
            from leave.services import sync_application
            sync_application(req, form_data)
    return req


def resubmit_checked(req, user, *, subject, body, comment="", form_data=None, file=None,
                     file_description=""):
    if req.request_type.code == LEAVE_TYPE_CODE:
        from leave.services import validate_application
        validate_application(user, form_data, exclude_request_id=req.pk)
    with transaction.atomic():
        resubmit(req, user, subject=subject, body=body, comment=comment, form_data=form_data)
        if req.request_type.code == LEAVE_TYPE_CODE:
            from leave.services import sync_application
            sync_application(req, form_data)
        if file:
            add_attachment(req, user, file=file, description=file_description or "")
    return req
