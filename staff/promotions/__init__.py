"""Promotion service — the only code that writes Promotion rows.

    plan = plan_promotion(roots)                       # everyone to their own next step (D7)
    plan = plan_promotion(roots, target, actor=es, override_reason="...")   # ES skip (D2)
    plan = plan_promotion(roots, cascade=True, cascade_scope="grade", scope=qs)  # D8
    result = apply_promotion(plan, effective_date=..., reason=..., approver=user, notify=True)
    rollback_promotion(promotion, reason=..., approver=es)                  # ES only (D3)
    eligibility(staff) / eligible_staff(qs) / overdue_staff(qs)             # D4, D5, D10

Rules (Master Plan §7.7):
  * Next step only (D2): a promotion lands on `levels.next_level(staff)`.
    Any other target above the current level needs the Executive Secretary's
    override (`staff.override_promotion_step`) and a written reason.
  * No demotion: a target at or below the current level is always refused;
    the only way down is an audited rollback by the ES (`staff.rollback_promotion`).
  * Cascade (off by default), two scopes:
      "department" - others on the root's designation, in the root's
                     department, appointed to it later than the root;
      "grade"      - everyone on the root's grade level in every department
                     the actor can see.
    Cascaded people move ONE step (their own next level). Each cascaded row
    records the root's Promotion in `cascade_source`.

Grade levels are read only through `staff.promotions.levels` (an adapter that
switches to the GradeLevel model after the pb-org merge).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core import audit
from staff.models import Designation, Promotion, Staff

from . import levels

OVERRIDE_PERM = "staff.override_promotion_step"
ROLLBACK_PERM = "staff.rollback_promotion"


class PromotionError(Exception):
    pass


@dataclass
class Change:
    staff: Staff
    from_designation: Designation | None
    to_designation: Designation
    from_grade: str
    to_grade: str
    root: Staff | None = None          # set on cascaded rows
    grade_autofilled: bool = False
    override: bool = False             # ES override of the next-step rule (D2)

    @property
    def is_cascade(self):
        return self.root is not None


@dataclass
class Plan:
    target: Designation
    changes: list = field(default_factory=list)       # [Change]
    skipped: list = field(default_factory=list)       # [(staff, reason)]
    warnings: list = field(default_factory=list)      # [str]
    cascade: bool = False
    cascade_scope: str = "department"
    override_reason: str = ""

    @property
    def overrides(self):
        return [c for c in self.changes if c.override]

    @property
    def roots(self):
        return [c for c in self.changes if not c.is_cascade]

    @property
    def cascaded(self):
        return [c for c in self.changes if c.is_cascade]


@dataclass
class Result:
    promotions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notified: int = 0


def _not_acting(d):
    from staff.nbte_reference import designation_key
    return "ACTING" not in designation_key(d.name).split()


def next_step(designation: Designation | None) -> Designation | None:
    """The normal promotion from `designation`: its next_designation, else the
    active designation with the next higher rank (a substantive post first)."""
    if designation is None:
        return None
    if designation.next_designation_id and designation.next_designation.is_active:
        return designation.next_designation
    above = list(Designation.objects.filter(is_active=True, rank__gt=designation.rank)
                 .order_by("rank", "name"))
    if not above:
        return None
    low = above[0].rank
    tier = [d for d in above if d.rank == low]
    return next((d for d in tier if _not_acting(d)), tier[0])


def can_override_step(actor) -> bool:
    """May `actor` promote past the next step (ES override, D2)?"""
    return bool(actor and getattr(actor, "is_authenticated", False)
                and (actor.is_superuser or actor.has_perm(OVERRIDE_PERM)))


def can_rollback(actor_or_promotion, promotion=None):
    """Two forms, for compatibility:
      can_rollback(promotion)        -> (ok, why) about the record itself
      can_rollback(actor, promotion) -> (ok, why) including the ES-only rule (D3)
    """
    if promotion is None:
        return _record_can_rollback(actor_or_promotion)
    actor = actor_or_promotion
    if not (actor and getattr(actor, "is_authenticated", False)
            and (actor.is_superuser or actor.has_perm(ROLLBACK_PERM))):
        return False, "Only the Executive Secretary can roll back a promotion."
    return _record_can_rollback(promotion)


def target_choices(staff_list, actor=None):
    """Designations a selection may be promoted to. Normally only the next
    step of each person; with the ES override, anything above the lowest
    current level."""
    if can_override_step(actor):
        ranks = [s.designation.rank for s in staff_list if s.designation_id]
        qs = Designation.objects.filter(is_active=True)
        if ranks:
            qs = qs.filter(rank__gt=min(ranks))
        return qs.order_by("-rank", "name")
    ids = {step.designation.pk for step in (levels.next_level(s) for s in staff_list) if step}
    return Designation.objects.filter(pk__in=ids).order_by("-rank", "name")


def grade_for(target: Designation, current: str) -> tuple[str, bool]:
    step = levels.step_to(target, current)
    return step.grade, step.grade_autofilled


def cascade_set(root: Staff, scope, cascade_scope="department") -> list[Staff]:
    """Who cascades with `root`.
    department: others on the root's designation, in the root's department,
                appointed to it later than the root;
    grade:      everyone on the root's grade level, any department in `scope`."""
    if cascade_scope == "grade":
        return list(scope.filter(levels.same_level_filter(root)).exclude(pk=root.pk)
                    .select_related("designation", "department")
                    .order_by("department__name", "last_name"))
    if not (root.designation_id and root.department_id and root.date_present_appointment):
        return []
    return list(
        scope.filter(designation_id=root.designation_id, department_id=root.department_id,
                     date_present_appointment__gt=root.date_present_appointment)
        .exclude(pk=root.pk)
        .select_related("designation", "department")
        .order_by("date_present_appointment", "last_name"))


