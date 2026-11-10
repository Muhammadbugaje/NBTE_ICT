"""v2.6: the professional body as a proper client of the portal — its own
people, its own accounts, its own registrar — modelled on how institutions
manage theirs (institutions/portal_views.py users page + invite_user).

* Accounts: the body's administrator adds people (accounts with a one-time
  set-password link, never a password in transit) or NBTE does it for them
  from /qa/professional-bodies/<pk>/ (pending action → ``create_body_user``).
* The registrar: the member flagged ``is_primary_contact`` is the body's
  No.1 — NBTE's co-ordination and the report reviews land with them first.
* Visit assignment: for a case their body is invited to, the registrar puts
  one of their people forward as the body's representative
  (accreditation.VisitExternalMember, kind = professional body), which puts
  them on the same visit team list NBTE works from.
"""
from __future__ import annotations

from django.utils import timezone

ROLE_CHOICES = [("pb-admin", "Administrator (registrar or secretariat) — manages the body's page"),
                ("pb-rep", "Representative — can be assigned to visits and writes the body's report"),
                ("pb-viewer", "Viewer — sees the body's programmes and letters (read only)")]
ROLES = tuple(code for code, _label in ROLE_CHOICES)


def role_for(code):
    """The portal Role row for a body account code, creating it when missing."""
    from accounts.models import Audience, Role

    if code not in ROLES:
        return None
    audience, _created = Audience.objects.get_or_create(code="professional_body", defaults={"name": "Professional body"})
    role, _created = Role.objects.get_or_create(code=code, defaults={"name": dict(ROLE_CHOICES)[code].split(" — ")[0],
                                                                    "audience": audience})
    return role


def members_of(body, *, active=True):
    """The body's people. ``active`` is passed False to keep removed ones visible."""
    from .models import ProfessionalBodyMember

    qs = (ProfessionalBodyMember.objects.select_related("user")
          .filter(body=body, is_active_link=True))
    if active:
        qs = qs.filter(user__is_active=True)
    return qs


def primary_contact(body):
    """The registrar (No.1): the primary-contact member, else the first active member."""
    return members_of(body).order_by("-is_primary_contact", "user__first_name").first()


def _split_name(full_name):
    parts = (full_name or "").strip().split()
    first = parts[0] if parts else ""
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    return first[:150], last[:150]


def add_member(body, *, email, full_name, job_title="", role_code="pb-rep", invited_by=None,
               primary_contact=False, request=None, send_link=True):
    """Create the body's portal account + membership row; e-mail a one-time
    set-password link. Raises ValidationError on anything wrong."""
    from django.core.exceptions import ValidationError
    from django.contrib.auth import get_user_model

    from .models import ProfessionalBodyMember

    User = get_user_model()
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()
    job_title = (job_title or "").strip()[:120]
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("An account with this e-mail already exists.")
    if not full_name:
        raise ValidationError("Enter the person's name.")
    role = role_for(role_code)
    if role is None:
        raise ValidationError("Choose a valid role for a person of the body.")
    first, last = _split_name(full_name)
    user = User(email=email, first_name=first, last_name=last, user_type="professional_body",
                professional_body=body, role=role, must_change_password=True, is_active=True)
    user.set_unusable_password()
    user.save()
    if primary_contact:  # one No.1 at a time
        ProfessionalBodyMember.objects.filter(body=body, is_primary_contact=True).update(is_primary_contact=False)
    ProfessionalBodyMember.objects.create(body=body, user=user, job_title=job_title,
                                          is_primary_contact=bool(primary_contact), invited_at=timezone.now(),
                                          invited_by=invited_by)
    if send_link:
        _send_set_password(user, request=request)
    return user


def _send_set_password(user, *, request=None):
    """The one-time set-password e-mail (same wording as institution invites)."""
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from qa_core import mail
    from qa_core.hosts import portal_absolute, portal_reverse
    from qa_core.portal_tokens import link_valid_hours, portal_token_generator

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = portal_token_generator.make_token(user)
    link = portal_absolute(portal_reverse("qa_portal:password_set", uidb64=uid, token=token), request)
    mail.send("Set your NBTE Quality Assurance Portal password", [user.email], "emails/qa_set_password.html",
              {"user": user, "set_url": link, "valid_hours": link_valid_hours(), "institution": None},
              category="reset")
    ProfessionalBodyMember_mark_credentials(user.pk)
    return link


def ProfessionalBodyMember_mark_credentials(user_pk):
    from .models import ProfessionalBodyMember

    ProfessionalBodyMember.objects.filter(user_id=user_pk).update(credentials_last_sent_at=timezone.now())


def set_primary(body, user):
    """Make ``user`` the body's No.1 (demotes the previous registrar)."""
    from .models import ProfessionalBodyMember

    ProfessionalBodyMember.objects.filter(body=body).update(is_primary_contact=False)
    updated = ProfessionalBodyMember.objects.filter(body=body, user=user, is_active_link=True).update(
        is_primary_contact=True)
    return updated == 1


