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
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

User = get_user_model()


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def user_list(request):
    qs = User.objects.select_related("department", "role").order_by(
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
    user = get_object_or_404(User, pk=pk)
    return render(request, "accounts/user_detail.html", {"u": user})

import logging
import secrets

logger = logging.getLogger(__name__)


@login_required
@permission_required("accounts.change_user", raise_exception=True)
def user_reset_password(request, pk):
    user = get_object_or_404(User, pk=pk)

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
        if action == "set":
            new_password = (request.POST.get("password") or "").strip()
            if len(new_password) < 8:
                messages.error(request, "Password must be at least 8 characters.")
                return redirect("accounts:user_reset_password", pk=pk)
        else:
            new_password = secrets.token_urlsafe(10)

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
        if not send_email:
            messages.success(request, f"Password reset. Email not sent (unchecked). New password: {new_password}")
            return redirect("accounts:user_detail", pk=pk)

        if not user.email:
            messages.warning(request, f"Password reset. No email on file. New password: {new_password}")
            return redirect("accounts:user_detail", pk=pk)

        sent = _send_reset_email(request, user, new_password, force_change)
        if sent:
            messages.success(request, f"Password reset — credentials email queued for {user.email}.")
        else:
            messages.warning(
                request,
                f"Password reset, but the email could not be sent. "
                f"Check server logs. New password: {new_password}",
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
    user = get_object_or_404(User, pk=pk)
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
    user = get_object_or_404(User, pk=pk)
    from django.contrib.auth.models import Group, Permission

    all_groups = Group.objects.order_by("name")
    if request.method == "POST":
        group_ids = request.POST.getlist("groups")
        user.groups.set(all_groups.filter(pk__in=group_ids))
        # Single-role convenience: also sync the user's `role` FK if the model has one.
        if hasattr(user, "role_id") and group_ids:
            try:
                role = all_groups.get(pk=group_ids[0])
                user.role = role
                user.save(update_fields=["role"])
            except Exception:
                pass
        messages.success(request, "Roles updated.")
        return redirect("accounts:user_detail", pk=pk)

    return render(request, "accounts/user_edit_roles.html", {
        "u": user, "all_groups": all_groups,
    })