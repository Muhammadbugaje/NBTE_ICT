"""Assets views — list, detail, create/edit, my assets, QR labels.

Visibility (read) lives in `assets/access.py` and is the ONLY place the rule
is written down:
  - Regular staff          → only the assets assigned to them personally.
  - Director / deputy / office head (or `assets.view_department_assets`)
                           → also every asset of their own department.
  - `assets.view_all_assets` (Executive Secretary by default), super admin
                           → every asset.
Writing is separate: it needs `assets.change_asset` (see `can_manage_asset`).
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
from .access import (
    asset_scope,
    can_export_assets,
    can_import_assets,
    can_manage_asset,
    can_view_all_assets,
    visible_assets_queryset,
)
from .forms import (
    AssetAttachmentForm,
    AssetForm,
    AssetIssueForm,
    AssignAssetForm,
    AssignmentModeForm,
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
# Thin aliases kept so older imports keep working. The rules themselves are in
# assets/access.py - do not add logic here.
def _can_see_all(user):
    return can_view_all_assets(user)


def _visible_assets_qs(user):
    """Return the queryset of assets this user is permitted to see."""
    return visible_assets_queryset(user)


def _can_edit_asset(user, asset):
    """Review H8: editing needs the real `assets.change_asset` permission, on
    top of being allowed to see the asset (see `assets.access.can_manage_asset`)."""
    return can_manage_asset(user, asset)


def _can_create_asset(user):
    return user.is_superuser or user.has_perm("assets.add_asset")


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

    mode = request.GET.get("mode")
    if mode in dict(Asset.ASSIGNMENT_MODE_CHOICES):
        qs = qs.filter(assignment_mode=mode)

    dept = request.GET.get("department")
    if dept and can_view_all_assets(request.user):
        qs = qs.filter(owner_department__code=dept)

    mine_only = request.GET.get("mine") == "1"
    if mine_only:  # K2: shared holders count as "mine" too
        qs = qs.filter(Q(current_custodian=request.user)
                       | Q(assignments__assigned_to=request.user, assignments__returned_at__isnull=True)).distinct()

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
        "mode_filter": mode or "",
        "modes": Asset.ASSIGNMENT_MODE_CHOICES,
        "dept_filter": dept or "",
        "mine_filter": mine_only,
        "q": q,
        "can_see_all": can_view_all_assets(request.user),
        "can_export_assets": can_export_assets(request.user),
        "can_import_assets": can_import_assets(request.user),
        "asset_scope": asset_scope(request.user),
        "total": _visible_assets_qs(request.user).count(),
    })


@login_required
def asset_export_xlsx(request):
    """The asset register as a Data Hub workbook (the "Assets" category: categories,
    assets, assignments, issues, attachment list). ``?template=1``: blank workbook with
    the dropdowns and the Instructions sheet. Import it on the Data Hub page."""
    if not can_export_assets(request.user):
        raise PermissionDenied("Exporting the asset register needs the right to see and edit every asset.")
    from django.http import HttpResponse
    from django.utils import timezone
    from core.datahub import engine
    blank = request.GET.get("template") == "1"
    data = engine.export_workbook(["assets"], include_rows=not blank, source_label=request.get_host())
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = (f'attachment; filename="NBTE_Assets_{"Template" if blank else "Export"}_'
                                   f'{stamp}.xlsx"')
    resp["Cache-Control"] = "no-store"
    if not blank:
        try:
            from core import audit
            audit.log("assets_export", request.user, actor=request.user, changes={"category": "assets"},
                      note="Assets list: Export assets (Excel)")
        except Exception:
            pass
    return resp


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
    open_assignments = list(asset.open_assignments())

    # X5: is this person here through a temporary grant (ticket assignee)?
    from assignments.access import access_via, record_event
    from assignments.models import AccessEvent
    via, grant = access_via(request.user, asset, "asset")
    grant_ticket = None
    if via == "grant":
        from tickets.asset_access import ticket_for_grant
        grant_ticket = ticket_for_grant(grant)
        record_event(request.user, asset, AccessEvent.VIEW_VIA_GRANT, via_grant=grant, request=request)
    asset_tickets = asset.tickets.select_related("raised_by", "assigned_to").order_by("-created_at")[:20]

    return render(request, "assets/detail.html", {
        "asset": asset,
        "assignments": assignments,
        "open_assignment": open_assignment,
        "open_assignments": open_assignments,
        "via_grant": grant if via == "grant" else None,
        "grant_ticket": grant_ticket,
        "asset_tickets": asset_tickets,
        "mode_form": AssignmentModeForm(initial={"assignment_mode": asset.assignment_mode}),
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
    if not _can_create_asset(request.user):
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
        errors = [
            f"{form.fields[field].label}: {error}"
            for field, field_errors in form.errors.items()
            for error in field_errors
            if field in form.fields
        ]
        messages.error(
            request,
            "Could not assign the asset. "
            + (" ".join(errors) if errors else "Please correct the form and try again."),
        )
        return redirect("assets:detail", asset_tag=asset.asset_tag)
    try:
        services.assign_asset(
            asset=asset,
            to_user=form.cleaned_data.get("assigned_to"),
            department=form.cleaned_data.get("department"),
            by_user=request.user,
            condition=form.cleaned_data["condition_on_assign"],
            location=form.cleaned_data.get("location_on_assign") or "",
            notes=form.cleaned_data.get("notes") or "",
            expected_return_at=form.cleaned_data.get("expected_return_at"),
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, f"Assigned {asset.asset_tag}.")
    return redirect("assets:detail", asset_tag=asset.asset_tag)


@login_required
@require_POST
def asset_set_mode(request, asset_tag):
    """K1/K2: individual / department / shared."""
    asset = get_object_or_404(_visible_assets_qs(request.user), asset_tag=asset_tag)
    if not _can_edit_asset(request.user, asset):
        raise PermissionDenied("You cannot change this asset.")
    form = AssignmentModeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose how the asset is held.")
        return redirect("assets:detail", asset_tag=asset.asset_tag)
    try:
        services.set_assignment_mode(asset=asset, mode=form.cleaned_data["assignment_mode"], by_user=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"{asset.asset_tag} is now held as: {asset.get_assignment_mode_display()}.")
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
        row = None
        if form.cleaned_data.get("assignment"):
            row = asset.assignments.filter(pk=form.cleaned_data["assignment"]).first()
            if row is None:
                raise ValidationError("That hand-off is not on this asset.")
        services.return_asset(
            asset=asset,
            by_user=request.user,
            condition_on_return=form.cleaned_data["condition_on_return"],
            notes=form.cleaned_data.get("notes") or "",
            assignment=row,
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
    """Printable labels for one asset or a batch, in a chosen style.

    Query string:
      ?tags=A,B,C      the assets to print (one or many)
      ?category=code   or: the first labels of one category
      ?style=key       one of assets.label_styles.LABEL_STYLES
      ?copies=N        copies of each label
      ?skip=N          (sheet style) leave the first N cells empty, to reuse
                       a partly used sheet
      ?guides=1        (sheet style) draw cut guides, for plain paper

    Access follows the asset visibility rule: only assets the user may see are
    ever drawn. Tags the user may not see are skipped; if NONE of the
    requested tags is visible the page is a 404, exactly like the asset's
    detail page.
    """
    from django.conf import settings
    from django.templatetags.static import static

    from . import label_styles

    style = label_styles.get_style(request.GET.get("style"))
    copies = label_styles.clamp_copies(request.GET.get("copies"))
    try:
        skip = int(request.GET.get("skip") or 0)
    except ValueError:
        skip = 0
    skip = max(0, min(skip, style.per_page - 1)) if style.is_sheet else 0
    guides = request.GET.get("guides") == "1"

    qs = _visible_assets_qs(request.user)
    tags_param = (request.GET.get("tags") or "").strip()
    category = (request.GET.get("category") or "").strip()
    requested = set()
    notice = ""
    if tags_param:
        requested = {t.strip().upper() for t in tags_param.split(",") if t.strip()}
        requested = set(sorted(requested)[:label_styles.MAX_LABELS])
        qs = qs.filter(asset_tag__in=requested)
    else:
        if category:
            qs = qs.filter(category__code=category)
        qs = qs[:48]

    assets = list(qs.order_by("asset_tag")) if tags_param else list(qs)

    if tags_param:
        found = {a.asset_tag.upper() for a in assets}
        skipped = len(requested - found)
        # Carry on with only the tags that were allowed, so rejected ones are
        # not echoed back into the form fields and style links.
        tags_param = ",".join(sorted(found))
        if requested and not assets:
            # Same answer as the detail page: not found / not yours.
            raise Http404("No such asset.")
        if skipped:
            notice = (
                f"{skipped} requested tag{'s were' if skipped != 1 else ' was'} "
                "skipped (not found or not available to you)."
            )

    labels = [a for a in assets for _ in range(copies)]
    if len(labels) > label_styles.MAX_LABELS:
        labels = labels[:label_styles.MAX_LABELS]
        notice = (notice + " " if notice else "") + (
            f"Limited to {label_styles.MAX_LABELS} labels per print job."
        )

    pages = []
    if style.is_sheet:
        per = style.per_page
        queue = list(labels)
        first = True
        while queue or first:
            blanks = skip if first else 0
            take = per - blanks
            pages.append({"blanks": range(blanks), "assets": queue[:take]})
            queue = queue[take:]
            first = False
            if not queue:
                break

    base = {}
    if tags_param:
        base["tags"] = tags_param
    elif category:
        base["category"] = category
    base["copies"] = copies
    if guides and style.is_sheet:
        base["guides"] = "1"
    from urllib.parse import urlencode
    options = [
        {
            "style": s,
            "active": s.key == style.key,
            "url": "?" + urlencode({**base, "style": s.key}),
        }
        for s in label_styles.LABEL_STYLES
    ]

    return render(request, "assets/print_labels.html", {
        "assets": assets,
        "sample": assets[0] if assets else None,
        "labels": labels,
        "pages": pages,
        "total_labels": len(labels),
        "style": style,
        "options": options,
        "copies": copies,
        "max_copies": label_styles.MAX_COPIES,
        "skip": skip,
        "guides": guides,
        "tags_param": tags_param,
        "category": category,
        "notice": notice,
        "org_name": settings.SITE_NAME,
        "logo_url": static("img/brand/nbte-logo-light.svg"),
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
        _visible_assets_qs(request.user).filter(asset_tag__in=tags)
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
        # Only look among assets this user may see: a scan must not confirm
        # that a tag the user has no right to exists.
        asset = _visible_assets_qs(request.user).filter(asset_tag__iexact=tag).first()
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
