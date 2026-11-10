"""Two-step confirmation (Master Plan §7): action → pending → a second
officer confirms → applied.

Whether an action needs a second officer, who may initiate and confirm, the
minimum confirmer rank, the department rule and the expiry are
:class:`~qa_core.models.ActionPolicy` **data**. What an action *does* is a
registered :class:`ActionHandler` — the only part in code.

Hard rules (not configurable — they are the point of the control):

* the confirmer is not the initiator — superusers included;
* the confirmer is not impersonating anyone;
* the confirmer holds the confirm permission, in scope (same department
  unless they hold ``qa_core.confirm_any_department``), at or above the
  policy's minimum rank;
* the change is applied inside ``transaction.atomic()`` with the target
  locked, re-validated, and only if the target has not changed since
  initiation ("stale — please re-initiate"); it is never applied over newer
  data;
* one open pending action per (action, target) at a time.

Three layers (v2.1 X47) for the TVET Support groups (``institutions.tvet_access``):
``qa.tvet_support.<group>.layers`` = 1 (apply at once), 2 (initiate + confirm,
default) or 3 (initiate + confirm + approve). The confirmer and approver are
different people, never the initiator, of the same or higher rank; a director
approves only items initiated in their own department unless they are the
configured approver. ``.confirmers`` / ``.approvers`` choose who may act:
``{"mode": "permission"}`` (default), ``{"mode": "users", "emails": [...]}``,
``{"mode": "group", "name": "..."}`` or ``{"mode": "role", "codes": [...]}``;
``.approver_is_director`` (bool) requires the initiator's own director. The ES
and super admin apply their own changes at once (audited) and may confirm or
approve anything.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core import audit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
@dataclass
class DiffRow:
    label: str
    before: str
    after: str

    @property
    def changed(self):
        return self.before != self.after


class ActionHandler:
    """Subclass and register. `target_model` may be None (e.g. Add TVET)."""

    code = ""
    target_model = None

    def target_label(self, target):
        return str(target) if target is not None else ""

    def payload_label(self, payload):
        """Label for actions without a target (e.g. Add TVET: the new name)."""
        return ""

    def snapshot(self, target):
        return {}

    def check_initiator(self, target, payload, actor):
        """Extra initiator checks beyond the policy permission (raise PermissionDenied)."""

    def validate(self, target, payload):
        """Raise ValidationError if the change is not acceptable. Re-run at confirm time."""

    def preview(self, target, payload):
        """Return a list of DiffRow for the confirm page."""
        return []

    def apply(self, target, payload, actor):
        raise NotImplementedError

    def on_closed(self, pa, target, actor):
        """Called when a pending item is rejected, withdrawn, expired or fails to apply (Phase 2:
        e.g. a refund request is declined). Must not raise."""

    def scope_department(self, target, initiator):
        """The department whose officers see and confirm this item."""
        institution = getattr(target, "institution", None) if target is not None else None
        if institution is not None and getattr(institution, "handling_department_id", None):
            return institution.handling_department
        if target is not None and hasattr(target, "handling_department_id"):
            return target.handling_department
        return getattr(initiator, "department", None)


_REGISTRY = {}


def register(handler_cls):
    handler = handler_cls()
    if not handler.code:
        raise ValueError("ActionHandler.code is required")
    _REGISTRY[handler.code] = handler
    return handler_cls


def get_handler(code):
    try:
        return _REGISTRY[code]
    except KeyError:
        raise ValidationError(f"No handler is registered for '{code}'.") from None


def registered_codes():
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _version_of(target):
    value = getattr(target, "updated_at", None)
    return value.isoformat() if value is not None else ""


def _expiry_hours(policy):
    import config

    if policy.expires_after_hours:
        return policy.expires_after_hours
    try:
        return int(config.get("qa.settings.pending_action_expiry_hours", 72))
    except (TypeError, ValueError):
        return 72


def _rank(user):
    role = getattr(user, "role", None)
    return getattr(role, "rank", 0) or 0


# ---------------------------------------------------------------------------
# X47: TVET Support groups — layers, who may confirm / approve, rank rules
# ---------------------------------------------------------------------------
def _group(code):
    try:
        from institutions.tvet_access import group_of
    except Exception:  # pragma: no cover
        return None
    return group_of(code)


def _cfg(key, default):
    import json

    import config
    value = config.get(key, None)
    if value in (None, ""):
        return default
    if isinstance(default, dict) and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def layers_for(policy):
    """1, 2 or 3 layers for ``policy`` (TVET Support groups read their setting)."""
    group = _group(policy.action_code)
    if group is None:
        return 2 if policy.requires_second_officer else 1
    try:
        n = int(_cfg(f"qa.tvet_support.{group}.layers", 2))
    except (TypeError, ValueError):
        n = 2
    return min(3, max(1, n))


def _is_top(user):
    import org
    return bool(user is not None and getattr(user, "is_authenticated", False) and user.is_active
                and (user.is_superuser or org.is_es(user) or org.is_super_admin(user)))


def approve_permission(policy):
    perm = policy.initiate_permission or ""
    return perm.replace(".initiate_", ".approve_", 1) if ".initiate_" in perm else ""


def _chosen(pa, user, step):
    """(configured_explicitly, allowed) for ``step`` ("confirmers" / "approvers")."""
    group = _group(pa.policy_id)
    spec = _cfg(f"qa.tvet_support.{group}.{step}", {"mode": "permission"}) if group else {"mode": "permission"}
    mode = str((spec or {}).get("mode", "permission"))
    if mode == "users":
        emails = {str(e).lower() for e in spec.get("emails", [])}
        return True, (user.email or "").lower() in emails
    if mode == "group":
        return True, user.groups.filter(name=spec.get("name", "")).exists()
    if mode == "role":
        return True, bool(user.role_id and user.role.code in set(spec.get("codes", [])))
    perm = pa.policy.confirm_permission if step == "confirmers" else approve_permission(pa.policy)
    return False, bool(not perm or user.has_perm(perm))


def _target_queryset_for_lock(target):
    model = type(target)
    manager = getattr(model, "unscoped", None) or model._base_manager
    return manager.select_for_update()


def _load_target(pa):
    if not pa.content_type_id:
        return None
    model = pa.content_type.model_class()
    manager = getattr(model, "unscoped", None) or model._base_manager
    return manager.filter(pk=pa.object_id).first()


# ---------------------------------------------------------------------------
# Visibility (scoping for the Pending confirmations pages)
# ---------------------------------------------------------------------------
def visible_to(user):
    """Pending actions `user` may see: their own, plus those in their
    department they could confirm or are allowed to view. Everything for
    superusers and cross-department holders. Fails closed."""
    from .models import ActionPolicy, PendingAction

    qs = PendingAction.objects.select_related(
        "policy", "initiated_by", "decided_by", "scope_department", "content_type")
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return qs.none()
    if (user.is_superuser or _is_top(user) or user.has_perm("accounts.access_all_departments")
            or user.has_perm("qa_core.confirm_any_department")):
        return qs
    mine = Q(initiated_by=user)
    # X47: items waiting for this person's confirmation or approval, wherever they sit.
    tvet_codes = [p.action_code for p in ActionPolicy.objects.all() if _group(p.action_code) is not None
                  and ((p.confirm_permission and user.has_perm(p.confirm_permission))
                       or (approve_permission(p) and user.has_perm(approve_permission(p))))]
    mine |= Q(policy_id__in=tvet_codes, status=PendingAction.PENDING)
    if not user.department_id:
        return qs.filter(mine)
    confirmable = [p.action_code for p in ActionPolicy.objects.all()
                   if p.confirm_permission and user.has_perm(p.confirm_permission)]
    in_dept = Q(scope_department_id=user.department_id)
    if user.has_perm("qa_core.view_pending_actions"):
        return qs.filter(mine | in_dept)
    return qs.filter(mine | (in_dept & Q(policy_id__in=confirmable)))


def eligible_confirmers(pa):
    """Users who could confirm `pa` right now (for notifications)."""
    from accounts.models import User

    policy = pa.policy
    candidates = User.objects.filter(is_active=True, user_type="staff").select_related("role")
    if policy.same_department_required and pa.scope_department_id:
        candidates = candidates.filter(
            Q(department_id=pa.scope_department_id) | Q(is_superuser=True)
            | Q(role__permissions__codename="access_all_departments")
        ).distinct()
    out = []
    for user in candidates[:500]:
        ok, _reason = can_decide(pa, user)
        if ok:
            out.append(user)
    return out


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
def can_decide(pa, user, *, impersonating=False):
    """(allowed: bool, reason: str) for confirming or rejecting `pa`."""
    policy = pa.policy
    if pa.status != pa.PENDING:
        return False, f"This item is already {pa.get_status_display().lower()}."
    if pa.expires_at and pa.expires_at <= timezone.now():
        return False, "This item has expired. Ask the initiator to start it again."
    if user is None or not user.is_authenticated or not user.is_active:
        return False, "Sign in to decide."
    if getattr(user, "user_type", "staff") != "staff":
        return False, "Only NBTE staff can confirm changes."
    if impersonating:
        return False, "You cannot confirm while impersonating another user."
    if user.pk == pa.initiated_by_id:
        return False, "You started this change, so a different officer must confirm it."
    if _group(pa.policy_id) is not None:
        return _can_decide_tvet(pa, user)
    if policy.confirm_permission and not user.has_perm(policy.confirm_permission):
        return False, "You do not hold the permission needed to confirm this change."
    if not user.is_superuser and _rank(user) < policy.confirmer_min_rank:
        return False, f"Confirming this needs rank {policy.confirmer_min_rank} or above."
    if (policy.same_department_required and pa.scope_department_id
            and not user.is_superuser
            and not user.has_perm("qa_core.confirm_any_department")
            and user.department_id != pa.scope_department_id):
        return False, "This change belongs to another department."
    return True, ""


def _can_decide_tvet(pa, user):
    """X47 rules for the confirm and approve layers (initiator already excluded)."""
    import org
    approving = pa.stage == "approve"
    verb = "approve" if approving else "confirm"
    if approving and user.pk == pa.confirmed_by_id:
        return False, "You confirmed this change, so a different officer must approve it."
    if _is_top(user):
        return True, ""
    explicit, allowed = _chosen(pa, user, "approvers" if approving else "confirmers")
    if not allowed:
        return False, f"You are not allowed to {verb} this change."
    initiator = pa.initiated_by
    if _rank(user) < _rank(initiator):
        return False, f"Only someone of the same or higher rank than the initiator can {verb} this change."
    if approving:
        group = _group(pa.policy_id)
        if str(_cfg(f"qa.tvet_support.{group}.approver_is_director", "false")).lower() in ("1", "true", "yes"):
            director = org.director_of(initiator.department_id) if initiator.department_id else None
            if director is None or director.pk != user.pk:
                return False, "This change must be approved by the initiator's own director."
        elif not explicit and org.is_director(user) and not org.is_director(user, initiator.department_id):
            return False, "A director approves only changes started in their own department."
    return True, ""


# ---------------------------------------------------------------------------
# Review data (the confirm page and the mobile API read the same thing)
# ---------------------------------------------------------------------------
def snapshot_before(pa, row):
    """The value a decided item replaced (from its before-snapshot)."""
    if row.label == "Value" and "value" in pa.before_snapshot:
        from . import settings_service

        return settings_service.display_value(pa.before_snapshot.get("value_type", "str"),
                                              pa.before_snapshot["value"])
    key = row.label.lower().replace(" ", "_")
    return str(pa.before_snapshot.get(key, row.before))


def review(pa):
    """{"target", "diff" (DiffRow list), "stale", "confirm_options"} for one
    pending action, exactly as the Pending confirmations page shows it."""
    handler = get_handler(pa.policy_id)
    target = _load_target(pa)
    try:
        # Create-type actions (Add TVET, bulk import) have no target yet; their
        # handlers preview from the payload alone.
        diff = handler.preview(target, pa.payload) if (target is not None or not pa.object_id) else []
    except Exception:
        diff = []
    if pa.status != pa.PENDING and pa.before_snapshot:
        # Show what was proposed against what it replaced, not today's value.
        diff = [DiffRow(row.label, snapshot_before(pa, row), row.after) for row in diff]
    stale = bool(target is not None and pa.target_version and pa.is_open
                 and _version_of(target) != pa.target_version)
    confirm_options = []
    if pa.is_open and hasattr(handler, "confirm_options"):
        try:
            confirm_options = handler.confirm_options(target, pa.payload) or []
        except Exception:
            confirm_options = []
    return {"target": target, "diff": diff, "stale": stale, "confirm_options": confirm_options}


def store_confirm_options(pa, user, chosen, *, impersonating=False):
    """Choices the confirming officer makes (a handler may offer checkboxes,
    e.g. "e-mail set-password links now"). ``chosen`` maps option name →
    bool; unknown names are ignored. Stored on the payload under
    ``confirm_options`` before applying."""
    from .models import PendingAction

    handler = get_handler(pa.policy_id)
    if not hasattr(handler, "confirm_options") or not pa.is_open:
        return
    names = [o["name"] for o in (handler.confirm_options(_load_target(pa), pa.payload) or [])]
    if not names or not can_decide(pa, user, impersonating=impersonating)[0]:
        return
    payload = dict(pa.payload or {})
    payload["confirm_options"] = {name: bool(chosen.get(name)) for name in names}
    PendingAction.objects.filter(pk=pa.pk, status=PendingAction.PENDING).update(payload=payload)
    pa.payload = payload


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def initiate(code, *, actor, target=None, payload=None, reason="", impersonating=False):
    """Start a two-step action. Returns the PendingAction (status `pending`,
    or `applied` when the policy needs no second officer)."""
    from . import references
    from .models import ActionPolicy, DemoTag, PendingAction

    payload = payload or {}
    handler = get_handler(code)
    policy = ActionPolicy.objects.filter(action_code=code).first()
    if policy is None or not policy.is_enabled:
        raise ValidationError("This action is not enabled.")
    if actor is None or not actor.is_authenticated or getattr(actor, "user_type", "staff") != "staff":
        raise PermissionDenied("Only NBTE staff can start this action.")
    if impersonating:
        raise PermissionDenied("You cannot start changes while impersonating another user.")
    if policy.initiate_permission and not actor.has_perm(policy.initiate_permission):
        raise PermissionDenied("You do not hold the permission needed to start this change.")
    handler.check_initiator(target, payload, actor)
    handler.validate(target, payload)

    ct = ContentType.objects.get_for_model(target, for_concrete_model=True) if target is not None else None
    with transaction.atomic():
        if target is not None:
            # Lock the target so two officers cannot both open a change on it.
            locked = _target_queryset_for_lock(target).filter(pk=target.pk).first()
            clash = PendingAction.objects.select_for_update().filter(
                policy=policy, content_type=ct, object_id=str(target.pk), status=PendingAction.PENDING,
            ).first()
            if clash is not None:
                raise ValidationError(
                    f"{clash.reference} is already waiting for confirmation on this item. "
                    "Withdraw it or wait for a decision.")
        now = timezone.now()
        # The version is read from the locked database row, not from the
        # object passed in (which may be an older copy of the same row).
        version_source = locked if target is not None and locked is not None else target
        pa = PendingAction(
            reference=references.issue("pending_action"),
            policy=policy,
            content_type=ct,
            object_id=str(target.pk) if target is not None else "",
            target_label=(handler.target_label(target) or handler.payload_label(payload) or "")[:255],
            payload=payload,
            before_snapshot=handler.snapshot(target) if target is not None else {},
            target_version=_version_of(version_source) if target is not None else "",
            reason=(reason or "").strip(),
            status=PendingAction.PENDING,
            scope_department=handler.scope_department(target, actor),
            initiated_by=actor,
            initiated_at=now,
            expires_at=now + datetime.timedelta(hours=_expiry_hours(policy)),
        )
        pa.save()
        # Demo activity is demo data: `seed_startup --reset-demo` removes it.
        if DemoTag.is_tagged(actor):
            DemoTag.tag(pa)
        audit.log(audit._action("state_change"), pa, actor=actor,
                  changes={"status": {"new": pa.status}}, note=f"initiated {code}")

        tvet = _group(code) is not None
        if (tvet and _is_top(actor)) or layers_for(policy) == 1 or (not tvet and not policy.requires_second_officer):
            if tvet and _is_top(actor):  # X47: the ES / super admin's own change applies at once
                audit.log(audit._action("state_change"), pa, actor=actor, changes={"layers": "skipped"},
                          note="applied at once (ES / super admin)")
            _apply(pa, actor, decided_by=actor)
            return pa

    _notify_created(pa)
    return pa


def confirm(pa, user, *, remark="", impersonating=False):
    """Confirm and apply. Returns the PendingAction (status `applied` or `failed`)."""
    from .models import PendingAction

    with transaction.atomic():
        pa = PendingAction.objects.select_for_update().select_related("policy").get(pk=pa.pk)
        ok, reason = can_decide(pa, user, impersonating=impersonating)
        if not ok:
            raise PermissionDenied(reason)
        event = getattr(_REGISTRY.get(pa.policy_id), "document_event", "")
        if event:  # v2.1 X23/X24: the confirmer signs the generated letter - ask for a signature first
            from documents.issue import precheck

            precheck(event, user)
        if pa.stage == "confirm" and layers_for(pa.policy) == 3:
            # X47: confirmed — now waiting for a third person to approve.
            pa.stage = "approve"
            pa.confirmed_by, pa.confirmed_at = user, timezone.now()
            pa.confirm_remark = (remark or "").strip()
            pa.save(update_fields=["stage", "confirmed_by", "confirmed_at", "confirm_remark", "updated_at"])
            audit.log(audit._action("state_change"), pa, actor=user,
                      changes={"stage": {"old": "confirm", "new": "approve"}}, note="confirmed; awaiting approval")
            transaction.on_commit(lambda: _notify_step(pa, "confirmed"))
            return pa
        if pa.stage == "confirm":
            pa.confirmed_by, pa.confirmed_at = user, timezone.now()
            pa.confirm_remark = (remark or "").strip()
        pa.status = PendingAction.CONFIRMED
        pa.decided_by = user
        pa.decided_at = timezone.now()
        pa.decision_remark = (remark or "").strip()
        pa.save(update_fields=["status", "decided_by", "decided_at", "decision_remark", "confirmed_by",
                               "confirmed_at", "confirm_remark", "updated_at"])
        audit.log(audit._action("state_change"), pa, actor=user,
                  changes={"status": {"old": "pending", "new": "confirmed"}},
                  note="approved" if pa.stage == "approve" else "confirmed")
        _apply(pa, user, decided_by=user)
    _notify_decided(pa)
    return pa


def approve(pa, user, *, remark="", impersonating=False):
    """The third layer (X47). Same rules and effect as ``confirm`` at the approve stage."""
    if pa.stage != "approve":
        raise ValidationError("This change is not waiting for approval.")
    return confirm(pa, user, remark=remark, impersonating=impersonating)


def trail(pa):
    """[(when, who, what)] for one pending action: initiated, confirmed, approved / rejected, applied."""
    rows = [(pa.initiated_at, pa.initiated_by, "Initiated")]
    if pa.confirmed_at:
        rows.append((pa.confirmed_at, pa.confirmed_by, "Confirmed" + (f": {pa.confirm_remark}" if pa.confirm_remark else "")))
    if pa.decided_at and pa.status in (pa.REJECTED,):
        rows.append((pa.decided_at, pa.decided_by, f"Rejected: {pa.decision_remark}"))
    elif pa.decided_at and pa.stage == "approve":
        rows.append((pa.decided_at, pa.decided_by, "Approved" + (f": {pa.decision_remark}" if pa.decision_remark else "")))
    if pa.applied_at:
        rows.append((pa.applied_at, pa.decided_by or pa.initiated_by, "Applied"))
    elif pa.status == pa.FAILED:
        rows.append((pa.updated_at, None, f"Could not be applied: {pa.failure_detail}"))
    return rows


def _apply(pa, actor, *, decided_by):
    """Apply inside the caller's transaction. Marks `applied` or `failed`."""
    from .models import PendingAction

    handler = get_handler(pa.policy_id)
    target = None
    try:
        with transaction.atomic():
            if pa.content_type_id:
                model = pa.content_type.model_class()
                manager = getattr(model, "unscoped", None) or model._base_manager
                target = manager.select_for_update().filter(pk=pa.object_id).first()
                if target is None:
                    raise ValidationError("The item no longer exists.")
                if pa.target_version and _version_of(target) != pa.target_version:
                    raise _Stale()
            handler.validate(target, pa.payload)
            handler.apply(target, pa.payload, actor)
    except _Stale:
        pa.status = PendingAction.FAILED
        pa.failure_detail = "Stale — the item changed after this was started. Please re-initiate."
    except (ValidationError, PermissionDenied) as exc:
        pa.status = PendingAction.FAILED
        pa.failure_detail = "; ".join(getattr(exc, "messages", [str(exc)]))[:2000]
    except Exception as exc:  # never leave a half-applied change
        logger.exception("Pending action %s failed to apply", pa.reference)
        pa.status = PendingAction.FAILED
        pa.failure_detail = f"Could not apply: {type(exc).__name__}."
    else:
        pa.status = PendingAction.APPLIED
        pa.applied_at = timezone.now()
        if decided_by is not None and not pa.decided_by_id:
            pa.decided_by = decided_by
            pa.decided_at = pa.applied_at
    pa.save()
    audit.log(audit._action("state_change"), pa, actor=actor,
              changes={"status": {"new": pa.status}},
              note=("applied" if pa.status == PendingAction.APPLIED else "failed")[:255])
    _timeline(pa, target, actor)
    if pa.status == PendingAction.FAILED:
        # A failed apply closes the request too (e.g. a bulk-import batch is
        # released so its officer can re-send it).
        _closed_hook(pa, actor)
    return pa


