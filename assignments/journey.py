"""P.6 (X2, X3, G1.g #1): what the staff institution page shows, per variant.

    full        whole journey: every department's part (only full viewers and
                the institution's ICT supervisor)
    department  "You see your department's part": the department's clearances,
                cases, complaints and work only — no other department's name,
                people or progress
    mine        "My work on this institution": the viewer's own assignments
"""
from __future__ import annotations

from . import access
from .models import WorkAssignment

# Which institution-page tab needs which scope.
TAB_SCOPES = {"overview": "summary", "programmes": "summary", "documents": "documents", "users": "summary",
              "applications": "documents", "payments": "payments", "changes": "full_journey",
              "timeline": "summary"}


def allowed_tabs(user, inst, tabs, variant):
    if variant == "full" or variant is None:
        return list(tabs)
    out = []
    for key, label in tabs:
        scope = TAB_SCOPES.get(key, "full_journey")
        if key in ("overview", "timeline", "documents") or access.can_see(user, inst, scope):
            out.append((key, label))
    return out


def _clearance_rows(inst, dept_id=None):
    from accreditation.models import Application, DepartmentalClearance
    app = Application.unscoped.filter(institution=inst).order_by("-created_at").first()
    if app is None:
        return []
    qs = DepartmentalClearance.unscoped.filter(application=app, round=max(app.round, 1)).select_related("department")
    if dept_id is not None:
        qs = qs.filter(department_id=dept_id)
    return list(qs.order_by("department__name"))


def context(user, inst, *, request=None):
    variant = access.journey_variant(user, inst)
    ctx = {"variant": variant, "privacy_mode": access.privacy_mode()}
    if variant is None:
        return ctx
    if variant == "full":
        ctx["dept_rows"] = _clearance_rows(inst)
        work = (WorkAssignment.objects.filter(institution=inst, status__in=WorkAssignment.OPEN)
                .select_related("assignee", "department").order_by("department__name", "kind"))
        ctx["open_work"] = list(work[:40])
        ctx["supervisors"] = [wa.assignee for wa in work if wa.kind == WorkAssignment.OVERSIGHT_ICT]
        ctx["is_supervisor"] = access.is_ict_supervisor(user, inst)
        if access.assignment_mode():
            access.record_event(user, inst, "view_full_journey", request=request, institution=inst)
    elif variant == "department":
        dept = user.department
        ctx["department"] = dept
        ctx["dept_rows"] = _clearance_rows(inst, dept.pk if dept else -1)
        ctx["open_work"] = list(WorkAssignment.objects.filter(
            institution=inst, department=dept, status__in=WorkAssignment.OPEN)
            .select_related("assignee").order_by("kind")[:40])
    else:
        ctx["my_work"] = list(WorkAssignment.objects.filter(institution=inst, assignee=user)
                              .order_by("-assigned_at")[:20])
        ctx["my_grants"] = list(access.AccessGrant.objects.live(user).filter(institution=inst))
    return ctx
