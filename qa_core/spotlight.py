"""v2.2 — "New institutions" spotlight and the ICT tracker.

A newly registered institution appears boldly on the dashboards of:

* super admins / superusers, the Executive Secretary and the Director of ICT
  (every new institution), and
* the director of the department that handles it (its own institutions only).

Until an ICT officer is assigned to oversee it, the card is highlighted and
the Director of ICT (or ES / super admin) can assign one right there, with the
rotation's suggestion pre-selected. The assigned officer then sees the
institution in "Institutions you are tracking", with its progress through
onboarding (SOP-01) and the internal stages (SOP-03).
"""
from __future__ import annotations

import datetime

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

import config

ONBOARDING_STATUSES = ("proposed", "under_review")


def _days():
    try:
        return max(1, int(config.get("qa.spotlight.days", 30)))
    except (TypeError, ValueError):
        return 30


def viewer_kind(user):
    """'full' (ES, super admin, Director of ICT), 'handling' (a department
    director), or '' (nothing to show)."""
    if not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "staff") != "staff":
        return ""
    import org
    from assignments import access

    if user.is_superuser or org.is_super_admin(user) or org.is_es(user) \
            or org.is_director(user, access.ict_department_code()):
        return "full"
    if org.directed_department(user) is not None:
        return "handling"
    return ""


def _can_assign(user):
    import org
    from assignments import access

    return bool(user.is_superuser or org.is_super_admin(user) or org.is_es(user)
                or org.is_director(user, access.ict_department_code()))


def progress(inst, app=None):
    """[(label, state)] onboarding steps + the SOP-03 stage when a case is open,
    and a percentage. v2.2: cached for two minutes per institution (it costs a
    dozen queries and is shown on several dashboards)."""
    from django.core.cache import cache

    stamp = int(inst.updated_at.timestamp()) if getattr(inst, "updated_at", None) else 0
    key = f"qa:spotlight:progress:{inst.pk}:{stamp}:{getattr(app, 'pk', '')}"
    try:
        hit = cache.get(key)
    except Exception:
        hit = None
    if hit is not None:
        return hit
    out = _progress(inst, app)
    try:
        cache.set(key, out, 120)
    except Exception:
        pass
    return out


def _progress(inst, app=None):
    steps = []
    try:
        from accreditation import onboarding_v2
        from accreditation.models import Application

        app = app or Application.unscoped.filter(institution=inst).order_by("-created_at").first()
        head = onboarding_v2.header(app, inst)
        steps = [{"label": "Registered", "state": "done"}] + [
            {"label": s["label"], "state": s["state"], "note": s.get("note", "")} for s in head["steps"]]
    except Exception:  # progress must never break a dashboard
        steps = [{"label": "Registered", "state": "done"}]
    case_label = public_case = ""
    try:
        from accreditation import case_services as cs
        from accreditation.models import QACase

        case = QACase.unscoped.filter(institution=inst, completed_at__isnull=True).order_by("-created_at").first()
        if case is not None:
            current = cs.stage_objects(case)
            # Stage 0 ("case opened") has no SOP number: count it as 0, not as "no stage".
            nos = [s.sop_stage_no or 0 for s in current]
            if nos:
                first = min(current, key=lambda s: s.sop_stage_no or 0)
                case_label = f"SOP-03 stage {min(nos)} of 17 — {first.label}"
                # Institutions never see internal codes or committee names.
                public = getattr(first, "public_label", "") or ""
                public_case = (f"Accreditation in progress · stage {min(nos)} of 17" if min(nos)
                               else "Accreditation in progress · case opened") + (f" — {public}" if public else "")
    except Exception:
        case_label = ""
    done = sum(1 for s in steps if s["state"] == "done")
    pct = round(100 * done / max(len(steps), 1))
    return {"steps": steps, "pct": pct, "case": case_label, "public_case": public_case,
            "current": next((s["label"] for s in steps if s["state"] == "current"), "")}


