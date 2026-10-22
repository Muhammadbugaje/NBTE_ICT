"""GET me/ - the signed-in user, capabilities and unread counts.

Sources (same as the website):
* qa_workspace      qa_core.workspace.can_use_qa (the sidebar's qa_nav["show"])
* approve_leave     user.has_perm("accounts.approve_requests")
* post_announcements announcements.permissions.can_post
* finance_queue     qa_core.permissions.can_view_finance
* insights          superuser or accounts.view_analytics_dashboard (User.is_power_user)
* unread.alerts     dashboards.counts.unread_notifications
* unread.chats      the inbox's threads (messaging.views._my_threads) with
                    Thread.unread_count_for's rules, as ONE query
* unread.announcements announcements Announcement.objects.unread_for (staff)
* unread.memos      messaging.memos.awaiting_memos_for + unread_memo_count_for
* profile_completeness accounts.profile_nudges.status (staff only)
"""
from django.db.models import F, OuterRef, Q, Subquery

from ..doors import door_for_user


def staff_profile(user):
    try:
        return user.staff_profile
    except Exception:
        return None


def photo_url(user, request=None):
    staff = staff_profile(user)
    if staff is None or not staff.photo:
        return None
    try:
        url = staff.photo_thumb_url(300) or staff.photo.url
    except Exception:
        return None
    return request.build_absolute_uri(url) if request is not None else url


def initials(user):
    letters = "".join(part[:1] for part in (user.first_name, user.last_name) if part).upper()
    return letters or (user.email[:1].upper() if user.email else "?")


def institution_dict(institution):
    if institution is None:
        return None
    return {"id": institution.pk, "code": institution.code, "name": institution.name,
            "status": institution.status}


def unread_chat_count(user):
    """Unread messages across the user's inbox threads, in one query (the
    website's home loops Thread.unread_count_for per thread)."""
    from messaging.models import Message, ThreadParticipant
    from messaging.views import _my_threads

    last_read = ThreadParticipant.objects.filter(thread=OuterRef("thread"), user=user).values("last_read_at")[:1]
    return (Message.objects
            .filter(thread__in=_my_threads(user).values("pk"), is_deleted_for_everyone=False)
            .exclude(sender=user)
            .exclude(hidden_for__user=user)
            .annotate(_last_read=Subquery(last_read))
            .filter(Q(_last_read__isnull=True) | Q(sent_at__gt=F("_last_read")))
            .values("pk").distinct().count())


def memos_needing_action(user):
    from messaging.memos import awaiting_memos_for, unread_memo_count_for

    return len(awaiting_memos_for(user)) + unread_memo_count_for(user)


def capabilities(user, request):
    caps = {"qa_workspace": False, "approve_leave": False, "post_announcements": False,
            "finance_queue": False, "insights": False}
    if getattr(user, "user_type", "staff") != "staff":
        return caps
    from qa_core.permissions import can_view_finance
    from qa_core.workspace import can_use_qa

    caps["qa_workspace"] = bool(can_use_qa(request))
    caps["approve_leave"] = user.has_perm("accounts.approve_requests")
    from announcements.permissions import can_post

    caps["post_announcements"] = bool(can_post(user))
    caps["finance_queue"] = bool(can_view_finance(user))
    caps["insights"] = bool(user.is_superuser or user.is_power_user)
    return caps


def announcement_unread_count(user):
    from .announcements import unread_count

    return unread_count(user)


def unread(user):
    from dashboards.counts import unread_notifications

    is_staff = getattr(user, "user_type", "staff") == "staff"
    return {
        "chats": unread_chat_count(user) if is_staff else 0,
        "alerts": unread_notifications(user).count(),
        "announcements": announcement_unread_count(user) if is_staff else 0,
        "memos": memos_needing_action(user) if is_staff else 0,
    }


def profile_completeness(user):
    from accounts import profile_nudges

    if not profile_nudges.applies_to(user):
        return None
    return profile_nudges.status(user)["percent"]


def me_payload(user, request):
    is_staff = getattr(user, "user_type", "staff") == "staff"
    staff = staff_profile(user) if is_staff else None
    department = None
    if is_staff and user.department_id:
        department = {"id": user.department_id, "name": user.department.name}
    return {
        "id": user.pk,
        "email": user.email,
        "full_name": user.get_full_name() or user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "initials": initials(user),
        "photo_url": photo_url(user, request) if is_staff else None,
        "user_type": user.user_type,
        "door": door_for_user(user),
        "designation": staff.designation.name if staff is not None and staff.designation_id else None,
        "department": department,
        "institution": institution_dict(user.institution) if user.user_type == "institution" else None,
        "must_change_password": bool(user.must_change_password),
        "capabilities": capabilities(user, request),
        "unread": unread(user),
        "profile_completeness": profile_completeness(user),
    }