def _change_to(staff, step, *, root=None, override=False):
    return Change(staff, staff.designation, step.designation, staff.grade_level, step.grade,
                  root=root, grade_autofilled=step.grade_autofilled, override=override)


def plan_promotion(roots, target: Designation | None = None, *, cascade=False,
                   cascade_scope="department", scope=None, actor=None, override_reason="") -> Plan:
    """Build (never save) a promotion plan.

    target None  -> each person goes to their own next level (bulk, D7).
    target given -> must be the person's next level; a higher target needs
                    the ES override and `override_reason` (D2). A target at or
                    below the current level is refused (no demotion).
    """
    override_reason = (override_reason or "").strip()
    plan = Plan(target=target, cascade=cascade, cascade_scope=cascade_scope,
                override_reason=override_reason)
    seen = set()
    for s in roots:
        seen.add(s.pk)
        step = levels.next_level(s)
        if target is None:
            if step is None:
                plan.skipped.append((s, "no level above " + (s.designation.name if s.designation_id
                                                              else "(no designation)")))
                continue
            plan.changes.append(_change_to(s, step))
            continue
        if s.designation_id == target.pk:
            plan.skipped.append((s, f"already {target.name}"))
            continue
        if s.designation_id and levels.level_rank(s.designation) >= levels.level_rank(target):
            plan.skipped.append((s, f"{s.designation.name} is not below {target.name} (no demotion)"))
            continue
        if step is not None and step.designation.pk == target.pk:
            plan.changes.append(_change_to(s, step))
            continue
        nxt = step.designation.name if step else "none"
        if not can_override_step(actor):
            plan.skipped.append((s, f"only the next step ({nxt}) is allowed; skipping a level "
                                    "needs the Executive Secretary's override"))
            continue
        if not override_reason:
            plan.skipped.append((s, f"skipping past {nxt} needs a reason for the override"))
            continue
        plan.changes.append(_change_to(s, levels.step_to(target, s.grade_level), override=True))
    tiers = {s.designation_id for s in roots}
    if len(tiers) > 1 and target is not None:
        names = sorted({s.designation.name if s.designation_id else "(no designation)" for s in roots})
        plan.warnings.append("The selection spans several designations: " + ", ".join(names) + ".")
    if cascade and scope is not None:
        for change in list(plan.roots):
            root = change.staff
            if cascade_scope != "grade" and not root.date_present_appointment:
                plan.warnings.append(
                    f"{root.full_name} has no date of present appointment, so nobody cascades with them.")
                continue
            if cascade_scope == "grade" and not (root.grade_level or "").strip():
                plan.warnings.append(f"{root.full_name} has no grade level, so nobody cascades with them.")
                continue
            for other in cascade_set(root, scope, cascade_scope):
                if other.pk in seen:
                    continue
                seen.add(other.pk)
                step = levels.next_level(other)
                if step is None:
                    plan.skipped.append((other, "no level above " + (
                        other.designation.name if other.designation_id else "(no designation)")))
                    continue
                plan.changes.append(_change_to(other, step, root=root))
    for c in plan.changes:
        if not c.grade_autofilled and not c.to_designation.contediss_min:
            plan.warnings.append(
                f"Grade level unchanged — set the CONTEDISS range of {c.to_designation.name} to enable auto-fill.")
            break
    return plan


