"""The staff home "work board" (Phase 7 polish): what needs me now.

Three panels, each defensive (a failing source shows an empty panel, never a
500) and returned as plain dicts so the home page cache can pickle them:

* **Tasks** — my open tasks, overdue and due-today first;
* **Pending confirmations** — two-step QA changes I can confirm, and (for the
  Director of Finance) payments ready for the final decision;
* **Recent QA activity** — the latest timeline events I may see, plus the
  complaints on my desk.

Anything urgent (overdue, expiring within a day, a breached complaint) is
flagged ``urgent`` and the template gives it a glowing edge.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import logging

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def _url(name, *args, query=""):
    try:
        return reverse(name, args=args) + (f"?{query}" if query else "")
    except NoReverseMatch:
        return ""


def tasks_panel(user, limit=6):
    from dashboards.counts import my_open_tasks

    today = timezone.localdate()
    # Phase 8: the old double .exclude() over assignee rows matched the user
    # and the "done" state on *different* rows, so a task disappeared from
    # here as soon as a colleague on it finished their part.
    qs = my_open_tasks(user)
    total = qs.count()
    rows = sorted(qs.only("pk", "reference", "title", "due_date", "priority", "state")[:60],
                  key=lambda t: (t.due_date is None, t.due_date or today, t.pk))
    items = []
    for t in rows[:limit]:
        overdue = bool(t.due_date and t.due_date < today)
        due_today = bool(t.due_date and t.due_date == today)
        items.append({
            "title": t.title, "ref": t.reference, "url": _url("tasks:detail", t.pk),
            "meta": ("Overdue since " + t.due_date.strftime("%d %b") if overdue else
                     "Due today" if due_today else
                     ("Due " + t.due_date.strftime("%d %b") if t.due_date else "No due date")),
            "urgent": overdue or (t.priority in ("high", "urgent", "critical") and due_today),
            "tone": "danger" if overdue else ("warn" if due_today else "neutral"),
            "qa": t.title.startswith("[QA]"),
        })
    overdue_total = sum(1 for t in rows if t.due_date and t.due_date < today)
    return {"items": items, "total": total, "overdue": overdue_total, "url": _url("tasks:list")}


def confirmations_panel(user, limit=5):
    from qa_core.actions import can_decide, visible_to

    now = timezone.now()
    items = []
    open_items = list(visible_to(user).filter(status="pending", expires_at__gt=now).exclude(initiated_by=user)
                      .select_related("policy", "initiated_by").order_by("expires_at")[:100])
    decidable = [pa for pa in open_items if can_decide(pa, user)[0]]
    for pa in decidable[:limit]:
        soon = bool(pa.expires_at and pa.expires_at - now < datetime.timedelta(hours=24))
        who = pa.initiated_by.get_full_name() or pa.initiated_by.email
        items.append({"title": pa.policy.label if pa.policy_id else pa.reference,
                      "meta": f"{pa.target_label or pa.reference} · from {who}",
                      "url": _url("qa:pending_detail", pa.pk), "urgent": soon,
                      "tone": "danger" if soon else "pending",
                      "badge": "Expires today" if soon else "Waiting"})
    total = len(decidable)
    # The Director of Finance: payments ready for the final decision.
    try:
        from payments import services as pay
        from payments.models import PaymentVerification

        if pay.is_final_approver(user):
            ready = [i for i in PaymentVerification.objects.for_user(user).filter(is_open=True)
                     .exclude(decision="").select_related("obligation")[:100] if pay.ready_for_final(i)[0]]
            if ready:
                items.insert(0, {"title": f"{len(ready)} payment{'s' if len(ready) != 1 else ''} ready for your "
                                          f"final confirmation",
                                 "meta": "Finance → Payment verification",
                                 "url": _url("qa_fin:queue", query="stage=final"), "urgent": True,
                                 "tone": "danger", "badge": "Director"})
                total += len(ready)
    except Exception:  # payments are optional for this panel
        logger.debug("work board: payment confirmations unavailable", exc_info=True)
    return {"items": items[:limit], "total": total, "url": _url("qa:pending_list", query="status=pending")}


def activity_panel(user, limit=6):
    from qa_core.models import TimelineEvent

    internal = user.is_superuser or user.has_perm("qa_core.view_timeline_internal")
    qs = TimelineEvent.objects.for_user(user).select_related("institution").order_by("-at", "-id")
    if not internal:
        qs = qs.filter(visibility="public")
    items = []
    for ev in qs[:limit]:
        text = (ev.internal_text or ev.public_text) if internal else ev.public_text
        if not text:
            continue
        items.append({"title": text[:140], "meta": f"{ev.institution.code} · {timezone.localtime(ev.at):%d %b, %H:%M}",
                      "url": _url("qa_inst:detail", ev.institution.code), "urgent": "breached" in ev.event_code,
                      "tone": "danger" if "breached" in ev.event_code else "neutral"})
    complaints = {"mine": 0, "overdue": 0, "url": ""}
    try:
        from complaints import views as cviews
        from complaints.models import ComplaintStage

        if cviews.can_open(user):
            mine = ComplaintStage.objects.filter(assigned_to=user, left_at__isnull=True, pauses_sla=False,
                                                 complaint__closed_at__isnull=True)
            complaints = {"mine": mine.values("complaint_id").distinct().count(),
                          "overdue": mine.filter(due_at__lt=timezone.now()).values("complaint_id").distinct().count(),
                          "url": _url("qa_complaints:queue", query="tab=mine")}
    except Exception:
        logger.debug("work board: complaints unavailable", exc_info=True)
    return {"items": items, "complaints": complaints, "url": _url("qa_inst:list")}


def build(user):
    """The whole board, or None for users with nothing to show."""
    if not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return None
    board = {}
    for key, fn in (("tasks", tasks_panel), ("confirmations", confirmations_panel), ("activity", activity_panel)):
        try:
            board[key] = fn(user)
        except Exception:
            logger.exception("work board panel %s failed", key)
            board[key] = {"items": [], "total": 0, "url": ""}
    board["urgent"] = (sum(1 for i in board["tasks"].get("items", []) if i.get("urgent"))
                       + sum(1 for i in board["confirmations"].get("items", []) if i.get("urgent"))
                       + (board["activity"].get("complaints") or {}).get("overdue", 0))
    return board
