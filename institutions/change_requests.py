"""Approval workflow for institution user and management changes."""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from qa_core import timeline

from .models import Institution, InstitutionChangeRequest, InstitutionUser, ManagementRole


def _ict_department():
    from qa_core.departments import qa_department

    return qa_department("ict")


def is_lead(user):
    if user is None or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    import org

    ict = _ict_department()
    return bool(user.is_superuser or org.is_es(user) or org.is_super_admin(user)
                or (ict and org.is_director(user, ict)))


def is_ict_reviewer(user):
    if user is None or not getattr(user, "is_active", False) or getattr(user, "user_type", "") != "staff":
        return False
    return reviewer_candidates().filter(pk=user.pk).exists()


def reviewer_candidates():
    from accounts.models import User

    ict = _ict_department()
    if ict is None:
        return User.objects.none()
    return (User.objects.filter(is_active=True, user_type=User.STAFF)
            .filter(Q(department=ict) | Q(staff_profile__department=ict))
            .select_related("role").distinct().order_by("first_name", "last_name", "email"))


def _lead_recipients():
    import org
    from accounts.models import User

    staff = User.objects.filter(is_active=True, user_type=User.STAFF).select_related("role", "department")
    recipients = {user.pk: user for user in staff
                  if user.is_superuser or org.is_es(user) or org.is_super_admin(user)}
    ict = _ict_department()
    if ict and ict.director_id:
        director = User.objects.filter(pk=ict.director_id, is_active=True, user_type=User.STAFF).first()
        if director:
            recipients[director.pk] = director
    return list(recipients.values())


def _notify(recipients, *, title, body, url, actor=None):
    from notifications.models import Notification
    from notifications.services import notify_many

    notify_many(recipients, title=title, body=body, url=url, category=Notification.QA, actor=actor)


