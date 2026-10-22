"""announcements/ (staff door): shaping announcements.Announcement for JSON.

Every rule is the announcements app's own:
* who sees what       Announcement.objects.visible_to / unread_for / needing_ack_from
                      and announcements.services.access_for (audience, or may edit /
                      see the report) - the website's detail page rule
* read / ack / vote   announcements.services.mark_read / acknowledge / vote
* create / edit       announcements.forms.AnnouncementForm validation +
                      announcements.services.save_from_form / publish
* permissions         announcements.permissions.can_post / can_edit / can_view_report
* attachments         AnnouncementAttachment.can_download
"""
from django.db.models import Count, Exists, OuterRef, Prefetch, Q

LIST_FILTERS = ("all", "unread", "needs_ack", "drafts", "sent")
BODY_PREVIEW = 240


def with_related(qs):
    from announcements.models import PollOption

    return qs.select_related("created_by", "department", "poll").prefetch_related(
        "roles", "attachments",
        Prefetch("poll__options", queryset=PollOption.objects.annotate(n=Count("votes")).order_by("position", "id")),
    )


def list_queryset(user, filter_, q=""):
    """The website list's filters ("ack" is called needs_ack here). drafts and
    sent are for users who may post (others get "all")."""
    from announcements.models import Announcement
    from announcements.permissions import can_post

    poster = can_post(user)
    if filter_ not in LIST_FILTERS or (filter_ in ("drafts", "sent") and not poster):
        filter_ = "all"
    if filter_ == "drafts":
        qs = Announcement.objects.drafts()
        if not user.has_perm("announcements.change_announcement"):
            qs = qs.filter(created_by=user)
        qs = qs.with_user_state(user).order_by("-updated_at", "-pk")
    elif filter_ == "sent":
        qs = (Announcement.objects.published().filter(created_by=user)
              .with_user_state(user).order_by("-published_at", "-pk"))
    else:
        qs = Announcement.objects.visible_to(user).with_user_state(user)
        if filter_ == "unread":
            qs = qs.filter(is_read=False)
        elif filter_ == "needs_ack":
            qs = qs.filter(requires_ack=True, is_acked=False)
        qs = qs.order_by("-pinned", "-published_at", "-pk")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    return with_related(qs), filter_


def counts(user):
    from announcements.models import Announcement
    from announcements.permissions import can_post

    poster = can_post(user)
    return {
        "all": Announcement.objects.visible_to(user).count(),
        "unread": Announcement.objects.unread_for(user).count(),
        "needs_ack": Announcement.objects.needing_ack_from(user).count(),
        "drafts": Announcement.objects.drafts().filter(created_by=user).count() if poster else 0,
        "sent": Announcement.objects.published().filter(created_by=user).count() if poster else 0,
    }


def _poll(ann):
    from announcements.models import AnnouncementPoll

    try:
        return ann.poll
    except AnnouncementPoll.DoesNotExist:
        return None


def _person(user):
    if user is None:
        return None
    return {"id": user.pk, "name": user.get_full_name() or user.email}


def poll_dict(poll, user, *, in_audience=True):
    from announcements.models import PollVote

    if poll is None:
        return None
    mine = set(PollVote.objects.filter(poll=poll, user=user).values_list("option_id", flat=True))
    voters = PollVote.objects.filter(poll=poll).values("user_id").distinct().count()
    show = bool(mine) or not poll.is_open or not in_audience
    options = []
    for opt in poll.options.all():
        n = getattr(opt, "n", None)
        if n is None:
            n = opt.votes.count()
        options.append({"id": opt.pk, "text": opt.text, "chosen": opt.pk in mine,
                        "votes": n if show else None,
                        "percent": (round(100 * n / voters) if voters else 0) if show else None})
    return {"id": poll.pk, "question": poll.question, "allow_multiple": poll.allow_multiple,
            "closes_at": poll.closes_at, "is_open": poll.is_open, "has_voted": bool(mine),
            "can_vote": bool(in_audience and poll.is_open), "show_results": show,
            "voters": voters if show else None, "options": options}


def attachment_dict(att, ann, request):
    from django.urls import reverse

    try:
        size = att.file.size
    except Exception:
        size = None
    path = reverse("api:announcement_attachment", args=[ann.pk, att.pk])
    return {"id": att.pk, "name": att.original_name or att.file.name.rsplit("/", 1)[-1], "size": size,
            "download_url": request.build_absolute_uri(path)}


def summary_dict(ann, user, *, in_audience=True):
    from announcements.permissions import can_edit, can_view_report

    body = ann.body or ""
    return {
        "id": ann.pk,
        "title": ann.title,
        "body_preview": body[:BODY_PREVIEW] + ("…" if len(body) > BODY_PREVIEW else ""),
        "kind": ann.kind,
        "kind_label": ann.get_kind_display(),
        "requires_ack": ann.requires_ack,
        "pinned": ann.pinned,
        "status": "draft" if ann.is_draft else "published",
        "published_at": ann.published_at,
        "expires_at": ann.expires_at,
        "author": _person(ann.created_by),
        "audience_label": ann.audience_label(),
        "read": bool(getattr(ann, "is_read", False)),
        "acknowledged": bool(getattr(ann, "is_acked", False)),
        "has_poll": _poll(ann) is not None,
        "attachments_count": len(ann.attachments.all()),
        "in_audience": in_audience,
        "can_edit": can_edit(user, ann),
        "can_report": can_view_report(user, ann),
        "route": f"/announcements/{ann.pk}",
    }


