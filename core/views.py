import mimetypes
import shutil
from pathlib import Path
from urllib.parse import quote

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from notifications.models import Notification
from core.models import ContactMessage, ContactMessageActivity
from core.cache import get_or_set_context, safe_context, TTL_MEDIUM, TTL_SHORT
from . import audit
from .forms import ContactAssignmentForm, ContactForm, ContactNoteForm
from .restricted import RestrictedFileModel


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
import logging  # make sure this is at the top of the file

logger = logging.getLogger(__name__)


def landing(request):
    """Public landing page — real portal data, no view-level caching.

    Leadership is cached inside `public.services`. Everything else is
    fetched fresh on each request: the queries are cheap and, more
    importantly, a five-minute cache was hiding newly-published content.

    Scope note: anonymous visitors only see records whose `visibility` is
    PUBLIC. Signed-in users additionally see STAFF and own-department
    records (same as inside the portal).
    """
    from public import services as public_services
    from portal import services as portal_services

    is_authed = request.user.is_authenticated
    data_user = request.user if is_authed else None
    ctx = {"is_authed": is_authed}
    from staff.models import Department
    ctx["contact_departments"] = Department.objects.filter(
        is_active=True
    ).order_by("name")

    # ---- Governance (cached at the services layer) ----------------------
    try:
        ctx["leadership_more"] = public_services.featured_leadership(limit=6)
    except Exception:
        logger.exception("landing: leadership_more failed")
        ctx["leadership_more"] = []
    try:
        ctx["leadership_count"] = len(public_services.all_leadership())
    except Exception:
        ctx["leadership_count"] = 0

    # ---- Announcements ---------------------------------------------------
    try:
        ctx["announcements"] = list(
            portal_services.live_announcements(user=data_user)[:3]
        )
    except Exception:
        logger.exception("landing: announcements failed")
        ctx["announcements"] = []

    # ---- Events: try upcoming, then ongoing, then past ------------------
    events = []
    events_phase = None
    for phase in ("upcoming", "ongoing", "past"):
        try:
            events = list(portal_services.live_events(user=data_user, when=phase)[:3])
        except Exception:
            logger.exception("landing: events (%s) failed", phase)
            events = []
        if events:
            events_phase = phase
            break
    ctx["events"] = events
    ctx["events_phase"] = events_phase

    # ---- Gallery ---------------------------------------------------------
    try:
        albums = list(portal_services.featured_albums(limit=6, user=data_user))
        if not albums:
            albums = list(portal_services.live_albums(user=data_user)[:6])
        ctx["gallery_albums"] = albums
    except Exception:
        logger.exception("landing: gallery failed")
        ctx["gallery_albums"] = []

    # ---- Documents -------------------------------------------------------
    try:
        docs = list(portal_services.featured_public_documents(limit=3))
        if not docs:
            docs = list(portal_services.published_documents(user=data_user)[:3])
        ctx["documents"] = docs
    except Exception:
        logger.exception("landing: documents failed")
        ctx["documents"] = []

    # ---- Awards ----------------------------------------------------------
    try:
        awards = list(
            portal_services.live_awards(user=data_user, featured_only=True)[:2]
        )
        if not awards:
            awards = list(portal_services.live_awards(user=data_user)[:2])
        ctx["awards"] = awards
    except Exception:
        logger.exception("landing: awards failed")
        ctx["awards"] = []

    # ---- Quick links for signed-in users --------------------------------
    if is_authed:
        ctx["quick_links"] = _landing_quick_links(request.user)

    return render(request, "public/landing.html", ctx)


def leadership_page(request):
    """Public leadership page — full portrait grid grouped by section."""
    from public import services as public_services

    ctx = public_services.leadership_page_context()
    ctx["is_authed"] = request.user.is_authenticated
    return render(request, "public/leadership.html", ctx)


