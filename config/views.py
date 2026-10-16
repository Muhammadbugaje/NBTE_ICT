from email.utils import parseaddr
import json
import logging
from itertools import groupby

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import ConfigEntry

logger = logging.getLogger(__name__)


@login_required
@permission_required("config.change_configentry", raise_exception=True)
def settings_index(request):
    from accounts.models import Role
    from config.registry import get as get_config
    from leave.forms import LeaveRoleEntitlementForm
    from leave.services import ROLE_ENTITLEMENT_CONFIG_KEY

    roles = list(Role.objects.order_by("-rank", "name"))
    role_entitlements = get_config(ROLE_ENTITLEMENT_CONFIG_KEY, {})
    role_entitlement_error = ""
    if not isinstance(role_entitlements, dict):
        role_entitlements = {}
        role_entitlement_error = (
            "The saved role entitlement policy is invalid. Save the role values "
            "again to replace it."
        )

    role_entitlement_form = LeaveRoleEntitlementForm(
        roles=roles,
        values=role_entitlements,
    )
    role_entitlement_rows = [
        {"role": role, "field": role_entitlement_form[f"role_{role.pk}"]}
        for role in roles
    ]
    entries = list(
        ConfigEntry.objects.exclude(key=ROLE_ENTITLEMENT_CONFIG_KEY)
        .order_by("group", "key", "scope")
    )
    grouped = [(g or "General", list(items)) for g, items in groupby(entries, key=lambda e: e.group)]
    from notifications import engine

    quota = [
        {"provider": key, "used": engine.used_today(key), "limit": engine.daily_limit(key)}
        for key in dict.fromkeys(engine.quota_key(p) for p in engine.PROVIDERS)
    ]
    from notifications.models import EmailLog

    recent_email_tests = EmailLog.objects.filter(category="test").order_by("-created_at")[:5]
    sender_email = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    sender_domain = sender_email.rsplit("@", 1)[-1] if "@" in sender_email else ""
    return render(request, "config/settings.html", {
        "grouped": grouped,
        "quota": quota,
        "email_provider_order": engine.provider_order("test"),
        "email_sender_domain": sender_domain,
        "recent_email_tests": recent_email_tests,
        "leave_roles": roles,
        "leave_role_entitlement_form": role_entitlement_form,
        "leave_role_entitlement_rows": role_entitlement_rows,
        "leave_role_entitlement_error": role_entitlement_error,
    })


@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def save_leave_role_entitlements(request):
    from accounts.models import Role
    from config.registry import get as get_config, invalidate
    from leave.forms import LeaveRoleEntitlementForm
    from leave.services import ROLE_ENTITLEMENT_CONFIG_KEY

    roles = list(Role.objects.order_by("-rank", "name"))
    form = LeaveRoleEntitlementForm(request.POST, roles=roles)
    if not form.is_valid():
        messages.error(
            request,
            "Check the role entitlement values. Days must be zero or greater "
            "and may include up to two decimal places.",
        )
        return redirect("config:settings")

    current = get_config(ROLE_ENTITLEMENT_CONFIG_KEY, {})
    if not isinstance(current, dict):
        current = {}
    policy = dict(current)
    for role in roles:
        days = form.cleaned_data[f"role_{role.pk}"]
        if days is None:
            policy.pop(role.code, None)
        else:
            policy[role.code] = format(days, "f")

    entry, _created = ConfigEntry.objects.get_or_create(
        key=ROLE_ENTITLEMENT_CONFIG_KEY,
        scope="global",
        defaults={
            "value": "{}",
            "value_type": "json",
            "group": "Leave",
            "description": (
                "Default annual leave days by system role. Blank roles have "
                "no configured entitlement."
            ),
        },
    )
    entry.value = json.dumps(policy, sort_keys=True)
    entry.value_type = "json"
    entry.group = "Leave"
    entry.description = (
        "Default annual leave days by system role. Blank roles have "
        "no configured entitlement."
    )
    try:
        with transaction.atomic():
            entry.full_clean()
            entry.save()
    except ValidationError as exc:
        messages.error(
            request,
            "Could not save role leave defaults: " + "; ".join(exc.messages),
        )
    else:
        invalidate(entry.key, entry.scope)
        messages.success(request, "Annual leave defaults saved for system roles.")
    return redirect("config:settings")


@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def settings_save(request, pk):
    entry = get_object_or_404(ConfigEntry, pk=pk)
    entry.value = request.POST.get("value", "")
    try:
        entry.full_clean()
    except ValidationError as exc:
        messages.error(request, f"{entry.key}: " + "; ".join(exc.messages))
    else:
        entry.save()  # post_save signal invalidates the cache -> effective immediately
        messages.success(request, f"Saved {entry.key}.")
    return redirect("config:settings")


@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def send_test_email(request):
    """Synchronous on purpose so the admin sees which provider actually delivered."""
    from notifications import engine
    from notifications.models import EmailLog

    recipient = request.user.email
    subject = f"{settings.SITE_NAME} test email"
    log = EmailLog.objects.create(
        subject=subject,
        recipients=[recipient] if recipient else [],
        recipient_text=recipient,
        category="test",
        user=request.user,
        scheduled_for=timezone.now(),
    )
    if not recipient:
        log.status = EmailLog.FAILED
        log.last_error = "The signed-in account does not have an email address."
        log.save(update_fields=["status", "last_error"])
        logger.error("Settings email test failed log_id=%s reason=no_recipient", log.pk)
        messages.error(request, log.last_error)
        return redirect("config:settings")

    trail = []
    try:
        provider = engine.send(
            subject,
            [recipient],
            "<p>This is a test email from the settings page.</p>",
            "Test email.",
            category="test",
            trail=trail,
        )
    except engine.DeliveryError as exc:
        error = engine.safe_error_message(exc)
        log.status = EmailLog.FAILED
        log.attempts = 1
        log.last_error = error
        log.attempt_trail = [dict(item, attempt=1) for item in trail]
        log.save(update_fields=["status", "attempts", "last_error", "attempt_trail"])
        logger.error("Settings email test failed log_id=%s error=%s", log.pk, error)
        messages.error(request, f"Test email failed: {error}")
    else:
        log.status = EmailLog.SENT
        log.provider = provider
        log.sent_at = timezone.now()
        log.attempts = 1
        log.attempt_trail = [dict(item, attempt=1) for item in trail]
        log.save(update_fields=["status", "provider", "sent_at", "attempts", "attempt_trail"])
        logger.info("Settings email test accepted log_id=%s provider=%s", log.pk, provider)
        messages.success(
            request,
            f"Test email accepted by {provider} for {recipient}. Check the provider's delivery log for final status.",
        )
    return redirect("config:settings")