class _Stale(Exception):
    pass


def reject(pa, user, *, remark, impersonating=False):
    from .models import PendingAction

    remark = (remark or "").strip()
    if len(remark) < 3:
        raise ValidationError("Give a reason for rejecting this change.")
    with transaction.atomic():
        pa = PendingAction.objects.select_for_update().select_related("policy").get(pk=pa.pk)
        ok, reason = can_decide(pa, user, impersonating=impersonating)
        if not ok:
            raise PermissionDenied(reason)
        pa.status = PendingAction.REJECTED
        pa.decided_by = user
        pa.decided_at = timezone.now()
        pa.decision_remark = remark
        pa.save(update_fields=["status", "decided_by", "decided_at", "decision_remark", "updated_at"])
        audit.log(audit._action("state_change"), pa, actor=user,
                  changes={"status": {"old": "pending", "new": "rejected"}}, note="rejected")
        _timeline(pa, _load_target(pa), user)
        _closed_hook(pa, user)
    _notify_decided(pa)
    return pa


def _closed_hook(pa, actor):
    try:
        get_handler(pa.policy_id).on_closed(pa, _load_target(pa), actor)
    except Exception:
        logger.exception("on_closed hook failed for %s", pa.reference)


def withdraw(pa, user):
    from .models import PendingAction

    with transaction.atomic():
        pa = PendingAction.objects.select_for_update().get(pk=pa.pk)
        if pa.initiated_by_id != getattr(user, "pk", None):
            raise PermissionDenied("Only the officer who started this change can withdraw it.")
        if pa.status != PendingAction.PENDING:
            raise ValidationError("Only pending items can be withdrawn.")
        pa.status = PendingAction.WITHDRAWN
        pa.decided_at = timezone.now()
        pa.save(update_fields=["status", "decided_at", "updated_at"])
        audit.log(audit._action("state_change"), pa, actor=user,
                  changes={"status": {"old": "pending", "new": "withdrawn"}}, note="withdrawn")
        _closed_hook(pa, user)
    return pa


