"""ICT supervision lifecycle (oversight_ict work).

An ICT officer supervises an institution while it has live work with NBTE
(onboarding, a QA request, an SOP-03 case). When everything is finished the
supervision completes by itself: it leaves "Assigned to you", counts as done
and is listed under "Supervised institutions". When the institution later
starts something new, the director sees who supervised it before and can
assign that person again in one click.
"""
from __future__ import annotations

import logging

from django.db.models import Q

logger = logging.getLogger(__name__)

APP_DONE = ("ssq_submitted", "withdrawn", "cancelled", "rejected", "approved", "closed")
REQUEST_DONE = ("decided", "completed", "closed", "cancelled", "withdrawn", "rejected", "lapsed")
CASE_DONE = ("closed", "cancelled", "withdrawn", "completed")


def open_work(inst):
    """Short labels of the institution's live work, [] when it is settled."""
    from accreditation.models import Application, QACase, QARequest

    out = []
    try:
        app = (Application.unscoped.filter(institution=inst).exclude(status__in=APP_DONE)
               .order_by("-created_at").first())
        if app is not None:
            out.append(f"onboarding {app.reference}")
        # An onboarding finished but not yet cleared for QA is still live.
        if not out and inst.status in ("proposed", "under_review") and not getattr(inst, "cleared_for_qa_at", None) \
                and Application.unscoped.filter(institution=inst, status="ssq_submitted").exists():
            out.append("self-study review")
        for ref in (QARequest.unscoped.filter(institution=inst).exclude(status__in=REQUEST_DONE)
                    .values_list("reference", flat=True)[:5]):
            out.append(f"request {ref}")
        for ref in (QACase.unscoped.filter(institution=inst).exclude(status__in=CASE_DONE)
                    .filter(completed_at__isnull=True).values_list("reference", flat=True)[:5]):
            out.append(f"case {ref}")
    except Exception:  # pragma: no cover - never block a page on this
        logger.exception("open_work failed for %s", getattr(inst, "code", inst))
        return ["unknown"]
    return out


def complete_if_settled(inst, actor=None):
    """Complete every open ICT supervision of ``inst`` that has nothing left
    to supervise. Returns the number completed."""
    from . import work
    from .models import WorkAssignment as WA

    if open_work(inst):
        return 0
    done = 0
    for wa in WA.objects.filter(institution=inst, kind=WA.OVERSIGHT_ICT, status__in=WA.OPEN):
        try:
            if wa.status in (WA.SUGGESTED, WA.OFFERED):
                work.cancel(wa, actor, "nothing left to supervise")
            else:
                work.complete(wa, actor)
            done += 1
        except Exception:
            logger.exception("could not close supervision %s", wa.pk)
    return done


def sweep_for(user, limit=50):
    """Lazy catch-up for one officer (My work): close their supervisions of
    institutions that have since settled."""
    from .models import WorkAssignment as WA

    n = 0
    for wa in (WA.objects.filter(assignee=user, kind=WA.OVERSIGHT_ICT, status__in=WA.OPEN)
               .select_related("institution")[:limit]):
        if wa.institution_id:
            n += complete_if_settled(wa.institution)
    return n


def supervised_by(user, limit=100):
    """Institutions ``user`` supervised to the end (completed oversight)."""
    from .models import WorkAssignment as WA

    rows, seen = [], set()
    for wa in (WA.objects.filter(assignee=user, kind=WA.OVERSIGHT_ICT, status=WA.COMPLETED)
               .select_related("institution").order_by("-completed_at")[:limit * 2]):
        if wa.institution_id in seen:
            continue
        seen.add(wa.institution_id)
        rows.append(wa)
        if len(rows) >= limit:
            break
    return rows


def previous_supervisor(inst):
    """The officer who last supervised ``inst`` to completion, or None."""
    from .models import WorkAssignment as WA

    wa = (WA.objects.filter(institution=inst, kind=WA.OVERSIGHT_ICT)
          .filter(Q(status=WA.COMPLETED) | Q(closed_reason="nothing left to supervise"))
          .select_related("assignee").order_by("-completed_at", "-assigned_at").first())
    return wa.assignee if wa is not None and wa.assignee and wa.assignee.is_active else None
