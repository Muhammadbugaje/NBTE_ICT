"""Staff views — list, detail, create, edit, delete, change state.

All access goes through staff.permissions — never a raw queryset on Staff.
"""
from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import permissions
from .forms import StaffForm, StaffSearchForm, StaffStateChangeForm
from .models import Staff, StaffState

User = get_user_model()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@login_required
def staff_list(request):
    qs = permissions.staff_visible_to(request.user).order_by("last_name", "first_name")

    search = StaffSearchForm(request.GET or None, user=request.user)
    if search.is_valid():
        q = search.cleaned_data.get("q")
        if q:
            qs = qs.filter(
                Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(employee_no__icontains=q)
                | Q(email__icontains=q)
                | Q(designation__icontains=q)
            )
        dept = search.cleaned_data.get("department")
        if dept:
            qs = qs.filter(department=dept)
        st = search.cleaned_data.get("state")
        if st:
            qs = qs.filter(state=st)

    # Was any filter actually applied?
    filters_active = bool(
        (request.GET.get("q") or "").strip()
        or request.GET.get("department")
        or request.GET.get("state")
    )

    # Is this user seeing all departments, or just their own?
    is_all_departments = bool(
        request.user.is_superuser
        or request.user.has_perm("staff.view_staff")
    )

    page = Paginator(qs, 25).get_page(request.GET.get("page") or 1)

    return render(request, "staff/list.html", {
        "page": page,
        "search": search,
        "total": qs.count(),
        "filters_active": filters_active,
        "is_all_departments": is_all_departments,
        "can_create": permissions.can_create_staff(request.user),
    })

# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
@login_required
def staff_detail(request, pk):
    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
    level = permissions.view_level(request.user, staff)

    history = []
    if permissions.can_view_state_history(request.user, staff):
        history = staff.status_history.select_related(
            "from_state", "to_state", "changed_by"
        )[:20]

    return render(request, "staff/detail.html", {
        "staff": staff,
        "level": level,
        "history": history,
        "can_view_hr":      permissions.can_view_hr_fields(request.user, staff),
        "can_view_history": permissions.can_view_state_history(request.user, staff),
        "can_manage":       permissions.can_manage_staff(request.user, staff),
        "can_change_state": permissions.can_change_state(request.user, staff),
        "can_delete":       permissions.can_delete_staff(request.user, staff),
        "can_message":      permissions.can_message_staff(request.user, staff),
        "can_notify":       permissions.can_notify_staff(request.user, staff),
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@login_required
def staff_create(request):
    if not permissions.can_create_staff(request.user):
        raise PermissionDenied

    scoped_dept = permissions.scoped_department_for_new(request.user)

    if request.method == "POST":
        form = StaffForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            staff = form.save(commit=False)
            if scoped_dept is not None and not staff.department_id:
                staff.department = scoped_dept
            staff._state_changed_by = request.user
            staff.save()

            if form.cleaned_data.get("create_account"):
                _provision_account(
                    request, staff,
                    password=form.cleaned_data.get("initial_password") or None,
                )

            messages.success(request, f"{staff.full_name} was added to the directory.")
            return redirect("staff:detail", pk=staff.pk)
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        initial = {}
        if scoped_dept is not None:
            initial["department"] = scoped_dept.pk
        # Default to the first active state so the record isn't blank.
        default_state = StaffState.objects.filter(is_active=True).first()
        if default_state:
            initial.setdefault("state", default_state.pk)
        form = StaffForm(initial=initial, user=request.user)

    return render(request, "staff/form.html", {
        "form": form,
        "mode": "create",
        "staff": None,
    })


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
@login_required
def staff_update(request, pk):
    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
    if not permissions.can_manage_staff(request.user, staff):
        raise PermissionDenied

    # State is only editable via the state-change flow, not the general form.
    # If the user can change state, they'll see a dedicated link on the detail page.
    state_snapshot = (staff.state_id, staff.state_start, staff.state_end)

    if request.method == "POST":
        form = StaffForm(request.POST, request.FILES, instance=staff, user=request.user)
        if form.is_valid():
            # Guard: silently re-apply the original state so the general form
            # can never bypass change_staff_state.
            obj = form.save(commit=False)
            obj.state_id, obj.state_start, obj.state_end = state_snapshot
            obj._state_changed_by = request.user
            obj.save()
            messages.success(request, "Staff record updated.")
            return redirect("staff:detail", pk=staff.pk)
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = StaffForm(instance=staff, user=request.user)

    return render(request, "staff/form.html", {
        "form": form,
        "mode": "update",
        "staff": staff,
    })


# ---------------------------------------------------------------------------
# Delete (soft)
# ---------------------------------------------------------------------------
@login_required
def staff_delete(request, pk):
    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
    if not permissions.can_delete_staff(request.user, staff):
        raise PermissionDenied

    if request.method == "POST":
        name = staff.full_name
        staff.delete()  # SoftDeleteModel
        messages.success(request, f"{name} was removed from the directory.")
        return redirect("staff:list")

    return render(request, "staff/confirm_delete.html", {"staff": staff})


# ---------------------------------------------------------------------------
# Change state
# ---------------------------------------------------------------------------
@login_required
def staff_change_state(request, pk):
    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
    if not permissions.can_change_state(request.user, staff):
        raise PermissionDenied

    if request.method == "POST":
        form = StaffStateChangeForm(request.POST)
        if form.is_valid():
            try:
                staff.change_state(
                    new_state=form.cleaned_data["state"],
                    by=request.user,
                    start=form.cleaned_data.get("start_date"),
                    end=form.cleaned_data.get("end_date"),
                    reason=form.cleaned_data.get("reason", ""),
                )
            except PermissionError:
                raise PermissionDenied
            messages.success(
                request,
                f"{staff.full_name} is now “{staff.state.name}”."
            )
            return redirect("staff:detail", pk=staff.pk)
    else:
        form = StaffStateChangeForm(initial={
            "state": staff.state_id,
            "start_date": staff.state_start,
            "end_date": staff.state_end,
        })

    return render(request, "staff/state_change.html", {
        "staff": staff,
        "form": form,
    })


# ---------------------------------------------------------------------------
# Account provisioning
# ---------------------------------------------------------------------------
def _provision_account(request, staff: Staff, password: str | None = None):
    """Create a User for this Staff record, set a temporary password,
    flag must_change_password, and email the login details.

    If `password` is None or empty, a random one is generated.
    """
    if staff.user_id:
        messages.info(request, "This staff member already has an account.")
        return
    if not staff.email:
        messages.warning(request, "No email — account not created.")
        return

    existing = User.objects.filter(email__iexact=staff.email).first()
    if existing:
        staff.user = existing
        staff.save(update_fields=["user"])
        messages.info(request, f"Linked to existing account {existing.email}.")
        return

    temp_password = (password or "").strip() or secrets.token_urlsafe(10)

    try:
        user = User.objects.create_user(
            email=staff.email,
            password=temp_password,
            first_name=staff.first_name,
            last_name=staff.last_name,
            **({"department": staff.department} if staff.department_id else {}),
        )
    except Exception as exc:
        messages.error(request, f"Could not create account: {exc}")
        return

    if hasattr(user, "must_change_password"):
        user.must_change_password = True
        try:
            user.save(update_fields=["must_change_password"])
        except Exception:
            user.save()

    staff.user = user
    staff.save(update_fields=["user"])

    try:
        from core.email_utils import send_templated_email
        send_templated_email(
            subject="Your NBTE ICT account",
            recipients=[staff.email],
            template_name="emails/staff_welcome.html",
            context={
                "staff": staff, "user": user,
                "temp_password": temp_password,
                "login_url": request.build_absolute_uri(reverse("login")),
            },
            category="general",
        )
        messages.success(
            request,
            f"Account created for {staff.email} — credentials emailed.",
        )
    except Exception as exc:
        messages.warning(
            request,
            f"Account created, but the welcome email failed ({exc}). "
            f"Password: {temp_password}",
        )
        
        
# ---------------------------------------------------------------------------
# Message / notify the staff member from the detail page
# ---------------------------------------------------------------------------
@login_required
@require_POST
def staff_message(request, pk):
    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)

    if not permissions.can_message_staff(request.user, staff):
        raise PermissionDenied("You don't have permission to message this staff member.")

    if not staff.user_id:
        messages.error(request, "This staff member has no user account yet.")
        return redirect("staff:detail", pk=staff.pk)

    subject = (request.POST.get("subject") or "").strip()
    body    = (request.POST.get("body") or "").strip()

    if not body:
        messages.error(request, "Message body can't be empty.")
        return redirect("staff:detail", pk=staff.pk)

    subject = subject or f"Message from {request.user.get_full_name() or request.user.email}"

    thread_pk = _create_message_thread(
        request.user, [staff.user], subject, body,
    )
    if thread_pk is None:
        messages.error(
            request,
            "Could not send the message — check that the messaging app is "
            "installed and its `services.start_thread` helper exists."
        )
        return redirect("staff:detail", pk=staff.pk)

    messages.success(request, "Message sent.")
    try:
        return redirect("messaging:thread", pk=thread_pk)
    except Exception:
        return redirect("messaging:inbox")


def _create_message_thread(sender, recipients, subject, body):
    """Create a messaging thread. Returns the thread pk, or None on failure.

    Tries `messaging.services.start_thread` first (with a couple of common
    signatures); falls back to creating the Thread + first message directly.
    """
    # Attempt 1: service helper with keyword args
    try:
        from messaging import services as _msg
        if hasattr(_msg, "start_thread"):
            thread = _msg.start_thread(
                creator=sender,
                recipients=recipients,
                subject=subject,
                first_message=body,
            )
            return getattr(thread, "pk", None)
    except Exception:
        pass

    # Attempt 2: direct model manipulation
    try:
        from messaging.models import Thread
        thread = Thread.objects.create(
            created_by=sender,
            subject=subject,
        )
        # Add participants if a through model exists
        participant_model = None
        try:
            from messaging.models import ThreadParticipant as participant_model  # noqa
        except Exception:
            participant_model = None

        if participant_model is not None:
            participant_model.objects.create(thread=thread, user=sender, is_archived=False)
            for r in recipients:
                participant_model.objects.create(thread=thread, user=r, is_archived=False)
        else:
            # Fall back to M2M field, whatever its name is.
            for field_name in ("participants", "members", "users"):
                field = getattr(thread, field_name, None)
                if field is not None and hasattr(field, "add"):
                    field.add(sender)
                    field.add(*recipients)
                    break

        # Create the first message if a Message model exists.
        try:
            from messaging.models import Message
            Message.objects.create(thread=thread, sender=sender, body=body)
        except Exception:
            pass

        return thread.pk
    except Exception:
        return None

@login_required
@require_POST
def staff_notify(request, pk):
    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
    if not permissions.can_notify_staff(request.user, staff):
        raise PermissionDenied
    if not staff.user_id:
        messages.error(request, "This staff member has no user account yet.")
        return redirect("staff:detail", pk=staff.pk)

    title = (request.POST.get("title") or "").strip()
    body  = (request.POST.get("body") or "").strip()
    if not title:
        messages.error(request, "Notification title is required.")
        return redirect("staff:detail", pk=staff.pk)

    try:
        from notifications.models import Notification
        from notifications.services import notify
        notify(
            recipient=staff.user,
            title=title,
            body=body,
            url="",  # in-app only, no target URL
            category=Notification.SYSTEM,
            level=Notification.INFO,
            actor=request.user,
        )
        messages.success(request, "Notification sent.")
    except Exception as exc:
        messages.error(request, f"Could not send notification: {exc}")
    return redirect("staff:detail", pk=staff.pk)      
        
        
# ============================================================================
# LEADERSHIP (public)
# ============================================================================
def public_leadership(request):
    """Public leadership page — current + full timeline."""
    from . import services

    current_federal  = services.current_leadership(scope=ExecutivePosition.SCOPE_FEDERAL)
    current_board    = services.current_leadership(scope=ExecutivePosition.SCOPE_BOARD)
    current_depts    = services.current_leadership(scope=ExecutivePosition.SCOPE_DEPARTMENTAL)
    timeline         = services.positions_with_history(public_only=True)

    return render(request, "staff/leadership/public.html", {
        "current_federal": current_federal,
        "current_board":   current_board,
        "current_depts":   current_depts,
        "timeline":        timeline,
    })


def leadership_tenure_detail(request, pk):
    """A single tenure — profile view with bio, qualifications, history."""
    tenure = get_object_or_404(
        ExecutiveTenure.objects.select_related("staff", "position"),
        pk=pk, is_public=True,
    )
    # Other positions the same person has held
    other_tenures = (
        ExecutiveTenure.objects
        .filter(staff=tenure.staff, is_public=True)
        .exclude(pk=tenure.pk)
        .select_related("position")
        .order_by("-started_on")
    )
    return render(request, "staff/leadership/tenure.html", {
        "tenure": tenure,
        "other_tenures": other_tenures,
    })        
        
        
# ============================================================================
# LEADERSHIP MANAGEMENT PANEL
# ============================================================================
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ExecutivePositionForm, ExecutiveTenureForm
from .models import ExecutivePosition, ExecutiveTenure


# ----- Permissions helpers -------------------------------------------------
def _can_manage_positions(user):
    if not user.is_authenticated:
        return False
    return (
        user.is_superuser
        or user.has_perm("staff.manage_executive_position")
    )


def _can_manage_tenures(user):
    if not user.is_authenticated:
        return False
    return (
        user.is_superuser
        or user.has_perm("staff.manage_executive_tenure")
        or user.has_perm("staff.manage_executive_position")
    )


# ----- Positions -----------------------------------------------------------
@login_required
def leadership_panel(request):
    """The overview page of the management panel."""
    if not (_can_manage_positions(request.user) or _can_manage_tenures(request.user)):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    positions = ExecutivePosition.objects.order_by("order", "name")
    current   = (
        ExecutiveTenure.objects
        .filter(ended_on__isnull=True)
        .select_related("staff", "position")
        .order_by("position__order", "-started_on")
    )
    recent_past = (
        ExecutiveTenure.objects
        .filter(ended_on__isnull=False)
        .select_related("staff", "position")
        .order_by("-ended_on")[:10]
    )

    return render(request, "staff/leadership/manage_overview.html", {
        "positions": positions,
        "current":   current,
        "recent_past": recent_past,
        "can_manage_positions": _can_manage_positions(request.user),
        "can_manage_tenures":   _can_manage_tenures(request.user),
    })


@login_required
def position_list(request):
    if not _can_manage_positions(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    q = (request.GET.get("q") or "").strip()
    qs = ExecutivePosition.objects.order_by("order", "name")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))

    page = Paginator(qs, 30).get_page(request.GET.get("page") or 1)

    return render(request, "staff/leadership/positions.html", {
        "page": page, "q": q,
    })


