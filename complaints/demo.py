"""Phase 7 demo data (``seed_startup --with-demo`` and ``seed_phase7_demo``).

* Three open complaints from the demo institution, one in each SLA state:
  on track, near its deadline, and breached + escalated through every level
  (it is Critical, so the ES is told too).
* Twelve months of confirmed QA revenue for the demo institutions, so the
  leadership dashboards have a history to draw (``--history``).

Every row is tagged in ``qa_core.DemoTag`` and goes with ``--reset-demo``.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from qa_core.models import DemoTag
from qa_core.seed_data_phase7 import DEMO_COMPLAINTS

from . import services, sla
from .models import Complaint, ComplaintLayer


DEMO_CODES = ("DMP", "DCH")  # seed_startup --with-demo


def _demo_codes():
    """The demo institutions present: seed_startup's (DMP, DCH) and the
    Phase 4/5 case demo's (seed_data_phase4.DEMO_INSTITUTION)."""
    from qa_core.seed_data_phase4 import DEMO_INSTITUTION

    return DEMO_CODES + (DEMO_INSTITUTION["code"],)


def _demo_institution():
    """The first demo institution that has an active portal account."""
    from accounts.models import User
    from institutions.models import Institution

    for code in _demo_codes():
        inst = Institution.unscoped.filter(code=code).first()
        if inst is not None and User.objects.filter(institution=inst, user_type="institution",
                                                    is_active=True).exists():
            return inst
    return None


def _raiser(institution):
    from accounts.models import User

    return (User.objects.filter(email__iexact="admin@demo-poly.local", institution=institution,
                                is_active=True).first()
            or User.objects.filter(institution=institution, user_type="institution", is_active=True)
            .order_by("pk").first())


def _back_to(fraction_of_sla, cs, now, cal):
    """Move the stage's clock so ``fraction_of_sla`` of its working time has passed."""
    target = (cs.sla_seconds or 0) * fraction_of_sla
    start = now
    for _ in range(24 * 60):  # step back an hour at a time (at most 60 days)
        if sla.elapsed(start, now, business=cs.business_hours, calendar=cal) >= target:
            break
        start -= datetime.timedelta(hours=1)
    cs.entered_at = start
    cs.due_at = sla.deadline(start, cs.sla_seconds, business=cs.business_hours, calendar=cal)
    cs.save(update_fields=["entered_at", "due_at"])
    # Earlier stages of the same complaint move back with it, so the history reads in order.
    earlier = list(cs.complaint.stages.exclude(pk=cs.pk).filter(left_at__isnull=False).order_by("-entered_at"))
    edge = start
    for prev in earlier:
        span = max(datetime.timedelta(minutes=30), (prev.left_at - prev.entered_at))
        prev.left_at = edge
        prev.entered_at = edge - span
        if prev.due_at and prev.sla_seconds:
            prev.due_at = sla.deadline(prev.entered_at, prev.sla_seconds, business=prev.business_hours,
                                       calendar=cal)
        prev.save(update_fields=["left_at", "entered_at", "due_at"])
        edge = prev.entered_at
    if cs.task_id:
        from qa_core.models import QATaskLink

        QATaskLink.objects.filter(task_id=cs.task_id).update(opened_at=start, due_at=cs.due_at)
    Complaint.unscoped.filter(pk=cs.complaint_id).update(created_at=edge)
    first = cs.complaint.messages.order_by("created_at", "id").first()
    if first is not None:
        type(first).objects.filter(pk=first.pk).update(created_at=edge)


@transaction.atomic
def build_demo(now=None):
    """Create the three demo complaints once. Returns {"created", "total"}."""
    from django.contrib.contenttypes.models import ContentType

    now = now or timezone.now()
    institution = _demo_institution()
    raiser = _raiser(institution) if institution is not None else None
    ct = ContentType.objects.get_for_model(Complaint)
    tagged = DemoTag.objects.filter(content_type=ct).count()
    if institution is None or raiser is None or tagged >= len(DEMO_COMPLAINTS):
        return {"created": 0, "total": tagged}
    cal = sla.Calendar()
    created = 0
    for layer_code, subject, body, priority, _hours, state in DEMO_COMPLAINTS:
        layer = ComplaintLayer.objects.filter(code=layer_code).first()
        if layer is None or Complaint.unscoped.filter(institution=institution, subject=subject).exists():
            continue
        complaint = services.raise_complaint(raiser, layer=layer, subject=subject, body=body, priority=priority)
        DemoTag.tag(complaint)
        created += 1
        if state == "on_track":
            _back_to(0.1, complaint.open_stage(), now, cal)
            continue
        handler = complaint.open_stage().assigned_to
        actor = handler or services.task_bridge.system_user()
        services.act(complaint, "acknowledge", actor, system=handler is None)
        complaint.refresh_from_db()
        if state == "near":
            _back_to(0.7, complaint.open_stage(), now, cal)
            services.add_message(complaint, raiser, "The error says 'file could not be read'. We tried twice.")
            continue
        # breached: past the deadline, then escalated through every level
        _back_to(1.3, complaint.open_stage(), now, cal)
        services.sla_sweep(now)                       # breach + level 1 (the Director)
        cs = complaint.open_stage()
        cs.breached_at = now - datetime.timedelta(hours=17)
        cs.save(update_fields=["breached_at"])
        services.sla_sweep(now)                       # level 2 (Deputy) and level 3 (ES: Critical)
    return {"created": created, "total": DemoTag.objects.filter(content_type=ct).count()}


@transaction.atomic
def build_revenue_history(months=12, now=None):
    """Confirmed demo bills spread over the last ``months`` months (dashboards)."""
    from accounts.models import User
    from institutions.models import Institution
    from payments.models import FeeSchedule, PaymentObligation
    from payments.money import amount_in_words
    from qa_core import flow, references

    now = now or timezone.now()
    schedule = (FeeSchedule.objects.filter(code="programme_processing").first() or FeeSchedule.objects.first())
    institutions = list(Institution.unscoped.filter(code__in=_demo_codes()).order_by("code"))
    if schedule is None or not institutions:
        return 0
    if PaymentObligation.unscoped.filter(formula_snapshot__demo_history=True).exists():
        return 0
    director = (User.objects.filter(email__iexact="finance.director@demo.nbte.local").first()
                or User.objects.filter(role__code="qa-finance-director", is_active=True).order_by("pk").first())
    amounts = [1500000, 2000000, 2750000, 1000000, 4000000, 2000000, 3250000, 2500000, 5000000, 3000000,
               4500000, 6000000]
    made = 0
    for i in range(months):
        when = now - datetime.timedelta(days=30 * (months - 1 - i) + 3)
        inst = institutions[i % len(institutions)]
        amount = Decimal(amounts[i % len(amounts)])
        ob = PaymentObligation.unscoped.create(
            reference=references.issue("payment", inst=inst.code), institution=inst, fee_schedule=schedule,
            description=f"{schedule.name} (demo history)", amount=amount, amount_in_words=amount_in_words(amount),
            formula_snapshot={"demo_history": True, "result": str(amount)}, due_at=when, status="confirmed",
            amount_paid=amount, balance=Decimal("0"), confirmed_at=when)
        PaymentObligation.unscoped.filter(pk=ob.pk).update(created_at=when - datetime.timedelta(days=7))
        instance = flow.start("payment", ob, actor=director)
        flow.force_stage(instance, "confirmed", director, remark="Demo history for the dashboards (Phase 7).")
        PaymentObligation.unscoped.filter(pk=ob.pk).update(status="confirmed", confirmed_at=when)
        DemoTag.tag(ob)
        made += 1
    return made
