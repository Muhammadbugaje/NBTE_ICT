"""Who may do what with announcements. One place, used by views, services,
templates and the attachment download check."""

POST_PERM = "announcements.post_announcement"
REPORT_PERM = "announcements.view_announcement_report"
CHANGE_PERM = "announcements.change_announcement"


def _is_staff(user):
    return (
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "user_type", None) == "staff"
    )


def can_post(user):
    return _is_staff(user) and user.has_perm(POST_PERM)


def can_edit(user, announcement):
    """The poster who wrote it (while they can still post), or anyone with the
    model's change permission (superusers included)."""
    if not _is_staff(user):
        return False
    if user.has_perm(CHANGE_PERM):
        return True
    return announcement.created_by_id == user.pk and user.has_perm(POST_PERM)


def can_view_report(user, announcement):
    if not _is_staff(user):
        return False
    return announcement.created_by_id == user.pk or user.has_perm(REPORT_PERM)
