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
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import permissions
from .forms import StaffForm, StaffSearchForm, StaffSelfForm, StaffStateChangeForm
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
                | Q(designation__name__icontains=q)
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
    is_all_departments = permissions.sees_all_departments(request.user)

    page = Paginator(qs, 25).get_page(request.GET.get("page") or 1)

    from . import role_ui, staff_id_styles
    can_print_ids = request.user.has_perm("staff.print_staff_id")
    can_manage_roles = role_ui.can_manage_roles(request.user)
    return render(request, "staff/list.html", {
        "can_print_ids": can_print_ids,
        "can_manage_roles": can_manage_roles,
        "quick_actions": can_print_ids or can_manage_roles,
        "id_styles": staff_id_styles.STYLES,
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

    tab = "access" if request.GET.get("tab") == "access" else "profile"
    return render(request, "staff/detail.html", {
        "staff": staff,
        "level": level,
        "tab": tab,
        **_role_context(request, staff, with_access=(tab == "access")),
        "history": history,
        "can_view_hr":      permissions.can_view_hr_fields(request.user, staff),
        "can_view_history": permissions.can_view_state_history(request.user, staff),
        "can_manage":       permissions.can_manage_staff(request.user, staff),
        "can_change_state": permissions.can_change_state(request.user, staff),
        "can_delete":       permissions.can_delete_staff(request.user, staff),
        "can_message":      permissions.can_message_staff(request.user, staff),
        "can_notify":       permissions.can_notify_staff(request.user, staff),
        **_lifecycle_context(request, staff),
    })


def _role_context(request, staff, with_access=False):
    """B.8 (C1-C3): role chip, Access tab and the Manage role sheet."""
    from accounts.permissions import can_act_on_user

    from . import role_ui

    user = request.user
    account = staff.user if staff.user_id else None
    can_see_access = bool(account) and (
        permissions.can_view_hr_fields(user, staff) or account.pk == user.pk or role_ui.can_manage_roles(user))
    can_manage_role = bool(account) and role_ui.can_manage_roles(user) and can_act_on_user(user, account) \
        and account.pk != user.pk
    return {
        "account": account,
        "can_print_id": bool(user.has_perm("staff.print_staff_id")),
        "can_see_access": can_see_access,
        "can_manage_role": can_manage_role,
        "grantable_roles": role_ui.grantable_roles(user, account) if can_manage_role else [],
        "access": role_ui.access_summary(account) if (with_access and can_see_access) else None,
    }


@login_required
def staff_access(request, pk):
    """The Access tab (read-only role + effective permissions) as an HTMX
    fragment; a normal request gets the full profile on that tab."""
    from qa_core import ui

    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
    if not ui.is_htmx(request):
        return redirect(reverse("staff:detail", args=[pk]) + "?tab=access")
    ctx = {"staff": staff, **_role_context(request, staff, with_access=True)}
    if not ctx["can_see_access"]:
        raise PermissionDenied
    return render(request, "staff/_access_tab.html", ctx)


@login_required
@require_POST
def staff_role(request, pk):
    """Manage role (C2/C3): HTMX save through role_ui.role_assign."""
    from accounts.models import Role
    from qa_core import ui

    from . import role_ui

    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
    if not staff.user_id:
        raise Http404("No account")
    raw = (request.POST.get("role") or "").strip()
    role = Role.objects.filter(pk=int(raw)).first() if raw.isdigit() else None
    error = ""
    if raw and role is None:
        error = "Choose a valid role."
    else:
        try:
            role_ui.role_assign(staff.user, role, request.user, reason=request.POST.get("reason", ""))
        except role_ui.RoleAssignError as exc:
            error = str(exc)
    staff.user.refresh_from_db()
    if not ui.is_htmx(request):
        (messages.error if error else messages.success)(
            request, error or f"Role set to {staff.user.role.name if staff.user.role else 'none'}.")
        return redirect(reverse("staff:detail", args=[pk]) + "?tab=access")
    ctx = {"staff": staff, "oob_chip": True, **_role_context(request, staff, with_access=True)}
    resp = render(request, "staff/_access_tab.html", ctx, status=422 if error else 200)
    if error:
        return ui.toast(resp, "error", "Role not changed", error)
    return ui.toast(resp, "success", "Role saved",
                    f"{staff.full_name} is now {staff.user.role.name if staff.user.role else 'without a role'}.")


def _may_print_ids(user, staff_ids):
    """E4: staff.print_staff_id prints anyone in scope; everyone may print their own."""
    if user.has_perm("staff.print_staff_id"):
        return True
    own = Staff.objects.filter(user_id=user.pk).values_list("pk", flat=True).first()
    return bool(own) and set(staff_ids) == {own}


@login_required
def staff_id_print(request):
    """E3/E4: print page for one or many ID cards in a registry style.
    GET ids=<pk>&ids=<pk>…&style=cr80|a4x8|badge. @page sets the paper size."""
    from . import staff_id_styles

    ids = [int(i) for i in request.GET.getlist("ids") if str(i).isdigit()][:staff_id_styles.MAX_CARDS]
    if not ids:
        raise Http404("Choose staff to print.")
    if not _may_print_ids(request.user, ids):
        raise PermissionDenied
    staff = list(permissions.staff_visible_to(request.user).filter(pk__in=ids).order_by("last_name", "first_name"))
    if not staff:
        raise Http404
    style = staff_id_styles.get_style(request.GET.get("style"))
    cards = staff_id_styles.cards_for(staff, request)
    from core import audit
    for s in staff:
        if s.user_id != request.user.pk:
            audit.log("staff_id_print", s, actor=request.user, changes={"style": style.key})
    resp = render(request, "staff/id_cards/print.html", {
        "style": style, "cards": cards, "pages": staff_id_styles.pages_for(style, cards)})
    resp["Cache-Control"] = "private, no-store"
    return resp


@login_required
def staff_id_card(request, pk):
    """E4: HR opens a colleague's ID card page (formats and print buttons)."""
    from . import staff_id_styles

    staff = get_object_or_404(permissions.staff_visible_to(request.user), pk=pk)
    if not _may_print_ids(request.user, [staff.pk]):
        raise PermissionDenied
    card = staff_id_styles.cards_for([staff], request)[0]
    return render(request, "accounts/staff_id.html", {
        "card": card, "staff": staff, "own": False, "styles": staff_id_styles.STYLES})


def _log_contact(request, staff, channel, subject, body):
    """Staff file: record that this person was contacted from their page
    (subject and a short preview only)."""
    from core import audit
    preview = (body or "").strip().replace("\n", " ")
    audit.log("staff_contact", staff, actor=request.user, changes={
        "channel": channel, "subject": (subject or "")[:200],
        "preview": preview[:140] + ("…" if len(preview) > 140 else ""),
    }, note=f"{channel} sent from the staff page")


def _lifecycle_context(request, staff):
    """Nominal-roll facts and the promotion timeline / Promote button on the
    detail page (nominal-roll release)."""
    from django.utils import timezone

    from accounts.scoping import scope_to_department

    from .models import Designation
    from .promotions import can_override_step, eligibility, levels

    user = request.user
    can_promote = user.has_perm("staff.promote_staff") and scope_to_department(
        Staff.objects.filter(pk=staff.pk), user).exists()
    can_transfer = user.has_perm("staff.transfer_staff") and scope_to_department(
        Staff.objects.filter(pk=staff.pk), user).exists()
    show_promotions = can_promote or can_transfer or permissions.can_view_hr_fields(user, staff)
    promotions = []
    if show_promotions:
        promotions = list(staff.promotions.select_related(
            "from_designation", "to_designation", "approved_by", "reverses")[:30])
    quals = [q.strip() for q in (staff.qualifications or "").replace(";", ",").split(",") if q.strip()]
    return {
        "can_promote": can_promote,
        "show_promotions": show_promotions,
        "promotions": promotions,
        "qualification_list": quals,
        "promotion_targets": (Designation.objects.filter(is_active=True).order_by("-rank", "name")
                              if can_promote and can_override_step(user) else []),
        "promotion_next": levels.next_level(staff) if can_promote else None,
        "promotion_eligibility": eligibility(staff) if show_promotions else None,
        "can_override_promotion": can_promote and can_override_step(user),
        "today": timezone.localdate(),
        "can_transfer": can_transfer,
        "transfer_departments": _transfer_choices(user, "department") if can_transfer else [],
        "transfer_offices": _transfer_choices(user, "office") if can_transfer else [],
        **_staff_file(staff, show_promotions),
    }


def _staff_file(staff, visible):
    if not visible:
        return {"staff_file": [], "staff_file_counts": {}, "items_held": []}
    from . import staff_file
    events = staff_file.timeline(staff)
    return {"staff_file": events, "staff_file_counts": staff_file.counts(events),
            "staff_file_kinds": staff_file.KINDS, "items_held": staff_file.items_held(staff)}


def _transfer_choices(user, kind):
    """Destinations for the Transfer dialog: any active department / office
    (the permission check is on the staff member being moved)."""
    from .models import Department, Office
    model = Office if kind == "office" else Department
    return model.objects.filter(is_active=True).order_by("name")


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
                role = form.cleaned_data.get("role")
                staff.refresh_from_db()
                if role is not None and staff.user_id:
                    from . import role_ui
                    try:
                        role_ui.role_assign(staff.user, role, request.user, reason="Set when the staff member was added")
                    except role_ui.RoleAssignError as exc:
                        messages.error(request, f"Role not set: {exc}")

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

    # Review H7: people editing their own record get the contact-only form.
    self_edit = not permissions.can_hr_edit_staff(request.user, staff)
    FormClass = StaffSelfForm if self_edit else StaffForm

    if request.method == "POST":
        form = FormClass(request.POST, request.FILES, instance=staff, user=request.user)
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
        form = FormClass(instance=staff, user=request.user)

    return render(request, "staff/form.html", {
        "form": form,
        "mode": "update",
        "staff": staff,
        "self_edit": self_edit,
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
    Review H5: delegates to staff.services.provision_account_for_staff,
    which avoids the duplicate-Staff crash and never displays the password.
    """
    from .services import provision_account_for_staff

    try:
        status, user = provision_account_for_staff(
            staff, password=password,
            login_url=request.build_absolute_uri(reverse("accounts:login")),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Account provisioning failed for staff %s", staff.pk)
        messages.error(request, "Could not create the account. Check the server log.")
        return

    if status == "exists":
        messages.info(request, "This staff member already has an account.")
    elif status == "no_email":
        messages.warning(request, "No email — account not created.")
    elif status == "linked":
        messages.info(request, f"Linked to existing account {user.email}.")
    elif status == "conflict":
        messages.error(
            request,
            f"An account with {staff.email} already belongs to another directory "
            f"record. Account not created; resolve the duplicate first.",
        )
    elif status == "created":
        messages.success(request, f"Account created for {staff.email} — credentials emailed.")
    else:
        messages.warning(
            request,
            f"Account created for {staff.email}, but the welcome email could not be "
            f"queued. Use 'Reset password' on the user page to send new credentials.",
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
    _log_contact(request, staff, "message", subject, body)
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
        _log_contact(request, staff, "notification", title, body)
    except Exception as exc:
        messages.error(request, f"Could not send notification: {exc}")
    return redirect("staff:detail", pk=staff.pk)      
        
        
# ============================================================================
# LEADERSHIP (public)
# ============================================================================
def public_leadership(request):
    """v2.1 T-CONS-4: the two leadership pages are merged into /leadership/
    (current leaders first, then every previous holder). Old links redirect."""
    return redirect("core:leadership_page", permanent=False)


def leadership_tenure_detail(request, pk):
    """A single tenure — profile view with bio, qualifications, history."""
    tenure = get_object_or_404(
        ExecutiveTenure.objects.select_related("staff", "position"),
        pk=pk, is_public=True,
    )
    # Other positions the same person has held
    other_tenures = (
        ExecutiveTenure.objects
        .filter(staff=tenure.staff, is_public=True, staff__isnull=False)
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
from django.http import Http404
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
                f"Recorded “{tenure.position.name}” for {tenure.person_name}.",
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
        f"Ended “{tenure.position.name}” for {tenure.person_name}.",
    )
    return redirect("staff:leadership_panel")        
        
        
        
        
        
        
        
        