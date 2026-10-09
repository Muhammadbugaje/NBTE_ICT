"""Assets views — list, detail, create/edit, my assets, QR labels.

Permission model:
  - Regular staff         → see only their own assignments + assets in their
                            department (read).
  - Office Head / Director → see every asset in their own department (read
                            + write).
  - Superuser / access_all → see every asset everywhere.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.sorting import apply_sort

from django.http import Http404, JsonResponse
from django.urls import reverse

from . import services
from .forms import (
    AssetAttachmentForm,
    AssetForm,
    AssetIssueForm,
    AssignAssetForm,
    ResolveIssueForm,
    ReturnAssetForm,
    SetStateForm,
)
from .models import (
    Asset,
    AssetAssignment,
    AssetCategory,
    AssetIssue,
    CONDITION_CHOICES,
    STATE_CHOICES,
)

PER_PAGE = 25


# ---------------------------------------------------------------------------
# Visibility helpers
# ---------------------------------------------------------------------------
def _can_see_all(user):
    return user.is_superuser or user.has_perm("accounts.access_all_departments")


def _visible_assets_qs(user):
    """Return the queryset of assets this user is permitted to see."""
    base = Asset.objects.select_related(
        "category", "owner_department", "current_custodian",
    )
    if _can_see_all(user):
        return base
    if user.department_id:
        return base.filter(
            Q(owner_department_id=user.department_id)
            | Q(current_custodian=user)
            | Q(assignments__assigned_to=user)
        ).distinct()
    return base.filter(
        Q(current_custodian=user) | Q(assignments__assigned_to=user)
    ).distinct()


def _can_edit_asset(user, asset):
    if user.is_superuser or user.has_perm("accounts.access_all_departments"):
        return True
    if not user.department_id:
        return False
    if asset.owner_department_id == user.department_id:
        return bool(user.has_perm("assets.change_asset") or user.role_id)
    return False


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@login_required
def asset_list(request):
    qs = _visible_assets_qs(request.user)

    # Filters
    cat = request.GET.get("category")
    if cat:
        qs = qs.filter(category__code=cat)

    state = request.GET.get("state")
    if state in dict(STATE_CHOICES):
        qs = qs.filter(state=state)

    dept = request.GET.get("department")
    if dept and _can_see_all(request.user):
        qs = qs.filter(owner_department__code=dept)

    mine_only = request.GET.get("mine") == "1"
    if mine_only:
        qs = qs.filter(current_custodian=request.user)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(asset_tag__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(brand__icontains=q)
            | Q(model__icontains=q)
            | Q(description__icontains=q)
            | Q(current_location__icontains=q)
        )

    qs = qs.order_by("asset_tag")
    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    from staff.models import Department
    return render(request, "assets/list.html", {
        "page": page,
        "categories": AssetCategory.objects.filter(is_active=True),
        "states": STATE_CHOICES,
        "departments": Department.objects.filter(is_active=True).order_by("name"),
        "cat_filter": cat or "",
        "state_filter": state or "",
        "dept_filter": dept or "",
        "mine_filter": mine_only,
        "q": q,
        "can_see_all": _can_see_all(request.user),
        "total": _visible_assets_qs(request.user).count(),
    })


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
@login_required
def asset_detail(request, asset_tag):
    asset = get_object_or_404(
        _visible_assets_qs(request.user),
        asset_tag=asset_tag,
    )
    assignments = asset.assignments.select_related(
        "assigned_to", "assigned_by", "returned_to",
    ).order_by("-assigned_at")
    issues = asset.issues.select_related(
        "reported_by", "resolved_by", "request",
    ).order_by("-reported_at")
    attachments = asset.attachments.select_related("uploaded_by").order_by("-created_at")

    can_edit = _can_edit_asset(request.user, asset)
    open_assignment = asset.open_assignment()

    return render(request, "assets/detail.html", {
        "asset": asset,
        "assignments": assignments,
        "open_assignment": open_assignment,
        "issues": issues,
        "attachments": attachments,
        "can_edit": can_edit,
        "assign_form": AssignAssetForm(asset=asset, user=request.user),
        "return_form": ReturnAssetForm(),
        "state_form": SetStateForm(initial={"state": asset.state}),
        "issue_form": AssetIssueForm(),
        "attachment_form": AssetAttachmentForm(),
        "from_scan": request.GET.get("from") == "scan",
    })


# ---------------------------------------------------------------------------
# Create / edit
# ---------------------------------------------------------------------------
@login_required
def asset_create(request):
    if not request.user.has_perm("assets.add_asset") and not request.user.is_superuser \
            and not request.user.role_id:
        raise PermissionDenied("You do not have permission to create assets.")

    if request.method == "POST":
        form = AssetForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                asset = services.create_asset(
                    category=form.cleaned_data["category"],
                    created_by=request.user,
                    owner_department=form.cleaned_data.get("owner_department"),
                    asset_tag=form.cleaned_data.get("asset_tag") or None,
                    serial_number=form.cleaned_data.get("serial_number") or "",
                    brand=form.cleaned_data.get("brand") or "",
                    model=form.cleaned_data.get("model") or "",
                    color=form.cleaned_data.get("color") or "",
                    description=form.cleaned_data.get("description") or "",
                    current_location=form.cleaned_data.get("current_location") or "",
                    state=form.cleaned_data.get("state") or "new",
                    acquired_at=form.cleaned_data.get("acquired_at"),
                    cost=form.cleaned_data.get("cost"),
                    warranty_until=form.cleaned_data.get("warranty_until"),
                    notes=form.cleaned_data.get("notes") or "",
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, f"Asset {asset.asset_tag} created.")
                return redirect("assets:detail", asset_tag=asset.asset_tag)
    else:
        form = AssetForm(user=request.user)
    return render(request, "assets/form.html", {
        "form": form,
        "categories": AssetCategory.objects.filter(is_active=True),
        "is_create": True,
    })


@login_required
def asset_edit(request, asset_tag):
    asset = get_object_or_404(_visible_assets_qs(request.user), asset_tag=asset_tag)
    if not _can_edit_asset(request.user, asset):
        raise PermissionDenied("You cannot edit this asset.")

    if request.method == "POST":
        form = AssetForm(request.POST, instance=asset, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            # Preserve the tag (form disables it, but be safe).
            obj.asset_tag = asset.asset_tag
            obj.save()
            messages.success(request, f"Asset {obj.asset_tag} saved.")
            return redirect("assets:detail", asset_tag=obj.asset_tag)
    else:
        form = AssetForm(instance=asset, user=request.user)
    return render(request, "assets/form.html", {
        "form": form,
        "asset": asset,
        "categories": AssetCategory.objects.filter(is_active=True),
        "is_create": False,
    })


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
@login_required
@require_POST
def asset_assign(request, asset_tag):
    asset = get_object_or_404(_visible_assets_qs(request.user), asset_tag=asset_tag)
    if not _can_edit_asset(request.user, asset):
        raise PermissionDenied("You cannot assign this asset.")
    form = AssignAssetForm(request.POST, asset=asset, user=request.user)
    if not form.is_valid():
        messages.error(request, "Please select a valid user.")
        return redirect("assets:detail", asset_tag=asset.asset_tag)
    try:
        services.assign_asset(
            asset=asset,
            to_user=form.cleaned_data["assigned_to"],
            by_user=request.user,
            condition=form.cleaned_data["condition_on_assign"],
            location=form.cleaned_data.get("location_on_assign") or "",
            notes=form.cleaned_data.get("notes") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, f"Assigned {asset.asset_tag}.")
    return redirect("assets:detail", asset_tag=asset.asset_tag)


@login_required
@require_POST
def asset_return(request, asset_tag):
    asset = get_object_or_404(_visible_assets_qs(request.user), asset_tag=asset_tag)
    if not _can_edit_asset(request.user, asset):
        raise PermissionDenied("You cannot return this asset.")
    form = ReturnAssetForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form.")
        return redirect("assets:detail", asset_tag=asset.asset_tag)
    try:
        services.return_asset(
            asset=asset,
            by_user=request.user,
            condition_on_return=form.cleaned_data["condition_on_return"],
            notes=form.cleaned_data.get("notes") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, f"Returned {asset.asset_tag}.")
    return redirect("assets:detail", asset_tag=asset.asset_tag)


@login_required
@require_POST
def asset_set_state(request, asset_tag):
    asset = get_object_or_404(_visible_assets_qs(request.user), asset_tag=asset_tag)
    if not _can_edit_asset(request.user, asset):
        raise PermissionDenied("You cannot change this asset's state.")
    form = SetStateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form.")
        return redirect("assets:detail", asset_tag=asset.asset_tag)
    try:
        services.set_state(
            asset=asset,
            new_state=form.cleaned_data["state"],
            by_user=request.user,
            reason=form.cleaned_data.get("reason") or "",
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"State updated to {asset.get_state_display()}.")
    return redirect("assets:detail", asset_tag=asset.asset_tag)


@login_required
@require_POST
def asset_report_issue(request, asset_tag):
    asset = get_object_or_404(_visible_assets_qs(request.user), asset_tag=asset_tag)
    form = AssetIssueForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form.")
        return redirect("assets:detail", asset_tag=asset.asset_tag)
    services.report_issue(
        asset=asset,
        reported_by=request.user,
        summary=form.cleaned_data["summary"],
        description=form.cleaned_data.get("description") or "",
    )
    messages.success(request, "Issue reported.")
    return redirect("assets:detail", asset_tag=asset.asset_tag)


@login_required
@require_POST
def asset_resolve_issue(request, asset_tag, issue_id):
    asset = get_object_or_404(_visible_assets_qs(request.user), asset_tag=asset_tag)
    if not _can_edit_asset(request.user, asset):
        raise PermissionDenied("You cannot resolve this issue.")
    issue = get_object_or_404(AssetIssue, pk=issue_id, asset=asset)
    form = ResolveIssueForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form.")
        return redirect("assets:detail", asset_tag=asset.asset_tag)
    try:
        services.resolve_issue(
            issue=issue,
            resolved_by=request.user,
            resolution_notes=form.cleaned_data.get("resolution_notes") or "",
            restore_state=form.cleaned_data.get("restore_state") or "in_service",
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Issue resolved.")
    return redirect("assets:detail", asset_tag=asset.asset_tag)


@login_required
@require_POST
def asset_add_attachment(request, asset_tag):
    asset = get_object_or_404(_visible_assets_qs(request.user), asset_tag=asset_tag)
    if not _can_edit_asset(request.user, asset):
        raise PermissionDenied("You cannot add attachments.")
    form = AssetAttachmentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Please choose a file.")
        return redirect("assets:detail", asset_tag=asset.asset_tag)

    from core.storage import capture_original_filename
    from .models import AssetAttachment as AA

    att = AA(
        asset=asset,
        uploaded_by=request.user,
        file=form.cleaned_data["file"],
        kind=form.cleaned_data["kind"],
        description=form.cleaned_data.get("description") or "",
    )
    att.save()
    messages.success(request, "Attachment added.")
    return redirect("assets:detail", asset_tag=asset.asset_tag)


# ---------------------------------------------------------------------------
# My assets
# ---------------------------------------------------------------------------
SORTABLE_MY_ASSETS = {
    "assigned": "assigned_at",
    "returned": "returned_at",
    "tag":      "asset__asset_tag",
    "name":     "asset__brand",
}


@login_required
def my_assets(request):
    current = (
        AssetAssignment.objects
        .filter(assigned_to=request.user, returned_at__isnull=True)
        .select_related("asset", "asset__category", "assigned_by")
    )
    history = (
        AssetAssignment.objects
        .filter(assigned_to=request.user, returned_at__isnull=False)
        .select_related("asset", "asset__category", "assigned_by")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        asset_q = (
            Q(asset__asset_tag__icontains=q)
            | Q(asset__brand__icontains=q)
            | Q(asset__model__icontains=q)
            | Q(asset__serial_number__icontains=q)
        )
        current = current.filter(asset_q)
        history = history.filter(asset_q)

    current, sort_key, sort_dir = apply_sort(
        current, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_MY_ASSETS, default="assigned",
    )
    history = history.order_by("-returned_at")[:20]

    return render(request, "assets/my_assets.html", {
        "assignments": list(current),
        "history": list(history),
        "q": q,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
    })

# ---------------------------------------------------------------------------
# QR label sheet
# ---------------------------------------------------------------------------
@login_required
def print_labels(request):
    """Printable QR labels for a list of assets. Pass ?tags=tag1,tag2 or
    ?category=computer to print a whole category."""
    qs = _visible_assets_qs(request.user)
    tags_param = request.GET.get("tags")
    if tags_param:
        tags = [t.strip().upper() for t in tags_param.split(",") if t.strip()]
        qs = qs.filter(asset_tag__in=tags)
    else:
        cat = request.GET.get("category")
        if cat:
            qs = qs.filter(category__code=cat)
        qs = qs[:48]  # cap at 48 labels per sheet (6x8 grid)

    assets = list(qs)
    return render(request, "assets/print_labels.html", {
        "assets": assets,
        "categories": AssetCategory.objects.filter(is_active=True),
    })

# ---------------------------------------------------------------------------
# Scan / lookup
# ---------------------------------------------------------------------------
def _extract_tag(value):
    """Accept either a bare tag or a full URL containing /assets/<tag>/.

    Physical QR scanners act as keyboards — they type the decoded content
    into whatever has focus and press Enter. If the QR payload is a full URL
    (which ours is), the input contains something like
    'http://localhost:8000/assets/ICT-COMPUT-00001/'. We pull the tag out.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if "/assets/" in value:
        try:
            after = value.split("/assets/", 1)[1]
            tag = after.split("/", 1)[0]
            return tag.strip().upper()
        except IndexError:
            return value.upper()
    return value.upper()


