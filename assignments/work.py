"""WorkAssignment lifecycle (P.1/P.5; spec_v21 G1.d, G6).

    assign(institution, kind, assignee, *, subject=None, department=None, assigned_by=None, ...)
    accept(wa, actor) / decline(wa, actor, reason)
    start(wa, actor) / submit(wa, actor) / return_to_assignee(wa, actor, remark)
    complete(wa, actor)      -> locks the step (X7) and revokes its grants (X5)
    cancel(wa, actor, reason="withdrawn") / reassign(wa, new_assignee, actor)

Grants (X5): when work is taken on (accepted / in progress) the assignee gets
the grants of ``qa.access.kind_scopes`` for the kind ("documents:subject"
limits the grant to the assigned record); they are revoked in the same
request when the work completes, is cancelled, declined or reassigned.
Every change is audited (``core.audit.log``) and bumps the access cache.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from . import grants, locks
from .models import AccessGrant, WorkAssignment

WA = WorkAssignment


def _audit(action, wa, actor, changes=None, note=""):
    from core import audit
    audit.log(action, wa, actor=actor, changes=changes or {}, note=note or "")


def _ct(obj):
    from django.contrib.contenttypes.models import ContentType
    return ContentType.objects.get_for_model(obj, for_concrete_model=False)


def _bump(wa):
    from .access import bump_global_version
    grants.bump_version(wa.assignee_id)
    bump_global_version()


def is_top(actor):
    """ES and super admin assign anyone, in any department (X4)."""
    import org
    return bool(actor is not None and getattr(actor, "is_authenticated", False) and actor.is_active
                and (actor.is_superuser or org.is_es(actor) or org.is_super_admin(actor)))


def in_department(user, department):
    """Active staff member of ``department`` (User.department or the staff profile's)."""
    dept_id = getattr(department, "pk", department)
    if not dept_id or user is None or not user.is_active or getattr(user, "user_type", "staff") != "staff":
        return False
    if user.department_id == dept_id:
        return True
    profile = getattr(user, "staff_profile", None) if hasattr(user, "staff_profile") else None
    return bool(profile is not None and getattr(profile, "department_id", None) == dept_id)


def has_delegated_assign(actor):
    """The director granted ``assignments.assign_work`` to this person
    (org.grant_permission, X31). Holding it through a role does not count."""
    from accounts.models import UserPermissionOverride
    rows = UserPermissionOverride.objects.filter(
        user=actor, effect=UserPermissionOverride.GRANT, permission__codename="assign_work",
        permission__content_type__app_label="assignments")
    return any(r.is_current for r in rows)


def can_assign(actor, department):
    """The director of the department, a staff member of it the director
    delegated ``assignments.assign_work`` to, the ES or super admin. Nobody
    else — holding the permission through a role is not a delegation."""
    if actor is None:
        return True  # the system (rotation auto-assign, back-fill, seeds)
    if is_top(actor):
        return True
    import org
    dept_id = getattr(department, "pk", department)
    if not dept_id:
        return False
    if org.is_director(actor, dept_id):
        return True
    return bool(in_department(actor, dept_id) and has_delegated_assign(actor))


def check_assignee(actor, department, assignee):
    """Server-side rule for every assign / reassign / override (X4): below the
    ES and super admin the assignee must be active staff of the department
    the actor may assign for."""
    if assignee is None or not assignee.is_active:
        raise ValidationError("Choose an active person.")
    if actor is None or is_top(actor):
        return
    if not can_assign(actor, department):
        raise PermissionDenied("Only the director of the department (or a delegate) can assign this work.")
    if not in_department(assignee, department):
        raise PermissionDenied("You can only assign work to active staff of your own department.")


@transaction.atomic
def assign(institution, kind, assignee, *, subject=None, department=None, assigned_by=None, due_at=None,
           role_on_work="", payable=False, meta=None, division=None, accept_now=False, notify_assignee=True):
    if kind not in WA.KINDS:
        raise ValidationError(f"Unknown kind of work: {kind}")
    if assignee is None or not assignee.is_active:
        raise ValidationError("Choose an active person.")
    department = department or getattr(assignee, "department", None)
    if not can_assign(assigned_by, department):
        raise PermissionDenied("Only the director of the department (or a delegate) can assign this work.")
    check_assignee(assigned_by, department, assignee)
    subject = subject if subject is not None else institution
    existing = (WA.objects.for_subject(subject).filter(kind=kind, assignee=assignee, status__in=WA.OPEN).first())
    if existing is not None:
        if meta and any(existing.meta.get(k) != v for k, v in meta.items()):
            existing.meta = {**existing.meta, **meta}  # e.g. visit dates moved (meta "from"/"to")
            existing.save(update_fields=["meta"])
        return existing
    now = timezone.now()
    wa = WA.objects.create(
        institution=institution, kind=kind, subject_ct=_ct(subject), subject_id=str(subject.pk),
        department=department, division=division, assignee=assignee, role_on_work=role_on_work,
        status=WA.OFFERED, assigned_by=assigned_by if getattr(assigned_by, "pk", None) else None,
        assigned_at=now, due_at=due_at, payable=payable, meta=meta or {})
    _audit("work_assigned", wa, assigned_by, {"kind": kind, "assignee": assignee.pk,
                                              "subject": f"{wa.subject_ct.model}:{wa.subject_id}"})
    if notify_assignee:
        transaction.on_commit(lambda: _notify_assigned(wa))
    if accept_now:
        accept(wa, assignee)
    else:
        _bump(wa)
    return wa


def _notify_assigned(wa):
    try:
        from notifications.services import notify
        from django.urls import reverse
        due = f" Due {timezone.localtime(wa.due_at):%d %b %Y}." if wa.due_at else ""
        notify(wa.assignee, title=f"New work: {wa.get_kind_display()}",
               body=f"{wa.institution.name}. {wa.subject_label}.{due}".replace(". .", "."),
               url=reverse("assignments:my_work"), actor=wa.assigned_by)
    except Exception:  # pragma: no cover
        import logging
        logging.getLogger(__name__).exception("Could not notify assignee")


def _set(wa, actor, status, action, **fields):
    old = wa.status
    wa.status = status
    for k, v in fields.items():
        setattr(wa, k, v)
    wa.save()
    _audit(action, wa, actor, {"status": {"old": old, "new": status}})
    _bump(wa)
    return wa


def _require_open(wa):
    if not wa.is_open:
        raise ValidationError("This work is already closed.")


def open_grants(wa):
    """Create the grants the kind gives (idempotent)."""
    from .access import kind_scopes, parse_scope
    out = []
    for spec in kind_scopes(wa.kind):
        scope, limit = parse_scope(spec)
        if scope not in grants.GRANT_SCOPES:
            continue  # carried by the assignment itself ("case", "visits"…)
        if wa.kind in WA.FULL_JOURNEY_KINDS and scope == AccessGrant.SCOPE_FULL_JOURNEY:
            continue  # the open oversight assignment itself opens the full journey
        obj = wa.subject if (limit == "subject" and wa.subject is not None) else wa.institution
        out.append(grants.grant(
            wa.assignee, obj, scope, source=wa, institution=wa.institution, department=wa.department,
            granted_by=wa.assigned_by, reason=f"{wa.get_kind_display()} {wa.subject_label}".strip()[:255],
            notify_grantee=False))
    return out


def close_grants(wa, reason=AccessGrant.R_COMPLETED, by=None):
    return grants.revoke_for_source(wa, reason=reason, by=by)


@transaction.atomic
def accept(wa, actor):
    _require_open(wa)
    if actor is not None and actor.pk != wa.assignee_id and not can_assign(actor, wa.department):
        raise PermissionDenied("Only the assignee can accept this work.")
    if wa.status in (WA.SUGGESTED, WA.OFFERED):
        _set(wa, actor, WA.ACCEPTED, "work_accepted", accepted_at=timezone.now())
        _notify_assigner(wa, "accepted")
    open_grants(wa)
    return wa


@transaction.atomic
def decline(wa, actor, reason=""):
    _require_open(wa)
    if wa.status not in (WA.SUGGESTED, WA.OFFERED):
        raise ValidationError("Work already taken on cannot be declined; ask the director to reassign it.")
    _set(wa, actor, WA.DECLINED, "work_declined", closed_reason="declined",
         meta={**wa.meta, "decline_reason": (reason or "")[:255]})
    close_grants(wa, AccessGrant.R_REASSIGNED, actor)
    _notify_assigner(wa, "declined")
    return wa


@transaction.atomic
def start(wa, actor=None):
    _require_open(wa)
    if wa.status in (WA.SUGGESTED, WA.OFFERED):
        accept(wa, actor)
    if wa.status == WA.ACCEPTED:
        _set(wa, actor, WA.IN_PROGRESS, "work_started")
    return wa


@transaction.atomic
def submit(wa, actor):
    _require_open(wa)
    start(wa, actor)
    return _set(wa, actor, WA.SUBMITTED, "work_submitted", submitted_at=timezone.now())


@transaction.atomic
def return_to_assignee(wa, actor, remark):
    _require_open(wa)
    if not (remark or "").strip():
        raise ValidationError("Say what needs to change.")
    _set(wa, actor, WA.RETURNED, "work_returned", meta={**wa.meta, "return_remark": remark[:500]})
    open_grants(wa)
    return wa


@transaction.atomic
def complete(wa, actor=None):
    """Close the work: completed, locked (X7), grants revoked now (X5)."""
    _require_open(wa)
    _set(wa, actor, WA.COMPLETED, "work_completed", completed_at=timezone.now(), closed_reason="completed")
    close_grants(wa, AccessGrant.R_COMPLETED, actor)
    locks.lock(wa, actor)
    if wa.payable:
        from honoraria.schedules import accrue_assignment

        accrue_assignment(wa)
    return wa


@transaction.atomic
def cancel(wa, actor=None, reason="withdrawn"):
    _require_open(wa)
    _set(wa, actor, WA.CANCELLED, "work_cancelled", closed_reason=reason)
    close_grants(wa, AccessGrant.R_REASSIGNED if reason == "reassigned" else AccessGrant.R_COMPLETED, actor)
    return wa


@transaction.atomic
def reassign(wa, new_assignee, actor):
    if not can_assign(actor, wa.department):
        raise PermissionDenied("Only the director of the department (or a delegate) can reassign this work.")
    check_assignee(actor, wa.department, new_assignee)
    cancel(wa, actor, "reassigned")
    new = assign(wa.institution, wa.kind, new_assignee, subject=wa.subject, department=wa.department,
                 assigned_by=actor, due_at=wa.due_at, role_on_work=wa.role_on_work, payable=wa.payable,
                 meta={**wa.meta, "reassigned_from": wa.pk}, division=wa.division)
    return new


def ensure(institution, kind, assignee, *, subject=None, department=None, assigned_by=None,
           notify_assignee=True, **kw):
    """Open-and-accepted assignment for a person on a subject (hooks from
    existing services: SSQ reviewer chosen, complaint assigned…).  Hooks are
    not a reason to stay silent: the assignee is told about the work like any
    other assignment (X: "assigned means notified")."""
    wa = assign(institution, kind, assignee, subject=subject, department=department, assigned_by=assigned_by,
                notify_assignee=notify_assignee, **kw)
    if wa.status in (WA.SUGGESTED, WA.OFFERED):
        accept(wa, None)
    return wa


def close_for_subject(subject, *, kind=None, actor=None, keep=None, reason="completed"):
    """Close every open assignment on ``subject`` (optionally of one kind,
    except the person ``keep``)."""
    qs = WA.objects.for_subject(subject).filter(status__in=WA.OPEN)
    if kind:
        qs = qs.filter(kind=kind)
    if keep is not None:
        qs = qs.exclude(assignee=keep)
    n = 0
    for wa in qs:
        if reason == "completed":
            complete(wa, actor)
        else:
            cancel(wa, actor, reason)
        n += 1
    return n


def _notify_assigner(wa, what):
    if wa.assigned_by_id is None:
        return
    try:
        from notifications.services import notify
        notify(wa.assigned_by, title=f"Work {what}: {wa.get_kind_display()}",
               body=f"{wa.assignee.get_full_name() or wa.assignee.email} {what} the work on "
                    f"{wa.institution.name}.", actor=wa.assignee)
    except Exception:  # pragma: no cover
        pass


def my_open_work(user):
    return (WA.objects.filter(assignee=user, status__in=WA.OPEN)
            .select_related("institution", "department", "subject_ct").order_by("due_at", "-assigned_at"))
