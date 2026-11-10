"""Memo routing engine.

This is to memos what `workflow.services` is to requests: the runtime
that resolves who approves what, tracks position through the chain,
and executes the transitions.

Public API:
    route_for_memo(memo) -> MemoRoute | None
    build_memo_chain(memo) -> list[MemoRouteStep]
    resolve_memo_approver(step, memo) -> User | None
    current_step(memo) -> MemoRouteStep | None
    start_chain(memo, *, actor=None) -> None
    advance_chain(memo, from_position) -> None
    approve_step(memo, actor, comment="") -> Memo
    return_to_sender(memo, actor, comment="") -> Memo
    reject_memo(memo, actor, comment="") -> Memo
    publish_memo(memo, *, actor=None) -> Memo
    can_act_on_memo(memo, user) -> bool
    actor_for_memo(memo) -> User | None
    resolve_memo_audience(memo) -> QuerySet[User]

Rank rules (mirrors workflow.services._advance_to_next_actor):
    - Skip step if actor is the sender (no self-approval).
    - Skip step if actor is the memo's recipient (delivering it IS the hand-over).
    - Skip a sender-side step (line head / deputy / director) when the sender is
      already at that level or above — even if nobody holds the step (a director
      never waits for "the sender's director"; a deputy skips the deputy step).
    - Skip step if actor is strictly junior to sender (is_hierarchical only).
    - Same-rank behaviour driven by route.same_rank_behaviour.
    - Skip step if is_optional and no actor exists.
    - Park the memo if a required step has no actor (alert admins).
    - When the chain is exhausted, publish.

Departments (directorate routing, 18 Oct 2026):
    memo_relation(memo) -> "same" | "other" | None (broadcast)
    receiving_department(memo) -> (Department | None, reason)
    A rule can require department_relation = same / other, and a step of kind
    "receiving_director" is the director of the recipient's department: it
    acts even when that director is the sender's equal (director → staff in
    another directorate goes through that directorate's director), and is
    skipped when the memo stays in the sender's department or the director
    is the recipient.
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from notifications.models import Notification
from notifications.services import notify

logger = logging.getLogger(__name__)


# ===========================================================================
# Rank helpers
# ===========================================================================
# A nominal-roll designation sets a floor on seniority, so someone whose post
# is DIRECTOR counts as a director even if their sign-in role is still the
# default "Staff". Values are on the Role.rank scale.
_DESIGNATION_RANK_FLOOR = ((100, "executive-secretary", 999), (90, "director", 100),
                           (80, "deputy-director", 80))


def _role_rank(code, default):
    """Rank of a role on the Role.rank scale (as set in the admin)."""
    from accounts.models import Role
    r = Role.objects.filter(code=code).values_list("rank", flat=True).first()
    return r if r is not None else default


def _staff_of(user):
    try:
        return user.staff_profile
    except Exception:
        return None


def _designation_rank(user):
    staff = _staff_of(user)
    if staff is None or not staff.designation_id:
        return 0
    return staff.designation.rank or 0


def _rank_of(user):
    """Effective seniority of a user: the higher of their role's rank and the
    floor their designation gives. A superuser without a role outranks
    everyone; a superuser WITH a role (an admin who is also a director)
    ranks by that role, so admin rights don't bypass the approval chain."""
    if not user or not user.is_authenticated:
        return 0
    rank = (user.role.rank or 0) if user.role_id else 0
    d = _designation_rank(user)
    for min_d, code, default in _DESIGNATION_RANK_FLOOR:
        if d >= min_d:
            rank = max(rank, _role_rank(code, default))
            break
    # The appointed head of a department ranks as its director.
    try:
        if user.headed_departments.exists():
            rank = max(rank, _role_rank("director", 100))
    except Exception:
        pass
    if user.is_superuser and not user.role_id:
        return 1000
    return rank


def memo_url(memo):
    """The memo's page. (Links used to be built as /messaging/memos/<pk>/,
    but the app is mounted at /messages/ — those links went to a 404.)"""
    from django.urls import reverse
    return reverse("messaging:memo_detail", args=[memo.pk])


def _role_code(user):
    return user.role.code if (user and user.role_id) else ""


def role_codes_of(user):
    """Role codes a routing rule can match for this person: their sign-in
    role, plus what their post makes them — a nominal-roll designation of
    Executive Secretary / Director / Deputy Director, or being a department's
    head — so rules keep working for staff whose account role is still the
    default "Staff"."""
    if user is None:
        return set()
    codes = {_role_code(user)} - {""}
    d = _designation_rank(user)
    if d >= 100:
        codes.add("executive-secretary")
    elif d >= 90:
        codes.add("director")
    elif d >= 80:
        codes.add("deputy-director")
    try:
        if user.headed_departments.exists():
            codes.add("director")
    except Exception:
        pass
    return codes


def sender_department(user):
    """The sender's department: the account's, else their staff record's."""
    if user is None:
        return None
    if getattr(user, "department_id", None):
        return user.department
    staff = _staff_of(user)
    return staff.department if staff is not None and staff.department_id else None


def _recipient_departments(memo):
    """Departments of the people a memo is addressed to, as a set of
    Department objects (None for a recipient without one). None for
    broadcasts (all staff / all directors / roles), which have no single
    department."""
    if memo.visibility == memo.SPECIFIC_USER:
        if not memo.target_user_id:
            return set()
        return {sender_department(memo.target_user)}
    if memo.visibility == memo.INDIVIDUALS:
        if not memo.pk:
            return set()
        return {sender_department(u) for u in memo.recipients.select_related("department")}
    if memo.visibility == memo.DEPARTMENT:
        return {memo.department} if memo.department_id else set()
    return None


def memo_relation(memo):
    """'same' when every recipient is in the sender's department, 'other' when
    at least one is not (a recipient without a department counts as outside),
    None for broadcasts or when there are no recipients yet."""
    depts = _recipient_departments(memo)
    if not depts:
        return None
    own = sender_department(memo.sender)
    if own is not None and all(d is not None and d.pk == own.pk for d in depts):
        return "same"
    return "other"