def _record_lookup(request, asset):
    """Store the last few looked-up tags in session for a "recent" list."""
    recent = request.session.get("asset_recent_lookups") or []
    tag = asset.asset_tag
    recent = [tag] + [t for t in recent if t != tag]
    request.session["asset_recent_lookups"] = recent[:8]
    request.session.modified = True


def _recent_assets(request):
    tags = request.session.get("asset_recent_lookups") or []
    if not tags:
        return []
    return list(
        Asset.objects.filter(asset_tag__in=tags)
        .select_related("category", "current_custodian", "owner_department")
    )


@login_required
def scan(request):
    """Scan or type an asset tag → jump straight to the asset.

    Handles both a bare tag (typed) and a full URL (scanned from the QR we
    printed, which contains a URL to the detail page).
    """
    raw = request.GET.get("tag") or ""
    tag = _extract_tag(raw)

    if tag:
        asset = Asset.objects.filter(asset_tag__iexact=tag).first()
        if asset is not None:
            _record_lookup(request, asset)
            return redirect(f"{reverse('assets:detail', args=[asset.asset_tag])}?from=scan")

    return render(request, "assets/scan.html", {
        "raw_input": raw,
        "tag": tag,
        "not_found": bool(tag),
        "recent": _recent_assets(request),
    })