def deactivate_member(member, *, by=None):
    """Remove a person from the body: their portal account stops working."""
    from django.contrib.auth import get_user_model

    member.is_active_link = False
    member.save(update_fields=["is_active_link", "updated_at"])
    User = get_user_model()
    user = User.objects.filter(pk=member.user_id).first()
    if user is not None and not user.is_superuser:
        user.is_active = False
        user.save(update_fields=["is_active"])
    return member


def reactivate_member(member):
    """Bring a removed person back: fresh account + a new set-password link."""
    from django.contrib.auth import get_user_model

    member.is_active_link = True
    member.save(update_fields=["is_active_link", "updated_at"])
    User = get_user_model()
    user = User.objects.filter(pk=member.user_id).first()
    if user is not None:
        user.is_active = True
        user.save(update_fields=["is_active"])
    _send_set_password(user)
    return member


def notify_body_people(body, *, title, text, url="", actor=None, admins_only=False):
    """In-app notification to the body's people: the registrar first (and, for
    admin things, only the administrators). Never raises."""
    try:
        from notifications.services import notify_many

        people = []
        for member in members_of(body):
            code = getattr(getattr(member.user, "role", None), "code", "")
            if admins_only and code != "pb-admin":
                continue
            people.append(member.user)
        return notify_many(people, title=title, body=text, url=url, category="qa", actor=actor)
    except Exception:
        return []


# ===========================================================================
# Visit assignment: the registrar puts one of their people on the visit
# ===========================================================================
def assignable_members(body):
    """People who can represent the body on a visit (active accounts)."""
    return members_of(body)


def representative_for_case(case, body):
    """The member of record for this visit, if the body has already sent one."""
    from accreditation.models import VisitExternalMember

    return (VisitExternalMember.unscoped.filter(case=case, body=body).select_related("user").first())


def assign_representative(case, body, user, member_user, *, remark=""):
    """The registrar assigns ``member_user`` as the body's representative on
    ``case``'s visit. Creates/updates the case's VisitExternalMember, so the
    person appears on the visit team NBTE works from, and tells NBTE."""
    from django.core.exceptions import ValidationError

    from accounts.models import User
    from accreditation.models import VisitExternalMember
    from qa_core import timeline

    if user_type_of(member_user) != "professional_body" or member_user.professional_body_id != body.pk:
        raise ValidationError("That person is not on your body's list.")
    member_user = User.objects.filter(pk=member_user.pk, is_active=True, professional_body=body).first()
    if member_user is None:
        raise ValidationError("That person is not on your body's list.")
    existing = representative_for_case(case, body)
    if existing is not None and existing.user_id == member_user.pk:
        raise ValidationError(f"{member_user.get_full_name() or member_user.email} is already the visit "
                              "representative.")
    name = (member_user.get_full_name() or member_user.email).strip()
    changed = existing is not None
    if existing is None:
        row = VisitExternalMember.unscoped.create(case=case, body=body, kind=VisitExternalMember.PROFESSIONAL_BODY,
                                                  user=member_user, status="proposed", remark=remark or "",
                                                  added_by=user)
        timeline.record(case.institution, "qa_case.external_added", subject=case, actor=user, visibility="internal",
                        internal_text=f"{body.acronym or body.name} put {name} forward as its visit representative.")
    else:
        existing.user, existing.added_by, existing.status = member_user, user, "proposed"
        if remark:
            existing.remark = remark
        existing.save(update_fields=["user", "added_by", "status", "remark", "updated_at"])
        row = existing
    _tell_nbte_about_assignment(case, body, member_user, changed=changed)
    return row


def user_type_of(user):
    return getattr(user, "user_type", "staff")


def _tell_nbte_about_assignment(case, body, member_user, *, changed=False):
    """NBTE's working people learn who the body is sending."""
    from django.contrib.auth import get_user_model

    from notifications.services import notify_many

    name = (member_user.get_full_name() or member_user.email).strip()
    title = f"{body.acronym or body.name} visit representative{' changed' if changed else ''}"
    text = f"{body.name} has put {name} forward for the visit to {case.institution.name} ({case.reference})."
    User = get_user_model()
    staff = list(User.objects.filter(is_active=True, user_type="staff"))
    people = [u for u in staff
              if u.pk == case.desk_officer_id or u.pk in {i.pk for i in case.tracking_ict_officers.all()}
              or u.pk in {case.supervising_audit_officer_id, case.supervising_finance_officer_id}]
    dept = getattr(case.institution, "handling_department_id", None)
    if dept:
        people += list(User.objects.filter(is_active=True, user_type="staff", department_id=dept,
                                           role__code__in=("director", "officer")))
    notify_many(people, title=title, body=text, url=f"/qa/cases/{case.pk}/")
