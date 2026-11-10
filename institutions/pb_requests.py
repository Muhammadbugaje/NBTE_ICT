"""v2.1 Part 5: professional-body programme requests.

A body asks NBTE to put a programme under it. The service guards both sides:

* on submission the programme master custodians (holders of
  ``institutions.manage_programme_master``) are notified;
* approval maps the programme to the body in the three places the mapping
  lives — the master row, the name-based ``ProgrammeBodyMapping`` and the
  institution's own programmes — and tells the body and the institution;
* a rejection tells the body why (a remark is required).

Only the PB portal administrator submits or withdraws; only the NBTE
custodian decides. Everything is audited (``AUDITED = True``).
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (BodyProgrammeRequest, BodyRequirement, Institution, ProgrammeBodyMapping,
                     ProgrammeCategory, ProgrammeLevel, ProgrammeMaster, ProfessionalBody)
from .programme_master import normalise

REMARK_MIN = 5


def can_manage(user):
    return bool(user and user.is_authenticated and (user.is_superuser
                                                    or user.has_perm("institutions.manage_programme_master")))


def can_request(user, body):
    """The body's portal administrator (or NBTE acting on the body's behalf)."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    from .pb_portal import is_pb_admin

    if is_pb_admin(user):
        return user.professional_body_id == getattr(_body(body), "pk", None)
    return can_manage(user)


def _body(body):
    if body is None or getattr(body, "pk", None) is None:
        raise ValidationError("Choose the professional body.")
    return body


def _custodians():
    from django.contrib.auth.models import Permission
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    User = get_user_model()
    try:
        perm = Permission.objects.get(codename="manage_programme_master",
                                      content_type__app_label="institutions")
    except Permission.DoesNotExist:
        return list(User.objects.filter(is_active=True, is_superuser=True))
    return list(User.objects.filter(is_active=True)
                .filter(Q(is_superuser=True) | Q(user_permissions=perm) | Q(groups__permissions=perm))
                .distinct())


def _notify_custodians(row):
    from notifications.services import notify_many

    title = f"{row.body.acronym or row.body.name} requests: {row.programme_name}"
    where = row.institution.name if row.institution_id else "—"
    text = (f"{where} · {row.level.code.upper() if row.level_id else 'any level'}. "
            f"{(row.rationale or '').strip()[:180]}".strip())
    return notify_many(_custodians(), title=title, body=text,
                       url=f"/qa/programmes/requests/{row.pk}/")


def _notify_pb(row):
    from .pb_portal import body_users
    from notifications.services import notify_many

    outcome = {
        BodyProgrammeRequest.APPROVED: ("approved",
                                        f"Your programme request for {row.programme_name}"
                                        f" ({'any level' if row.level_id is None else row.level.code.upper()})"
                                        f" was approved.{(' ' + row.remark) if row.remark else ''}"),
        BodyProgrammeRequest.REJECTED: ("rejected",
                                        f"Your programme request for {row.programme_name} was not approved."
                                        f" NBTE: “{row.remark}”"),
        BodyProgrammeRequest.WITHDRAWN: ("withdrawn", "You withdrew this programme request."),
    }.get(row.status)
    if outcome is None:
        return []
    label, text = outcome
    return notify_many(body_users(row.body), title=f"Request {label}: {row.programme_name}", body=text,
                       url=f"/pb/programmes/-/requests/")


def _clean(body, *, user, institution, programme_name, level=None):
    body = _body(body)
    if not can_request(user, body):
        raise PermissionDenied("Only the body's administrator can send or withdraw a programme request.")
    if not body.is_active:
        raise ValidationError("This body is not active.")
    name = " ".join(str(programme_name or "").split())
    if len(name) < 3:
        raise ValidationError("Give the full programme name.")
    if institution is None or institution.pk is None:
        raise ValidationError("Choose the institution the programme belongs to.")
    return body, name, institution


@transaction.atomic
def submit(body, *, user, institution=None, programme_name="", level=None, category=None, rationale=""):
    """The body asks; NBTE's custodians are told. Returns the new row."""
    body, name, institution = _clean(body, user=user, institution=institution, programme_name=programme_name,
                                     level=level)
    clash = (BodyProgrammeRequest.objects
             .filter(body=body, institution=institution, programme_name__iexact=name,
                     status=BodyProgrammeRequest.PENDING))
    if level is not None:
        clash = clash.filter(level=level)
    if clash.exists():
        raise ValidationError("A request for this programme at this institution is already waiting for NBTE.")
    rationale = (rationale or "").strip()
    row = BodyProgrammeRequest.objects.create(
        body=body, institution=institution, programme_name=name, level=level,
        category=(category if getattr(category, "pk", None) else None), rationale=rationale[:2000],
        requested_by=user)
    transaction.on_commit(lambda: _notify_custodians(row))
    return row


