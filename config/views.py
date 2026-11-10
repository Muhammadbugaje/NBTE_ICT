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
    # QA keys (Master Plan §14) live on Settings → QA, which enforces
    # `editable_by`, dual control and locked keys. They are not listed here.
    entries = list(
        ConfigEntry.objects.exclude(key=ROLE_ENTITLEMENT_CONFIG_KEY)
        .exclude(key__startswith="qa.").exclude(key__startswith="honoraria.")
        # v2.1 A9: appearance rows live on Settings → Appearance
        .exclude(key__startswith="appearance.").exclude(key="ui.legacy_css")
        .order_by("group", "key", "scope")
    )
    qa_settings_count = ConfigEntry.objects.filter(key__startswith="qa.").count()
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
        "qa_settings_count": qa_settings_count,
        "backup_status": _backup_status(request.user),
    })


def _backup_status(user):
    """One line for the Backups card on this page (Phase 8, §22)."""
    if not user.has_perm("backups.view_backups"):
        return None
    try:
        from backups.models import BackupDestination, BackupRun

        if not BackupDestination.objects.filter(is_active=True).exists():
            return {"ok": False, "text": "No backup destination configured"}
        last = BackupRun.objects.exclude(status=BackupRun.RUNNING).first()
        if last is None:
            return {"ok": False, "text": "No backup taken yet"}
        when = timezone.localtime(last.started_at).strftime("%d %b %Y")
        return {"ok": last.status == BackupRun.SUCCEEDED, "text": f"Last backup {when}: {last.get_status_display().lower()}"}
    except Exception:
        return None


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
    # QA (Master Plan §14): locked keys never change, and dual-control or
    # permission-gated keys only change through Settings → QA, which routes
    # them to a second officer. This generic page must not be a way around it.
    if entry.is_locked:
        messages.error(request, f"{entry.key} is fixed by policy and cannot be changed.")
        return redirect("config:settings")
    if entry.key.startswith("appearance.") or entry.key == "ui.legacy_css":
        messages.error(request, f"{entry.key} is managed on Settings → Appearance.")
        return redirect("config:appearance")
    if entry.dual_control or entry.editable_by or entry.key.startswith("qa."):
        messages.error(request, f"{entry.key} is managed on Settings → QA.")
        return redirect("config:settings")
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


# ---------------------------------------------------------------------------
# v2.1 Phase A (A5/A6): favicon and Settings → Branding
# ---------------------------------------------------------------------------
def favicon(request):
    """The app-icon slot as the browser icon: the SVG for built-ins (PNG
    fallbacks 32/180/512 are linked in <head>), or the uploaded image."""
    from django.http import FileResponse, HttpResponse, HttpResponseRedirect

    from . import branding

    snap = branding.active_logo("app_icon")
    if snap is not None and snap.light_url and not snap.light_url.endswith(".svg"):
        return HttpResponseRedirect(snap.light_url)
    path = branding.BRAND_DIR / "nbte-app-icon.svg"
    if snap is not None and snap.light_url.endswith(".svg"):
        name = snap.light_url.rsplit("/", 1)[-1].split("?")[0]
        candidate = branding.BRAND_DIR / name
        if candidate.exists():
            path = candidate
    resp = HttpResponse(path.read_bytes(), content_type="image/svg+xml")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


def favicon_ico(request):
    from django.http import HttpResponseRedirect
    from django.templatetags.static import static

    return HttpResponseRedirect(static("img/brand/nbte-icon-32.png"))


def _can_brand(user):
    return user.is_authenticated and (user.has_perm("config.manage_branding")
                                      or user.has_perm("config.change_configentry"))


def _branding_context():
    from . import branding
    from .models import BrandLogo

    logos = list(BrandLogo.objects.all())
    slots = []
    for code, label, where in branding.SLOTS:
        slots.append({"code": code, "label": label, "where": where, "current": branding.slot_code(code),
                      "markup": branding.active_logo_markup(code, 56 if code != "wordmark" else 40)})
    cards = [{"logo": lg, "markup": branding.logo_markup(branding._snapshot_of(lg), 64 if not lg.is_wordmark else 40)}
             for lg in logos if lg.image or lg.static_path]
    return {"slots": slots, "cards": cards}


@login_required
def branding_page(request):
    from django.core.exceptions import PermissionDenied

    if not _can_brand(request.user):
        raise PermissionDenied
    return render(request, "config/branding.html", _branding_context())


@login_required
@require_POST
def branding_select(request):
    from django.core.exceptions import PermissionDenied

    from core import audit

    from . import branding
    from .models import BrandLogo
    from .registry import invalidate

    if not _can_brand(request.user):
        raise PermissionDenied
    slot, code = request.POST.get("slot", ""), request.POST.get("code", "")
    if slot not in {s[0] for s in branding.SLOTS} or not BrandLogo.objects.filter(code=code).exists():
        messages.error(request, "Choose a valid logo and place.")
    else:
        entry, _ = ConfigEntry.objects.get_or_create(
            key=f"brand.logo.{slot}", scope="global",
            defaults={"value": code, "value_type": "str", "group": "Branding"})
        old = entry.value
        entry.value = code
        entry.save()
        invalidate(entry.key, entry.scope)
        branding.invalidate()
        audit.log("update", entry, actor=request.user, changes={"value": [old, code]}, note="brand logo chosen")
        messages.success(request, f"{dict((s[0], s[1]) for s in branding.SLOTS)[slot]} updated.")
    if request.headers.get("HX-Request"):
        return render(request, "config/_branding_slots.html", _branding_context())
    return redirect("config:branding")


@login_required
@require_POST
def branding_upload(request):
    from django.core.exceptions import PermissionDenied
    from django.utils.text import slugify

    from . import branding
    from .models import BrandLogo

    if not _can_brand(request.user):
        raise PermissionDenied
    name = (request.POST.get("name") or "").strip()[:80]
    image, image_dark = request.FILES.get("image"), request.FILES.get("image_dark")
    if not name or not image:
        messages.error(request, "Give the logo a name and choose an image.")
        return redirect("config:branding")
    base = slugify(name)[:30] or "logo"
    code, n = base, 2
    while BrandLogo.objects.filter(code=code).exists():
        code, n = f"{base}-{n}", n + 1
    logo = BrandLogo(code=code, name=name, image=image, image_dark=image_dark or None,
                     is_wordmark=bool(request.POST.get("is_wordmark")), order=100)
    try:
        from PIL import Image as _Image

        with _Image.open(image) as im:
            logo.aspect = round(im.width / max(1, im.height), 3)
        image.seek(0)
    except Exception:
        messages.error(request, "That file is not an image we can read.")
        return redirect("config:branding")
    try:
        logo.full_clean()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("config:branding")
    logo.save()
    branding.invalidate()
    messages.success(request, f"Uploaded {name}. Choose it for a place below.")
    return redirect("config:branding")
