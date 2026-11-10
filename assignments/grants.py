"""Grant lifecycle (v2.1 X5; spec_v21 G1.d "Grant lifecycle").

    grant(grantee, obj, scope, *, reason, source=None, granted_by=None, ...)
    revoke(grant, *, by=None, reason="manual")
    revoke_for_source(source, *, reason="work_completed", by=None, grantee=None, keep_grantee=None)
    sync_for_source(source, grantee, obj, scope, *, wanted, reason, granted_by=None, ...)
    active_grants(user=None, *, obj=None, scope=None, model=None, source=None)
    granted_ids(user, model, scope)
    sweep(now=None, *, report=True)

Every create, extension and revoke writes ``core.audit.log`` (actions
``grant_created``, ``grant_extended``, ``grant_revoked``). Grants are never
deleted. Settings: ``qa.access.grant_max_days`` (30), ``qa.access.grant_grace_hours``
(48), ``qa.access.long_grant_days`` (30).
"""
from __future__ import annotations

import datetime
import logging

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from . import registry
from .models import AccessGrant

logger = logging.getLogger(__name__)

GRANT_SCOPES = {code for code, _ in AccessGrant.SCOPE_CHOICES}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _cfg_int(key, default):
    try:
        import config
        return int(config.get(key, default))
    except Exception:
        return default


def max_days():
    return max(1, _cfg_int("qa.access.grant_max_days", 30))


def grace_hours():
    return max(0, _cfg_int("qa.access.grant_grace_hours", 48))


def long_grant_days():
    return max(1, _cfg_int("qa.access.long_grant_days", 30))


# ---------------------------------------------------------------------------
# Cache version (Phase P caches visible_institution_ids per user on it)
# ---------------------------------------------------------------------------
def _version_key(user_id):
    return f"assignments:access_version:{user_id}"


def access_version(user) -> int:
    uid = getattr(user, "pk", user)
    return cache.get(_version_key(uid), 0)


def bump_version(user):
    uid = getattr(user, "pk", user)
    if uid is None:
        return
    key = _version_key(uid)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ct(obj_or_model):
    return ContentType.objects.get_for_model(obj_or_model, for_concrete_model=False)


def _audit(action, grant, actor, changes=None, note=""):
    from core import audit
    audit.log(action, grant, actor=actor, changes=changes or {}, note=note or "")


def _source_label(source):
    spec = registry.source_spec(source)
    if spec and spec.label:
        try:
            return str(spec.label(source))
        except Exception:  # pragma: no cover - a label must never break a grant
            pass
    return str(source)


def default_expiry(source=None, now=None):
    """min(source due + grace, now + max days); now + max days without a due date."""
    now = now or timezone.now()
    cap = now + datetime.timedelta(days=max_days())
    spec = registry.source_spec(source)
    due = None
    if spec and spec.due_at:
        try:
            due = spec.due_at(source)
        except Exception:
            due = None
    if due is None:
        return cap
    return min(max(due, now) + datetime.timedelta(hours=grace_hours()), cap)


def _notify(grant, created):
    """G1.f: the grantee is told (in-app, low priority)."""
    try:
        from notifications.models import Notification
        from notifications.services import notify
        label = _object_label(grant)
        if created:
            title = f"Temporary access: {label}"
            body = grant.reason
            if grant.expires_at:
                body += f" Until {timezone.localtime(grant.expires_at):%d %b %Y %H:%M} or until the work closes."
        else:
            title = f"Temporary access ended: {label}"
            body = grant.get_revoke_reason_display() if grant.revoke_reason else ""
        notify(grant.grantee, title=title, body=body, url=_object_url(grant) if created else "",
               category=Notification.SYSTEM, level=Notification.INFO, actor=grant.granted_by,
               metadata={"access_grant": grant.pk})
    except Exception:  # pragma: no cover
        logger.exception("Could not notify grantee of grant %s", grant.pk)


def _object_label(grant):
    obj = grant.obj
    if obj is None:
        return f"{grant.object_ct.model} #{grant.object_id}"
    return getattr(obj, "asset_tag", None) or getattr(obj, "reference", None) or str(obj)


def _object_url(grant):
    obj = grant.obj
    try:
        return obj.get_absolute_url()
    except Exception:
        pass
    if grant.object_ct.app_label == "assets" and obj is not None:
        from django.urls import reverse
        return reverse("assets:detail", args=[obj.asset_tag])
    return ""