def expire_overdue(now=None):
    """Mark overdue pending items `expired` and tell their initiators. Run by
    Celery every 15 minutes (Master Plan §7) and safe to run any time."""
    from .models import PendingAction

    now = now or timezone.now()
    expired = []
    with transaction.atomic():
        for pa in (PendingAction.objects.select_for_update()
                   .filter(status=PendingAction.PENDING, expires_at__lte=now)):
            pa.status = PendingAction.EXPIRED
            pa.save(update_fields=["status", "updated_at"])
            audit.log(audit._action("state_change"), pa, actor=None,
                      changes={"status": {"old": "pending", "new": "expired"}}, note="expired")
            _closed_hook(pa, None)
            expired.append(pa)
    for pa in expired:
        _dispatch(pa, "pending_action.expired", {"initiator": [pa.initiated_by]})
    return len(expired)


# ---------------------------------------------------------------------------
# Notifications and timeline
# ---------------------------------------------------------------------------
def _context(pa):
    return {
        "reference": pa.reference,
        "action": pa.policy.label,
        "target": pa.target_label,
        "status": pa.get_status_display(),
        "initiator": pa.initiated_by.get_full_name() or pa.initiated_by.email,
        "decider": (pa.decided_by.get_full_name() or pa.decided_by.email) if pa.decided_by_id else "",
        "remark": pa.decision_remark,
        "reason": pa.reason,
        "expires_at": timezone.localtime(pa.expires_at).strftime("%d %b %Y, %H:%M") if pa.expires_at else "",
        "failure": pa.failure_detail,
    }