def receiving_department(memo):
    """(department, reason). The one department outside the sender's that the
    memo is addressed to; None with a plain reason when there is none or
    there are several."""
    depts = _recipient_departments(memo)
    if depts is None:
        return None, "A broadcast has no single receiving department."
    own = sender_department(memo.sender)
    outside = {d.pk: d for d in depts if d is not None and (own is None or d.pk != own.pk)}
    if not outside:
        if any(d is None for d in depts):
            return None, "The recipient has no department."
        return None, "The memo stays in the sender's department."
    if len(outside) > 1:
        return None, ("The recipients are in several other departments; "
                      "send one memo per department to route it through each director.")
    return next(iter(outside.values())), ""


def _is_sole_recipient(memo, user):
    return (user is not None and memo.visibility == memo.SPECIFIC_USER
            and memo.target_user_id == user.pk)


# Level of each sender-side step on the Role.rank scale.
_STEP_LEVEL_ROLE = {"division_head": ("division-head", 50, "line head"),
                    "deputy": ("deputy-director", 80, "deputy director"),
                    "director": ("director", 100, "director")}


def _step_level(kind):
    spec = _STEP_LEVEL_ROLE.get(kind)
    if spec is None:
        return None, ""
    code, default, label = spec
    return _role_rank(code, default), label


# ===========================================================================
# Step keys — survive step reordering
# ===========================================================================
def step_key(step):
    if step is None or not step.pk:
        return ""
    return f"db:{step.pk}"


# ===========================================================================
# Route resolution
# ===========================================================================
def route_for_memo(memo):
    """Pick the route that applies to this memo.

    Matching, in order of precedence:
      1. Rule's `sender_role_codes` includes the sender's role code
         (empty = any).
      2. Rule's `target_kind` equals the memo's visibility.
      3. If target_kind = 'specific_user', rule's `target_role_codes` is
         empty OR includes the target user's role code.

    Returns the route of the highest-priority match, or None if no rule
    matches (memo publishes directly).
    """
    from .models import MemoRouteRule

    if not memo.sender_id:
        return None

    sender_codes = role_codes_of(memo.sender)
    target_codes = set()
    if memo.visibility == memo.SPECIFIC_USER and memo.target_user_id:
        target_codes = role_codes_of(memo.target_user)

    rules = (
        MemoRouteRule.objects
        .filter(is_active=True, target_kind=memo.visibility)
        .select_related("route")
        .order_by("-priority", "-updated_at")
    )

    relation = memo_relation(memo)

    for rule in rules:
        wanted = getattr(rule, "department_relation", "any") or "any"
        if wanted != "any" and wanted != relation:
            continue

        allowed_senders = set(rule.sender_role_codes or [])
        if allowed_senders and not (sender_codes & allowed_senders):
            continue

        if memo.visibility == memo.SPECIFIC_USER:
            allowed_targets = set(rule.target_role_codes or [])
            if allowed_targets and not (target_codes & allowed_targets):
                continue

        if rule.route and rule.route.is_active:
            return rule.route

    return None


# ===========================================================================
# Chain building
# ===========================================================================
def build_memo_chain(memo):
    """Return the ordered list of steps for this memo's route.

    If the memo has no route, returns [].
    """
    if memo.route_id is None:
        return []
    return list(
        memo.route.steps
        .select_related("approver_role", "approver_user")
        .order_by("position", "pk")
    )


# ===========================================================================
# Approver resolution
# ===========================================================================
def _active_users():
    from accounts.selectors import staff_users
    return staff_users()  # Phase 0 (M9): memo approvers are staff


def _find_user_with_role_in_department(role_code, department):
    """First active user with role=role_code in the given department
    (account department, or staff-record department)."""
    if not role_code or department is None:
        return None
    import org
    from django.db.models import Q
    return (
        _active_users()
        .filter(org.role_q(role_code))
        .filter(Q(department=department) | Q(staff_profile__department=department))
        .order_by("pk")
        .first()
    )


def _find_by_designation(department, min_rank, max_rank=None, exclude=None):
    """Active user whose staff record is in `department` with a designation
    ranked in [min_rank, max_rank]. Substantive posts before 'Acting' ones."""
    if department is None:
        return None
    qs = (_active_users()
          .filter(staff_profile__department=department,
                  staff_profile__is_deleted=False,
                  staff_profile__designation__rank__gte=min_rank)
          .select_related("staff_profile__designation"))
    if max_rank is not None:
        qs = qs.filter(staff_profile__designation__rank__lte=max_rank)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    users = list(qs.order_by("-staff_profile__designation__rank", "pk")[:10])
    if not users:
        return None
    def acting(u):
        name = (u.staff_profile.designation.name or "").upper()
        return name.startswith(("AG.", "AGT", "ACTING"))
    users.sort(key=lambda u: (acting(u), -u.staff_profile.designation.rank, u.pk))
    return users[0]


def find_director(department):
    """The department's director (Phase B): the director seat
    (org.director_of); while the seat is empty, Department.head, else a staff
    member whose designation is Director / Acting Director (nominal roll)."""
    if department is None:
        return None
    import org
    director = org.director_of(department)
    if director is not None:
        return director
    head = getattr(department, "head", None)
    if head is not None and head.is_active:
        return head
    return _find_by_designation(department, 90, 99)


def find_deputy(department):
    return (_find_user_with_role_in_department("deputy-director", department)
            or _find_by_designation(department, 80, 89))