def _supervisors(inst_ids):
    from assignments.models import WorkAssignment

    out = {}
    for wa in (WorkAssignment.objects.filter(institution_id__in=inst_ids, kind=WorkAssignment.OVERSIGHT_ICT,
                                             status__in=WorkAssignment.OPEN)
               .select_related("assignee").order_by("assigned_at")):
        out[wa.institution_id] = wa.assignee
    return out


def ict_candidates():
    from accounts.models import User
    from qa_core.departments import qa_department

    ict = qa_department("ict")
    if ict is None:
        return []
    return list(User.objects.filter(is_active=True, user_type=User.STAFF)
                .filter(Q(department=ict) | Q(staff_profile__department=ict)).distinct()
                .order_by("first_name", "last_name"))


def new_institutions(user, *, limit=8):
    """The spotlight for ``user`` (empty list when they should not see it)."""
    kind = viewer_kind(user)
    if not kind:
        return {"kind": "", "rows": [], "unassigned": 0}
    from institutions.models import Institution

    since = timezone.now() - datetime.timedelta(days=_days())
    qs = Institution.unscoped.select_related("category", "institution_type", "state", "handling_department",
                                             "ownership")
    qs = qs.filter(status__in=ONBOARDING_STATUSES)
    if kind == "handling":
        from staff.models import Department

        qs = qs.filter(handling_department__in=Department.objects.filter(director=user))
    sup = _supervisors(list(qs.values_list("pk", flat=True)))
    insts = [i for i in qs.order_by("-created_at") if i.created_at >= since or i.pk not in sup]
    unassigned = sum(1 for i in insts if i.pk not in sup)
    insts.sort(key=lambda i: (i.pk in sup, -i.created_at.timestamp()))
    can_assign = _can_assign(user)
    candidates = ict_candidates() if can_assign else []
    rows = []
    # One rotation suggestion serves every unassigned card (it names the next
    # officer in line); cached for five minutes — the rotation is expensive.
    shared_suggestion = None
    first_unassigned = next((i for i in insts[:limit] if i.pk not in sup), None)
    if can_assign and first_unassigned is not None:
        shared_suggestion = cache.get("spotlight:suggest:ict")
        if shared_suggestion is None:
            try:
                import assignments.rotation as rotation

                s = rotation.suggest_ict_supervisor(first_unassigned, record=False)
                shared_suggestion = s.suggested.user.pk if s.suggested is not None else 0
            except Exception:
                shared_suggestion = 0
            cache.set("spotlight:suggest:ict", shared_suggestion, 300)
    for inst in insts[:limit]:
        suggestion = (shared_suggestion or None) if (can_assign and inst.pk not in sup) else None
        rows.append({"inst": inst, "supervisor": sup.get(inst.pk), "is_new": inst.created_at >= since,
                     "age_days": (timezone.now() - inst.created_at).days, "progress": progress(inst),
                     "suggestion": suggestion})
    return {"kind": kind, "rows": rows, "unassigned": unassigned, "total": len(insts), "can_assign": can_assign,
            "candidates": candidates, "days": _days()}


def tracked(user, *, limit=12):
    """Institutions the ICT officer oversees, with live progress."""
    from assignments import access
    from institutions.models import Institution

    if not getattr(user, "is_authenticated", False):
        return []
    from assignments.models import WorkAssignment

    ids = set(access.supervised_institution_ids(user))
    offered = set(WorkAssignment.objects.filter(assignee=user, kind=WorkAssignment.OVERSIGHT_ICT,
                                                status__in=(WorkAssignment.SUGGESTED, WorkAssignment.OFFERED))
                  .values_list("institution_id", flat=True))
    ids |= offered
    if not ids:
        return []
    insts = list(Institution.unscoped.filter(pk__in=ids).select_related("category", "state")
                 .order_by("-updated_at")[:limit])
    return [{"inst": i, "progress": progress(i), "to_accept": i.pk in offered} for i in insts]