@login_required
def lookup_api(request):
    """Small JSON endpoint used for live search-as-you-type in the scan page.

    Returns up to 8 matches whose tag, brand, model, or serial contains the
    query. Scoped to what the user is permitted to see.
    """
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})

    qs = _visible_assets_qs(request.user).filter(
        Q(asset_tag__icontains=q)
        | Q(serial_number__icontains=q)
        | Q(brand__icontains=q)
        | Q(model__icontains=q)
        | Q(current_location__icontains=q)
    ).order_by("asset_tag")[:8]

    results = [
        {
            "tag": a.asset_tag,
            "label": a.display_name,
            "category": a.category.name,
            "custodian": (
                a.current_custodian.get_full_name() or a.current_custodian.email
                if a.current_custodian else None
            ),
            "url": reverse("assets:detail", args=[a.asset_tag]),
        }
        for a in qs
    ]
    return JsonResponse({"results": results})

# ---------------------------------------------------------------------------
# JSON asset detail (used by the scan page for no-reload lookups)
# ---------------------------------------------------------------------------
@login_required
def asset_json(request, asset_tag):
    """Return the full asset record as JSON. Used by the scan page.

    Includes a compact QR SVG, everything needed to render a "scanned card",
    and every URL for the quick-action buttons.
    """
    asset = get_object_or_404(
        _visible_assets_qs(request.user),
        asset_tag__iexact=asset_tag,
    )

    open_assignment = asset.open_assignment()
    custodian_data = None
    if asset.current_custodian_id:
        custodian_data = {
            "name": asset.current_custodian.get_full_name() or asset.current_custodian.email,
            "email": asset.current_custodian.email,
            "since": open_assignment.assigned_at.isoformat() if open_assignment else None,
            "since_human": open_assignment.assigned_at.strftime("%b %d, %Y") if open_assignment else None,
        }

    # Can the current user return this asset? (assignee or editor)
    can_return = bool(
        asset.current_custodian_id == request.user.pk
        or _can_edit_asset(request.user, asset)
    )

    return JsonResponse({
        "tag": asset.asset_tag,
        "display_name": asset.display_name,
        "category": asset.category.name,
        "state": asset.state,
        "state_display": asset.get_state_display(),
        "serial_number": asset.serial_number,
        "brand": asset.brand,
        "model": asset.model,
        "color": asset.color,
        "description": asset.description,
        "location": asset.current_location,
        "owner_department": (
            asset.owner_department.name if asset.owner_department_id else None
        ),
        "acquired_at": (
            asset.acquired_at.isoformat() if asset.acquired_at else None
        ),
        "warranty_until": (
            asset.warranty_until.isoformat() if asset.warranty_until else None
        ),
        "spec": asset.spec or {},
        "custodian": custodian_data,
        "is_assigned": open_assignment is not None,
        "can_edit": _can_edit_asset(request.user, asset),
        "can_return": can_return,
        "detail_url": reverse("assets:detail", args=[asset.asset_tag]),
        "qr_svg": asset.qr_svg(box_size=3, border=1),
        "open_issue_count": asset.issues.filter(resolved_at__isnull=True).count(),
        "actions": {
            "assign_url": reverse("assets:assign", args=[asset.asset_tag]),
            "return_url": reverse("assets:return", args=[asset.asset_tag]),
            "report_url": reverse("assets:report_issue", args=[asset.asset_tag]),
            "print_url": reverse("assets:print_labels") + "?tags=" + asset.asset_tag,
        },
    })