def find_line_head(sender):
    """The sender's line head: the head of their office/unit, else a Division
    Head or Office Head in their department (same office first)."""
    staff = _staff_of(sender)
    # v2.2: offices are units in the Department list; a unit (not a full
    # department) with a named head routes to that head first.
    unit = staff.department if staff is not None and staff.department_id else None
    office = unit if unit is not None and unit.kind != "department" else None
    head_user = office.head if office is not None and office.head_id else None
    if head_user is None and staff is not None and staff.office_id:
        # A legacy Office row not yet merged into a unit still routes to its head.
        legacy = staff.office
        if legacy.head_id and legacy.head.user_id and not getattr(legacy, "merged_into_id", None):
            head_user = legacy.head.user
    if head_user is not None and head_user.is_active and head_user.pk != getattr(sender, "pk", None):
        return head_user
    # Phase B (B7): the head of the sender's division comes next.
    division = staff.division if staff is not None and staff.division_id else None
    if division is not None and division.is_active and division.head_id \
            and division.head.is_active and division.head_id != getattr(sender, "pk", None):
        return division.head
    dept = sender_department(sender)
    if dept is None:
        return None
    from django.db.models import Q
    import org
    qs = (_active_users()
          .filter(org.role_q("division-head", prefix="office-head") | Q(headed_divisions__department=dept))
          .filter(Q(department=dept) | Q(staff_profile__department=dept)))
    return qs.order_by("-role__rank", "pk").first()


def find_es():
    import org
    from django.db.models import Q
    return (_active_users()
            .filter(org.role_q("executive-secretary")
                    | Q(staff_profile__designation__rank__gte=100, staff_profile__is_deleted=False))
            .order_by("-role__rank", "pk")
            .first())


def resolve_memo_approver(step, memo):
    """Who should act at this step for this memo? None if no one matches."""
    sender = memo.sender
    sender_dept = sender_department(sender)

    kind = step.approver_kind

    if kind == step.APPROVER_SPECIFIC:
        return (
            step.approver_user
            if (step.approver_user_id and step.approver_user.is_active)
            else None
        )

    if kind == step.APPROVER_ES:
        return find_es()

    if kind == step.APPROVER_DIVISION_HEAD:
        return find_line_head(sender)

    if kind == step.APPROVER_DIRECTOR:
        return find_director(sender_dept)

    if kind == step.APPROVER_DEPUTY:
        return find_deputy(sender_dept)

    if kind == getattr(step, "APPROVER_RECEIVING_DIRECTOR", "receiving_director"):
        dept, _reason = receiving_department(memo)
        return find_director(dept) if dept is not None else None

    if kind == step.APPROVER_ROLE_IN_DEPT:
        if not step.approver_role_id:
            return None
        scope = (step.scope or "requester").strip().lower()
        if scope == "es":
            # No department scope: anyone holding the role.
            import org
            return (_active_users().filter(org.role_q(step.approver_role.code))
                    .order_by("pk").first())
        if scope == "requester":
            dept = sender_dept
        elif scope == "receiving":
            dept = memo.department or (sender_department(memo.target_user)
                                       if memo.target_user_id else None)
        else:
            from staff.models import Department
            dept = Department.objects.filter(code=scope).first()
        return _find_user_with_role_in_department(step.approver_role.code, dept)

    return None


def describe_missing(step, memo):
    """Plain-language reason nobody can act at `step` (for the memo page)."""
    dept = sender_department(memo.sender)
    where = f" in {dept.name}" if dept is not None else " (the sender has no department)"
    kind = step.approver_kind
    if kind == step.APPROVER_SPECIFIC:
        return "The person chosen for this step is not set or no longer active."
    if kind == step.APPROVER_ES:
        return "Nobody has the Executive Secretary role or designation."
    if kind == step.APPROVER_DIVISION_HEAD:
        return f"No office head or division head{where}."
    if kind == step.APPROVER_DIRECTOR:
        return (f"No director{where}: set the department's head, give someone the Director role, "
                "or import their DIRECTOR designation.")
    if kind == step.APPROVER_DEPUTY:
        return f"No deputy director{where}."
    if kind == step.APPROVER_ROLE_IN_DEPT:
        role = step.approver_role.name if step.approver_role_id else "(no role chosen)"
        return f"Nobody with the role “{role}” in the step's department."
    if kind == "receiving_director":
        rdept, reason = receiving_department(memo)
        if rdept is None:
            return reason
        return (f"No director in {rdept.name} (the recipient's department): set the department's head, "
                "give someone the Director role, or import their DIRECTOR designation.")
    return "No one matches this step."


# ===========================================================================
# Current step tracking
# ===========================================================================
def current_step(memo):
    """The step the memo is currently parked at, or None."""
    if not memo.current_step_key:
        return None
    for s in build_memo_chain(memo):
        if step_key(s) == memo.current_step_key:
            return s
    return None


def _set_current_step(memo, step):
    memo.current_step = step if (step and step.pk) else None
    memo.current_step_key = step_key(step) if step else ""
    memo.current_step_started_at = timezone.now()
    memo.save(update_fields=[
        "current_step", "current_step_key", "current_step_started_at",
        "updated_at",
    ])


def _clear_current_step(memo):
    memo.current_step = None
    memo.current_step_key = ""
    memo.current_step_started_at = None
    memo.save(update_fields=[
        "current_step", "current_step_key", "current_step_started_at",
        "updated_at",
    ])


def actor_for_memo(memo):
    """Resolve the user who should act on the memo right now, or None."""
    step = current_step(memo)
    if step is None:
        return None
    return resolve_memo_approver(step, memo)


# ===========================================================================
# Activity helper
# ===========================================================================
def _log(memo, kind, actor=None, message="", metadata=None):
    """Small structured log on the memo. Reuses RequestActivity semantics
    but writes to a private history list on the memo. Kept inline for
    simplicity — no new model.
    """
    # No dedicated log model for memos yet; we surface key events via
    # notifications and MemoApproval rows. This hook exists so a future
    # audit table can drop in cleanly.
    logger.info(
        "[memo %s] %s — %s",
        memo.reference, kind, message or "",
    )


