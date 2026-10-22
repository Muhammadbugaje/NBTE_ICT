"""Profile completeness nudges (Phase 8).

What happened before this module: a user below
``profile.completion_threshold`` saw a small "Profile N% complete" badge in
the user menu and a card on the home page, and got one notification when
they crossed the threshold. Nothing was gated or reminded — the setting's
description ("required to unlock gated features") was never enforced.

Now, for an internal user below the threshold:
  * a banner on every internal page shows the % and what is missing; it can
    be dismissed for the browser session (``profile.banner_enabled``);
  * an in-app reminder notification is sent at most every
    ``profile.reminder_days`` days (0 = off) — checked lazily once a day per
    session when a page renders, and by ``manage.py send_profile_reminders``
    for a scheduled run;
  * starting a new request (ticket, request/leave, task, memo, QA action)
    opens a dialog offering "Complete my profile" or "Continue anyway"
    (``profile.interrupt_enabled``); continuing is written to the audit log.
    It is a nudge, not a block.
"""
from datetime import timedelta

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

REMINDER_KIND = "profile_reminder"
SESSION_CHECK_KEY = "profile_nudge_checked_on"

# Where a "new request" starts. URL names (prefix match on the reversed path).
GATED_URL_NAMES = (
    ("tickets:create", ()),
    ("workflow:raise", ()),       # every request type, leave included
    ("tasks:create", ()),
    ("messaging:memo_create", ()),
    ("messaging:compose", ()),
)
# QA launchers that start a two-step request: /qa/institutions/actions/<code>/
GATED_PREFIX_NAMES = (
    ("qa_inst:launcher", ("x",)),
    ("qa_req:query_raise", ("x", 0)),
)


def _int(key, default):
    import config

    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def threshold():
    return _int("profile.completion_threshold", 70)


def reminder_days():
    return _int("profile.reminder_days", 7)


def banner_enabled():
    return _int("profile.banner_enabled", 1) == 1


def interrupt_enabled():
    return _int("profile.interrupt_enabled", 1) == 1


def applies_to(user):
    """Internal staff only (portal users have no staff profile)."""
    return bool(user and user.is_authenticated and getattr(user, "user_type", "staff") == "staff"
                and hasattr(user, "profile_completeness"))


def status(user):
    """{percent, threshold, complete, missing: [labels]} for ``user``."""
    data = user.profile_completeness()
    limit = threshold()
    missing = [i["label"] for i in data["items"] if not i["filled"]]
    # Most valuable first, so the banner shows what moves the needle.
    weights = {i["label"]: i["weight"] for i in data["items"]}
    missing.sort(key=lambda label: -weights.get(label, 0))
    return {"percent": data["percent"], "threshold": limit, "complete": data["percent"] >= limit,
            "missing": missing}


def gated_paths():
    paths = []
    for name, args in GATED_URL_NAMES:
        try:
            paths.append(reverse(name, args=args))
        except NoReverseMatch:
            continue
    for name, args in GATED_PREFIX_NAMES:
        try:
            full = reverse(name, args=args)
        except NoReverseMatch:
            continue
        # strip the placeholder segments → the stable prefix
        cut = full
        for a in args:
            marker = f"/{a}/"
            if marker in cut:
                cut = cut[: cut.index(marker) + 1]
        paths.append(cut)
    return sorted(set(paths))


def maybe_send_reminder(user, now=None, force_check=False):
    """Send one reminder if the user is still below the threshold and the
    last reminder is older than ``profile.reminder_days``. Returns the
    notification or None."""
    from notifications.models import Notification
    from notifications.services import notify

    days = reminder_days()
    if days <= 0 or not applies_to(user) or not user.is_active:
        return None
    st = status(user)
    if st["complete"]:
        return None
    now = now or timezone.now()
    last = (Notification.objects.filter(recipient=user, metadata__kind=REMINDER_KIND)
            .order_by("-created_at").values_list("created_at", flat=True).first())
    if last and last > now - timedelta(days=days):
        return None
    top = ", ".join(st["missing"][:3]).lower()
    return notify(
        recipient=user,
        title=f"Your profile is {st['percent']}% complete",
        body=(f"Please bring it to at least {st['threshold']}%. Still missing: {top}."
              if top else f"Please bring it to at least {st['threshold']}%."),
        url=reverse("accounts:profile"),
        category=Notification.SYSTEM,
        level=Notification.WARNING,
        metadata={"kind": REMINDER_KIND, "percent": st["percent"]},
    )


def daily_check(request):
    """Run the reminder check at most once a day per session."""
    today = timezone.localdate().isoformat()
    try:
        if request.session.get(SESSION_CHECK_KEY) == today:
            return None
        request.session[SESSION_CHECK_KEY] = today
    except Exception:
        return None
    return maybe_send_reminder(request.user)