def detail_dict(ann, user, request, *, in_audience=True):
    from announcements.models import Announcement, AnnouncementAck

    fresh = with_related(Announcement.objects.with_user_state(user)).get(pk=ann.pk)
    data = summary_dict(fresh, user, in_audience=in_audience)
    ack_at = None
    if fresh.is_acked:
        ack_at = (AnnouncementAck.objects.filter(announcement=fresh, user=user)
                  .values_list("acknowledged_at", flat=True).first())
    data.update({
        "body": fresh.body,
        "acknowledged_at": ack_at,
        "audience": fresh.audience,
        "department": ({"id": fresh.department_id, "name": fresh.department.name}
                       if fresh.department_id else None),
        "roles": [{"id": r.pk, "name": r.name} for r in fresh.roles.all()],
        "attachments": [attachment_dict(a, fresh, request) for a in fresh.attachments.all()],
        "poll": poll_dict(_poll(fresh), user, in_audience=in_audience),
        "can_acknowledge": bool(in_audience and fresh.requires_ack and not fresh.is_acked
                                and not fresh.is_draft and not fresh.is_expired),
    })
    return data


def report_dict(ann):
    from announcements import services

    data = services.report(ann)
    pending = [{"id": u.pk, "name": u.get_full_name() or u.email,
                "department": u.department.name if u.department_id else None,
                "role": u.role.name if u.role_id else None} for u in data["pending"]]
    results = None
    if data["poll_results"]:
        results = {"voters": data["poll_results"]["voters"], "total_votes": data["poll_results"]["total_votes"],
                   "options": [{"id": r["option"].pk, "text": r["option"].text, "votes": r["votes"],
                                "percent": r["percent"]} for r in data["poll_results"]["rows"]]}
    return {
        "id": ann.pk,
        "title": ann.title,
        "requires_ack": ann.requires_ack,
        "audience_size": data["audience_size"],
        "read": data["read"],
        "read_percent": data["read_percent"],
        "acknowledged": data["acknowledged"],
        "acknowledged_percent": data["acknowledged_percent"],
        "pending_count": len(pending),
        "pending": pending,
        "poll": (({"question": data["poll"].question, **results}) if results else None),
    }


def home_announcement(user):
    """The newest live action_needed / urgent announcement still waiting for
    the user (not acknowledged when acknowledgement is asked, else unread)."""
    from announcements.models import Announcement, AnnouncementAck, AnnouncementRead

    acked = Exists(AnnouncementAck.objects.filter(announcement=OuterRef("pk"), user=user))
    read = Exists(AnnouncementRead.objects.filter(announcement=OuterRef("pk"), user=user))
    ann = (Announcement.objects.visible_to(user)
           .filter(kind__in=[Announcement.ACTION_NEEDED, Announcement.URGENT])
           .annotate(_acked=acked, _read=read)
           .filter(Q(requires_ack=True, _acked=False) | Q(requires_ack=False, _read=False))
           .order_by("-published_at", "-pk").first())
    if ann is None:
        return None
    return {"id": ann.pk, "title": ann.title, "kind": ann.kind, "requires_ack": ann.requires_ack,
            "acknowledged": False}


def unread_count(user):
    from announcements.models import Announcement

    if getattr(user, "user_type", "staff") != "staff":
        return 0
    return Announcement.objects.unread_for(user).count()


# ---------------------------------------------------------------------------
# Create / edit through AnnouncementForm
# ---------------------------------------------------------------------------
FORM_FIELDS = ("title", "body", "kind", "requires_ack", "audience", "department", "roles", "pinned",
               "expires_at", "poll_question", "poll_allow_multiple", "poll_closes_at")


def form_initial(ann):
    """Current values (model defaults for a new one) - PATCH starts here."""
    poll = ann.poll_or_none() if ann.pk else None
    data = {
        "title": ann.title, "body": ann.body, "kind": ann.kind, "requires_ack": ann.requires_ack,
        "audience": ann.audience, "department": ann.department_id, "roles": list(ann.roles.values_list("pk", flat=True)) if ann.pk else [],
        "pinned": ann.pinned, "expires_at": ann.expires_at,
        "poll_question": poll.question if poll else "",
        "poll_allow_multiple": poll.allow_multiple if poll else False,
        "poll_closes_at": poll.closes_at if poll else None,
        "poll_options": [o.text for o in poll.options.all()] if poll else [],
    }
    return data


def form_data(values):
    """API values -> AnnouncementForm data (QueryDict-like dict)."""
    from django.utils.datastructures import MultiValueDict

    from announcements.forms import MAX_POLL_OPTIONS

    data = MultiValueDict()
    for key in FORM_FIELDS:
        value = values.get(key)
        if key == "roles":
            data.setlist("roles", [str(v) for v in (value or [])])
        elif isinstance(value, bool):
            if value:
                data[key] = "on"
        elif value is None:
            data[key] = ""
        elif hasattr(value, "strftime"):
            from django.utils import timezone

            data[key] = timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S")
        else:
            data[key] = str(value)
    options = list(values.get("poll_options") or [])
    for i in range(1, MAX_POLL_OPTIONS + 1):
        data[f"poll_option_{i}"] = options[i - 1] if i <= len(options) else ""
    return data


FORM_FIELD_NAMES = {f"poll_option_{i}": "poll_options" for i in range(1, 7)}


def form_errors(form):
    out = {}
    for name, errs in form.errors.items():
        key = "non_field_errors" if name == "__all__" else FORM_FIELD_NAMES.get(name, name)
        out.setdefault(key, []).extend(str(e) for e in errs)
    return out
