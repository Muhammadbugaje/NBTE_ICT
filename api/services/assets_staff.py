"""assets/, staff/ and workspace/.

Assets reuse assets.access (visible_assets_queryset, can_view_asset,
can_manage_asset), assets.views._extract_tag (the scan page's tag parsing),
assets.forms.AssetIssueForm / AssetAttachmentForm and
assets.services.report_issue; "my assets" is the website's my_assets query.

The directory reuses staff.permissions (staff_visible_to, view_level,
can_message_staff, sees_all_departments) and staff.forms.StaffSearchForm's
department scoping. Only the fields the website's staff detail page shows to
every viewer level are returned (no HR fields).

The workspace reuses dashboards.services.self_service_context (the "My
workspace" page) and api.services.leave.overview.
"""
from django.db.models import Q

from .work import attachment_ref, iso, user_ref


def _ref(obj):
    return {"id": obj.pk, "name": obj.name} if obj is not None else None


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
def asset_ref(a):
    return {"tag": a.asset_tag, "name": a.display_name, "category": a.category.name, "state": a.state,
            "state_display": a.get_state_display()}


def my_assignments(user, status="current", q=""):
    from assets.models import AssetAssignment

    qs = AssetAssignment.objects.filter(assigned_to=user, returned_at__isnull=(status == "current"))
    qs = qs.select_related("asset", "asset__category", "assigned_by")
    if q:
        qs = qs.filter(Q(asset__asset_tag__icontains=q) | Q(asset__brand__icontains=q)
                       | Q(asset__model__icontains=q) | Q(asset__serial_number__icontains=q))
    return qs.order_by("-assigned_at" if status == "current" else "-returned_at")


def assignment_item(a):
    return {"asset": asset_ref(a.asset), "assigned_at": iso(a.assigned_at), "assigned_by": user_ref(a.assigned_by),
            "expected_return_at": a.expected_return_at, "returned_at": iso(a.returned_at),
            "is_overdue": bool(a.is_overdue), "condition_on_assign": a.condition_on_assign or "",
            "condition_on_return": a.condition_on_return or ""}


def visible_assets(user):
    from assets.access import visible_assets_queryset

    return visible_assets_queryset(user)


def by_code(user, code):
    """The scan page: a bare tag or a scanned QR URL, among assets this user
    may see only (a scan must not confirm that a hidden tag exists)."""
    from assets.views import _extract_tag

    tag = _extract_tag(code)
    if not tag:
        return None
    return visible_assets(user).filter(asset_tag__iexact=tag).first()


def asset_detail(asset, user, request=None):
    from assets.access import can_manage_asset

    open_assignment = asset.open_assignment()
    can_edit = can_manage_asset(user, asset)
    data = asset_ref(asset)
    data.update({
        "serial_number": asset.serial_number or "", "brand": asset.brand or "", "model": asset.model or "",
        "color": asset.color or "", "description": asset.description or "",
        "location": asset.current_location or "", "owner_department": _ref(asset.owner_department),
        "custodian": user_ref(asset.current_custodian),
        "assigned_since": iso(open_assignment.assigned_at) if open_assignment else None,
        "expected_return_at": open_assignment.expected_return_at if open_assignment else None,
        "acquired_at": asset.acquired_at, "warranty_until": asset.warranty_until,
        "is_mine": bool(asset.current_custodian_id == user.pk
                        or (open_assignment and open_assignment.assigned_to_id == user.pk)),
        "issues": [{"id": i.pk, "summary": i.summary, "description": i.description or "",
                    "reported_by": user_ref(i.reported_by), "reported_at": iso(i.reported_at),
                    "resolved_at": iso(i.resolved_at), "resolution_notes": i.resolution_notes or ""}
                   for i in asset.issues.select_related("reported_by").order_by("-reported_at")],
        "attachments": [attachment_ref(a, "assets", asset.asset_tag, request)
                        for a in asset.attachments.select_related("uploaded_by").order_by("-created_at")],
        "can_edit": can_edit, "can_report_issue": True, "can_attach_photo": can_edit,
    })
    return data


