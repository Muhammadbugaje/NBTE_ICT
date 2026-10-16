"""User Management — admin-side account operations.

Distinct from `force_password_change` (self-service, requires current password).
These views are for Super Admin / HR / anyone with the right permission:
  - View all users, filter by department / role / active state
  - Reset a user's password (custom or random)
  - Force change on next login
  - Activate / deactivate
  - Edit roles
"""
from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import password_validation
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

User = get_user_model()


def _users_visible_to(actor):
    """Review M7: user management is department-scoped like everything else.
    Superusers and `access_all_departments` holders see every account."""
    from .scoping import scope_to_department
    return scope_to_department(User.objects.all(), actor)


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def user_list(request):
    qs = _users_visible_to(request.user).select_related("department", "role").order_by(
        "first_name", "last_name", "email"
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
        )
    active = request.GET.get("active")
    if active == "1":
        qs = qs.filter(is_active=True)
    elif active == "0":
        qs = qs.filter(is_active=False)

    page = Paginator(qs, 30).get_page(request.GET.get("page") or 1)
    return render(request, "accounts/user_list.html", {
        "page": page, "q": q, "active_filter": active, "total": qs.count(),
    })


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def user_detail(request, pk):
    user = get_object_or_404(_users_visible_to(request.user), pk=pk)
    return render(request, "accounts/user_detail.html", {"u": user})

import logging
import secrets

logger = logging.getLogger(__name__)


@login_required
@permission_required("accounts.change_user", raise_exception=True)
def user_reset_password(request, pk):
    user = get_object_or_404(_users_visible_to(request.user), pk=pk)
    # Review H3: never act on a superuser or someone who outranks you.
    from .permissions import can_act_on_user
    if not can_act_on_user(request.user, user):
        raise PermissionDenied("You cannot reset the password of this account.")

    if request.method == "POST":
        action = request.POST.get("action") or "generate"
        force_change = request.POST.get("force_change") == "on"

        # ---- Default-on: only skip if the operator explicitly unchecked it.
        raw_send = request.POST.get("send_email")
        # Checkboxes that are checked submit "on"; unchecked submit nothing.
        # If the key is missing entirely, we treat it as "checked by default"
        # because the form has it ticked by default.
        if raw_send is None:
            send_email = True
        else:
            send_email = raw_send == "on"

        # ---- Password choice
        # Review H3: the new password is never shown on screen (flash
        # messages live in a cookie). A generated password can only be
        # delivered by email; a typed one is known to the operator already.
        if action == "set":
            new_password = (request.POST.get("password") or "").strip()
            try:
                password_validation.validate_password(new_password, user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("accounts:user_reset_password", pk=pk)
        else:
            if not (send_email and user.email):
                messages.error(
                    request,
                    "A generated password can only be delivered by email. "
                    "Tick 'Email the new credentials' (the account needs an email "
                    "address), or choose 'Set a specific password'.",
                )
                return redirect("accounts:user_reset_password", pk=pk)
            from .services import generate_temporary_password
            new_password = generate_temporary_password()

        # ---- Apply
        user.set_password(new_password)
        if hasattr(user, "must_change_password"):
            user.must_change_password = force_change
        user.save()
        logger.info(
            "Password reset for %s by %s (send_email=%s, force_change=%s)",
            user.email, request.user.email, send_email, force_change,
        )

        # ---- Email
        if not send_email or not user.email:
            messages.success(request, "Password reset. No email was sent; give the user the password you set.")
            return redirect("accounts:user_detail", pk=pk)

        sent = _send_reset_email(request, user, new_password, force_change)
        if sent:
            messages.success(request, f"Password reset — credentials email queued for {user.email}.")
        else:
            messages.warning(
                request,
                "Password reset, but the email could not be queued. "
                "Check the server log, then reset again.",
            )
        return redirect("accounts:user_detail", pk=pk)

    return render(request, "accounts/user_reset_password.html", {"u": user})


def _send_reset_email(request, user, new_password, force_change):
    """Queue credentials through the configured provider engine."""
    login_url = request.build_absolute_uri("/accounts/login/")
    try:
        from core.email_utils import send_templated_email
        email_log = send_templated_email(
            subject="Your NBTE ICT password has been reset",
            recipients=[user.email],
            template_name="emails/password_reset_by_admin.html",
            context={
                "user": user,
                "temp_password": new_password,
                "force_change": force_change,
                "login_url": login_url,
            },
            category="credentials",
        )
        if email_log is None:
            logger.error("Password-reset credentials email could not be queued.")
            return False
        logger.info("Password-reset credentials email queued log_id=%s", email_log.pk)
        return True
    except Exception as exc:
        from notifications.engine import safe_error_message

        logger.error(
            "Password-reset credentials email could not be queued: %s",
            safe_error_message(exc),
        )
        return False


@login_required
@permission_required("accounts.change_user", raise_exception=True)
@require_POST
def user_toggle_active(request, pk):
    user = get_object_or_404(_users_visible_to(request.user), pk=pk)
    from .permissions import can_act_on_user
    if not can_act_on_user(request.user, user):
        raise PermissionDenied("You cannot change this account.")
    if user.pk == request.user.pk:
        messages.error(request, "You can't deactivate your own account.")
        return redirect("accounts:user_detail", pk=pk)
    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])
    messages.success(request, f"{user.email} is now {'active' if user.is_active else 'inactive'}.")
    return redirect("accounts:user_detail", pk=pk)


@login_required
@permission_required("accounts.change_user", raise_exception=True)
def user_edit_roles(request, pk):
    """Review M6: assign the user's Role (accounts.Role), which is what
    RolePermissionBackend reads. The old page wrote Django groups, which
    the backend ignores, and then failed silently to set the role."""
    from .models import Role
    from .permissions import can_act_on_user, rank_of

    user = get_object_or_404(_users_visible_to(request.user), pk=pk)
    if not can_act_on_user(request.user, user):
        raise PermissionDenied("You cannot change this account's role.")

    roles = Role.objects.order_by("-rank", "name")
    if not request.user.is_superuser:
        # Nobody may hand out a role more senior than their own.
        roles = roles.filter(rank__lte=rank_of(request.user))

    if request.method == "POST":
        if "role" not in request.POST:
            messages.error(request, "Choose a role.")
            return redirect("accounts:user_edit_roles", pk=pk)
        raw = (request.POST.get("role") or "").strip()
        if raw == "":
            new_role = None
        else:
            new_role = roles.filter(pk=raw).first() if raw.isdigit() else None
            if new_role is None:
                messages.error(request, "Choose a valid role.")
                return redirect("accounts:user_edit_roles", pk=pk)
        if user.role_id != getattr(new_role, "pk", None):
            user.role = new_role
            user.save(update_fields=["role"])
            messages.success(request, f"Role set to {new_role.name if new_role else 'none'}.")
        else:
            messages.info(request, "Role unchanged.")
        return redirect("accounts:user_detail", pk=pk)

    return render(request, "accounts/user_edit_roles.html", {
        "u": user, "roles": roles,
    })