@transaction.atomic
def withdraw(row, *, user):
    if row.status != BodyProgrammeRequest.PENDING:
        raise ValidationError("Only a request that is still waiting for NBTE can be withdrawn.")
    if not can_request(user, row.body):
        raise PermissionDenied("Only the body's administrator can withdraw this request.")
    row.status = BodyProgrammeRequest.WITHDRAWN
    row.decided_by = user
    row.decided_at = timezone.now()
    row.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    transaction.on_commit(lambda: _notify_pb(row))
    return row


def _master_row(row):
    """Get-or-create the master row for the approved programme."""
    name = " ".join(row.programme_name.split())
    master = (ProgrammeMaster.objects.filter(name__iexact=name).order_by("pk").first()
              if row.level_id is None else
              (ProgrammeMaster.objects.filter(name__iexact=name, level=row.level).first()
               or ProgrammeMaster.objects.filter(name__iexact=name).order_by("pk").first()))
    if master is not None:
        if master.professional_body_id and master.professional_body_id != row.body_id:
            other = master.professional_body.acronym or master.professional_body.name
            raise ValidationError(
                f"The master row for {master} is mapped to {other}; approve the request only after the "
                "master list is right.")
        fields = []
        if not master.professional_body_id:
            master.professional_body = row.body
            fields.append("professional_body")
        if row.category_id and not master.programme_category_id:
            master.programme_category = row.category
            fields.append("programme_category")
        if not master.is_active:
            master.is_active = True
            fields.append("is_active")
        if fields:
            fields.append("updated_at")
            master.save(update_fields=fields)
        return master
    return ProgrammeMaster.objects.create(
        name=name, level=row.level or ProgrammeLevel.objects.order_by("sort_order").first(),
        programme_category=row.category, professional_body=row.body)


def related_requirements(body, programme_name, level=None):
    """The body's requirement checklist that already covers this programme."""
    qs = BodyRequirement.objects.filter(body=body, is_active=True)
    return list(qs.order_by("sort_order", "code")[:8])


@transaction.atomic
def decide(row, *, user, approve=True, remark=""):
    """The NBTE custodian answers the request."""
    if row.status != BodyProgrammeRequest.PENDING:
        raise ValidationError("This request has already been decided.")
    if not can_manage(user):
        raise PermissionDenied("Only a holder of institutions.manage_programme_master decides programme "
                               "requests.")
    remark = (remark or "").strip()
    if not approve and len(remark) < REMARK_MIN:
        raise ValidationError(f"Give the body a reason to reject it ({REMARK_MIN}+ characters).")
    if approve:
        row.master = _master_row(row)
        body, key = row.body, normalise(row.programme_name)
        mapping, _made = ProgrammeBodyMapping.objects.get_or_create(
            body=body, programme_key=key, level=row.level, defaults={"is_active": True, "created_by": user})
        if not mapping.is_active:
            mapping.is_active, mapping.created_by = True, user
            mapping.save(update_fields=["is_active", "created_by", "updated_at"])
        mapped = 0
        programmes = Programme.unscoped.filter(institution=row.institution,
                                               name__iexact=row.programme_name).exclude(status="dropped")
        if row.level_id:
            programmes = programmes.filter(level=row.level)
        for programme in programmes:
            if programme.professional_body_id != body.pk:
                programme.professional_body = body
                programme.save(update_fields=["professional_body", "updated_at"])
            mapped += 1
        row.status, row.remark = BodyProgrammeRequest.APPROVED, remark[:255]
    else:
        row.status, row.remark = BodyProgrammeRequest.REJECTED, remark[:255]
    row.decided_by, row.decided_at = user, timezone.now()
    row.save(update_fields=["status", "remark", "decided_by", "decided_at", "master", "updated_at"])
    transaction.on_commit(lambda: _notify_pb(row))
    return row


__all__ = ["can_manage", "can_request", "submit", "withdraw", "decide", "related_requirements",
           "_notify_custodians", "_notify_pb", "_custodians"]