# ---------------------------------------------------------------------------
# Create / revoke
# ---------------------------------------------------------------------------
@transaction.atomic
def grant(grantee, obj, scope, *, reason, source=None, granted_by=None, expires_at=None, institution=None,
          department=None, close_on_completion=True, notify_grantee=True):
    """Give ``grantee`` read access of ``scope`` to ``obj``. Idempotent: a live
    grant with the same grantee, object, scope and source is returned (its
    expiry moved later if the new one is later)."""
    if scope not in GRANT_SCOPES:
        raise ValueError(f"Unknown grant scope: {scope}")
    if grantee is None or not getattr(grantee, "pk", None):
        raise ValueError("A grant needs a grantee.")
    now = timezone.now()
    if expires_at is None:
        expires_at = default_expiry(source, now)
    if institution is None:
        institution = getattr(obj, "institution", None) if hasattr(obj, "institution_id") else None
    qs = AccessGrant.objects.live(grantee, now).for_object(obj).filter(scope=scope)
    if source is not None:
        qs = qs.for_source(source)
    else:
        qs = qs.filter(source_ct__isnull=True)
    existing = qs.select_for_update().first()
    if existing is not None:
        if existing.expires_at is not None and expires_at > existing.expires_at:
            old = existing.expires_at
            existing.expires_at = expires_at
            existing.save(update_fields=["expires_at"])
            _audit("grant_extended", existing, granted_by,
                   {"expires_at": {"old": old.isoformat(), "new": expires_at.isoformat()}})
        return existing
    g = AccessGrant.objects.create(
        grantee=grantee, object_ct=_ct(obj), object_id=str(obj.pk), scope=scope,
        institution=institution, department=department,
        source_ct=_ct(source) if source is not None else None,
        source_id=str(source.pk) if source is not None else "",
        reason=(reason or "")[:255] or "Access for assigned work",
        granted_by=granted_by if getattr(granted_by, "pk", None) else None,
        granted_at=now, expires_at=expires_at, close_on_completion=close_on_completion,
    )
    _audit("grant_created", g, granted_by, {
        "grantee": grantee.pk, "object": f"{g.object_ct.app_label}.{g.object_ct.model}:{g.object_id}",
        "scope": scope, "source": (f"{g.source_ct.app_label}.{g.source_ct.model}:{g.source_id}"
                                   if g.source_ct_id else None),
        "expires_at": expires_at.isoformat() if expires_at else None}, note=g.reason)
    bump_version(grantee)
    if notify_grantee:
        transaction.on_commit(lambda: _notify(g, True))
    return g


@transaction.atomic
def revoke(g, *, by=None, reason=AccessGrant.R_MANUAL, notify_grantee=True):
    """End one grant now. Revoking an ended grant is a no-op (returns False)."""
    if g.revoked_at is not None:
        return False
    g.revoked_at = timezone.now()
    g.revoked_by = by if getattr(by, "pk", None) else None
    g.revoke_reason = (reason or AccessGrant.R_MANUAL)[:80]
    g.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
    _audit("grant_revoked", g, by, {"reason": g.revoke_reason, "grantee": g.grantee_id})
    bump_version(g.grantee_id)
    if notify_grantee and reason != AccessGrant.R_EXPIRED:
        transaction.on_commit(lambda: _notify(g, False))
    return True


def revoke_for_source(source, *, reason=AccessGrant.R_COMPLETED, by=None, grantee=None, keep_grantee=None,
                      only_close_on_completion=False):
    """Revoke the live grants caused by ``source`` (optionally only one
    grantee's, or everyone's except ``keep_grantee``). Returns the count."""
    qs = AccessGrant.objects.live().for_source(source)
    if grantee is not None:
        qs = qs.filter(grantee=grantee)
    if keep_grantee is not None:
        qs = qs.exclude(grantee=keep_grantee)
    if only_close_on_completion:
        qs = qs.filter(close_on_completion=True)
    n = 0
    for g in qs:
        n += bool(revoke(g, by=by, reason=reason))
    return n