@transaction.atomic
def apply_promotion(plan: Plan, *, effective_date: date, reason: str, approver, notify=False) -> Result:
    if not plan.changes:
        raise PromotionError("Nobody to promote.")
    if plan.overrides and not can_override_step(approver):
        raise PromotionError("Only the Executive Secretary can promote past the next step.")
    result = Result(warnings=list(plan.warnings))
    root_rows = {}
    ordered = plan.roots + plan.cascaded
    for change in ordered:
        s = Staff.objects.select_for_update().get(pk=change.staff.pk)
        if s.designation_id != (change.from_designation.pk if change.from_designation else None):
            raise PromotionError(f"{s.full_name}'s designation changed while you were reviewing. "
                                 "Preview again.")
        promo = Promotion.objects.create(
            staff=s, from_designation=change.from_designation, to_designation=change.to_designation,
            from_grade_level=change.from_grade, to_grade_level=change.to_grade,
            from_present_appointment=s.date_present_appointment,
            effective_date=effective_date,
            reason=(f"{reason}\n[ES override: {plan.override_reason}]".strip() if change.override else reason),
            approved_by=approver,
            cascade_source=root_rows.get(change.root.pk) if change.root else None,
        )
        if not change.root:
            root_rows[s.pk] = promo
        s.designation = change.to_designation
        s.grade_level = change.to_grade
        s.date_present_appointment = effective_date
        s.save()
        audit.log("promotion", s, actor=approver, changes={
            "designation": {"old": str(change.from_designation or ""), "new": change.to_designation.name},
            "grade_level": {"old": change.from_grade, "new": change.to_grade},
            "promotion_id": promo.pk,
            "cascade_of": change.root.pk if change.root else None,
            "override": ({"reason": plan.override_reason} if change.override else None),
        }, note=(reason or "")[:255])
        result.promotions.append(promo)
    if notify:
        result.notified = _notify(result.promotions, approver)
    return result


def _notify(promotions, actor):
    from notifications.services import notify
    sent = 0
    heads = {}
    for p in promotions:
        s = p.staff
        url = reverse("staff:detail", args=[s.pk])
        if s.user_id:
            if notify(s.user, title="Congratulations on your promotion",
                      body=f"You have been promoted to {p.to_designation.name} "
                           f"with effect from {p.effective_date.day} {p.effective_date:%B %Y}.",
                      url=url, actor=actor, metadata={"promotion": p.pk}):
                sent += 1
        dept = s.department
        if dept is not None and getattr(dept, "head_id", None):
            heads.setdefault(dept.head_id, (dept.head, []))[1].append(p)
    for head, promos in heads.values():
        if head.pk == getattr(actor, "pk", None):
            continue
        names = ", ".join(p.staff.full_name for p in promos[:5]) + ("…" if len(promos) > 5 else "")
        if notify(head, title=f"{len(promos)} promotion{'s' if len(promos) != 1 else ''} in your department",
                  body=names, url=reverse("staff:promotion_history"), actor=actor):
            sent += 1
    return sent


def _record_can_rollback(promotion: Promotion) -> tuple[bool, str]:
    if promotion.is_reversal:
        return False, "This entry is itself a rollback."
    if hasattr(promotion, "reversed_by"):
        try:
            if promotion.reversed_by:
                return False, "Already rolled back."
        except Promotion.DoesNotExist:
            pass
    if promotion.from_designation_id is None:
        return False, "There is no earlier designation to go back to."
    if promotion.staff.designation_id != promotion.to_designation_id:
        return False, (f"{promotion.staff.full_name} is no longer {promotion.to_designation.name}; "
                       "roll back the later change first.")
    return True, ""


