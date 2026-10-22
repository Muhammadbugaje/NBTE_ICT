"""Account deletion requests (Phase 9).

Apple and Google require that users can START account deletion from inside
an app; NBTE must keep statutory records, so the user files a request and an
administrator handles it.

    request_deletion(user, reason="") -> AccountDeletionRequest
    withdraw(user) -> bool
    open_request(user) -> AccountDeletionRequest | None
    approve(req, by, note="") / decline(req, by, note="") / complete(req, by, note="")
    administrators() -> list[User]

"Completed" deactivates the account and blanks clearly personal, optional
fields (phone numbers, photo, emergency contact, 2FA secret). The user row and
everything NBTE must keep (payments, accreditation decisions, audit logs) stay.
"""
from __future__ import annotations

import logging

from django.contrib.auth.models import Permission
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import AccountDeletionRequest, User

logger = logging.getLogger(__name__)

ADMIN_PERMISSION = "accounts.change_user"

# Shown on both doors before the user confirms.
REMOVED_ITEMS = (
    "Your ability to sign in (the account is switched off)",
    "Your phone number and profile photo",
    "Your emergency contact details",
    "Signed-in devices and passkeys",
)
KEPT_ITEMS = (
    "Payment records and receipts",
    "Accreditation and quality-assurance decisions",
    "Audit logs of actions taken with the account",
    "Official correspondence and records NBTE must keep by law",
)


def open_request(user):
    return (AccountDeletionRequest.objects
            .filter(user=user, status__in=AccountDeletionRequest.OPEN_STATUSES).first())


def latest_request(user):
    return AccountDeletionRequest.objects.filter(user=user).first()


def administrators():
    """Active superusers and holders of accounts.change_user (role, group,
    direct grant), minus anyone whose override revokes it."""
    try:
        perm = Permission.objects.get(content_type__app_label="accounts", codename="change_user")
    except Permission.DoesNotExist:
        return list(User.objects.filter(is_active=True, is_superuser=True))
    candidates = (User.objects.filter(is_active=True)
                  .filter(Q(is_superuser=True) | Q(role__permissions=perm) | Q(role__groups__permissions=perm)
                          | Q(groups__permissions=perm) | Q(user_permissions=perm)
                          | Q(permission_overrides__permission=perm, permission_overrides__effect="grant"))
                  .distinct())
    return [u for u in candidates if u.is_superuser or u.has_perm(ADMIN_PERMISSION)]


def _notify_admins(req):
    from notifications.models import Notification
    from notifications.services import notify_many

    who = req.user.get_full_name() or req.user.email
    notify_many(
        administrators(),
        title=f"Account deletion requested: {who}",
        body=(req.reason or "No reason given.")[:500],
        url=f"/admin/accounts/accountdeletionrequest/{req.pk}/change/",
        category=Notification.SYSTEM, level=Notification.WARNING, actor=req.user,
        metadata={"deletion_request_id": req.pk, "user_id": req.user_id},
    )


def request_deletion(user, reason=""):
    """File a request (idempotent: an open request is returned as is)."""
    existing = open_request(user)
    if existing is not None:
        return existing
    with transaction.atomic():
        req = AccountDeletionRequest.objects.create(user=user, reason=(reason or "").strip()[:2000])
    try:
        _notify_admins(req)
    except Exception:
        logger.exception("Could not notify administrators about deletion request %s", req.pk)
    return req


def withdraw(user):
    req = AccountDeletionRequest.objects.filter(user=user, status=AccountDeletionRequest.PENDING).first()
    if req is None:
        return False
    req.status = AccountDeletionRequest.WITHDRAWN
    req.handled_at = timezone.now()
    req.save(update_fields=["status", "handled_at"])
    return True


def _handle(req, status, by, note):
    req.status = status
    req.handled_by = by
    req.handled_at = timezone.now()
    if note:
        req.note = note
    req.save(update_fields=["status", "handled_by", "handled_at", "note"])


def _tell_user(req, title, body, level):
    from notifications.models import Notification
    from notifications.services import notify

    notify(req.user, title=title, body=body, category=Notification.SYSTEM, level=level,
           metadata={"deletion_request_id": req.pk})


def approve(req, by, note=""):
    if req.status != AccountDeletionRequest.PENDING:
        return False
    _handle(req, AccountDeletionRequest.APPROVED, by, note)
    _tell_user(req, "Your account deletion request was approved",
               note or "An administrator will close your account shortly.", "info")
    return True


def decline(req, by, note=""):
    if req.status not in AccountDeletionRequest.OPEN_STATUSES:
        return False
    _handle(req, AccountDeletionRequest.DECLINED, by, note)
    _tell_user(req, "Your account deletion request was declined",
               note or "Contact NBTE if you have questions.", "warning")
    return True


def anonymise(user):
    """Switch the account off and blank clearly personal optional fields.
    Returns the list of field names that were blanked."""
    from . import sessions_service

    blanked = []
    try:
        staff = user.staff_profile
    except Exception:
        staff = None
    if staff is not None:
        changed = []
        for field in ("phone", "emergency_contact_name", "emergency_contact_phone"):
            if getattr(staff, field):
                setattr(staff, field, "")
                changed.append(field)
        if staff.photo:
            try:
                staff.photo.delete(save=False)
            except Exception:
                logger.exception("Could not delete photo file for staff %s", staff.pk)
            staff.photo = None
            changed.append("photo")
        if changed:
            staff.save()
            blanked += [f"staff.{f}" for f in changed]
    try:
        rp = user.resource_person
    except Exception:
        rp = None
    if rp is not None and getattr(rp, "phone", ""):
        rp.phone = ""
        rp.save(update_fields=["phone"])
        blanked.append("resource_person.phone")

    user.is_active = False
    user.two_factor_enabled = False
    user.totp_secret = ""
    user.save(update_fields=["is_active", "two_factor_enabled", "totp_secret"])
    sessions_service.revoke_all_except(user, None)
    user.passkeys.all().delete()
    return blanked


def complete(req, by, note=""):
    if req.status == AccountDeletionRequest.COMPLETED:
        return False
    with transaction.atomic():
        blanked = anonymise(req.user)
        _handle(req, AccountDeletionRequest.COMPLETED, by,
                note or ("Account deactivated; cleared: " + ", ".join(blanked) if blanked
                         else "Account deactivated."))
    return True
