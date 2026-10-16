"""Staff-side services — leadership views and lookups.

Shared by:
  - the public leadership page (public.html)
  - the custom management panel (manage_*.html)
  - the homepage preview widget (_preview.html)
"""
from __future__ import annotations

from django.db.models import Q

from .models import ExecutivePosition, ExecutiveTenure


# ============================================================================
# PUBLIC READ APIs
# ============================================================================
def current_leadership(*, scope=None, department=None, public_only=True):
    """Current tenure for every position, ordered by rank."""
    qs = (
        ExecutiveTenure.objects
        .filter(ended_on__isnull=True)
        .select_related("staff", "staff__department",
                        "position", "position__department")
        .order_by("position__order", "display_order", "started_on")
    )
    if public_only:
        qs = qs.filter(is_public=True)
    if scope:
        qs = qs.filter(position__scope=scope)
    if department:
        qs = qs.filter(position__department=department)
    return qs


def featured_leadership(limit=6, public_only=True):
    """Leadership strip for the public homepage."""
    qs = current_leadership(public_only=public_only)
    featured = qs.filter(is_featured=True)
    return (featured if featured.exists() else qs)[:limit]


def leadership_timeline(*, position=None, public_only=True):
    """Full chronology — past and present, newest first."""
    qs = (
        ExecutiveTenure.objects
        .select_related("staff", "staff__department", "position")
        .order_by("-started_on", "-ended_on", "position__order")
    )
    if public_only:
        qs = qs.filter(is_public=True)
    if position:
        qs = qs.filter(position=position)
    return qs


def positions_with_history(public_only=True):
    """Every position with its tenures, grouped. For the public timeline page."""
    positions = (
        ExecutivePosition.objects
        .filter(is_active=True)
        .order_by("order", "name")
    )
    result = []
    for p in positions:
        tenures = list(
            p.tenures.select_related("staff").order_by("-started_on")
        )
        if public_only:
            tenures = [t for t in tenures if t.is_public]
        if tenures:
            result.append({"position": p, "tenures": tenures})
    return result


# ============================================================================
# MANAGEMENT PANEL QUERIES
# ============================================================================
def all_positions(*, active_only=False):
    qs = ExecutivePosition.objects.order_by("order", "name")
    if active_only:
        qs = qs.filter(is_active=True)
    return qs


def all_tenures(*, position=None, staff=None, current_only=False, q=None):
    """Every tenure — used by the management table. Includes private records."""
    qs = (
        ExecutiveTenure.objects
        .select_related("staff", "staff__department", "position")
        .order_by("-started_on", "-ended_on", "position__order")
    )
    if position:
        qs = qs.filter(position=position)
    if staff:
        qs = qs.filter(staff=staff)
    if current_only:
        qs = qs.filter(ended_on__isnull=True)
    if q:
        qs = qs.filter(
            Q(staff__first_name__icontains=q)
            | Q(staff__last_name__icontains=q)
            | Q(staff__employee_no__icontains=q)
            | Q(position__name__icontains=q)
        )
    return qs


# ============================================================================
# ACCOUNT PROVISIONING (review H5)
# ============================================================================
def provision_account_for_staff(staff, password=None, login_url=""):
    """Create a login for an existing Staff record and email the credentials.

    Returns (status, user) where status is one of:
      "exists"        - the record already has an account
      "no_email"      - the record has no email address
      "linked"        - an existing account with that email was linked
      "conflict"      - an account with that email already has another
                        directory record; nothing was changed
      "created"       - a new account was created and the email queued
      "created_no_mail" - created, but the email could not be queued

    The password is never returned or displayed; it is only emailed.
    The User is created first and the post_save signal links THIS Staff
    record (it matches by email) instead of creating a second one.
    """
    import secrets

    from django.contrib.auth import get_user_model
    from django.db import transaction

    User = get_user_model()

    if staff.user_id:
        return "exists", staff.user
    if not staff.email:
        return "no_email", None

    existing = User.objects.filter(email__iexact=staff.email).first()
    if existing is not None:
        try:
            other = existing.staff_profile
        except Exception:
            other = None
        if other is not None and other.pk != staff.pk:
            return "conflict", existing
        staff.user = existing
        staff.save(update_fields=["user"])
        return "linked", existing

    temp_password = (password or "").strip() or secrets.token_urlsafe(10)
    with transaction.atomic():
        user = User.objects.create_user(
            email=staff.email,
            password=temp_password,
            first_name=staff.first_name,
            last_name=staff.last_name,
            **({"department": staff.department} if staff.department_id else {}),
        )
        if not user.must_change_password:
            user.must_change_password = True
            user.save(update_fields=["must_change_password"])
        staff.refresh_from_db(fields=["user"])
        if staff.user_id != user.pk:
            staff.user = user
            staff.save(update_fields=["user"])

    from core.email_utils import send_templated_email

    log = send_templated_email(
        subject="Your NBTE ICT account",
        recipients=[staff.email],
        template_name="emails/staff_welcome.html",
        context={
            "staff": staff, "user": user,
            "temp_password": temp_password,
            "login_url": login_url,
        },
        # "credentials" keeps the body (which holds the password) out of the
        # email log, unlike the previous "general" category.
        category="credentials",
    )
    return ("created" if log is not None else "created_no_mail"), user
