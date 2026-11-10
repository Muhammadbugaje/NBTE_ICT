"""Role dashboards.

Each view is thin: guard the role, ask `services` for a context dict,
render. All data gathering lives in `services.py` and is defensive.
"""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

import org

from . import services


def _guard(user, *allowed_role_codes, permission=None):
    if not user.is_authenticated:
        raise PermissionDenied
    if user.is_superuser:
        return
    import org
    if org.has_role(user, *allowed_role_codes):
        return
    if permission and user.has_perm(permission):
        return
    raise PermissionDenied("You don't have access to this dashboard.")


def _render(request, template, key, title, ctx):
    # Phase 7: the QA overview (revenue, institutions, deadlines, cases).
    if "qa" not in ctx:
        try:
            from . import qa_metrics

            ctx["qa"] = qa_metrics.overview()
        except Exception:  # never break a dashboard over the QA panel
            ctx["qa"] = None
    # v2.2: new institutions spotlight (ES, super admin, Director of ICT,
    # handling directors) and the ICT officer's tracker.
    try:
        from qa_core import spotlight

        ctx.setdefault("spotlight", spotlight.new_institutions(request.user))
        ctx.setdefault("tracked", spotlight.tracked(request.user))
    except Exception:  # never break a dashboard over the spotlight
        ctx.setdefault("spotlight", None)
    from assignments import inbox   # v2.3: "Your work" opens each task where it is done

    for k, v in inbox.context(request.user).items():
        ctx.setdefault(k, v)
    ctx["dashboard_key"] = key
    ctx["dashboard_title"] = title
    ctx["now"] = timezone.now()
    return render(request, template, ctx)


@login_required
def self_service(request):
    ctx = services.self_service_context(request.user)
    # Phase 9: personal insights (?period=week|month|quarter); data in dashboards/insights.py
    from . import insights

    ctx["insights"] = insights.chart_context(request.user, request.GET.get("period"))
    try:
        from qa_core import spotlight

        ctx["spotlight"] = spotlight.new_institutions(request.user)
        ctx["tracked"] = spotlight.tracked(request.user)
    except Exception:
        pass
    from assignments import inbox   # v2.3: officers see their assigned documents/SSQs first

    ctx.update(inbox.context(request.user))
    return render(request, "dashboards/self_service.html", ctx)


# --------------------------------------------------------------------------
# Director
# --------------------------------------------------------------------------
@login_required
def director(request):
    _guard(request.user, "director")
    ctx = services.director_context(request.user)
    return _render(request, "dashboards/director.html",
                   "director", "Director of ICT", ctx)


# --------------------------------------------------------------------------
# Deputy Director
# --------------------------------------------------------------------------
@login_required
def deputy(request):
    _guard(request.user, "deputy-director")
    ctx = services.deputy_context(request.user)
    return _render(request, "dashboards/deputy.html",
                   "deputy", "Deputy Director", ctx)


# --------------------------------------------------------------------------
# Executive Secretary
# --------------------------------------------------------------------------
@login_required
def es(request):
    _guard(request.user, "executive-secretary")
    ctx = services.es_context(request.user)
    return _render(request, "dashboards/es.html",
                   "es", "Executive Secretary", ctx)


# --------------------------------------------------------------------------
# Chairman (permission-gated)
# --------------------------------------------------------------------------
@login_required
def chairman(request):
    _guard(request.user, permission="accounts.view_chairman_dashboard")  # review L1
    ctx = services.chairman_context(request.user)
    return _render(request, "dashboards/chairman.html",
                   "chairman", "Chairman", ctx)


# --------------------------------------------------------------------------
# Super Admin
# --------------------------------------------------------------------------
@login_required
def superadmin(request):
    _guard(request.user, "super-admin")
    ctx = services.superadmin_context(request.user)
    return _render(request, "dashboards/superadmin.html",
                   "superadmin", "Super Admin", ctx)