def _payload_for(change_type, data, institution):
    from accounts.models import Role, User
    from . import services

    if change_type == InstitutionChangeRequest.PROFILE:
        from . import portal_views

        form = portal_views.PortalProfileForm(data.get("profile", {}))
        if not form.is_valid():
            raise ValidationError(form.errors)
        current = portal_views._profile_initial(institution)
        payload = {}
        changed = False
        for key, value in form.cleaned_data.items():
            serialized = value.pk if getattr(value, "pk", None) else value
            current_value = current.get(key)
            if getattr(current_value, "pk", None):
                current_value = current_value.pk
            payload[key] = serialized
            changed = changed or serialized != current_value
        if not changed:
            raise ValidationError("Change at least one institution profile detail.")
        return payload
    if change_type == InstitutionChangeRequest.MANAGEMENT:
        role = ManagementRole.objects.filter(code=data.get("role_code"), is_active=True).first()
        if role is None or role.is_chief_executive or role.is_ict_head:
            raise ValidationError("Choose an active management role other than Chief Executive or Head of ICT.")
        name = (data.get("full_name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        phone = (data.get("phone") or "").strip()
        if len(name) < 3:
            raise ValidationError({"full_name": "Enter the person's full name."})
        if role.requires_email and not email:
            raise ValidationError({"email": f"NBTE needs an e-mail address for the {role.name}."})
        if role.requires_phone and not phone:
            raise ValidationError({"phone": f"NBTE needs a phone number for the {role.name}."})
        return {"role_code": role.code, "title": (data.get("title") or "").strip()[:30],
                "full_name": name[:150], "email": email, "phone": phone[:30]}
    if change_type == InstitutionChangeRequest.USER_INVITE:
        role_code = data.get("role")
        if role_code not in ("inst-academic-dept", "inst-admin"):
            raise ValidationError({"role": "Choose an eligible institution-user role."})
        clean = {key: data.get(key) for key in ("first_name", "last_name", "email", "role",
                                                 "job_title", "institution_department", "grade_level")}
        clean["email"] = (clean["email"] or "").strip().lower()
        if User.objects.filter(email__iexact=clean["email"]).exists():
            raise ValidationError({"email": "That e-mail address already has an account."})
        if Role.objects.filter(code=role_code).first() is None:
            raise ValidationError({"role": "Choose a valid institution-user role."})
        problems = services.nominee_problems(institution, clean)
        if problems:
            raise ValidationError(problems)
        return clean
    if change_type == InstitutionChangeRequest.USER_REMOVE:
        membership = (InstitutionUser.unscoped.filter(
            pk=data.get("membership_id"), institution=institution, is_active_link=True,
            account_kind=InstitutionUser.STAFF_USER).select_related("user").first())
        if membership is None:
            raise ValidationError({"membership_id": "Choose an active staff account that can be removed."})
        return {"membership_id": membership.pk}
    raise ValidationError("Choose a supported change type.")


@transaction.atomic
def submit(institution, *, actor, change_type, data, reason):
    from . import services

    services.require_chief_executive(actor, "an institution change request")
    if getattr(actor, "institution_id", None) != institution.pk:
        raise PermissionDenied("You can submit change requests only for your own institution.")
    if not services.institution_team_locked(institution):
        raise ValidationError("Changes can still be made directly before the first application is submitted.")
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValidationError({"reason": "Explain why this change is needed (at least 10 characters)."})
    payload = _payload_for(change_type, data, institution)
    request = InstitutionChangeRequest.unscoped.create(
        institution=institution, requested_by=actor, change_type=change_type, payload=payload, reason=reason)
    timeline.record(institution, "institution.change_requested", subject=request, actor=actor, visibility="public",
                    public_text=f"A request to change institution {change_type.replace('_', ' ')} records was sent to NBTE.")
    transaction.on_commit(lambda: _notify(
        _lead_recipients(), title="Institution change request",
        body=f"{institution.name} submitted request {request.reference}.", url=f"/qa/institutions/change-requests/{request.pk}/",
        actor=actor))
    return request


@transaction.atomic
def assign(request, *, actor, assignee):
    if not is_lead(actor):
        raise PermissionDenied("Only the ES, super admin, or Director of ICT can assign change requests.")
    if not is_ict_reviewer(assignee):
        raise ValidationError("Assign this request to an active ICT department staff member.")
    row = InstitutionChangeRequest.unscoped.select_for_update().get(pk=request.pk)
    if row.status not in (InstitutionChangeRequest.PENDING, InstitutionChangeRequest.ASSIGNED):
        raise ValidationError("Only open change requests can be assigned.")
    if row.change_type == InstitutionChangeRequest.PROFILE and is_lead(assignee):
        raise ValidationError("Assign a different ICT staff member to review profile changes before senior approval.")
    row.assigned_to, row.assigned_by = assignee, actor
    row.status = InstitutionChangeRequest.ASSIGNED
    row.save(update_fields=["assigned_to", "assigned_by", "status", "updated_at"])
    transaction.on_commit(lambda: _notify(
        [assignee], title="Institution change request assigned",
        body=f"Review {row.reference} for {row.institution.name}.",
        url=f"/qa/institutions/change-requests/{row.pk}/", actor=actor))
    return row


def _apply(row, actor):
    from . import profile, services

    if row.change_type == InstitutionChangeRequest.PROFILE:
        from .portal_views import PortalProfileForm, _save_profile
        from .services import ensure_unique_name

        form = PortalProfileForm(row.payload)
        if not form.is_valid():
            raise ValidationError(form.errors)
        if form.cleaned_data["name"] != row.institution.name:
            ensure_unique_name(form.cleaned_data["name"], exclude=row.institution.pk)
        _save_profile(row.institution, form.cleaned_data, actor)
    elif row.change_type == InstitutionChangeRequest.MANAGEMENT:
        role = ManagementRole.objects.filter(code=row.payload["role_code"], is_active=True).first()
        if role is None or role.is_chief_executive or role.is_ict_head:
            raise ValidationError("The requested management role is no longer available.")
        profile.set_holder(row.institution, role, row.payload, actor=actor, approved_request=True)
    elif row.change_type == InstitutionChangeRequest.USER_INVITE:
        services.invite_user(row.institution, row.payload, actor=actor, approved_request=True)
    elif row.change_type == InstitutionChangeRequest.USER_REMOVE:
        membership = (InstitutionUser.unscoped.select_for_update().filter(
            pk=row.payload["membership_id"], institution=row.institution, is_active_link=True,
            account_kind=InstitutionUser.STAFF_USER).select_related("user").first())
        if membership is None:
            raise ValidationError("The institution user is no longer available to remove.")
        membership.is_active_link = False
        membership.save(update_fields=["is_active_link", "updated_at"])
        membership.user.is_active = False
        membership.user.save(update_fields=["is_active"])
        timeline.record(row.institution, "users.removed", subject=membership, actor=actor, visibility="public",
                        public_text=f"{membership.user.get_full_name()}'s institution account was removed.")
    else:
        raise ValidationError("This change request type is not supported.")


@transaction.atomic
def decide(request, *, actor, decision, note=""):
    row = InstitutionChangeRequest.unscoped.select_for_update().select_related(
        "institution", "requested_by", "assigned_to").get(pk=request.pk)
    note = (note or "").strip()
    if row.change_type == InstitutionChangeRequest.PROFILE and decision == "review":
        if (row.assigned_to_id != getattr(actor, "pk", None) or not is_ict_reviewer(actor)):
            raise PermissionDenied("Only the assigned ICT reviewer can submit the profile review.")
        if row.status != InstitutionChangeRequest.ASSIGNED:
            raise ValidationError("Assign this profile request before reviewing it.")
        if len(note) < 5:
            raise ValidationError({"note": "Summarize your review before sending it for senior approval."})
        row.status = InstitutionChangeRequest.REVIEWED
        row.reviewed_by, row.reviewed_at, row.review_note = actor, timezone.now(), note
        row.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
        timeline.record(row.institution, "institution.change_reviewed", subject=row, actor=actor,
                        visibility="public", public_text=f"NBTE is reviewing the profile change request {row.reference}.")
        transaction.on_commit(lambda: _notify(
            _lead_recipients() + [row.requested_by], title="Profile change awaiting senior approval",
            body=f"ICT review is complete for {row.reference}; a senior NBTE decision is required.",
            url=f"/qa/institutions/change-requests/{row.pk}/", actor=actor))
        return row

    if row.change_type == InstitutionChangeRequest.PROFILE:
        if not is_lead(actor):
            raise PermissionDenied("Only an NBTE lead can make the final profile-change decision.")
        if row.status != InstitutionChangeRequest.REVIEWED:
            raise ValidationError("An ICT reviewer must complete the profile review before senior approval.")
        if row.reviewed_by_id == getattr(actor, "pk", None):
            raise PermissionDenied("A different NBTE officer must make the final profile-change decision.")
    else:
        if not (is_lead(actor) or (
                row.assigned_to_id == getattr(actor, "pk", None) and is_ict_reviewer(actor))):
            raise PermissionDenied("Only the assigned ICT reviewer or an NBTE lead can decide this request.")
        if row.status != InstitutionChangeRequest.ASSIGNED:
            raise ValidationError("Assign this change request before making a decision.")
    if decision not in ("approve", "reject"):
        raise ValidationError("Choose approve or reject.")
    if decision == "reject" and len(note) < 5:
        raise ValidationError({"note": "Give a reason for not approving the change."})
    if decision == "approve":
        _apply(row, actor)
        row.status = InstitutionChangeRequest.APPROVED
        timeline_text = f"NBTE approved institution change request {row.reference}."
    else:
        row.status = InstitutionChangeRequest.REJECTED
        timeline_text = f"NBTE did not approve institution change request {row.reference}."
    row.decision_note = note
    row.decided_by, row.decided_at = actor, timezone.now()
    row.save(update_fields=["status", "decision_note", "decided_by", "decided_at", "updated_at"])
    timeline.record(row.institution, "institution.change_decided", subject=row, actor=actor, visibility="public",
                    public_text=timeline_text)
    transaction.on_commit(lambda: _notify(
        [row.requested_by], title="Institution change request updated",
        body=f"{row.reference}: {'approved' if decision == 'approve' else 'not approved'}."
             + (f" {note}" if note else ""),
        url="/institution/change-requests/", actor=actor))
    return row