def _landing_quick_links(user):
    """Quick links shown on the public landing page for signed-in users.
    URL failures degrade to an empty card (never a 500)."""
    from django.urls import NoReverseMatch, reverse

    specs = [
        ("workflow",  "My requests",  "Raise, track, and approve",   "workflow:my_requests",         "indigo"),
        ("awaiting",  "Awaiting me",  "Requests needing your action", "workflow:awaiting_action",     "amber"),
        ("tickets",   "Helpdesk",     "Raise and track IT tickets",   "tickets:list",                 "green"),
        ("tasks",     "My tasks",     "Work assigned to you",         "tasks:assigned_to_me",         "blue"),
        ("attendance","Attendance",   "Check in and see your record", "attendance:home",              "teal"),
        ("messages",  "Messages",     "Inbox and threads",            "messaging:inbox",              "rose"),
    ]

    cards = []
    for key, label, hint, url_name, accent in specs:
        try:
            url = reverse(url_name)
        except NoReverseMatch:
            continue
        cards.append({
            "key": key, "label": label, "hint": hint,
            "url": url, "accent": accent,
        })
    return cards

def _module_cards(user):
    """Every card shown on the home dashboard. Each has a real URL where the
    module exists; the counter is best-effort and None when we can't compute it.
    """
    from django.utils import timezone as _tz
    today = _tz.localdate()
    cards = []

    # Phase 8 card audit: counters come from dashboards.counts, scoped to
    # what this user may see (the tickets card used to show every ticket in
    # the system, open or closed, to everyone; tasks, requests and assets
    # showed no number at all).
    from dashboards import counts as _counts

    def _n(fn):
        try:
            return fn()
        except Exception:
            return None

    # ---- Tickets ----
    cards.append(_make_card(
        user, key="tickets", label="Helpdesk tickets",
        hint="Report, track, resolve IT issues",
        url_name="tickets:list",
        counter=_n(lambda: _counts.my_visible_open_tickets(user).count()),
        count_label="open",
    ))

    # ---- Requests & approvals ----
    cards.append(_make_card(
        user, key="requests", label="Requests & approvals",
        hint="Leave, proposals, sign-off chains",
        url_name="workflow:my_requests",
        counter=_n(lambda: _counts.my_active_requests(user).count()), count_label="active",
    ))

    # ---- Tasks ----
    cards.append(_make_card(
        user, key="tasks", label="My tasks",
        hint="Assigned work and progress",
        url_name="tasks:assigned_to_me",
        counter=_n(lambda: _counts.my_open_tasks(user).count()), count_label="open",
    ))

    # ---- Assets ----
    cards.append(_make_card(
        user, key="assets", label="My assets",
        hint="Devices and equipment in your custody",
        url_name="assets:my_assets",
        counter=_n(lambda: _counts.my_assets(user).count()), count_label="in your custody",
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
    if key is None and user.has_perm("accounts.view_chairman_dashboard"):  # review L1
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


# ---------------------------------------------------------------------------
# Signed-in home (with real widgets)
# ---------------------------------------------------------------------------
@login_required
def home(request):
    """Signed-in landing page. Cached for 2 minutes."""
    user = request.user

    # --- Home context (cached per user) -----------------------------------
    def _build_home():
        ctx = {}

        ctx["unread_messages"] = _count_unread_messages(user)
        ctx["unread_notifications"] = Notification.objects.filter(
            recipient=user, is_read=False,
        ).count()

        ctx["show_staff_widget"] = user.has_perm("staff.view_staff")
        if ctx["show_staff_widget"]:
            ctx["staff_count"] = _staff_count_for(user)
            if user.is_superuser or user.has_perm("accounts.access_all_departments"):
                ctx["staff_label"] = "Staff in directory"
            else:
                ctx["staff_label"] = "Staff in your department"

        ctx["recent_threads"] = _recent_threads(user, limit=4)
        ctx["recent_notifications"] = list(
            Notification.objects
            .filter(recipient=user)
            .select_related("actor")
            .values(
                "pk", "title", "body", "level", "is_read",
                "created_at", "url", "actor__first_name", "actor__last_name",
            )[:5]
        )

        try:
            ctx["user_staff"] = user.staff_profile
        except Exception:
            ctx["user_staff"] = None

        ctx["can_import"] = user.has_perm("config.change_configentry")
        ctx["can_view_settings"] = user.has_perm("config.change_configentry")
        ctx["module_cards"] = _module_cards(user)
        ctx["dashboard"] = _dashboard_for(user)
        # Phase 7: the work board (tasks, confirmations, recent QA activity).
        from .workboard import build as build_workboard

        ctx["workboard"] = build_workboard(user)

        if user.is_superuser or user.has_perm("config.view_configentry"):
            ctx["system_health"] = _quick_health()

        return ctx

    ctx = safe_context(
        "home", ["ctx"], _build_home,
        ttl=TTL_MEDIUM, user=user, default={},
    )
    if not ctx:
        # Fallback — build fresh, without caching, if the cache path failed.
        ctx = _build_home()

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
    # Restricted-storage models: RestrictedFileModel subclasses, plus the
    # attachment models that opt in with PROTECTED_DOWNLOAD (review H1).
    if not (issubclass(model, RestrictedFileModel) or getattr(model, "PROTECTED_DOWNLOAD", False)):
        raise Http404
    # QA models are institution-scoped (Master Plan §4): their default manager
    # refuses unscoped reads, so fetch by pk through the explicit `unscoped`
    # manager and let `can_download()` make the row-level decision.
    manager = getattr(model, "unscoped", None) or model._default_manager
    obj = manager.filter(pk=pk).first()
    if obj is None or not obj.file:
        raise Http404
    if not obj.can_download(request.user):
        raise PermissionDenied
    return restricted_file_response(request, obj)


def restricted_file_response(request, obj):
    """The file of a restricted-storage row whose access the caller already
    checked (website download above and the mobile API's download endpoints)."""
    from . import file_preview

    filename = obj.original_name or obj.file.name.rsplit("/", 1)[-1]
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    # ?inline=1 → quick preview (PDF, images, text); same checks as a download.
    inline = file_preview.wants_inline(request, ctype)
    ctype = file_preview.text_charset(ctype)
    audit.log("other", obj, actor=request.user, note=file_preview.audit_note(inline))
    if settings.USE_X_ACCEL_REDIRECT:
        response = HttpResponse(content_type=ctype)
        response["X-Accel-Redirect"] = settings.PROTECTED_MEDIA_URL + quote(obj.file.name)
    else:
        response = FileResponse(obj.file.open("rb"), content_type=ctype)
    return file_preview.finish(response, filename, inline)


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
    # Review M18: each message sends an email, so cap it per client address
    # (5 per hour) to stop a script draining the daily email quota.
    from core import ratelimit
    limit, window = ratelimit.CONTACT_MESSAGES
    ip = ratelimit.client_ip(request)
    if ratelimit.is_limited("contact", ip, limit):
        return JsonResponse(
            {"ok": False,
             "error": "You have sent several messages recently. Please try again in an hour."},
            status=429,
        )

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

    ratelimit.hit("contact", ip, window)
    msg = form.save(commit=False)
    msg.ip_address = _client_ip(request)
    msg.user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:255]
    with transaction.atomic():
        msg.save()
        ContactMessageActivity.objects.create(
            contact_message=msg,
            action=ContactMessageActivity.ACTION_RECEIVED,
            details="Submitted through the public contact form.",
        )

    try:
        from core.email_utils import send_templated_email
        from django.conf import settings as _s
        from django.urls import reverse
        to_email = getattr(_s, "CONTACT_NOTIFY_EMAIL", None) or getattr(_s, "DEFAULT_FROM_EMAIL", None)
        if to_email:
            send_templated_email(
                subject=f"[Contact] {msg.subject or msg.name}",
                recipients=[to_email],
                template_name="emails/contact_notify.html",
                context={
                    "message": msg,
                    "inbox_url": request.build_absolute_uri(
                        reverse("core:contact_detail", kwargs={"pk": msg.pk})
                    ),
                },
                category="general",
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Contact notification email failed")

    return JsonResponse({
        "ok": True,
        "message": "Thanks — we've received your message and will get back to you shortly.",
    })


CONTACT_FILTERS = (
    ("all", "All messages"),
    ("new", "New / unread"),
    ("assigned_to_me", "Assigned to me"),
    ("handled", "Handled"),
    ("unassigned", "Unassigned"),
)


def _contact_messages_for(user):
    queryset = ContactMessage.objects.select_related(
        "handling_department", "assigned_to", "handled_by"
    )
    if user.is_superuser or user.has_perm("accounts.access_all_departments"):
        return queryset
    if user.department_id:
        return queryset.filter(
            Q(handling_department__isnull=True)
            | Q(handling_department_id=user.department_id)
        )
    return queryset.filter(handling_department__isnull=True)


def _contact_message_for_handler(request, pk):
    return get_object_or_404(_contact_messages_for(request.user), pk=pk)


@login_required
@permission_required("core.can_handle_contact_messages", raise_exception=True)
def contact_inbox(request):
    queryset = _contact_messages_for(request.user)
    query = (request.GET.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(email__icontains=query)
            | Q(subject__icontains=query)
            | Q(message__icontains=query)
        )

    status_filter = request.GET.get("status", "all")
    if status_filter == "new":
        queryset = queryset.filter(is_read=False)
    elif status_filter == "assigned_to_me":
        queryset = queryset.filter(assigned_to=request.user)
    elif status_filter == "handled":
        queryset = queryset.filter(is_handled=True)
    elif status_filter == "unassigned":
        queryset = queryset.filter(assigned_to__isnull=True, is_handled=False)
    elif status_filter != "all":
        status_filter = "all"

    page = Paginator(queryset.order_by("-created_at", "-pk"), 25).get_page(
        request.GET.get("page")
    )
    return render(request, "core/contact_inbox.html", {
        "page": page,
        "q": query,
        "status_filter": status_filter,
        "status_choices": CONTACT_FILTERS,
        "filters_active": bool(query or status_filter != "all"),
    })


@login_required
@permission_required("core.can_handle_contact_messages", raise_exception=True)
def contact_detail(request, pk):
    contact_message = _contact_message_for_handler(request, pk)
    return render(request, "core/contact_detail.html", {
        "contact_message": contact_message,
        "assignment_form": ContactAssignmentForm(
            contact_message=contact_message
        ),
        "note_form": ContactNoteForm(),
        "activities": contact_message.activities.select_related("actor"),
    })


@login_required
@permission_required("core.can_handle_contact_messages", raise_exception=True)
@require_POST
def contact_assign(request, pk):
    contact_message = _contact_message_for_handler(request, pk)
    form = ContactAssignmentForm(
        contact_message=contact_message,
        data=request.POST,
    )
    if not form.is_valid():
        messages.error(request, "Choose an authorized staff handler or leave unassigned.")
        return redirect("core:contact_detail", pk=contact_message.pk)

    handler = form.cleaned_data["assigned_to"]
    if contact_message.assigned_to_id != getattr(handler, "pk", None):
        old_handler = contact_message.assigned_to
        with transaction.atomic():
            contact_message.assigned_to = handler
            contact_message.save(update_fields=["assigned_to", "updated_at"])
            ContactMessageActivity.objects.create(
                contact_message=contact_message,
                actor=request.user,
                action=ContactMessageActivity.ACTION_ASSIGNED,
                details=(
                    f"Assigned to {handler.get_full_name() or handler.email}."
                    if handler else
                    f"Unassigned from {old_handler.get_full_name() or old_handler.email}."
                    if old_handler else
                    "Message left unassigned."
                ),
            )
    messages.success(request, "Contact message assignment updated.")
    return redirect("core:contact_detail", pk=contact_message.pk)


@login_required
@permission_required("core.can_handle_contact_messages", raise_exception=True)
@require_POST
def contact_mark_read(request, pk):
    contact_message = _contact_message_for_handler(request, pk)
    if not contact_message.is_read:
        with transaction.atomic():
            contact_message.is_read = True
            contact_message.save(update_fields=["is_read", "updated_at"])
            ContactMessageActivity.objects.create(
                contact_message=contact_message,
                actor=request.user,
                action=ContactMessageActivity.ACTION_READ,
            )
    messages.success(request, "Contact message marked as read.")
    return redirect("core:contact_detail", pk=contact_message.pk)


@login_required
@permission_required("core.can_handle_contact_messages", raise_exception=True)
@require_POST
def contact_update_status(request, pk):
    contact_message = _contact_message_for_handler(request, pk)
    action = request.POST.get("action")
    if action == "handled":
        contact_message.is_handled = True
        contact_message.handled_by = request.user
        contact_message.handled_at = timezone.now()
        activity_action = ContactMessageActivity.ACTION_HANDLED
        success_message = "Contact message marked as handled."
        activity_details = "Marked as handled."
    elif action == "reopen":
        contact_message.is_handled = False
        contact_message.handled_by = None
        contact_message.handled_at = None
        activity_action = ContactMessageActivity.ACTION_REOPENED
        success_message = "Contact message reopened."
        activity_details = "Reopened for follow-up."
    else:
        messages.error(request, "Choose a valid contact-message status.")
        return redirect("core:contact_detail", pk=contact_message.pk)

    with transaction.atomic():
        contact_message.save(update_fields=[
            "is_handled", "handled_by", "handled_at", "updated_at",
        ])
        ContactMessageActivity.objects.create(
            contact_message=contact_message,
            actor=request.user,
            action=activity_action,
            details=activity_details,
        )
    messages.success(request, success_message)
    return redirect("core:contact_detail", pk=contact_message.pk)


@login_required
@permission_required("core.can_handle_contact_messages", raise_exception=True)
@require_POST
def contact_add_note(request, pk):
    contact_message = _contact_message_for_handler(request, pk)
    form = ContactNoteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter an internal note of at least three characters.")
        return redirect("core:contact_detail", pk=contact_message.pk)

    ContactMessageActivity.objects.create(
        contact_message=contact_message,
        actor=request.user,
        action=ContactMessageActivity.ACTION_NOTE,
        details=form.cleaned_data["details"],
    )
    messages.success(request, "Internal note added.")
    return redirect("core:contact_detail", pk=contact_message.pk)


# ---------------------------------------------------------------------------
# Contact inbox — bulk actions (Phase 8)
# ---------------------------------------------------------------------------
@login_required
@permission_required("core.can_handle_contact_messages", raise_exception=True)
@require_POST
def contact_bulk(request):
    """Mark read / mark handled / delete several contact messages. Only rows
    in the handler's own scope are touched; delete needs
    ``core.delete_contactmessage`` (it removes the message for every handler)."""
    from django.urls import reverse

    from . import bulk as bulk_util

    action = request.POST.get("action", "")
    fallback = reverse("core:contact_inbox")
    if action not in ("mark_read", "mark_handled", "delete"):
        return bulk_util.bad_request(request, "Choose what to do with the selected messages.", fallback)
    if action == "delete" and not request.user.has_perm("core.delete_contactmessage"):
        return bulk_util.respond(request, action=action, done=[], status=403, fallback=fallback,
                                 message="Only administrators can delete contact messages.")
    ids = bulk_util.parse_ids(request)
    if not ids:
        return bulk_util.bad_request(request, "Select at least one message.", fallback)
    scoped = _contact_messages_for(request.user).filter(pk__in=ids)
    rows = {m.pk: m for m in scoped}
    done = [i for i in ids if i in rows]
    skipped = [{"id": i, "reason": "Some messages are outside your inbox and were left alone."}
               for i in ids if i not in rows]
    now = timezone.now()
    with transaction.atomic():
        for pk in done:
            m = rows[pk]
            if action == "mark_read" and not m.is_read:
                m.is_read = True
                m.save(update_fields=["is_read", "updated_at"])
                ContactMessageActivity.objects.create(contact_message=m, actor=request.user,
                                                      action=ContactMessageActivity.ACTION_READ)
            elif action == "mark_handled" and not m.is_handled:
                m.is_handled, m.handled_by, m.handled_at = True, request.user, now
                m.save(update_fields=["is_handled", "handled_by", "handled_at", "updated_at"])
                ContactMessageActivity.objects.create(contact_message=m, actor=request.user,
                                                      action=ContactMessageActivity.ACTION_HANDLED,
                                                      details="Marked as handled (bulk).")
            elif action == "delete":
                audit.log("delete", m, actor=request.user, note="contact message deleted (bulk)")
                m.delete()
    verb = {"mark_read": "marked as read", "mark_handled": "marked as handled", "delete": "deleted"}[action]
    return bulk_util.respond(request, action=action, done=done, skipped=skipped, fallback=fallback,
                             message=f"{bulk_util.plural(len(done), 'message')} {verb}.")