# --------------------------------------------------------------------------
# Audit log page
# --------------------------------------------------------------------------
@login_required
def audit_log(request):
    if not (request.user.is_superuser
            or request.user.has_perm("accounts.access_all_departments")
            or org.has_role(request.user, "super-admin", "director")):
        raise PermissionDenied

    try:
        page = int(request.GET.get("page") or 1)
    except (TypeError, ValueError):
        page = 1

    def _int_or_none(key):
        v = request.GET.get(key)
        if not v:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    ctx = services.audit_log_context(
        request.user,
        page=page,
        action=(request.GET.get("action") or "").strip() or None,
        actor_id=_int_or_none("actor"),
        department_id=_int_or_none("department"),
        days=_int_or_none("days"),
        search=(request.GET.get("q") or "").strip() or None,
    )

    # ---- Pagination query string: every current filter EXCEPT page ------
    from urllib.parse import urlencode
    filter_params = {
        k: v for k, v in {
            "action":      request.GET.get("action", ""),
            "actor":       request.GET.get("actor", ""),
            "department":  request.GET.get("department", ""),
            "days":        request.GET.get("days", ""),
            "q":           request.GET.get("q", ""),
        }.items() if v
    }
    ctx["base_query"] = urlencode(filter_params)

    # ---- Actor picker (superuser only) ----------------------------------
    if ctx.get("can_filter_actor"):
        from accounts.models import User as _User
        ctx["users"] = list(
            _User.objects
            .filter(is_active=True)
            .select_related("department", "role")
            .order_by("first_name", "last_name")[:250]
        )

    ctx["dashboard_key"] = "superadmin"
    ctx["dashboard_title"] = "Audit log"
    ctx["now"] = timezone.now()
    return render(request, "dashboards/audit_log.html", ctx)


# --------------------------------------------------------------------------
# Audit log CSV export
# --------------------------------------------------------------------------
import csv
from django.http import HttpResponse


@login_required
def export_audit_log(request):
    """Stream a CSV of audit entries matching the CURRENT filter set.

    Same permission gate as `audit_log`. Same filter parameter names, so
    the download link on the audit page can pass `?{{ request.GET.urlencode }}`
    straight through and stay in sync.

    Note: this writes CSV inline. If/when the shared `core` import/export
    utility exposes a streaming CSV writer, swap the generator below for it.
    """
    if not (request.user.is_superuser
            or request.user.has_perm("accounts.access_all_departments")
            or org.has_role(request.user, "super-admin", "director")):
        raise PermissionDenied

    def _int_or_none(key):
        v = request.GET.get(key)
        if not v:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # Same scope logic as audit_log_context — factor once, use twice.
    from accounts.models import User as _User
    from staff.models import Department

    department = None
    actor = None
    if request.user.is_superuser:
        dept_id = _int_or_none("department")
        if dept_id:
            department = Department.objects.filter(pk=dept_id).first()
        actor_id = _int_or_none("actor")
        if actor_id:
            actor = _User.objects.filter(pk=actor_id).first()
    elif request.user.has_perm("accounts.access_all_departments"):
        dept_id = _int_or_none("department")
        if dept_id:
            department = Department.objects.filter(pk=dept_id).first()

    if not request.user.is_superuser and not request.user.has_perm("accounts.access_all_departments"):
        if request.user.department_id and org.is_director(request.user, request.user.department_id):
            department = request.user.department  # Phase B (B10): own department only
        else:
            actor = request.user

    days = _int_or_none("days")
    since = None
    if days:
        from datetime import timedelta
        since = timezone.now() - timedelta(days=days)

    qs = services.audit_queryset(
        user=request.user,
        actor=actor,
        department=department,
        action=(request.GET.get("action") or "").strip() or None,
        since=since,
        search=(request.GET.get("q") or "").strip() or None,
    )

    stamp = timezone.now().strftime("%Y%m%d-%H%M")
    filename = f"audit-{stamp}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"

    writer = csv.writer(response)

    if qs is None:
        writer.writerow(["When", "Actor", "Department", "Action", "Summary", "Detail", "Object ID"])
        writer.writerow(["", "", "", "", "(audit model not installed)", "", ""])
        return response

    writer.writerow(["When", "Actor", "Department", "Action", "Summary", "Detail", "Object ID"])

    # Cap at 50k rows — a runaway filter shouldn't OOM the worker.
    LIMIT = 50_000
    for entry in qs.iterator(chunk_size=500):
        row = services._normalize_audit_row(entry)
        when = row["when"].strftime("%Y-%m-%d %H:%M:%S") if row["when"] else ""
        writer.writerow([
            when,
            row["actor_name"],
            row["actor_dept"],
            row["action"],
            row["summary"],
            row["detail"],
            row["object_id"] or "",
        ])
        LIMIT -= 1
        if LIMIT <= 0:
            writer.writerow(["", "", "", "", "(truncated at 50000 rows — narrow the filter)", "", ""])
            break

    return response