def sync_for_source(source, grantee, obj, scope, *, wanted, reason, granted_by=None, revoke_reason=None,
                    **kwargs):
    """Make the live grants of ``source`` match one wanted state: when
    ``wanted`` is true exactly one live grant for ``grantee`` (others of this
    source revoked as reassigned); when false none. Returns the live grant or None."""
    if wanted and grantee is not None and obj is not None:
        revoke_for_source(source, reason=AccessGrant.R_REASSIGNED, by=granted_by, keep_grantee=grantee)
        # A grant on a different object (the ticket's asset changed) ends too.
        for g in AccessGrant.objects.live(grantee).for_source(source):
            if g.object_ct_id != _ct(obj).pk or g.object_id != str(obj.pk) or g.scope != scope:
                revoke(g, by=granted_by, reason=AccessGrant.R_REASSIGNED)
        return grant(grantee, obj, scope, reason=reason, source=source, granted_by=granted_by, **kwargs)
    revoke_for_source(source, reason=revoke_reason or AccessGrant.R_COMPLETED, by=granted_by)
    return None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def active_grants(user=None, *, obj=None, scope=None, model=None, source=None, now=None):
    qs = AccessGrant.objects.live(user, now).select_related("grantee", "granted_by", "object_ct", "source_ct")
    if obj is not None:
        qs = qs.for_object(obj)
    if model is not None:
        qs = qs.filter(object_ct=_ct(model))
    if scope is not None:
        scopes = [scope] if isinstance(scope, str) else list(scope)
        qs = qs.filter(scope__in=scopes)
    if source is not None:
        qs = qs.for_source(source)
    return qs


def granted_ids(user, model, scope):
    """Primary keys (as strings) of ``model`` rows the user holds a live grant of ``scope`` on."""
    if not getattr(user, "is_authenticated", False):
        return []
    return list(active_grants(user, model=model, scope=scope).values_list("object_id", flat=True))


# ---------------------------------------------------------------------------
# Hourly sweep
# ---------------------------------------------------------------------------
def sweep(now=None, *, report=True):
    """1. extend live grants whose source work is still open (up to
    ``grant_max_days`` after the grant); 2. revoke live grants whose source
    closed (safety net: consumers revoke on close); 3. expire grants past
    ``expires_at``; 4. report live grants older than ``long_grant_days`` to
    the Director of ICT (each grant at most once a week)."""
    now = now or timezone.now()
    out = {"extended": 0, "closed": 0, "expired": 0, "reported": 0}
    live = AccessGrant.objects.live(now=now).filter(source_ct__isnull=False).select_related("source_ct")
    for g in live:
        src = g.source
        spec = registry.source_spec(src) if src is not None else None
        if src is None:
            if g.close_on_completion:
                out["closed"] += bool(revoke(g, reason=AccessGrant.R_COMPLETED))
            continue
        if spec is None:
            continue
        try:
            is_open = bool(spec.is_open(src))
        except Exception:
            logger.exception("is_open failed for grant %s", g.pk)
            continue
        if not is_open:
            if g.close_on_completion:
                out["closed"] += bool(revoke(g, reason=AccessGrant.R_COMPLETED))
            continue
        if g.expires_at is None:
            continue
        cap = g.granted_at + datetime.timedelta(days=max_days())
        due = None
        if spec.due_at:
            try:
                due = spec.due_at(src)
            except Exception:
                due = None
        target = min(max(due or now, now) + datetime.timedelta(hours=grace_hours()), cap)
        if target > g.expires_at:
            old = g.expires_at
            g.expires_at = target
            g.save(update_fields=["expires_at"])
            _audit("grant_extended", g, None, {"expires_at": {"old": old.isoformat(), "new": target.isoformat()}},
                   note="Source work still open")
            out["extended"] += 1
    for g in AccessGrant.objects.filter(revoked_at__isnull=True, expires_at__lte=now):
        out["expired"] += bool(revoke(g, reason=AccessGrant.R_EXPIRED))
    if report:
        out["reported"] = _report_long_grants(now)
    return out


def _report_long_grants(now):
    """Weekly per grant: tell the Director of ICT about grants live for more
    than ``long_grant_days``."""
    cutoff = now - datetime.timedelta(days=long_grant_days())
    week = now - datetime.timedelta(days=7)
    rows = list(AccessGrant.objects.live(now=now).filter(granted_at__lte=cutoff)
                .exclude(long_reported_at__gt=week).select_related("grantee"))
    if not rows:
        return 0
    try:
        import config
        import org
        director = org.director_of(config.get("qa.privacy.ict_department", "ict"))
    except Exception:
        director = None
    if director is not None:
        from notifications.models import Notification
        from notifications.services import notify
        notify(director, title=f"{len(rows)} access grant(s) older than {long_grant_days()} days",
               body="; ".join(f"{_object_label(g)} ({g.get_scope_display()})" for g in rows[:10]),
               url="/admin/assignments/accessgrant/", category=Notification.SYSTEM, level=Notification.WARNING)
    AccessGrant.objects.filter(pk__in=[g.pk for g in rows]).update(long_reported_at=now)
    return len(rows)