@transaction.atomic
def rollback_promotion(promotion: Promotion, *, reason: str, approver) -> Promotion:
    reason = (reason or "").strip()
    if not reason:
        raise PromotionError("A reason is required to roll back a promotion.")
    promotion = Promotion.objects.select_for_update().select_related(
        "staff", "from_designation", "to_designation").get(pk=promotion.pk)
    ok, why = can_rollback(approver, promotion)
    if not ok:
        raise PromotionError(why)
    s = Staff.objects.select_for_update().get(pk=promotion.staff_id)
    reversal = Promotion.objects.create(
        staff=s, from_designation=promotion.to_designation, to_designation=promotion.from_designation,
        from_grade_level=s.grade_level, to_grade_level=promotion.from_grade_level,
        from_present_appointment=s.date_present_appointment,
        effective_date=timezone.localdate(), reason=reason, approved_by=approver, reverses=promotion,
    )
    s.designation = promotion.from_designation
    s.grade_level = promotion.from_grade_level
    s.date_present_appointment = promotion.from_present_appointment
    s.save()
    audit.log("promotion_reversal", s, actor=approver, changes={
        "designation": {"old": promotion.to_designation.name, "new": promotion.from_designation.name},
        "grade_level": {"old": promotion.to_grade_level, "new": promotion.from_grade_level},
        "reverses": promotion.pk, "promotion_id": reversal.pk,
    }, note=reason[:255])
    return reversal


# ---------------------------------------------------------------------------
# Eligibility and the overdue tracker (D4, D5, D10)
# ---------------------------------------------------------------------------
@dataclass
class Eligibility:
    eligible: bool
    overdue: bool
    years_in_grade: int | None
    min_years: int
    due_on: date | None
    next_step: object = None          # levels.Step | None
    reason: str = ""


def _whole_years(start, on):
    return on.year - start.year - ((on.month, on.day) < (start.month, start.day))


def eligibility(staff: Staff, on: date | None = None) -> Eligibility:
    on = on or timezone.localdate()
    step = levels.next_level(staff)
    years_needed = levels.min_years_for(staff)
    start = staff.date_present_appointment
    if start is None:
        return Eligibility(False, False, None, years_needed, None, step,
                           "No date of present appointment recorded.")
    due = levels.due_date(staff)
    years = _whole_years(start, on)
    if step is None:
        return Eligibility(False, False, years, years_needed, due, None, "Already at the top of the ladder.")
    met = due is not None and due <= on
    from datetime import timedelta
    overdue = met and due + timedelta(days=levels.overdue_grace_days()) < on
    why = "" if met else f"Due on {due:%d %b %Y}." if due else ""
    return Eligibility(met, overdue, years, years_needed, due, step, why)


def eligible_staff(qs, on: date | None = None):
    """Staff in `qs` whose time in grade is met and who have a next level."""
    on = on or timezone.localdate()
    return qs.filter(levels.eligible_filter(on)).exclude(
        designation_id__in=levels.top_level_designation_ids()).exclude(designation__isnull=True)


def overdue_staff(qs, on: date | None = None):
    """Eligible for longer than hr.promotion.overdue_grace_days (default 90)."""
    on = on or timezone.localdate()
    return qs.filter(levels.eligible_filter(on, grace_days=levels.overdue_grace_days())).exclude(
        designation_id__in=levels.top_level_designation_ids()).exclude(designation__isnull=True)


def overdue_notice_recipients():
    """HR and ES: active staff accounts holding staff.promote_staff (and the ES
    rollback permission holders). Superusers are left out unless they hold the
    permission through a role or group."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    perms = Permission.objects.filter(content_type__app_label="staff",
                                      codename__in=["promote_staff", "rollback_promotion"])
    q = (Q(role__permissions__in=perms) | Q(role__groups__permissions__in=perms)
         | Q(groups__permissions__in=perms) | Q(user_permissions__in=perms))
    return list(User.objects.filter(q, is_active=True, user_type="staff").distinct())


def send_overdue_notice(on: date | None = None) -> int:
    """Weekly: tell HR and the ES how many staff are overdue (D10)."""
    from notifications.services import notify
    on = on or timezone.localdate()
    overdue = overdue_staff(Staff.objects.all(), on)
    n = overdue.count()
    if not n:
        return 0
    names = ", ".join(s.full_name for s in overdue.order_by("date_present_appointment")[:5])
    url = reverse("staff:promotions") + "?tab=overdue"
    sent = 0
    for user in overdue_notice_recipients():
        visible = n
        try:
            from accounts.scoping import scope_to_department
            visible = scope_to_department(overdue, user).count()
        except Exception:
            pass
        if not visible:
            continue
        if notify(user, title=f"{visible} staff overdue for promotion",
                  body=(names + ("…" if n > 5 else "")) if visible == n else
                  f"{visible} in the departments you look after.",
                  url=url, metadata={"kind": "promotion_overdue"}):
            sent += 1
    return sent