# ===========================================================================
# Notifications
# ===========================================================================
def _notify_actor(memo, step, actor):
    if actor is None:
        return
    try:
        notify(
            recipient=actor,
            title=f"Memo awaiting your approval — {memo.reference}",
            body=f"{memo.subject} · step: {step.name}",
            url=memo_url(memo),
            category=Notification.MESSAGE,
            level=Notification.WARNING,
            actor=memo.sender,
        )
    except Exception:
        logger.exception("Could not notify memo actor %s", actor.pk)


def _notify_sender_returned(memo, actor, comment):
    try:
        notify(
            recipient=memo.sender,
            title=f"Memo returned for revision — {memo.reference}",
            body=comment or "Please review and resubmit.",
            url=memo_url(memo),
            category=Notification.MESSAGE,
            level=Notification.WARNING,
            actor=actor,
        )
    except Exception:
        logger.exception("Could not notify memo sender of return")


def _notify_sender_rejected(memo, actor, comment):
    try:
        notify(
            recipient=memo.sender,
            title=f"Memo rejected — {memo.reference}",
            body=comment or "Your memo was rejected.",
            url=memo_url(memo),
            category=Notification.MESSAGE,
            level=Notification.ERROR,
            actor=actor,
        )
    except Exception:
        logger.exception("Could not notify memo sender of rejection")


def _notify_admins_no_actor(memo, step, reason=""):
    from accounts.models import User
    for admin in User.objects.filter(is_active=True, is_superuser=True):
        try:
            notify(
                recipient=admin,
                title=f"No one matches a memo step — {memo.reference}",
                body=f"Step '{step.name}' has no acting user. {reason} "
                     "Fix the people or the route, then press “Re-check routing” on the memo.",
                url=memo_url(memo),
                category=Notification.SYSTEM,
                level=Notification.ERROR,
            )
        except Exception:
            logger.exception("Could not notify admin %s", admin.pk)


