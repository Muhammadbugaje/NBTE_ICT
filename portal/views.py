"""Portal views — announcements, documents, awards, holidays, gallery."""
from __future__ import annotations

import json
import logging

from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .forms import (
    AnnouncementForm, AnnouncementLinkFormSet,
    EventForm,
    ReminderForm,
    DocumentForm, DocumentVersionForm,
    AwardForm,
    HolidayForm,
    GalleryAlbumForm,
    GalleryItemUploadForm,
)
from .models import (
    Announcement, AnnouncementCategory, AnnouncementLink,
    Award, AwardCategory,
    Document, DocumentCategory, DocumentVersion,
    Event, EventCategory, EventAttendee,
    GalleryAlbum, GalleryCategory, GalleryItem,
    Holiday,
    Reminder,
    Scope,
)

logger = logging.getLogger(__name__)


def _is_htmx(request):
    return request.headers.get("HX-Request", "").lower() == "true"


def _render(request, full_template, partial_template, ctx):
    if _is_htmx(request):
        return render(request, partial_template, ctx)
    return render(request, full_template, ctx)


# ============================================================================
# LANDING + HOW-TO
# ============================================================================
@login_required
def portal_home(request):
    return render(request, "portal/home.html", {
        "announcements": services.dashboard_announcements(request.user, limit=6),
        "events":        services.dashboard_events(request.user, limit=4),
        "reminders":     services.dashboard_reminders(request.user, limit=6),
    })


def how_to(request):
    return render(request, "portal/how_to.html", {})


# ============================================================================
# ANNOUNCEMENTS
# ============================================================================
@login_required
def announcement_list(request):
    """Announcement list with Live / Mine / Drafts tabs."""
    show = (request.GET.get("show") or "live").strip()
    if show not in ("live", "mine", "drafts"):
        show = "live"

    # ---- Choose base queryset ----
    if show == "mine":
        qs = services.announcements_by_author(request.user, request.user)
    elif show == "drafts":
        qs = services.drafts_visible_to(request.user)
    else:
        qs = services.live_announcements(user=request.user)

    # ---- Apply search / filters on top ----
    q    = (request.GET.get("q") or "").strip()
    cat  = (request.GET.get("category") or "").strip()
    prio = (request.GET.get("priority") or "").strip()

    if q:
        from django.db.models import Q
        qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(body__icontains=q))
    if cat:
        qs = qs.filter(category__slug=cat)
    if prio:
        qs = qs.filter(priority=prio)

    # ---- Tab counts ----
    counts = {
        "live":   services.live_announcements(user=request.user).count(),
        "mine":   services.announcements_by_author(request.user, request.user).count(),
        "drafts": services.drafts_visible_to(request.user).count(),
    }

    ctx = {
        "announcements": qs,
        "categories": AnnouncementCategory.objects.filter(is_active=True),
        "active_category": cat,
        "active_priority": prio,
        "search_q": q,
        "active_show": show,
        "tab_counts": counts,
        "filters_active": bool(q or cat or prio),
    }
    return _render(request,
                   "portal/announcements/list.html",
                   "portal/announcements/_list.html",
                   ctx)


@login_required
def announcement_detail(request, pk):
    ann = get_object_or_404(
        Announcement.objects.select_related("author", "department", "category")
                            .prefetch_related("links"),
        pk=pk,
    )
    if not services.is_announcement_visible_to(ann, request.user):
        raise Http404("Announcement not found")
    return render(request, "portal/announcements/detail.html", {"announcement": ann})


