import mimetypes
import shutil
from pathlib import Path
from urllib.parse import quote

from django.apps import apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from notifications.models import Notification

from . import audit
from .forms import ContactForm
from .restricted import RestrictedFileModel


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
def landing(request):
    """Public landing page — no login required."""
    return render(request, "public/landing.html")


def _module_cards(user):
    """Every card shown on the home dashboard. Each has a real URL where the
    module exists; the counter is best-effort and None when we can't compute it.
    """
    from django.utils import timezone as _tz
    today = _tz.localdate()
    cards = []

    # ---- Tickets ----
    cards.append(_make_card(
        user, key="tickets", label="Helpdesk tickets",
        hint="Report, track, resolve IT issues",
        url_name="tickets:list",
        counter=_safe_count("tickets", "Ticket"),
        count_label="total",
    ))

    # ---- Requests & approvals ----
    cards.append(_make_card(
        user, key="requests", label="Requests & approvals",
        hint="Leave, proposals, sign-off chains",
        url_name="workflow:my_requests",
        counter=None, count_label="",
    ))

    # ---- Tasks ----
    cards.append(_make_card(
        user, key="tasks", label="My tasks",
        hint="Assigned work and progress",
        url_name="tasks:assigned_to_me",
        counter=None, count_label="",
    ))

    # ---- Assets ----
    cards.append(_make_card(
        user, key="assets", label="My assets",
        hint="Devices and equipment in your custody",
        url_name="assets:my_assets",
        counter=None, count_label="",
    ))

    # ---- Attendance ----
    attendance_today = None
    try:
        from attendance.models import AttendanceRecord
        rec = AttendanceRecord.objects.filter(user=user, date=today).first()
        attendance_today = rec.get_status_display() if rec else "Not yet"
    except Exception:
        attendance_today = None
    cards.append(_make_card(
        user, key="attendance", label="My attendance",
        hint="Check in and see your history",
        url_name="attendance:home",
        counter=attendance_today,
        count_label="today",
    ))

    # ---- Announcements ----
    ann_count = None
    try:
        from portal import services as _portal_services
        ann_count = _portal_services.live_announcements(user=user).count()
    except Exception:
        ann_count = None
    cards.append(_make_card(
        user, key="announcements", label="Announcements",
        hint="News, notices and updates",
        url_name="portal:announcement_list",
        counter=ann_count,
        count_label="live",
    ))

    # ---- Events ----
    events_count = None
    try:
        from portal import services as _portal_services
        events_count = _portal_services.live_events(user=user).count()
    except Exception:
        events_count = None
    cards.append(_make_card(
        user, key="events", label="Events",
        hint="Upcoming, ongoing and past events",
        url_name="portal:event_list",
        counter=events_count,
        count_label="live",
    ))

    # ---- Documents ----
    docs_count = None
    try:
        from portal import services as _portal_services
        docs_count = _portal_services.live_documents(user=user).count()
    except Exception:
        docs_count = None
    cards.append(_make_card(
        user, key="documents", label="Documents",
        hint="Policies, memos and reference files",
        url_name="portal:document_list",
        counter=docs_count,
        count_label="available",
    ))

    # ---- Awards ----
    awards_count = None
    try:
        from portal import services as _portal_services
        awards_count = _portal_services.live_awards(user=user).count()
    except Exception:
        awards_count = None
    cards.append(_make_card(
        user, key="awards", label="Awards & milestones",
        hint="Recognitions and service anniversaries",
        url_name="portal:awards_list",
        counter=awards_count,
        count_label="records",
    ))

    # ---- Gallery ----
    gallery_count = None
    try:
        from portal import services as _portal_services
        gallery_count = _portal_services.live_albums(user=user).count()
    except Exception:
        gallery_count = None
    cards.append(_make_card(
        user, key="gallery", label="Gallery",
        hint="Photos and videos from the directorate",
        url_name="portal:gallery_list",
        counter=gallery_count,
        count_label="albums",
    ))

    # ---- Holidays ----
    holiday_next = None
    try:
        from portal import services as _portal_services
        nxt = _portal_services.upcoming_holidays(user=user, limit=1).first()
        if nxt:
            holiday_next = nxt.date_start.strftime("%b %d")
    except Exception:
        holiday_next = None
    cards.append(_make_card(
        user, key="holidays", label="Holidays",
        hint="Holiday calendar and absences",
        url_name="portal:holidays_list",
        counter=holiday_next,
        count_label="next",
    ))

    # ---- Leadership (visible to everyone) ----
    leadership_count = None
    try:
        from staff import services as _staff_services
        leadership_count = _staff_services.current_leadership(
            public_only=True,
        ).count()
    except Exception:
        leadership_count = None
    cards.append(_make_card(
        user, key="leadership", label="Leadership",
        hint="Current executives and full history",
        url_name="staff:public_leadership",
        counter=leadership_count,
        count_label="executives",
    ))

    # ---- Staff directory ----
    staff_count = None
    if (user.is_superuser
            or user.has_perm("staff.view_staff")
            or user.has_perm("staff.view_own_department_staff")):
        try:
            from staff.permissions import staff_visible_to
            staff_count = staff_visible_to(user).count()
        except Exception:
            staff_count = None
        cards.append(_make_card(
            user, key="staff", label="Staff directory",
            hint="People, positions, records",
            url_name="staff:list",
            counter=staff_count,
            count_label="staff",
        ))

    # ---- Messages ----
    cards.append(_make_card(
        user, key="messages", label="Internal messages",
        hint="Inbox, threads, colleagues",
        url_name="messaging:inbox",
        counter=_count_unread_messages(user),
        count_label="unread",
    ))

    # ---- Notifications ----
    try:
        from notifications.models import Notification as _N
        notif_count = _N.objects.filter(recipient=user, is_read=False).count()
    except Exception:
        notif_count = None
    cards.append(_make_card(
        user, key="notifications", label="Notifications",
        hint="System updates and alerts",
        url_name="notifications:list",
        counter=notif_count,
        count_label="unread",
    ))

    return cards