@login_required
def position_create(request):
    if not _can_manage_positions(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    if request.method == "POST":
        form = ExecutivePositionForm(request.POST)
        if form.is_valid():
            pos = form.save()
            messages.success(request, f"Position “{pos.name}” created.")
            return redirect("staff:leadership_panel")
        messages.error(request, "Please fix the errors below.")
    else:
        form = ExecutivePositionForm()

    return render(request, "staff/leadership/position_form.html", {
        "form": form, "mode": "create", "position": None,
    })


@login_required
def position_update(request, pk):
    if not _can_manage_positions(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    position = get_object_or_404(ExecutivePosition, pk=pk)

    if request.method == "POST":
        form = ExecutivePositionForm(request.POST, instance=position)
        if form.is_valid():
            form.save()
            messages.success(request, "Position updated.")
            return redirect("staff:leadership_panel")
        messages.error(request, "Please fix the errors below.")
    else:
        form = ExecutivePositionForm(instance=position)

    return render(request, "staff/leadership/position_form.html", {
        "form": form, "mode": "update", "position": position,
    })


@login_required
def position_delete(request, pk):
    if not _can_manage_positions(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    position = get_object_or_404(ExecutivePosition, pk=pk)
    if request.method == "POST":
        name = position.name
        position.delete()  # cascade will also delete its tenures
        messages.success(request, f"Position “{name}” deleted.")
        return redirect("staff:leadership_panel")

    return render(request, "staff/leadership/confirm_delete.html", {
        "kind": "position", "position": position,
    })


# ----- Tenures -------------------------------------------------------------
@login_required
def tenure_list(request):
    if not _can_manage_tenures(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    q = (request.GET.get("q") or "").strip()
    position_id = request.GET.get("position")
    current_only = request.GET.get("current") == "1"

    qs = ExecutiveTenure.objects.select_related(
        "staff", "staff__department", "position",
    ).order_by("-started_on", "-ended_on")

    if q:
        qs = qs.filter(
            Q(staff__first_name__icontains=q)
            | Q(staff__last_name__icontains=q)
            | Q(staff__employee_no__icontains=q)
            | Q(position__name__icontains=q)
        )
    if position_id:
        qs = qs.filter(position_id=position_id)
    if current_only:
        qs = qs.filter(ended_on__isnull=True)

    page = Paginator(qs, 30).get_page(request.GET.get("page") or 1)

    return render(request, "staff/leadership/tenures.html", {
        "page": page,
        "q": q,
        "position_id": position_id,
        "current_only": current_only,
        "positions": ExecutivePosition.objects.order_by("order", "name"),
    })


@login_required
def tenure_create(request):
    if not _can_manage_tenures(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    if request.method == "POST":
        form = ExecutiveTenureForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            tenure = form.save()
            messages.success(
                request,
                f"Recorded “{tenure.position.name}” for {tenure.staff.full_name}.",
            )
            return redirect("staff:leadership_panel")
        messages.error(request, "Please fix the errors below.")
    else:
        initial = {}
        pos_id = request.GET.get("position")
        if pos_id:
            initial["position"] = pos_id
        form = ExecutiveTenureForm(initial=initial, user=request.user)

    return render(request, "staff/leadership/tenure_form.html", {
        "form": form, "mode": "create", "tenure": None,
    })


@login_required
def tenure_update(request, pk):
    if not _can_manage_tenures(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    tenure = get_object_or_404(ExecutiveTenure, pk=pk)

    if request.method == "POST":
        form = ExecutiveTenureForm(request.POST, request.FILES,
                                   instance=tenure, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Tenure updated.")
            return redirect("staff:leadership_panel")
        messages.error(request, "Please fix the errors below.")
    else:
        form = ExecutiveTenureForm(instance=tenure, user=request.user)

    return render(request, "staff/leadership/tenure_form.html", {
        "form": form, "mode": "update", "tenure": tenure,
    })


@login_required
def tenure_delete(request, pk):
    if not _can_manage_tenures(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    tenure = get_object_or_404(ExecutiveTenure, pk=pk)
    if request.method == "POST":
        messages.success(request, "Tenure removed.")
        tenure.delete()
        return redirect("staff:leadership_panel")

    return render(request, "staff/leadership/confirm_delete.html", {
        "kind": "tenure", "tenure": tenure,
    })


@login_required
def tenure_end_now(request, pk):
    """Convenience: end a current tenure today."""
    from django.views.decorators.http import require_POST
    if request.method != "POST":
        return redirect("staff:leadership_panel")
    if not _can_manage_tenures(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    tenure = get_object_or_404(ExecutiveTenure, pk=pk)
    tenure.ended_on = timezone.localdate()
    tenure.save(update_fields=["ended_on"])
    messages.success(
        request,
        f"Ended “{tenure.position.name}” for {tenure.staff.full_name}.",
    )
    return redirect("staff:leadership_panel")        
        
        
        
        
        
        
        
        