@login_required
@permission_required("portal.add_announcement", raise_exception=True)
def announcement_create(request):
    if request.method == "POST":
        form = AnnouncementForm(request.POST, request.FILES, user=request.user)
        formset = AnnouncementLinkFormSet(request.POST, instance=Announcement())
        if form.is_valid() and formset.is_valid():
            ann = form.save(commit=False)
            ann.author = request.user
            ann.save()
            form.save_m2m()
            formset.instance = ann
            formset.save()
            from django.utils import timezone as _tz
            if ann.is_draft:
                messages.success(request, "Announcement saved as draft.")
            elif ann.publish_at > _tz.now():
                messages.success(request, "Announcement scheduled.")
            else:
                messages.success(request, "Announcement published.")
            return redirect("portal:announcement_detail", pk=ann.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = AnnouncementForm(user=request.user)
        formset = AnnouncementLinkFormSet(instance=Announcement())
    return render(request, "portal/announcements/form.html", {
        "form": form, "formset": formset, "mode": "create", "announcement": None,
    })


@login_required
@permission_required("portal.change_announcement", raise_exception=True)
def announcement_update(request, pk):
    ann = get_object_or_404(Announcement, pk=pk)
    if not (ann.author_id == request.user.pk or request.user.is_superuser
            or request.user.has_perm("portal.change_announcement")):
        raise Http404

    if request.method == "POST":
        form = AnnouncementForm(request.POST, request.FILES, instance=ann, user=request.user)
        formset = AnnouncementLinkFormSet(request.POST, instance=ann)
        if form.is_valid() and formset.is_valid():
            form.save(); formset.save()
            messages.success(request, "Announcement updated.")
            return redirect("portal:announcement_detail", pk=ann.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = AnnouncementForm(instance=ann, user=request.user)
        formset = AnnouncementLinkFormSet(instance=ann)
    return render(request, "portal/announcements/form.html", {
        "form": form, "formset": formset, "mode": "update", "announcement": ann,
    })


@login_required
@permission_required("portal.delete_announcement", raise_exception=True)
def announcement_delete(request, pk):
    ann = get_object_or_404(Announcement, pk=pk)
    if request.method == "POST":
        ann.delete()
        messages.success(request, "Announcement deleted.")
        return redirect("portal:announcement_list")
    return render(request, "portal/announcements/confirm_delete.html", {"announcement": ann})


@login_required
@require_POST
@permission_required("portal.pin_announcement", raise_exception=True)
def announcement_toggle_pin(request, pk):
    ann = get_object_or_404(Announcement, pk=pk)
    ann.is_pinned = not ann.is_pinned
    ann.save(update_fields=["is_pinned"])

    if _is_htmx(request):
        response = render(request, "portal/announcements/_pin_button.html", {
            "announcement": ann,
        })
        response["HX-Trigger"] = json.dumps({
            "portalToast": {
                "type": "success",
                "title": "Pinned" if ann.is_pinned else "Unpinned",
                "message": ann.title,
            },
        })
        return response

    messages.success(request, "Pinned." if ann.is_pinned else "Unpinned.")
    return redirect("portal:announcement_detail", pk=ann.pk)


# ============================================================================
# EVENTS
# ============================================================================
@login_required
def event_list(request):
    when = (request.GET.get("when") or "upcoming").strip()
    if when not in ("upcoming", "ongoing", "past", "all"):
        when = "upcoming"
    show = (request.GET.get("show") or "live").strip()
    if show not in ("live", "mine", "drafts"):
        show = "live"

    # ---- Base queryset per tab ----
    if show == "mine":
        qs = services.events_by_organizer(request.user, request.user)
        when_applicable = False  # show all my events regardless of timing
    elif show == "drafts":
        qs = services.draft_events_visible_to(request.user)
        when_applicable = False
    else:
        qs = services.live_events(user=request.user,
                                  when=None if when == "all" else when)
        when_applicable = True

    # ---- Filters ----
    q    = (request.GET.get("q") or "").strip()
    cat  = (request.GET.get("category") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(description__icontains=q))
    if cat:
        qs = qs.filter(category__slug=cat)

    counts = {
        "live":   services.live_events(user=request.user, when=None).count(),
        "mine":   services.events_by_organizer(request.user, request.user).count(),
        "drafts": services.draft_events_visible_to(request.user).count(),
    }

    ctx = {
        "events": qs,
        "categories": EventCategory.objects.filter(is_active=True),
        "active_when": when,
        "active_category": cat,
        "active_show": show,
        "when_applicable": when_applicable,
        "tab_counts": counts,
        "search_q": q,
        "filters_active": bool(q or cat),
        "when_choices": [
            ("upcoming", "Upcoming"), ("ongoing", "Ongoing"),
            ("past", "Past"), ("all", "All"),
        ],
    }
    return _render(request,
                   "portal/events/list.html",
                   "portal/events/_list.html",
                   ctx)


@login_required
def event_detail(request, pk):
    ev = get_object_or_404(
        Event.objects.select_related("organizer", "department", "category"),
        pk=pk,
    )
    if not services.is_event_visible_to(ev, request.user):
        raise Http404("Event not found")
    att = ev.attendees.filter(user=request.user).first()
    my_rsvp = att.rsvp if att else None
    return render(request, "portal/events/detail.html", {
        "event": ev,
        "my_rsvp": my_rsvp,
        "rsvp_summary": ev.rsvp_summary(),
    })


@login_required
@permission_required("portal.add_event", raise_exception=True)
def event_create(request):
    if request.method == "POST":
        form = EventForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            ev = form.save(commit=False)
            ev.organizer = request.user
            ev.save()
            form.save_m2m()
            messages.success(request, "Event saved as draft." if ev.is_draft else "Event created.")
            return redirect("portal:event_detail", pk=ev.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = EventForm(user=request.user)
    return render(request, "portal/events/form.html", {
        "form": form, "mode": "create", "event": None,
    })


@login_required
@permission_required("portal.change_event", raise_exception=True)
def event_update(request, pk):
    ev = get_object_or_404(Event, pk=pk)
    if request.method == "POST":
        form = EventForm(request.POST, request.FILES, instance=ev, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Event updated.")
            return redirect("portal:event_detail", pk=ev.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = EventForm(instance=ev, user=request.user)
    return render(request, "portal/events/form.html", {
        "form": form, "mode": "update", "event": ev,
    })


@login_required
@permission_required("portal.delete_event", raise_exception=True)
def event_delete(request, pk):
    ev = get_object_or_404(Event, pk=pk)
    if request.method == "POST":
        ev.delete()
        messages.success(request, "Event deleted.")
        return redirect("portal:event_list")
    return render(request, "portal/events/confirm_delete.html", {"event": ev})


@login_required
@require_POST
def event_rsvp(request, pk):
    ev = get_object_or_404(Event, pk=pk)
    if not services.is_event_visible_to(ev, request.user):
        raise Http404
    rsvp = (request.POST.get("rsvp") or "").strip()
    if rsvp not in ("yes", "no", "maybe"):
        return HttpResponse("Invalid RSVP", status=400)

    att, _ = EventAttendee.objects.get_or_create(event=ev, user=request.user)
    att.rsvp = rsvp
    from django.utils import timezone as _tz
    att.responded_at = _tz.now()
    att.save()

    if _is_htmx(request):
        return render(request, "portal/events/_rsvp_widget.html", {
            "event": ev, "my_rsvp": rsvp, "rsvp_summary": ev.rsvp_summary(),
        })
    messages.success(request, "RSVP saved.")
    return redirect("portal:event_detail", pk=ev.pk)


@login_required
def event_ics(request, pk):
    ev = get_object_or_404(Event, pk=pk)
    if not services.is_event_visible_to(ev, request.user):
        raise Http404
    ics = _build_ics(ev, request)
    resp = HttpResponse(ics, content_type="text/calendar; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{ev.pk}.ics"'
    return resp


def _build_ics(ev, request):
    from django.utils import timezone as _tz
    def fmt(dt):
        return _tz.localtime(dt).strftime("%Y%m%dT%H%M%S") if dt else ""
    description = (ev.description or "").replace("\n", "\\n")
    location = ev.location_name or ""
    if ev.virtual_url:
        location = (location + " / " + ev.virtual_url).strip(" /")
    url = request.build_absolute_uri(f"/portal/events/{ev.pk}/")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//NBTE//Portal//EN",
        "BEGIN:VEVENT",
        f"UID:nbte-event-{ev.pk}@nbte.local",
        f"SUMMARY:{ev.title}",
        f"DTSTART:{fmt(ev.starts_at)}",
    ]
    if ev.ends_at:
        lines.append(f"DTEND:{fmt(ev.ends_at)}")
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    lines += [f"URL:{url}", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines)


# ============================================================================
# REMINDERS
# ============================================================================
@login_required
def reminder_list(request):
    qs = services.visible_reminders(user=request.user)
    q = (request.GET.get("q") or "").strip()
    kind = (request.GET.get("kind") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    if kind:
        qs = qs.filter(kind=kind)

    ctx = {
        "reminders": qs,
        "active_kind": kind,
        "search_q": q,
        "filters_active": bool(q or kind),
        "kind_choices": Reminder.KIND_CHOICES,
    }
    return _render(request,
                   "portal/reminders/list.html",
                   "portal/reminders/_list.html",
                   ctx)


@login_required
def reminder_detail(request, pk):
    rem = get_object_or_404(Reminder, pk=pk)
    # The notification links here, so everyone it was delivered to (department,
    # organisation or role reminders included) must be able to open it.
    if not (rem.creator_id == request.user.pk or request.user.is_superuser
            or rem.recipients.filter(pk=request.user.pk).exists()
            or rem.fanned.filter(user=request.user).exists()):
        raise Http404
    return render(request, "portal/reminders/detail.html", {"reminder": rem})


@login_required
def reminder_create(request):
    if request.method == "POST":
        form = ReminderForm(request.POST, user=request.user)
        if form.is_valid():
            rem = form.save(commit=False)
            rem.creator = request.user
            rem.save()
            form.save_m2m()
            messages.success(request, "Reminder scheduled.")
            return redirect("portal:reminder_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = ReminderForm(user=request.user)
    return render(request, "portal/reminders/form.html", {
        "form": form, "mode": "create", "reminder": None,
    })


@login_required
def reminder_update(request, pk):
    rem = get_object_or_404(Reminder, pk=pk)
    if not (rem.creator_id == request.user.pk or request.user.is_superuser):
        raise Http404
    if request.method == "POST":
        form = ReminderForm(request.POST, instance=rem, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Reminder updated.")
            return redirect("portal:reminder_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = ReminderForm(instance=rem, user=request.user)
    return render(request, "portal/reminders/form.html", {
        "form": form, "mode": "update", "reminder": rem,
    })


@login_required
@require_POST
def reminder_dismiss(request, pk):
    rem = get_object_or_404(Reminder, pk=pk)
    if rem.creator_id == request.user.pk:
        rem.is_dismissed = True
        rem.save(update_fields=["is_dismissed"])
    from .models import ReminderRecipient
    ReminderRecipient.objects.filter(reminder=rem, user=request.user).update(is_read=True)
    return HttpResponse(status=204)


import mimetypes
from urllib.parse import quote

from django.conf import settings
from django.db.models import F
from django.http import FileResponse, Http404
from django.utils import timezone
from django.urls import reverse

from .forms import DocumentForm, DocumentVersionForm
from .models import Document, DocumentCategory, DocumentDownloadLog, DocumentVersion


# ============================================================================
# DOCUMENTS
# ============================================================================
@login_required
def document_list(request):
    show = (request.GET.get("show") or "live").strip()
    if show not in ("live", "mine", "drafts", "archived"):
        show = "live"

    if show == "mine":
        qs = services.documents_owned_by(request.user, request.user)
    elif show == "drafts":
        qs = services.drafts_visible_to_documents(request.user)
    elif show == "archived":
        qs = services.archived_documents(request.user)
    else:
        qs = services.published_documents(user=request.user)

    q   = (request.GET.get("q") or "").strip()
    cat = (request.GET.get("category") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(description__icontains=q))
    if cat:
        qs = qs.filter(category__slug=cat)

    counts = {
        "live":     services.published_documents(user=request.user).count(),
        "mine":     services.documents_owned_by(request.user, request.user).count(),
        "drafts":   services.drafts_visible_to_documents(request.user).count(),
        "archived": services.archived_documents(request.user).count(),
    }

    ctx = {
        "documents": qs,
        "categories": DocumentCategory.objects.filter(is_active=True),
        "active_category": cat,
        "active_show": show,
        "search_q": q,
        "tab_counts": counts,
        "filters_active": bool(q or cat),
    }
    return _render(request,
                   "portal/documents/list.html",
                   "portal/documents/_list.html",
                   ctx)


@login_required
def document_detail(request, pk):
    doc = get_object_or_404(
        Document.objects
        .select_related("category", "owner", "department", "current_version")
        .prefetch_related("versions__uploaded_by", "roles", "recipients"),
        pk=pk,
    )
    if not services.is_document_visible_to(doc, request.user):
        raise Http404("Document not found")
    return render(request, "portal/documents/detail.html", {"document": doc})


@login_required
@permission_required("portal.add_document", raise_exception=True)
def document_create(request):
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.owner = request.user
            doc.save()
            form.save_m2m()

            # Upload the first version for file-sourced docs.
            if doc.source_type == Document.SOURCE_FILE:
                uploaded = form.cleaned_data.get("initial_file")
                if uploaded:
                    version = DocumentVersion(
                        document=doc,
                        version_number=1,
                        is_current=True,
                        uploaded_by=request.user,
                        change_note="Initial upload",
                    )
                    version.file.save(uploaded.name, uploaded, save=False)
                    version.save()
                    doc.current_version = version
                    doc.save(update_fields=["current_version"])

            if doc.status == Document.STATUS_PUBLISHED:
                _notify_document_published(doc, request.user)
                doc.notified_at = timezone.now()
                doc.save(update_fields=["notified_at"])
                messages.success(request, "Document published.")
            else:
                messages.success(request, "Document saved as draft.")
            return redirect("portal:document_detail", pk=doc.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = DocumentForm(user=request.user)
    return render(request, "portal/documents/form.html", {
        "form": form, "mode": "create", "document": None,
    })


@login_required
@permission_required("portal.change_document", raise_exception=True)
def document_update(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    if not (doc.owner_id == request.user.pk or request.user.is_superuser
            or request.user.has_perm("portal.change_document")):
        raise Http404

    was_published = doc.status == Document.STATUS_PUBLISHED

    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES, instance=doc, user=request.user)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.save()
            form.save_m2m()

            # Fires the notification on transition draft → published
            if (doc.status == Document.STATUS_PUBLISHED
                    and not was_published and not doc.notified_at):
                _notify_document_published(doc, request.user)
                doc.notified_at = timezone.now()
                doc.save(update_fields=["notified_at"])

            messages.success(request, "Document updated.")
            return redirect("portal:document_detail", pk=doc.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = DocumentForm(instance=doc, user=request.user)
    return render(request, "portal/documents/form.html", {
        "form": form, "mode": "update", "document": doc,
    })


@login_required
@permission_required("portal.change_document", raise_exception=True)
def document_upload_version(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    if doc.source_type != Document.SOURCE_FILE:
        messages.error(request, "External documents don't have versions. Update the URL instead.")
        return redirect("portal:document_detail", pk=doc.pk)

    if request.method == "POST":
        form = DocumentVersionForm(request.POST, request.FILES)
        if form.is_valid():
            DocumentVersion.objects.filter(document=doc, is_current=True).update(is_current=False)
            last = doc.versions.order_by("-version_number").first()
            next_num = (last.version_number + 1) if last else 1

            version = form.save(commit=False)
            version.document = doc
            version.version_number = next_num
            version.is_current = True
            version.uploaded_by = request.user
            version.file.save(form.cleaned_data["file"].name, form.cleaned_data["file"], save=False)
            version.save()

            doc.current_version = version
            doc.save(update_fields=["current_version"])

            messages.success(request, f"Version {next_num} uploaded.")
            return redirect("portal:document_detail", pk=doc.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = DocumentVersionForm()
    return render(request, "portal/documents/version_upload.html", {
        "document": doc, "form": form,
    })


@login_required
@permission_required("portal.delete_document", raise_exception=True)
def document_delete(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    if request.method == "POST":
        doc.delete()
        messages.success(request, "Document deleted.")
        return redirect("portal:document_list")
    return render(request, "portal/documents/confirm_delete.html", {"document": doc})


@login_required
def document_download(request, pk, version_pk=None):
    """Stream a document (or a specific version) — permission-checked.

    Routes:
      /portal/documents/<pk>/download/
        → serves the current version (or latest if none marked current)
      /portal/documents/<pk>/versions/<version_pk>/download/
        → serves that specific version

    - 403 if the user can't download the document.
    - Downloads are logged when `download_tracking_enabled` is on (NR-07).
    - If `USE_X_ACCEL_REDIRECT` is on, nginx serves the bytes; otherwise
      FileResponse streams it via Django.
    """
    import mimetypes
    from urllib.parse import quote

    from django.conf import settings
    from django.core.exceptions import PermissionDenied
    from django.http import FileResponse, Http404, HttpResponse
    from django.shortcuts import get_object_or_404, redirect

    doc = get_object_or_404(Document, pk=pk)

    # ---- Permission ----------------------------------------------------
    if not doc.can_be_downloaded_by(request.user):
        raise PermissionDenied("You don't have access to this document.")

    # ---- Resolve the version to serve ---------------------------------
    version = None
    if version_pk is not None:
        # Explicit version requested — it must belong to this document.
        version = get_object_or_404(
            DocumentVersion, pk=version_pk, document=doc,
        )
        file_field = getattr(version, "file", None)
        if not file_field:
            raise Http404("That version has no file.")
    else:
        # Default: current_version, falling back to latest.
        if doc.is_external and doc.external_url:
            return redirect(doc.external_url)
        file_field, version = doc.get_download_file()
        if file_field is None:
            raise Http404("This document has no downloadable file yet.")

    # ---- Log the download before streaming ----------------------------
    try:
        doc.record_download(request.user, request=request)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "record_download failed for document #%s", doc.pk,
        )

    # ---- Build the response -------------------------------------------
    original_name = (
        getattr(version, "original_name", "")
        or getattr(doc, "original_name", "")
        or file_field.name.rsplit("/", 1)[-1]
    )
    from core import file_preview

    ctype = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    inline = file_preview.wants_inline(request, ctype)
    ctype = file_preview.text_charset(ctype)

    if getattr(settings, "USE_X_ACCEL_REDIRECT", False):
        resp = HttpResponse(content_type=ctype)
        prefix = getattr(settings, "PROTECTED_MEDIA_URL", "/protected/")
        resp["X-Accel-Redirect"] = prefix + quote(file_field.name)
    else:
        resp = FileResponse(file_field.open("rb"), content_type=ctype)

    return file_preview.finish(resp, original_name, inline)


def _log_download(doc, version, request, is_external):
    if not doc.download_tracking_enabled:
        return
    Document.objects.filter(pk=doc.pk).update(download_count=F("download_count") + 1)
    ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
          or request.META.get("REMOTE_ADDR"))
    DocumentDownloadLog.objects.create(
        document=doc,
        version=version,
        user=request.user if request.user.is_authenticated else None,
        ip_address=ip,
        user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:255],
        is_external=is_external,
    )


def _notify_document_published(doc, actor):
    """Fan out a notification to everyone in scope."""
    from notifications.models import Notification
    from notifications.services import notify
    from django.contrib.auth import get_user_model
    User = get_user_model()

    if doc.visibility in (Scope.PUBLIC, Scope.STAFF):
        users = User.objects.filter(is_active=True)
    elif doc.visibility == Scope.DEPARTMENT and doc.department_id:
        users = User.objects.filter(is_active=True, department_id=doc.department_id)
    elif doc.visibility == Scope.ROLE:
        users = User.objects.filter(
            is_active=True, role__in=doc.roles.all()  # review M6
        ).distinct()
    elif doc.visibility == Scope.INDIVIDUAL:
        users = doc.recipients.all()
    else:
        users = User.objects.none()

    url = reverse("portal:document_detail", kwargs={"pk": doc.pk})
    title = f"New document: {doc.title}"
    body = doc.card_summary[:200]

    for u in users:
        try:
            notify(
                recipient=u, title=title, body=body, url=url,
                category=Notification.SYSTEM,
                level=Notification.INFO,
                actor=actor,
            )
        except Exception:
            pass


# ============================================================================
# PUBLIC DOCUMENTS (intentionally no login required)
# ============================================================================
def public_document_list(request):
    qs = services.published_documents(user=None)
    q   = (request.GET.get("q") or "").strip()
    cat = (request.GET.get("category") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q))
    if cat:
        qs = qs.filter(category__slug=cat)
    return render(request, "portal/public/documents.html", {
        "documents": qs,
        "categories": DocumentCategory.objects.filter(is_active=True),
        "active_category": cat,
        "search_q": q,
    })


# ============================================================================
# AWARDS
# ============================================================================
@login_required
def awards_list(request):
    """Awards timeline — visible to the current viewer."""
    qs = services.live_awards(user=request.user)

    # HTMX filter support
    kind = (request.GET.get("kind") or "").strip()
    if kind:
        qs = qs.filter(kind=kind)

    cat = (request.GET.get("category") or "").strip()
    if cat:
        qs = qs.filter(category__slug=cat)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            models.Q(title__icontains=q)
            | models.Q(description__icontains=q)
            | models.Q(recipient__first_name__icontains=q)
            | models.Q(recipient__last_name__icontains=q)
        )

    ctx = {
        "awards": qs,
        "categories": AwardCategory.objects.filter(is_active=True),
        "active_kind": kind,
        "active_category": cat,
        "q": q,
    }

    # HTMX request → return only the results fragment.
    if request.headers.get("HX-Request") == "true":
        return render(request, "portal/awards/_results.html", ctx)

    return render(request, "portal/awards/list.html", ctx)


@login_required
def award_detail(request, pk):
    award = get_object_or_404(Award, pk=pk)
    if not award.can_be_viewed_by(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return render(request, "portal/awards/detail.html", {"award": award})


@login_required
@permission_required("portal.manage_award", raise_exception=True)
def award_create(request):
    if request.method == "POST":
        form = AwardForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            award = form.save(commit=False)
            if not award.awarded_by_id:
                award.awarded_by = request.user
            award.save()
            form.save_m2m()
            messages.success(request, "Award recorded.")
            return redirect("portal:award_detail", pk=award.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = AwardForm(user=request.user)
    return render(request, "portal/awards/form.html",
                  {"form": form, "mode": "create", "award": None})


@login_required
@permission_required("portal.manage_award", raise_exception=True)
def award_update(request, pk):
    award = get_object_or_404(Award, pk=pk)
    if request.method == "POST":
        form = AwardForm(request.POST, request.FILES, instance=award,
                         user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Award updated.")
            return redirect("portal:award_detail", pk=award.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = AwardForm(instance=award, user=request.user)
    return render(request, "portal/awards/form.html",
                  {"form": form, "mode": "update", "award": award})


@login_required
@permission_required("portal.manage_award", raise_exception=True)
def award_delete(request, pk):
    award = get_object_or_404(Award, pk=pk)
    if request.method == "POST":
        award.delete()
        messages.success(request, "Award deleted.")
        return redirect("portal:awards_list")
    return render(request, "portal/awards/confirm_delete.html",
                  {"award": award})


# ============================================================================
# HOLIDAYS
# ============================================================================
@login_required
def holidays_list(request):
    """Holiday calendar."""
    year_param = request.GET.get("year")
    try:
        year = int(year_param) if year_param else timezone.localdate().year
    except (TypeError, ValueError):
        year = timezone.localdate().year

    from datetime import date
    from_date = date(year, 1, 1)
    to_date   = date(year, 12, 31)

    qs = services.live_holidays(user=request.user,
                                from_date=from_date, to_date=to_date)

    # Range for the year picker
    years = sorted({h.date_start.year for h in
                    Holiday.objects.all().only("date_start")}, reverse=True)
    today_year = timezone.localdate().year
    if today_year not in years:
        years.insert(0, today_year)

    ctx = {
        "holidays": qs,
        "year": year,
        "years": years,
        "today": timezone.localdate(),
    }

    if request.headers.get("HX-Request") == "true":
        return render(request, "portal/holidays/_results.html", ctx)
    return render(request, "portal/holidays/list.html", ctx)


@login_required
@permission_required("portal.manage_holiday", raise_exception=True)
def holiday_create(request):
    if request.method == "POST":
        form = HolidayForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Holiday added.")
            return redirect("portal:holidays_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = HolidayForm(user=request.user,
                           initial={"date_start": timezone.localdate(),
                                    "date_end":   timezone.localdate()})
    return render(request, "portal/holidays/form.html",
                  {"form": form, "mode": "create", "holiday": None})


@login_required
@permission_required("portal.manage_holiday", raise_exception=True)
def holiday_update(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    if request.method == "POST":
        form = HolidayForm(request.POST, instance=holiday, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Holiday updated.")
            return redirect("portal:holidays_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = HolidayForm(instance=holiday, user=request.user)
    return render(request, "portal/holidays/form.html",
                  {"form": form, "mode": "update", "holiday": holiday})


@login_required
@permission_required("portal.manage_holiday", raise_exception=True)  # review L1
def holiday_delete(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    if request.method == "POST":
        holiday.delete()
        messages.success(request, "Holiday removed.")
        return redirect("portal:holidays_list")
    return render(request, "portal/holidays/confirm_delete.html",
                  {"holiday": holiday})


# ============================================================================
# ABSENCE INDICATOR (live-derived; no model)
# ============================================================================
@login_required
def absence_today(request):
    """Who's currently absent today — visible in the viewer's scope."""
    staff = services.absent_staff_today(user=request.user)
    return render(request, "portal/holidays/_absence.html", {
        "absent_staff": staff,
        "today": timezone.localdate(),
    })


# ============================================================================
# GALLERY
# ============================================================================
@login_required
def gallery_list(request):
    qs = services.live_albums(user=request.user)

    cat = (request.GET.get("category") or "").strip()
    if cat:
        qs = qs.filter(category__slug=cat)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            models.Q(title__icontains=q) | models.Q(description__icontains=q)
        )

    ctx = {
        "albums": qs,
        "categories": GalleryCategory.objects.filter(is_active=True),
        "active_category": cat,
        "q": q,
    }
    if request.headers.get("HX-Request") == "true":
        return render(request, "portal/gallery/_results.html", ctx)
    return render(request, "portal/gallery/list.html", ctx)


@login_required
def gallery_detail(request, pk):
    album = get_object_or_404(GalleryAlbum, pk=pk)
    if not album.can_be_viewed_by(request.user):
        raise PermissionDenied
    items = album.items.select_related("uploaded_by").order_by("sort_order", "id")
    return render(request, "portal/gallery/detail.html", {
        "album": album,
        "items": items,
        "can_manage": _can_manage_gallery(request.user, album),
    })


@login_required
@permission_required("portal.manage_gallery", raise_exception=True)
def gallery_album_create(request):
    if request.method == "POST":
        form = GalleryAlbumForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            album = form.save(commit=False)
            album.owner = request.user
            album.save()
            form.save_m2m()
            messages.success(request, "Album created. Add photos next.")
            return redirect("portal:gallery_album_upload", pk=album.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = GalleryAlbumForm(user=request.user)
    return render(request, "portal/gallery/album_form.html",
                  {"form": form, "mode": "create", "album": None})


@login_required
@permission_required("portal.manage_gallery", raise_exception=True)
def gallery_album_update(request, pk):
    album = get_object_or_404(GalleryAlbum, pk=pk)
    if request.method == "POST":
        form = GalleryAlbumForm(request.POST, request.FILES,
                                instance=album, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Album updated.")
            return redirect("portal:gallery_detail", pk=album.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = GalleryAlbumForm(instance=album, user=request.user)
    return render(request, "portal/gallery/album_form.html",
                  {"form": form, "mode": "update", "album": album})


@login_required
@permission_required("portal.manage_gallery", raise_exception=True)
def gallery_album_delete(request, pk):
    album = get_object_or_404(GalleryAlbum, pk=pk)
    if request.method == "POST":
        album.delete()
        messages.success(request, "Album deleted.")
        return redirect("portal:gallery_list")
    return render(request, "portal/gallery/confirm_delete.html",
                  {"album": album, "kind": "album"})


@login_required
@permission_required("portal.manage_gallery", raise_exception=True)
def gallery_album_upload(request, pk):
    album = get_object_or_404(GalleryAlbum, pk=pk)

    if request.method == "POST":
        form = GalleryItemUploadForm(request.POST, request.FILES)
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "Please choose at least one file.")
            return redirect("portal:gallery_album_upload", pk=album.pk)

        # Sort order starts after the current max.
        max_order = album.items.order_by("-sort_order").values_list(
            "sort_order", flat=True,
        ).first() or 0

        created = 0
        for f in files:
            try:
                item = GalleryItem(
                    album=album,
                    file=f,
                    caption=form.data.get("caption", "")[:300],
                    credit=form.data.get("credit", "")[:200],
                    uploaded_by=request.user,
                    sort_order=max_order + created + 1,
                )
                item.save()
                created += 1
            except Exception:
                logger.exception("Failed to upload file %s", f.name)

        if created:
            messages.success(request, f"{created} item(s) uploaded.")
        return redirect("portal:gallery_detail", pk=album.pk)

    return render(request, "portal/gallery/item_upload.html", {"album": album})


@login_required
@permission_required("portal.manage_gallery", raise_exception=True)
def gallery_item_delete(request, pk):
    item = get_object_or_404(GalleryItem, pk=pk)
    album_pk = item.album_id
    if request.method == "POST":
        item.delete()
        messages.success(request, "Item removed.")
        return redirect("portal:gallery_detail", pk=album_pk)
    return render(request, "portal/gallery/confirm_delete.html",
                  {"item": item, "kind": "item"})


@login_required
@permission_required("portal.manage_gallery", raise_exception=True)
@require_POST
def gallery_item_reorder(request, pk):
    """AJAX reorder — accepts a JSON list of item ids in order."""
    import json
    album = get_object_or_404(GalleryAlbum, pk=pk)
    try:
        order = json.loads(request.body.decode("utf-8")).get("order", [])
    except Exception:
        return JsonResponse({"ok": False, "error": "bad payload"}, status=400)
    for idx, item_id in enumerate(order, start=1):
        GalleryItem.objects.filter(pk=item_id, album=album).update(sort_order=idx)
    return JsonResponse({"ok": True})


def _can_manage_gallery(user, album=None):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.has_perm("portal.manage_gallery"):
        return True
    if album and album.owner_id == user.pk:
        return True
    return False


# ============================================================================
# CATEGORY QUICK-CREATE (modal endpoint)
# ============================================================================
CATEGORY_KINDS = {
    "award": {
        "model": AwardCategory,
        "perm":  "portal.manage_award",
    },
    "gallery": {
        "model": GalleryCategory,
        "perm":  "portal.manage_gallery",
    },
    "announcement": {
        "model": AnnouncementCategory,
        "perm":  "portal.add_announcement",
    },
    "document": {
        "model": DocumentCategory,
        "perm":  "portal.publish_document",
    },
}


@login_required
@require_POST
def category_quick_create(request, kind):
    cfg = CATEGORY_KINDS.get(kind)
    if not cfg:
        return JsonResponse({"ok": False, "error": "Unknown category kind."}, status=400)

    if not (request.user.is_superuser or request.user.has_perm(cfg["perm"])):
        return JsonResponse(
            {"ok": False, "error": "You don't have permission to create categories here."},
            status=403,
        )

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    if len(name) > 80:
        return JsonResponse({"ok": False, "error": "Name must be 80 characters or fewer."}, status=400)

    Model = cfg["model"]
    existing = Model.objects.filter(name__iexact=name).first()
    if existing:
        return JsonResponse({
            "ok": True,
            "pk": existing.pk,
            "name": existing.name,
            "created": False,
        })

    obj = Model.objects.create(name=name)
    return JsonResponse({
        "ok": True,
        "pk": obj.pk,
        "name": obj.name,
        "created": True,
    })


@login_required
@permission_required("portal.manage_gallery", raise_exception=True)
@require_POST
def gallery_items_bulk_delete(request, pk):
    """Delete multiple items from an album in one POST."""
    album = get_object_or_404(GalleryAlbum, pk=pk)
    item_ids = request.POST.getlist("item_ids")
    if not item_ids:
        messages.error(request, "No items selected.")
        return redirect("portal:gallery_detail", pk=album.pk)

    qs = GalleryItem.objects.filter(pk__in=item_ids, album=album)
    count = qs.count()
    qs.delete()
    messages.success(request, f"{count} item{'s' if count != 1 else ''} removed.")
    return redirect("portal:gallery_detail", pk=album.pk)