# ---------------------------------------------------------------------------
# Staff directory
# ---------------------------------------------------------------------------
def directory(user, q="", department=None):
    from staff import permissions

    qs = permissions.staff_visible_to(user).filter(is_deleted=False).order_by("last_name", "first_name")
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(employee_no__icontains=q)
                       | Q(email__icontains=q) | Q(designation__name__icontains=q))
    if department:
        qs = qs.filter(department_id=department)
    return qs


def _photo(staff, request=None):
    if not staff.photo:
        return None
    try:
        url = staff.photo_thumb_url(300) or staff.photo.url
    except Exception:
        return None
    return request.build_absolute_uri(url) if request is not None else url


def staff_card(st, request=None):
    letters = (st.first_name[:1] + st.last_name[:1]).upper() or "?"
    return {"id": st.pk, "user_id": st.user_id, "full_name": st.full_name, "initials": letters,
            "photo_url": _photo(st, request), "designation": st.designation.name if st.designation_id else None,
            "department": _ref(st.department), "office": st.office.name if st.office_id else None,
            "state": st.state.name if st.state_id else None, "email": st.email or "", "phone": st.phone or ""}


def staff_profile(st, user, request=None):
    from staff import permissions

    data = staff_card(st, request)
    data.update({
        "level": permissions.view_level(user, st),
        "subsection": st.subsection or "", "office_location": st.office_location or "",
        "category": st.category.name if st.category_id else None, "grade_level": st.grade_level or "",
        "reports_to": st.reports_to.full_name if st.reports_to_id else None,
        "date_joined_service": st.date_joined_service,
        "contact_preference": st.get_contact_preference_display() if st.contact_preference else "",
        "duties": st.duties or "",
        "can_message": bool(st.user_id and permissions.can_message_staff(user, st)),
    })
    return data


def departments(user):
    from staff.forms import StaffSearchForm

    return StaffSearchForm(user=user).fields["department"].queryset.order_by("name")


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------
def workspace(user, request=None):
    from dashboards.services import self_service_context

    from .leave import overview

    ctx = self_service_context(user)
    staff = ctx["staff_profile"]
    widgets = []
    for w in ctx["widgets"]:
        items = []
        for obj in w["items"]:
            if w["key"] == "tickets":
                items.append({"id": str(obj.pk), "kind": "ticket", "title": obj.subject, "subtitle": obj.reference,
                              "status": obj.get_state_display(), "route": f"/tickets/{obj.pk}"})
            elif w["key"] == "tasks":
                items.append({"id": str(obj.pk), "kind": "task", "title": obj.title, "subtitle": obj.reference,
                              "status": obj.get_state_display(), "route": f"/tasks/{obj.pk}"})
            elif w["key"] == "requests":
                items.append({"id": str(obj.pk), "kind": "request", "title": obj.subject,
                              "subtitle": f"{obj.reference} · {obj.request_type.name}",
                              "status": obj.get_status_display(), "route": f"/requests/{obj.pk}"})
            elif w["key"] == "assets":
                a = obj.asset
                items.append({"id": a.asset_tag, "kind": "asset", "title": a.display_name, "subtitle": a.asset_tag,
                              "status": a.get_state_display(), "route": f"/assets/{a.asset_tag}"})
        widgets.append({"key": w["key"], "title": w["title"], "count": w["count"], "count_label": w["count_label"],
                        "items": items})
    return {
        "profile": ({"staff_id": staff.pk, "employee_no": staff.employee_no,
                     "designation": staff.designation.name if staff.designation_id else None,
                     "department": _ref(staff.department), "state": staff.state.name if staff.state_id else None}
                    if staff else None),
        "widgets": widgets,
        "leave": overview(user) if staff else None,
        "pending_approvals": [{"id": r.pk, "reference": r.reference, "subject": r.subject,
                               "step": r.active_step.name, "requester": user_ref(r.requester),
                               "route": f"/requests/{r.pk}"} for r in ctx["pending_approvals"]],
    }