def _url(pa):
    return f"/qa/pending/{pa.pk}/"


def _dispatch(pa, event, recipients):
    from . import notify

    notify.dispatch(event, _context(pa), recipients, url=_url(pa), actor=pa.decided_by or pa.initiated_by,
                    level="warning" if pa.status in ("rejected", "failed", "expired") else "info")


def _notify_created(pa):
    if pa.status == pa.PENDING:
        _dispatch(pa, "pending_action.created", {"eligible_confirmers": eligible_confirmers(pa)})


def _notify_decided(pa):
    _dispatch(pa, "pending_action.decided", {"initiator": [pa.initiated_by]})


def _notify_step(pa, what):
    """X47: tell the initiator each step, and the approvers when it is their turn."""
    try:
        from notifications.services import notify
        url = _url(pa)
        who = pa.confirmed_by.get_full_name() if pa.confirmed_by_id else "A confirmer"
        notify(pa.initiated_by, title=f"{pa.reference} {what}",
               body=f"{who} confirmed '{pa.target_label or pa.policy.label}'. It now waits for approval.", url=url,
               actor=pa.confirmed_by)
        for user in eligible_confirmers(pa):
            notify(user, title=f"Approval needed: {pa.reference}",
                   body=f"{pa.policy.label} — {pa.target_label}. Confirmed by {who}.", url=url, actor=pa.confirmed_by)
    except Exception:  # pragma: no cover
        logger.exception("Could not notify the X47 step for %s", pa.reference)