def _dashboard_for(user):
    """Return the role-specific dashboard link, or None."""
    if not user.is_authenticated:
        return None

    key = None
    # Superuser or super-admin role → SuperAdmin dashboard (not Director's).
    if user.is_superuser or (user.role_id and user.role.code == "super-admin"):
        key = "superadmin"
    elif user.role_id:
        key = {
            "director":            "director",
            "deputy-director":     "deputy",
            "executive-secretary": "es",
        }.get(user.role.code)

    # Chairman — no dedicated role exists; gate by permission.
    if key is None and user.has_perm("dashboards.view_chairman"):
        key = "chairman"

    if not key:
        return None

    from django.urls import NoReverseMatch, reverse
    try:
        url = reverse(f"dashboards:{key}")
    except NoReverseMatch:
        return None

    label, hint = {
        "superadmin": ("Super Admin dashboard",       "System health, audit log, user & role controls"),
        "director":   ("Director's dashboard",        "Full oversight — approvals, offices, trends"),
        "deputy":     ("Deputy Director's dashboard", "Approval queue and office oversight"),
        "es":         ("Executive Secretary dashboard","Sign-offs, filing and executive actions"),
        "chairman":   ("Chairman's dashboard",        "Institutional view and trend analytics"),
    }[key]

    return {"key": key, "label": label, "hint": hint, "url": url}


def _make_card(user, *, key, label, hint, url_name, counter=None, count_label="", perm=None):
    """Return a single card dict, resolving the URL if possible."""
    url = None
    available = True
    if perm and not (user.is_superuser or user.has_perm(perm)):
        available = False
    if available and url_name:
        try:
            from django.urls import reverse
            url = reverse(url_name)
        except Exception:
            available = False
    return {
        "key": key,
        "label": label,
        "hint": hint,
        "url": url,
        "counter": counter,
        "count_label": count_label,
        "available": available,
    }


def _safe_count(app_label, model_name):
    """Best-effort count. Returns None if the app isn't installed."""
    try:
        from django.apps import apps as _apps
        model = _apps.get_model(app_label, model_name)
        if model is None:
            return None
        return model.objects.count()
    except Exception:
        return None

def _count_or_none(app_label, model_name, qs_factory):
    """Safely count a model's queryset, returning None if the app isn't ready."""
    try:
        from django.apps import apps as _apps
        model = _apps.get_model(app_label, model_name)
        if qs_factory:
            return qs_factory().count()
        return model.objects.count()
    except Exception:
        return None


def user_scope_tickets(user):
    """Tickets scoped to the user — defensive since the app may not exist yet."""
    from tickets.models import Ticket
    from django.db.models import Q
    if user.is_superuser or user.has_perm("tickets.view_all_tickets"):
        return Ticket.objects.all()
    return Ticket.objects.filter(Q(raised_by=user) | Q(assigned_to=user))

# ---------------------------------------------------------------------------
# Signed-in home (with real widgets)
# ---------------------------------------------------------------------------
@login_required
def home(request):
    """Signed-in landing page.

    Widgets are role-aware and empty-state-aware:
      - KPI tiles reflect data across modules the user can access
      - Recent activity panels pull from Messaging and Notifications
      - Quick actions and system status only appear for the roles that need them

    As new modules land (tickets, assets, requests), add their widgets here
    rather than building separate dashboards.
    """
    user = request.user
    ctx = {}

    # --- KPI tiles --------------------------------------------------------
    ctx["unread_messages"] = _count_unread_messages(user)
    ctx["unread_notifications"] = Notification.objects.filter(
        recipient=user, is_read=False
    ).count()

    ctx["show_staff_widget"] = user.has_perm("staff.view_staff")
    if ctx["show_staff_widget"]:
        ctx["staff_count"] = _staff_count_for(user)
        # Label reads differently for users scoped to one department vs. those
        # with cross-department rights.
        if user.is_superuser or user.has_perm("accounts.access_all_departments"):
            ctx["staff_label"] = "Staff in directory"
        else:
            ctx["staff_label"] = "Staff in your department"

    # --- Recent activity --------------------------------------------------
    ctx["recent_threads"] = _recent_threads(user, limit=4)
    ctx["recent_notifications"] = (
        Notification.objects.filter(recipient=user)
        .select_related("actor")[:5]
    )

    # --- Profile widget ---------------------------------------------------
    try:
        ctx["user_staff"] = user.staff_profile
    except Exception:
        ctx["user_staff"] = None

    # --- Quick actions ----------------------------------------------------
    ctx["can_import"] = user.has_perm("config.change_configentry")
    ctx["can_view_settings"] = user.has_perm("config.change_configentry")

    ctx["module_cards"] = _module_cards(user)
    ctx["dashboard"] = _dashboard_for(user)

    # --- System status (admins only) -------------------------------------
    if user.is_superuser or user.has_perm("config.view_configentry"):
        ctx["system_health"] = _quick_health()

    return render(request, "core/home.html", ctx)


