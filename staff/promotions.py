"""Promotion service — the only code that writes Promotion rows.

    plan = plan_promotion(roots, target, cascade=True, scope=qs)
    result = apply_promotion(plan, effective_date=..., reason=..., approver=user, notify=True)
    rollback_promotion(promotion, reason=..., approver=user)

Cascade (off by default): for each selected ("root") person, everyone else on
the same designation, in the same department, whose date of present
appointment is LATER than the root's, moves ONE step up the ladder
(Designation.next_designation, else the next higher rank). Each cascaded row
records the root's Promotion in `cascade_source`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core import audit

from .models import Designation, Promotion, Staff


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
    from .nbte_reference import designation_key
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


def target_choices(staff_list):
    """Designations a selection can be promoted to: anything ranked above the
    lowest-ranked current designation in the selection."""
    ranks = [s.designation.rank for s in staff_list if s.designation_id]
    qs = Designation.objects.filter(is_active=True)
    if ranks:
        qs = qs.filter(rank__gt=min(ranks))
    return qs.order_by("-rank", "name")


def grade_for(target: Designation, current: str) -> tuple[str, bool]:
    if target.contediss_min:
        return target.contediss_min, True
    return current, False


def cascade_set(root: Staff, scope) -> list[Staff]:
    """Others on the root's designation, in the root's department, appointed
    to it later than the root."""
    if not (root.designation_id and root.department_id and root.date_present_appointment):
        return []
    return list(
        scope.filter(designation_id=root.designation_id, department_id=root.department_id,
                     date_present_appointment__gt=root.date_present_appointment)
        .exclude(pk=root.pk)
        .select_related("designation", "department")
        .order_by("date_present_appointment", "last_name"))


def plan_promotion(roots, target: Designation, *, cascade=False, scope=None) -> Plan:
    plan = Plan(target=target, cascade=cascade)
    seen = set()
    for s in roots:
        if s.designation_id == target.pk:
            plan.skipped.append((s, f"already {target.name}"))
            seen.add(s.pk)
            continue
        if s.designation_id and s.designation.rank >= target.rank:
            plan.skipped.append((s, f"{s.designation.name} is not below {target.name}"))
            seen.add(s.pk)
            continue
        grade, auto = grade_for(target, s.grade_level)
        plan.changes.append(Change(s, s.designation, target, s.grade_level, grade,
                                   grade_autofilled=auto))
        seen.add(s.pk)
    tiers = {s.designation_id for s in roots}
    if len(tiers) > 1:
        names = sorted({s.designation.name if s.designation_id else "(no designation)" for s in roots})
        plan.warnings.append("The selection spans several designations: " + ", ".join(names) + ".")
    if cascade and scope is not None:
        for change in list(plan.roots):
            root = change.staff
            if not root.date_present_appointment:
                plan.warnings.append(
                    f"{root.full_name} has no date of present appointment, so nobody cascades with them.")
                continue
            step = next_step(root.designation)
            if step is None:
                plan.warnings.append(f"No designation above {root.designation} to cascade into.")
                continue
            for other in cascade_set(root, scope):
                if other.pk in seen:
                    continue
                seen.add(other.pk)
                grade, auto = grade_for(step, other.grade_level)
                plan.changes.append(Change(other, other.designation, step, other.grade_level, grade,
                                           root=root, grade_autofilled=auto))
    if not target.contediss_min:
        plan.warnings.append(
            f"Grade level unchanged — set the CONTEDISS range of {target.name} to enable auto-fill.")
    return plan


@transaction.atomic
def apply_promotion(plan: Plan, *, effective_date: date, reason: str, approver, notify=False) -> Result:
    if not plan.changes:
        raise PromotionError("Nobody to promote.")
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
            effective_date=effective_date, reason=reason, approved_by=approver,
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


def can_rollback(promotion: Promotion) -> tuple[bool, str]:
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
    ok, why = can_rollback(promotion)
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