def _timeline(pa, target, actor):
    from . import timeline

    institution = None
    if target is not None:
        if target.__class__.__name__ == "Institution":
            institution = target
        else:
            institution = getattr(target, "institution", None)
    if institution is not None:
        timeline.record(institution, f"pending_action.{pa.status}", subject=pa, actor=actor,
                        internal_text=f"{pa.reference} · {pa.policy.label} · {pa.get_status_display()}")


# ===========================================================================
# Phase 1 handlers
# ===========================================================================
@register
class UpdateSettingHandler(ActionHandler):
    """Dual-control settings write path (Master Plan §14 point 2)."""

    code = "config.update_setting"

    @property
    def target_model(self):
        from config.models import ConfigEntry
        return ConfigEntry

    def target_label(self, target):
        return target.key if target is not None else ""

    def snapshot(self, target):
        return {"value": target.value, "value_type": target.value_type}

    def check_initiator(self, target, payload, actor):
        from .settings_service import can_edit

        allowed, reason = can_edit(actor, target)
        if not allowed:
            raise PermissionDenied(reason)

    def validate(self, target, payload):
        from config.models import ConfigEntry

        if target is None:
            raise ValidationError("Choose a setting.")
        if target.is_locked:
            raise ValidationError(f"{target.key} is fixed by policy and cannot be changed.")
        if "value" not in payload:
            raise ValidationError("No new value was given.")
        probe = ConfigEntry(pk=target.pk, key=target.key, scope=target.scope, value=str(payload["value"]),
                            value_type=target.value_type, group=target.group, description=target.description,
                            dual_control=target.dual_control, editable_by=target.editable_by)
        probe.clean()
        if probe.value == target.value:
            raise ValidationError("The new value is the same as the current one.")

    def preview(self, target, payload):
        from .settings_service import display_value

        before = self.snapshot_value(target)
        return [DiffRow("Value", display_value(target.value_type, before),
                        display_value(target.value_type, str(payload.get("value", ""))))]

    @staticmethod
    def snapshot_value(target):
        return target.value

    def apply(self, target, payload, actor):
        target.value = str(payload["value"])
        target.save()

    def scope_department(self, target, initiator):
        return getattr(initiator, "department", None)