def _count_unread_messages(user):
    """Sum of unread messages across every non-archived thread the user is in."""
    from messaging.models import Thread

    total = 0
    for t in (
        Thread.objects
        .filter(participants__user=user, participants__is_archived=False)
        .distinct()
    ):
        total += t.unread_count_for(user)
    return total


def _staff_count_for(user):
    """Staff count within the user's scope (own department, or all if permitted)."""
    from accounts.scoping import scope_to_department
    from staff.models import Staff

    return scope_to_department(Staff.objects.all(), user).count()


def _recent_threads(user, limit=4):
    from messaging.models import Thread

    qs = (
        Thread.objects
        .filter(participants__user=user, participants__is_archived=False)
        .distinct()
        .select_related("created_by")
        .prefetch_related("participants__user", "messages__sender")
    )
    threads = list(qs)
    for t in threads:
        t.unread = t.unread_count_for(user)
        t.last_msg = t.last_message()
        t.display_title = t.display_title_for(user)   # <-- ADD

    threads.sort(
        key=lambda t: (t.last_msg.sent_at if t.last_msg else t.updated_at),
        reverse=True,
    )
    return threads[:limit]

def _quick_health():
    """Small health snapshot for the admin system-status card."""
    checks = {}
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "down"

    try:
        cache.set("home_health_probe", "1", 5)
        checks["cache"] = "ok" if cache.get("home_health_probe") == "1" else "down"
    except Exception:
        checks["cache"] = "down"

    try:
        root = settings.MEDIA_ROOT if Path(settings.MEDIA_ROOT).exists() else settings.BASE_DIR
        usage = shutil.disk_usage(root)
        checks["disk_percent"] = round(usage.used * 100 / usage.total, 1)
        checks["disk_free_gb"] = round(usage.free / 1_000_000_000, 1)
    except Exception:
        checks["disk_percent"] = None
        checks["disk_free_gb"] = None

    return checks


# ---------------------------------------------------------------------------
# Protected file download
# ---------------------------------------------------------------------------
@login_required
def protected_download(request, label, pk):
    """Permission decision in Django; byte-shovelling in nginx."""
    try:
        model = apps.get_model(label)
    except (LookupError, ValueError):
        raise Http404
    if not issubclass(model, RestrictedFileModel):
        raise Http404
    obj = model._default_manager.filter(pk=pk).first()
    if obj is None or not obj.file:
        raise Http404
    if not obj.can_download(request.user):
        raise PermissionDenied
    audit.log("other", obj, actor=request.user, note="file downloaded")

    filename = obj.original_name or obj.file.name.rsplit("/", 1)[-1]
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    if settings.USE_X_ACCEL_REDIRECT:
        response = HttpResponse(content_type=ctype)
        response["X-Accel-Redirect"] = settings.PROTECTED_MEDIA_URL + quote(obj.file.name)
    else:
        response = FileResponse(obj.file.open("rb"), content_type=ctype)
    response["Content-Disposition"] = disposition
    response["Cache-Control"] = "private, no-store"
    return response


# ---------------------------------------------------------------------------
# Contact (public)
# ---------------------------------------------------------------------------
def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@require_POST
def contact_submit(request):
    form = ContactForm(request.POST)
    if not form.is_valid():
        first_errors = []
        for field, errors in form.errors.items():
            for err in errors:
                first_errors.append(str(err))
        return JsonResponse(
            {"ok": False,
             "error": first_errors[0] if first_errors else "Please check your input.",
             "errors": form.errors},
            status=400,
        )

    msg = form.save(commit=False)
    msg.ip_address = _client_ip(request)
    msg.user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:255]
    msg.save()

    try:
        from core.email_utils import send_templated_email
        from django.conf import settings as _s
        to_email = getattr(_s, "CONTACT_NOTIFY_EMAIL", None) or getattr(_s, "DEFAULT_FROM_EMAIL", None)
        if to_email:
            send_templated_email(
                subject=f"[Contact] {msg.subject or msg.name}",
                recipients=[to_email],
                template_name="emails/contact_notify.html",
                context={"message": msg},
                category="general",
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Contact notification email failed")

    return JsonResponse({
        "ok": True,
        "message": "Thanks — we've received your message and will get back to you shortly.",
    })