# ===========================================================================
# Audience resolution (for publishing)
# ===========================================================================
def resolve_memo_audience(memo):
    """Return a User queryset — the final recipients of a published memo.

    The sender is always excluded from the audience.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    from accounts.selectors import staff_users

    if getattr(memo, "kind", "internal") == "external" or memo.visibility == "no_staff":
        return User.objects.none()

    base = staff_users()  # Phase 0 (M9): internal memos never reach external users

    if memo.sender_id:
        base = base.exclude(pk=memo.sender_id)

    if memo.visibility == memo.SPECIFIC_USER:
        if not memo.target_user_id:
            return User.objects.none()
        return base.filter(pk=memo.target_user_id).distinct()

    if memo.visibility == memo.ALL_STAFF:
        return base.distinct()

    if memo.visibility == memo.ALL_DIRECTORS:
        from django.db.models import Q
        import org
        return base.filter(
            Q(directs__isnull=False) | org.role_q("director")
            | Q(staff_profile__designation__rank__gte=90, staff_profile__designation__rank__lt=100,
                staff_profile__is_deleted=False)
            | Q(headed_departments__isnull=False)
        ).distinct()

    if memo.visibility == memo.DEPARTMENT:
        if not memo.department_id:
            return User.objects.none()
        return base.filter(department_id=memo.department_id).distinct()

    if memo.visibility == memo.ROLE:
        group_ids = list(memo.roles.values_list("pk", flat=True))
        if not group_ids:
            return User.objects.none()
        return base.filter(groups__in=group_ids).distinct()

    if memo.visibility == memo.INDIVIDUALS:
        return base.filter(
            pk__in=memo.recipients.values_list("pk", flat=True),
        ).distinct()

    # v2.1 Phase E (M6): staff picked by department, grade range, designation, role.
    if memo.visibility == memo.STAFF_FILTER:
        from .memo_audience import staff_matching
        return staff_matching(memo.staff_filter or {}, exclude=memo.sender_id)

    # v2.1 Phase E (M1): an external memo reaches institutions only.
    return User.objects.none()


# ===========================================================================
# External memos (v2.1 Phase E, M2/M3)
# ===========================================================================
EXTERNAL_ROUTE_CODE = "external-memo-es"


def external_route():
    """The route every external / general memo not sent by the ES walks: one
    Executive Secretary step that always runs. Re-created if someone removed
    the ES step (the ES gate is not optional)."""
    from .models import MemoRoute, MemoRouteStep

    route, _ = MemoRoute.objects.get_or_create(
        code=EXTERNAL_ROUTE_CODE,
        defaults={"name": "External memo: Executive Secretary approval",
                  "description": "Memos to institutions leave NBTE only with the Executive Secretary's "
                                 "approval (v2.1 M3). Used automatically; not matched by rules."},
    )
    if not route.is_active:
        route.is_active = True
        route.save(update_fields=["is_active", "updated_at"])
    if not route.steps.filter(approver_kind=MemoRouteStep.APPROVER_ES).exists():
        pos = (route.steps.order_by("-position").values_list("position", flat=True).first() or 0) + 1
        MemoRouteStep.objects.create(
            route=route, position=pos, name="Executive Secretary approval",
            approver_kind=MemoRouteStep.APPROVER_ES, scope="es", is_optional=False, is_hierarchical=False,
            instructions="This memo goes to institutions on NBTE's letterhead with your signature. "
                         "Approve to send it, return it for changes, or reject it.")
    return route


def _is_es_gate(memo, step):
    return bool(memo.is_outbound and step is not None
                and step.approver_kind == step.APPROVER_ES)


def _deliver_to_institutions(memo, actor, now):
    """MemoInstitutionRecipient rows, a portal notification for each of the
    institution's portal users and an e-mail (console in dev). Returns the
    number of institutions."""
    from institutions.models import InstitutionUser

    from .memo_audience import memo_institutions
    from .models import MemoInstitutionRecipient

    institutions = list(memo_institutions(memo))
    for inst in institutions:
        MemoInstitutionRecipient.objects.get_or_create(memo=memo, institution=inst,
                                                       defaults={"delivered_at": now})
    if not institutions:
        return 0
    from django.urls import NoReverseMatch, reverse
    try:
        url = reverse("qa_portal:memo", args=[memo.pk], urlconf="qa_core.portal_urls")
    except NoReverseMatch:  # pragma: no cover
        url = ""
    title = f"New memo from NBTE — {memo.reference}"
    links = (InstitutionUser.unscoped.filter(institution__in=institutions, user__is_active=True)
             .select_related("user"))
    emails = []
    for link in links:
        try:
            notify(recipient=link.user, title=title, body=memo.subject, url=url,
                   category=Notification.MESSAGE,
                   level=(Notification.WARNING if memo.priority in (memo.HIGH, memo.URGENT)
                          else Notification.INFO),
                   actor=actor or memo.sender,
                   metadata={"memo_id": memo.pk, "reference": memo.reference, "institution": link.institution_id})
        except Exception:
            logger.exception("Memo portal notify failed for user %s", link.user_id)
        if link.user.email:
            emails.append(link.user.email)
    if emails:
        try:
            from django.core.mail import send_mail
            from django.conf import settings as dj_settings
            body = (f"NBTE has sent your institution a memo: {memo.subject}\n\n"
                    f"Reference: {memo.reference}\nRead it and download the signed letter in the "
                    f"Quality Assurance Portal under Memos.")
            for addr in sorted(set(emails)):
                send_mail(title, body, getattr(dj_settings, "DEFAULT_FROM_EMAIL", None), [addr],
                          fail_silently=True)
        except Exception:
            logger.exception("Memo e-mail to institutions failed for %s", memo.reference)
    return len(institutions)


# ===========================================================================
# Chain lifecycle
# ===========================================================================
@transaction.atomic
def start_chain(memo, *, actor=None):
    """Attach the route (if not already attached) and advance to the
    first actionable step. Publishes immediately if no route or chain
    is empty after skips.
    """
    if memo.is_outbound:
        # v2.1 Phase E (M2, M3): only the ES sends a memo to institutions.
        # The ES's own memo goes straight out, signed "From the Executive
        # Secretary"; anyone else's waits for the ES ("Approved by ...").
        from .memos import can_send_external_memo
        if can_send_external_memo(memo.sender):
            from documents.signature import require_signature_for_approval

            require_signature_for_approval(memo.sender)
            memo.es_signatory = memo.sender
            memo.es_signed_at = timezone.now()
            memo.route = None
            memo.save(update_fields=["es_signatory", "es_signed_at", "route", "updated_at"])
            return publish_memo(memo, actor=actor or memo.sender)
        memo.route = external_route()
        memo.save(update_fields=["route", "updated_at"])

    if memo.route_id is None:
        memo.route = route_for_memo(memo)
        if memo.route_id is not None:
            memo.save(update_fields=["route", "updated_at"])

    if memo.route_id is None:
        # No route — direct publish.
        return publish_memo(memo, actor=actor or memo.sender)

    memo.status = memo.AWAITING
    memo.save(update_fields=["status", "is_draft", "updated_at"])

    advance_chain(memo, from_position=-1)
    return memo


def evaluate_step(step, memo, *, sender_rank=None, same_rank_mode=None):
    """-> (decision, actor, reason) for one step, without changing anything.

    decision is "act" (actor must act), "skip" or "park" (required step and
    nobody can act). Used by the engine, the memo page and the route tester,
    so all three always agree."""
    if sender_rank is None:
        sender_rank = _rank_of(memo.sender)
    if same_rank_mode is None:
        same_rank_mode = memo.route.same_rank_behaviour if memo.route_id else "skip"

    # --- the recipient's department director -------------------------------
    if step.approver_kind == "receiving_director":
        rdept, why = receiving_department(memo)
        if rdept is None:
            return "skip", None, why
        actor = find_director(rdept)
        if actor is None:
            if step.is_optional:
                return "skip", None, "Optional step — " + describe_missing(step, memo)
            return "park", None, describe_missing(step, memo)
        name = actor.get_full_name() or actor.email
        if actor.pk == memo.sender_id:
            return "skip", actor, "The sender is that department's director."
        if _is_sole_recipient(memo, actor):
            return "skip", actor, f"{name} is the recipient and the director of {rdept.name}: delivered directly."
        if memo.visibility == memo.INDIVIDUALS and memo.recipients.filter(pk=actor.pk).exists():
            return "skip", actor, f"{name}, the director of {rdept.name}, is one of the recipients."
        # A director's equal still approves (director → staff in another
        # directorate); only someone senior to that director (the ES) skips it.
        if step.is_hierarchical and _rank_of(actor) < sender_rank:
            return "skip", actor, f"The sender is senior to {name}."
        return "act", actor, ""

    actor = resolve_memo_approver(step, memo)
    sender_side = step.approver_kind in _STEP_LEVEL_ROLE

    if actor is not None and actor.pk == memo.sender_id:
        return "skip", actor, "The sender would be the approver (no self-approval)."

    # --- sender-side steps (line head / deputy / director) -----------------
    # Skip when the sender is already at that level (a director never waits
    # for "the sender's director", even if someone else holds that post), and
    # when the approver is the memo's own recipient (staff → their director:
    # delivering it is the hand-over). Gates such as the ES step are untouched.
    if sender_side and step.is_hierarchical:
        level, label = _step_level(step.approver_kind)
        if level is not None and sender_rank >= level:
            return "skip", actor, f"The sender is already at {label} level or above."
    if sender_side and _is_sole_recipient(memo, actor):
        name = actor.get_full_name() or actor.email
        return "skip", actor, f"{name} is the recipient: the memo is delivered to them directly."

    if actor is None:
        if step.is_optional:
            return "skip", None, "Optional step — " + describe_missing(step, memo)
        return "park", None, describe_missing(step, memo)
    if step.is_hierarchical:
        actor_rank = _rank_of(actor)
        name = actor.get_full_name() or actor.email
        if actor_rank < sender_rank:
            return "skip", actor, f"{name} is junior to the sender."
        if actor_rank == sender_rank and same_rank_mode == "skip":
            return "skip", actor, f"{name} is the same rank as the sender (route: same rank skips)."
    return "act", actor, ""


def simulate_chain(memo):
    """Evaluate every step of the memo's route as if it were walked now:
    [{"step", "decision", "actor", "reason"}]. Changes nothing."""
    steps = build_memo_chain(memo)
    sender_rank = _rank_of(memo.sender)
    mode = memo.route.same_rank_behaviour if memo.route_id else "skip"
    out = []
    for step in steps:
        decision, actor, reason = evaluate_step(step, memo, sender_rank=sender_rank, same_rank_mode=mode)
        out.append({"step": step, "decision": decision, "actor": actor, "reason": reason})
    return out


def _record_skip(memo, step, reason):
    from .models import MemoApproval
    MemoApproval.objects.create(memo=memo, step=step, step_name=step.name[:150], actor=None,
                                decision=MemoApproval.SKIP, comment=reason[:1000])


@transaction.atomic
def advance_chain(memo, from_position):
    """Move the chain forward from `from_position`. Skipped steps are
    recorded (with the reason) so the memo page shows what happened.
    Parks on a required step nobody can act on; publishes when exhausted.
    """
    steps = build_memo_chain(memo)
    idx = from_position + 1

    sender_rank = _rank_of(memo.sender)
    same_rank_mode = (
        memo.route.same_rank_behaviour if memo.route_id else "skip"
    )

    while idx < len(steps):
        step = steps[idx]
        decision, actor, reason = evaluate_step(step, memo, sender_rank=sender_rank,
                                                same_rank_mode=same_rank_mode)
        if decision == "skip":
            _record_skip(memo, step, reason)
            _log(memo, "skipped_step", message=f"Skipped '{step.name}': {reason}")
            idx += 1
            continue

        if decision == "park":
            already_parked_here = memo.current_step_key == step_key(step)
            if not already_parked_here:
                _set_current_step(memo, step)
            memo.status = memo.AWAITING
            memo.save(update_fields=["status", "is_draft", "updated_at"])
            _log(memo, "parked_no_actor",
                 message=f"Waiting for someone to fill '{step.name}': {reason}")
            if not already_parked_here:
                _notify_admins_no_actor(memo, step, reason)
            return memo

        # We have an actionable actor.
        _set_current_step(memo, step)
        memo.status = memo.AWAITING
        memo.save(update_fields=["status", "is_draft", "updated_at"])
        _notify_actor(memo, step, actor)
        return memo

    # Chain exhausted → publish.
    return publish_memo(memo, actor=memo.sender)


def round_approvals(memo):
    """Decisions of the memo's current pass through the chain: everything
    after the last 'returned' (a resubmitted memo walks the chain afresh)."""
    from .models import MemoApproval
    rows = list(memo.approvals.select_related("step", "actor").order_by("decided_at", "id"))
    if memo.status == memo.RETURNED:
        # Show the pass that ended with the return.
        last = max((i for i, a in enumerate(rows) if a.decision == MemoApproval.RETURN), default=-1)
        prev = max((i for i, a in enumerate(rows[:last]) if a.decision == MemoApproval.RETURN), default=-1)
        return rows[prev + 1:last + 1]
    last = max((i for i, a in enumerate(rows) if a.decision == MemoApproval.RETURN), default=-1)
    return rows[last + 1:]


def reconcile_memo(memo):
    """Re-check a memo that is waiting on a step nobody can act on, or on a
    step that was deleted or changed. Moves it on if it can. Returns True
    if the memo moved."""
    from .models import MemoApproval
    if memo.status != memo.AWAITING or memo.route_id is None:
        return False
    steps = build_memo_chain(memo)
    keys = [step_key(s) for s in steps]
    step = current_step(memo)
    if step is not None:
        decision, _actor, _reason = evaluate_step(step, memo)
        if decision == "act":
            return False
        start = keys.index(step_key(step)) - 1          # re-evaluate the step itself
    else:
        done = {step_key(a.step) for a in round_approvals(memo)
                if a.step_id and a.decision in (MemoApproval.APPROVE, MemoApproval.SKIP)}
        start = max((i for i, k in enumerate(keys) if k in done), default=-1)
    before = (memo.status, memo.current_step_key)
    with transaction.atomic():
        advance_chain(memo, from_position=start)
    memo.refresh_from_db()
    return (memo.status, memo.current_step_key) != before


def reconcile_parked():
    """Re-check every memo that is waiting with nobody to act (after a role,
    department head, designation or route change). Returns memos moved."""
    from .models import Memo
    moved = 0
    for memo in Memo.objects.filter(status=Memo.AWAITING).select_related("route", "sender"):
        try:
            if actor_for_memo(memo) is None and reconcile_memo(memo):
                moved += 1
        except Exception:
            logger.exception("Could not re-check memo %s", memo.reference)
    return moved


def _check_actor(memo, step, actor):
    """The acting user, or a superuser acting for a step nobody holds."""
    resolved = resolve_memo_approver(step, memo)
    if resolved is not None and resolved.pk == actor.pk:
        return ""
    if _is_es_gate(memo, step):
        # v2.1 M2: nobody approves a memo to institutions for the ES - not
        # even an administrator. A delegated holder of
        # messaging.send_external_memo may (acting ES).
        from .memos import can_send_external_memo
        if can_send_external_memo(actor):
            return "Approved by the acting Executive Secretary."
        raise PermissionDenied("Only the Executive Secretary approves a memo to institutions.")
    if actor.is_superuser:
        if resolved is None:
            return "Approved by an administrator because nobody holds this step."
        return f"Acted by an administrator on behalf of {resolved.get_full_name() or resolved.email}."
    raise PermissionDenied("You are not the acting user for this step.")


@transaction.atomic
def approve_step(memo, actor, comment=""):
    """Record an approval, then advance."""
    from .models import MemoApproval

    step = current_step(memo)
    if step is None:
        raise ValidationError("This memo is not awaiting an approval.")
    override_note = _check_actor(memo, step, actor)
    # v2.1 X23 (owner decision): a Director (or above) approves a memo - which
    # carries their signature - only once their signature is on file.
    from documents.signature import require_signature_for_approval

    require_signature_for_approval(actor)

    now = timezone.now()
    duration = 0
    if memo.current_step_started_at:
        duration = int((now - memo.current_step_started_at).total_seconds())

    MemoApproval.objects.create(
        memo=memo, step=step,
        actor=actor,
        actor_role=actor.role.name if actor.role_id else "",
        decision=MemoApproval.APPROVE,
        step_name=step.name[:150],
        comment=" ".join(x for x in (comment or "", override_note) if x),
        duration_seconds=duration,
    )

    _log(memo, "approved",
         actor=actor, message=f"Approved step '{step.name}'.")

    if _is_es_gate(memo, step):
        memo.es_signatory = actor
        memo.es_signed_at = now
        memo.save(update_fields=["es_signatory", "es_signed_at", "updated_at"])

    # Find where we are in the chain and advance past it.
    steps = build_memo_chain(memo)
    current_idx = -1
    key = step_key(step)
    for i, s in enumerate(steps):
        if step_key(s) == key:
            current_idx = i
            break

    return advance_chain(memo, from_position=current_idx)


@transaction.atomic
def return_to_sender(memo, actor, comment=""):
    """Send the memo back to the sender for revision. The chain is
    reset so that on resubmit it starts from the beginning (per your
    design — a resubmitted memo walks the chain fresh)."""
    from .models import MemoApproval

    step = current_step(memo)
    if step is None:
        raise ValidationError("This memo is not awaiting an approval.")
    override_note = _check_actor(memo, step, actor)

    now = timezone.now()
    duration = 0
    if memo.current_step_started_at:
        duration = int((now - memo.current_step_started_at).total_seconds())

    MemoApproval.objects.create(
        memo=memo, step=step,
        actor=actor,
        actor_role=actor.role.name if actor.role_id else "",
        decision=MemoApproval.RETURN,
        step_name=step.name[:150],
        comment=" ".join(x for x in (comment or "", override_note) if x),
        duration_seconds=duration,
    )

    memo.status = memo.RETURNED
    memo.returned_by = actor
    memo.returned_from_step_key = step_key(step)
    memo.returned_at = now
    memo.returned_comment = comment or ""
    _clear_current_step(memo)
    memo.save(update_fields=[
        "status", "is_draft",
        "returned_by", "returned_from_step_key", "returned_at",
        "returned_comment", "updated_at",
    ])

    _log(memo, "returned",
         actor=actor, message=f"Returned from step '{step.name}'.")
    _notify_sender_returned(memo, actor, comment)
    return memo


@transaction.atomic
def reject_memo(memo, actor, comment=""):
    """Terminate the memo. Rejection is final."""
    from .models import MemoApproval

    if not (comment or "").strip():
        raise ValidationError("A comment is required when rejecting a memo.")

    step = current_step(memo)
    if step is None:
        raise ValidationError("This memo is not awaiting an approval.")
    override_note = _check_actor(memo, step, actor)

    now = timezone.now()
    duration = 0
    if memo.current_step_started_at:
        duration = int((now - memo.current_step_started_at).total_seconds())

    MemoApproval.objects.create(
        memo=memo, step=step,
        actor=actor,
        actor_role=actor.role.name if actor.role_id else "",
        decision=MemoApproval.REJECT,
        step_name=step.name[:150],
        comment=" ".join(x for x in (comment, override_note) if x),
        duration_seconds=duration,
    )

    memo.status = memo.REJECTED
    memo.rejected_by = actor
    memo.rejected_at = now
    memo.rejection_comment = comment
    _clear_current_step(memo)
    memo.save(update_fields=[
        "status", "is_draft",
        "rejected_by", "rejected_at", "rejection_comment", "updated_at",
    ])

    _log(memo, "rejected",
         actor=actor, message=f"Rejected at step '{step.name}'.")
    _notify_sender_rejected(memo, actor, comment)
    return memo


@transaction.atomic
def publish_memo(memo, *, actor=None):
    """Deliver the memo to its audience.

    Idempotent — safe to call twice. Materializes `MemoRecipient` rows
    and sends an in-app notification to each recipient.
    """
    from .models import MemoRecipient

    if memo.status == memo.PUBLISHED:
        return memo

    if memo.is_outbound and not memo.es_signatory_id:
        # v2.1 M2: nothing leaves NBTE without the Executive Secretary.
        raise PermissionDenied("A memo to institutions is published only by or with the approval "
                               "of the Executive Secretary.")

    now = timezone.now()
    memo.status = memo.PUBLISHED
    memo.published_at = now
    memo.is_draft = False
    _clear_current_step(memo)
    memo.save(update_fields=[
        "status", "is_draft", "published_at", "updated_at",
    ])

    audience = list(resolve_memo_audience(memo))
    for user in audience:
        MemoRecipient.objects.get_or_create(
            memo=memo, user=user,
            defaults={"delivered_at": now},
        )

    # Notify recipients.
    url = memo_url(memo)
    level = (
        Notification.WARNING
        if memo.priority in (memo.HIGH, memo.URGENT)
        else Notification.INFO
    )
    title = f"New memo — {memo.reference}"
    if memo.priority == memo.URGENT:
        title = f"URGENT memo — {memo.reference}"
    body = memo.subject
    if memo.requires_acknowledgment:
        body += " · Acknowledgment required"

    for user in audience:
        try:
            notify(
                recipient=user,
                title=title,
                body=body,
                url=url,
                category=Notification.MESSAGE,
                level=level,
                actor=actor or memo.sender,
                metadata={"memo_id": memo.pk, "reference": memo.reference},
            )
        except Exception:
            logger.exception("Memo notify failed for user %s", user.pk)

    n_inst = _deliver_to_institutions(memo, actor, now) if memo.is_outbound else 0

    _log(memo, "published",
         actor=actor,
         message=(f"Delivered to {len(audience)} recipient(s)"
                  + (f" and {n_inst} institution(s)." if memo.is_outbound else ".")))
    return memo


@transaction.atomic
def resubmit_memo(memo, actor, *, subject=None, body=None, comment=""):
    """Sender revises a returned memo and sends it back through the chain.

    The chain is reset — resubmission walks it fresh from position -1,
    applying current rank and optional rules.
    """
    if memo.sender_id != actor.pk and not actor.is_superuser:
        raise PermissionDenied("Only the sender can resubmit this memo.")
    if memo.status != memo.RETURNED:
        raise ValidationError("Only returned memos can be resubmitted.")

    if subject is not None and subject.strip():
        memo.subject = subject.strip()
    if body is not None and body.strip():
        memo.body = body.strip()

    memo.status = memo.AWAITING
    memo.returned_by = None
    memo.returned_from_step_key = ""
    memo.returned_at = None
    memo.returned_comment = ""
    memo.resubmitted_count += 1
    memo.save(update_fields=[
        "subject", "body", "status", "is_draft",
        "returned_by", "returned_from_step_key", "returned_at",
        "returned_comment", "resubmitted_count", "updated_at",
    ])

    _log(memo, "resubmitted", actor=actor,
         message=f"Resubmitted (revision #{memo.resubmitted_count}).")

    advance_chain(memo, from_position=-1)
    return memo


# ===========================================================================
# Permission helpers
# ===========================================================================
def can_act_on_memo(memo, user, *, admin_override=True):
    """True if `user` is the current step's actor. A superuser may also act
    on any step (admin_override) — the decision is labelled as such."""
    if not user or not user.is_authenticated:
        return False
    if memo.status != memo.AWAITING:
        return False
    step = current_step(memo)
    if step is None:
        return False
    actor = resolve_memo_approver(step, memo)
    if actor is not None and actor.pk == user.pk:
        return True
    if _is_es_gate(memo, step):
        from .memos import can_send_external_memo
        return can_send_external_memo(user)
    return bool(admin_override and user.is_superuser)


def is_parked(memo):
    """Waiting on a step nobody can act on."""
    return memo.status == memo.AWAITING and current_step(memo) is not None and actor_for_memo(memo) is None


# ===========================================================================
# Memo page: the chain as people should read it
# ===========================================================================
def chain_view(memo):
    """One row per step of the memo's route (or, for a draft, of the route it
    will get), saying what happened or what will happen and why:

        state ∈ approved | returned | rejected | skipped     (recorded)
                current | parked                              (now)
                pending | will_skip | blocked                 (ahead, predicted)
                not_reached                                   (finished before it)

    Recorded decisions come from this pass through the chain only (a
    resubmitted memo walks the chain afresh); predictions use the same rules
    as the engine (evaluate_step), so the page never says "no one assigned"
    for a step that will simply be skipped."""
    from .models import MemoApproval

    preview = memo.route_id is None and memo.status == memo.DRAFT
    if preview:
        route = route_for_memo(memo)
        if route is None:
            return []
        steps = list(route.steps.select_related("approver_role", "approver_user").order_by("position", "pk"))
        mode = route.same_rank_behaviour
    else:
        steps = build_memo_chain(memo)
        mode = memo.route.same_rank_behaviour if memo.route_id else "skip"
    if not steps:
        return []
    decided = {}
    for a in round_approvals(memo) if not preview else []:
        if a.step_id:
            decided[step_key(a.step)] = a
    label = {MemoApproval.APPROVE: "approved", MemoApproval.RETURN: "returned",
             MemoApproval.REJECT: "rejected", MemoApproval.SKIP: "skipped"}
    sender_rank = _rank_of(memo.sender) if memo.sender_id else 0
    live = memo.status in (memo.DRAFT, memo.AWAITING) or preview
    current_key = memo.current_step_key if memo.status == memo.AWAITING else ""
    rows = []
    for i, step in enumerate(steps):
        key = step_key(step)
        row = {"step": step, "position": i + 1, "state": "not_reached", "actor": None, "reason": "",
               "decided_by": None, "decided_at": None, "comment": "", "is_current": False}
        a = decided.get(key)
        if key and key == current_key:
            decision, actor, reason = evaluate_step(step, memo, sender_rank=sender_rank, same_rank_mode=mode)
            row.update(state="current" if decision == "act" else "parked", actor=actor, reason=reason,
                       is_current=True)
        elif a is not None:
            row.update(state=label.get(a.decision, "approved"), decided_by=a.actor, decided_at=a.decided_at)
            if a.decision == MemoApproval.SKIP:
                row["reason"] = a.comment
            else:
                row["comment"] = a.comment
        elif live:
            decision, actor, reason = evaluate_step(step, memo, sender_rank=sender_rank, same_rank_mode=mode)
            row.update(state={"act": "pending", "skip": "will_skip", "park": "blocked"}[decision],
                       actor=actor, reason=reason)
        elif memo.status == memo.PUBLISHED:
            row.update(state="skipped", reason="Skipped (recorded before step reasons were kept).")
        rows.append(row)
    return rows


# ===========================================================================
# Keep parked memos moving when people or routes change
# ===========================================================================
def schedule_reconcile():
    """Re-check parked memos once the current transaction commits (at most
    once per transaction, however many saves trigger it)."""
    from django.db import connection
    if getattr(connection, "_memo_reconcile_pending", False):
        return
    connection._memo_reconcile_pending = True

    def run():
        connection._memo_reconcile_pending = False
        try:
            moved = reconcile_parked()
            if moved:
                logger.info("Re-checked parked memos: %s moved on", moved)
        except Exception:
            logger.exception("Could not re-check parked memos")
    transaction.on_commit(run)