@register
class UpdateReferenceFormatHandler(ActionHandler):
    """Reference formats are dual control (Master Plan §14 tables block)."""

    code = "qa_core.update_reference_format"
    FIELDS = ("pattern", "reset_period", "check_digit", "allowed_chars", "sequence_scope", "is_active")

    def target_label(self, target):
        return f"Reference format: {target.code}" if target is not None else ""

    def snapshot(self, target):
        return {f: getattr(target, f) for f in self.FIELDS}

    def check_initiator(self, target, payload, actor):
        if not actor.has_perm("qa_core.manage_reference_formats"):
            raise PermissionDenied("You do not hold qa_core.manage_reference_formats.")

    def validate(self, target, payload):
        from .models import ReferenceFormat
        from .references import ReferenceError_, validate_pattern

        if target is None:
            raise ValidationError("Choose a reference format.")
        data = {**self.snapshot(target), **{k: v for k, v in payload.items() if k in self.FIELDS}}
        if data["reset_period"] not in dict(ReferenceFormat.RESET_CHOICES):
            raise ValidationError("Choose a valid reset period.")
        if data["check_digit"] not in dict(ReferenceFormat.CHECK_CHOICES):
            raise ValidationError("Choose a valid check-digit method.")
        if data["sequence_scope"] not in dict(ReferenceFormat.SCOPE_CHOICES):
            raise ValidationError("Choose a valid numbering scope.")
        try:
            validate_pattern(data["pattern"], data["check_digit"], data["allowed_chars"])
        except ReferenceError_ as exc:
            raise ValidationError(str(exc))
        if all(data[f] == getattr(target, f) for f in self.FIELDS):
            raise ValidationError("Nothing would change.")

    def preview(self, target, payload):
        rows = []
        for f in self.FIELDS:
            before = getattr(target, f)
            after = payload.get(f, before)
            rows.append(DiffRow(f.replace("_", " ").capitalize(), str(before), str(after)))
        return rows

    def apply(self, target, payload, actor):
        for f in self.FIELDS:
            if f in payload:
                setattr(target, f, payload[f])
        target.save()

    def scope_department(self, target, initiator):
        return getattr(initiator, "department", None)
