"""Institution services (Master Plan §5.2, §6) — system code.

Views never query institution data directly; they scope with
``Model.objects.for_user(user)`` and call these functions with objects they
already hold. Functions here use the explicit ``unscoped`` managers on
purpose (``# scoped-by-caller``): the caller has already proved the user may
see the institution.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import json
import logging
import re
import secrets
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

import config
from core import audit
from qa_core import flow, timeline
from qa_core.departments import qa_department
from qa_core.files import validate_upload

from .models import (
    DocumentKind,
    DocumentRequirement,
    ImpersonationSession,
    Institution,
    InstitutionAddress,
    InstitutionContact,
    InstitutionDocument,
    InstitutionUser,
    Programme,
)

logger = logging.getLogger(__name__)
LIFECYCLE = "institution_lifecycle"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def cfg_int(key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def cfg_bool(key, default=False):
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def proposed_department():
    code = str(config.get("qa.institution.proposed_department", "ntird") or "ntird")
    return qa_department(code)


@lru_cache(maxsize=1)
def lga_map():
    path = Path(settings.BASE_DIR) / "staff" / "data" / "lgas.json"
    try:
        data = json.loads(path.read_text())["states"]
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def lgas_for(state):
    if state is None:
        return []
    name = state.name if hasattr(state, "name") else str(state)
    data = lga_map()
    for key, values in data.items():
        if key.lower().replace(" state", "") == name.lower().replace(" state", ""):
            return list(values)
    if name.lower().startswith("fct") or "abuja" in name.lower():
        return list(data.get("FCT", data.get("Abuja", [])))
    return []


def validate_lga(state, lga):
    if not lga:
        return
    options = lgas_for(state)
    if options and lga not in options:
        raise ValidationError(f"'{lga}' is not an LGA of {state}. Choose one from the list.")


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------
STOP_WORDS = {"of", "and", "the", "for", "&", "in", "at"}


def suggest_code(name):
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name or "") if w and w.lower() not in STOP_WORDS]
    base = "".join(w[0] for w in words).upper()[:6]
    if len(base) < 3:
        base = re.sub(r"[^A-Z0-9]", "", (name or "").upper())[:3].ljust(3, "X")
    return base


def allocate_code(name, preferred=""):
    """A unique 3–6 character code (Q-02), preferring `preferred`."""
    preferred = (preferred or "").strip().upper()
    if preferred:
        if not re.fullmatch(r"[A-Z0-9]{3,6}", preferred):
            raise ValidationError("Codes are 3 to 6 uppercase letters or digits, for example KDP.")
        if Institution.unscoped.filter(code=preferred).exists():
            raise ValidationError(f"The code {preferred} is already in use.")
        return preferred
    base = suggest_code(name)
    if not Institution.unscoped.filter(code=base).exists():
        return base
    for n in range(2, 1000):
        suffix = str(n)
        candidate = (base[: 6 - len(suffix)] + suffix)
        if not Institution.unscoped.filter(code=candidate).exists():
            return candidate
    raise ValidationError("Could not allocate a free code; enter one.")


# ---------------------------------------------------------------------------
# Lifecycle flow
# ---------------------------------------------------------------------------
def lifecycle(inst):
    """The institution's lifecycle FlowInstance, created on first use.
    Phase 1 rows (no instance yet) are adopted at their current status."""
    instance = flow.instance_for(inst, LIFECYCLE)
    if instance is not None:
        return instance
    if flow.current_version(LIFECYCLE) is None:
        return None
    existing_status = inst.status
    instance = flow.start(LIFECYCLE, inst)
    if existing_status and existing_status != Institution.PROPOSED:
        instance = flow.force_stage(instance, existing_status, None,
                                    remark="Existing record adopted into the lifecycle flow.",
                                    metadata={"adopted": True})
    return instance


def lifecycle_move(inst, action, actor, *, remark="", confirmed=False):
    instance = lifecycle(inst)
    if instance is None:
        return None
    if confirmed:
        instance = flow.apply_confirmed(instance, action, actor, remark=remark)
    else:
        instance = flow.transition(instance, action, actor, remark=remark)
    inst.refresh_from_db(fields=["status", "updated_at"])
    return instance


# ---------------------------------------------------------------------------
# Creating institutions (Add TVET, self-registration, import)
# ---------------------------------------------------------------------------
CONTACT_KINDS = [k for k, _l in InstitutionContact.KIND_CHOICES]


@transaction.atomic
def create_institution(data, *, actor=None, origin="nbte", activate_admin=False, admin_password=None):
    """Create a proposed institution with its contacts, address and primary
    portal account. `data` keys: name, code, ownership, institution_type,
    category, state, lga, year_established, cac_rc_number, website,
    contacts {kind: {"value", "name"}}, address {line1, town, lga, state},
    admin {first_name, last_name, email, phone, job_title}."""
    from accounts.models import Role, User

    dept = proposed_department()
    if dept is None:
        raise ValidationError("The department that handles proposed institutions (NTIRD) does not exist.")
    admin = data.get("admin") or {}
    email = (admin.get("email") or "").strip().lower()
    if email and User.objects.filter(email__iexact=email).exists():
        raise ValidationError(f"{email} already has an account. Use a different e-mail for the administrator.")
    if email:  # §9: the Institutional Admin nominee's department (Phase 5 wiring)
        import config

        required = str(config.get("qa.institution.admin_required_department", "any") or "any").strip()
        if required.lower() != "any" and (admin.get("department") or "").strip().lower() != required.lower():
            raise ValidationError({"admin_department": f"The administrator must come from the {required} department "
                                                       "(qa.institution.admin_required_department)."})
    inst = Institution(
        code=allocate_code(data["name"], data.get("code", "")),
        name=data["name"].strip(),
        ownership=data.get("ownership"), institution_type=data.get("institution_type"),
        category=data.get("category"), state=data.get("state"), lga=data.get("lga", "") or "",
        year_established=data.get("year_established"), cac_rc_number=data.get("cac_rc_number", "") or "",
        website=data.get("website", "") or "", status=Institution.PROPOSED, handling_department=dept,
        origin=origin, portal_enabled=True, desk_officer=actor if getattr(actor, "user_type", "") == "staff" else None,
    )
    inst.recompute_zone()
    inst.full_clean(exclude=["handling_department"])
    inst.save()
    lifecycle(inst)
    for kind, item in (data.get("contacts") or {}).items():
        if kind in CONTACT_KINDS and item and (item.get("value") or item.get("name")):
            InstitutionContact.unscoped.create(institution=inst, kind=kind, value=(item.get("value") or "").strip(),
                                               name=(item.get("name") or "").strip())
    address = data.get("address") or {}
    if address.get("line1"):
        InstitutionAddress.unscoped.create(
            institution=inst, kind=address.get("kind") or "permanent_site", line1=address["line1"],
            town=address.get("town", ""), lga=address.get("lga", "") or inst.lga, state=address.get("state") or inst.state)
    user = None
    if email:
        if admin.get("phone") and not InstitutionContact.unscoped.filter(institution=inst, kind="phone").exists():
            InstitutionContact.unscoped.create(institution=inst, kind="phone", value=admin["phone"].strip(),
                                               name=f"{admin.get('first_name', '')} {admin.get('last_name', '')}".strip())
        user = User(email=email, first_name=(admin.get("first_name") or "").strip(),
                    last_name=(admin.get("last_name") or "").strip(),
                    user_type=User.INSTITUTION, institution=inst, role=Role.objects.filter(code="inst-admin").first(),
                    is_active=bool(activate_admin), must_change_password=False)
        if admin_password:
            user.set_password(admin_password)
        else:
            user.set_unusable_password()
        user.save()
        InstitutionUser.unscoped.create(institution=inst, user=user, institution_role=user.role,
                                        is_primary_contact=True, job_title=(admin.get("job_title") or "")[:120],
                                        institution_department=(admin.get("department") or "")[:120],
                                        invited_at=timezone.now(), invited_by=actor if getattr(actor, "pk", None) else None)
    label = {"nbte": "added by NBTE", "self": "self-registered on the portal", "import": "created by bulk import"}
    timeline.record(inst, "institution.created", actor=actor, visibility="public",
                    public_text=f"{inst.name} was registered as a proposed institution ({inst.code}).",
                    internal_text=f"Institution {label.get(origin, origin)}; handled by {dept.name}.")
    return inst


# ---------------------------------------------------------------------------
# Contacts and addresses (history kept)
# ---------------------------------------------------------------------------
def set_contact(inst, kind, value, name="", *, actor=None):
    if kind not in CONTACT_KINDS:
        raise ValidationError("Choose a contact type.")
    now = timezone.now()
    InstitutionContact.unscoped.filter(institution=inst, kind=kind, is_current=True).update(
        is_current=False, ended_at=now, updated_at=now)
    return InstitutionContact.unscoped.create(institution=inst, kind=kind, value=(value or "").strip(),
                                              name=(name or "").strip(), is_primary=True)


def set_address(inst, kind, *, line1, town="", lga="", state=None, gps="", actor=None):
    now = timezone.now()
    InstitutionAddress.unscoped.filter(institution=inst, kind=kind, is_current=True).update(
        is_current=False, ended_at=now, updated_at=now)
    return InstitutionAddress.unscoped.create(institution=inst, kind=kind, line1=line1.strip(), town=town.strip(),
                                              lga=lga, state=state, gps=gps.strip(), started_at=now)


def contacts_of(inst):
    return list(InstitutionContact.unscoped.filter(institution=inst, is_current=True).order_by("kind", "-id"))


def contact_history(inst):
    return list(InstitutionContact.unscoped.filter(institution=inst).order_by("kind", "-created_at"))


def addresses_of(inst, current=True):
    qs = InstitutionAddress.unscoped.filter(institution=inst).select_related("state")
    if current:
        qs = qs.filter(is_current=True)
    return list(qs.order_by("kind", "-started_at"))


def members_of(inst):
    return list(InstitutionUser.unscoped.filter(institution=inst).select_related("user", "institution_role")
                .order_by("-is_primary_contact", "user__first_name"))


def programmes_of(inst, *, include_dropped=True):
    qs = Programme.unscoped.filter(institution=inst).select_related("level", "category")
    if not include_dropped:
        qs = qs.exclude(status="dropped")
    return list(qs.order_by("name"))


# ---------------------------------------------------------------------------
# Documents (Requirement 1)
# ---------------------------------------------------------------------------
def requirements_for(inst, process=DocumentRequirement.PROCESS_REGISTRATION, *, ownership=None):
    """Active DocumentRequirements for the institution's ownership and type
    (a blank ownership/type on the rule means "any"). One row per kind: the
    mandatory rule wins over an optional one."""
    ownership = ownership if ownership is not None else inst.ownership
    qs = (DocumentRequirement.objects.filter(process=process, is_active=True, document_kind__is_active=True)
          .select_related("document_kind", "reviewing_department", "ownership", "institution_type"))
    rows = {}
    for req in qs:
        if req.ownership_id and (ownership is None or req.ownership_id != ownership.pk):
            continue
        if req.institution_type_id and req.institution_type_id != inst.institution_type_id:
            continue
        current = rows.get(req.document_kind_id)
        if current is None or (req.is_mandatory and not current.is_mandatory):
            rows[req.document_kind_id] = req
    return sorted(rows.values(), key=lambda r: (not r.is_mandatory, r.document_kind.sort_order,
                                               r.document_kind.name))


def latest_documents(inst):
    """{kind_id: newest live-or-returned InstitutionDocument}."""
    out = {}
    for doc in (InstitutionDocument.unscoped.filter(institution=inst)
                .exclude(status=InstitutionDocument.SUPERSEDED).select_related("kind")
                .order_by("kind_id", "-version")):
        out.setdefault(doc.kind_id, doc)
    return out


def checklist(inst, process=DocumentRequirement.PROCESS_REGISTRATION):
    """One row per required document with its state:
    missing · submitted · accepted · returned · expired (refresh due)."""
    docs = latest_documents(inst)
    today = timezone.localdate()
    rows = []
    for req in requirements_for(inst, process):
        doc = docs.get(req.document_kind_id)
        state = "missing"
        if doc is not None:
            state = doc.status
            if doc.status == InstitutionDocument.ACCEPTED and req.refresh_after_months and doc.accepted_at:
                due = (doc.accepted_at + datetime.timedelta(days=30 * req.refresh_after_months)).date()
                if due <= today:
                    state = "expired"
            if doc.valid_until and doc.valid_until < today:
                state = "expired"
        rows.append({"requirement": req, "kind": req.document_kind, "document": doc, "state": state,
                     "mandatory": req.is_mandatory, "department": req.reviewing_department})
    return rows


def checklist_summary(rows):
    mandatory = [r for r in rows if r["mandatory"]]
    ready = [r for r in mandatory if r["state"] in ("submitted", "accepted")]
    return {"mandatory": len(mandatory), "ready": len(ready),
            "missing": [r for r in mandatory if r["state"] in ("missing", "returned", "expired")],
            "complete": len(ready) == len(mandatory),
            "percent": int(100 * len(ready) / len(mandatory)) if mandatory else 100}


def max_document_mb(kind):
    return min(kind.max_mb or 10, cfg_int("qa.documents.max_upload_mb", 10))


@transaction.atomic
def upload_document(inst, kind, upload, *, user, application=None):
    if not kind.is_active:
        raise ValidationError("This document type is no longer collected.")
    validate_upload(upload, extensions=kind.extensions(), max_mb=max_document_mb(kind), label="document")
    previous = (InstitutionDocument.unscoped.select_for_update()
                .filter(institution=inst, kind=kind).order_by("-version").first())
    version = (previous.version + 1) if previous else 1
    doc = InstitutionDocument(institution=inst, kind=kind, version=version, supersedes=previous,
                              status=InstitutionDocument.SUBMITTED, uploaded_by=user, application=application)
    doc.file = upload
    doc.save()
    if previous is not None and previous.status != InstitutionDocument.SUPERSEDED:
        previous.status = InstitutionDocument.SUPERSEDED
        previous.save(update_fields=["status", "updated_at"])
    timeline.record(inst, "document.uploaded", subject=doc, actor=user, visibility="public",
                    public_text=f"{kind.name} uploaded (version {version}).")
    _maybe_lift_ownership_hold(inst, user)
    return doc


@transaction.atomic
def review_document(doc, decision, *, user, remark=""):
    """Accept or return one document (clearance departments)."""
    if doc.status not in (InstitutionDocument.SUBMITTED, InstitutionDocument.ACCEPTED,
                          InstitutionDocument.RETURNED):
        raise ValidationError("Only the current version of a document can be reviewed.")
    now = timezone.now()
    if decision == "accept":
        doc.status = InstitutionDocument.ACCEPTED
        doc.accepted_by, doc.accepted_at = user, now
    elif decision == "return":
        if len((remark or "").strip()) < 3:
            raise ValidationError("Say what is wrong with the document.")
        doc.status = InstitutionDocument.RETURNED
    else:
        raise ValidationError("Choose accept or return.")
    doc.reviewed_by, doc.reviewed_at, doc.review_remark = user, now, (remark or "").strip()
    doc.save(update_fields=["status", "accepted_by", "accepted_at", "reviewed_by", "reviewed_at",
                            "review_remark", "updated_at"])
    if decision == "return":
        timeline.record(doc.institution, "document.returned", subject=doc, actor=user, visibility="public",
                        public_text=f"{doc.kind.name} was returned: {doc.review_remark}")
        notify_institution(doc.institution, "institution.document_returned",
                           {"document": doc.kind.name, "remark": doc.review_remark,
                            "department": getattr(user.department, "name", "NBTE")}, url="/documents/")
    else:
        timeline.record(doc.institution, "document.accepted", subject=doc, actor=user, visibility="public",
                        public_text=f"{doc.kind.name} was accepted.")
    return doc


# ---------------------------------------------------------------------------
# Ownership change: re-evaluate documents and hold the open application
# ---------------------------------------------------------------------------
def newly_required_kinds(inst, new_ownership):
    before = {r.document_kind_id for r in requirements_for(inst) if r.is_mandatory}
    after = [r for r in requirements_for(inst, ownership=new_ownership) if r.is_mandatory]
    docs = latest_documents(inst)
    out = []
    for req in after:
        if req.document_kind_id in before:
            continue
        doc = docs.get(req.document_kind_id)
        if doc is None or doc.status not in (InstitutionDocument.SUBMITTED, InstitutionDocument.ACCEPTED):
            out.append(req.document_kind)
    return out


def hold_open_application(inst, actor, reason):
    from accreditation.models import Application
    from qa_core.models import Suspension
    from django.contrib.contenttypes.models import ContentType

    app = (Application.unscoped.filter(institution=inst).exclude(status__in=("ssq_submitted", "withdrawn"))
           .order_by("-created_at").first())
    if app is None:
        return None
    instance = flow.instance_for(app, "onboarding")
    if instance is not None and not instance.paused:
        instance.paused = True
        instance.paused_at = timezone.now()
        instance.save(update_fields=["paused", "paused_at", "updated_at"])
    hold = Suspension.objects.create(content_type=ContentType.objects.get_for_model(app),
                                     object_id=str(app.pk), reason=reason, placed_by=actor)
    timeline.record(inst, "application.held", subject=app, actor=actor, visibility="public",
                    public_text="Your application is on hold until the documents your new ownership needs are "
                                "uploaded.", internal_text=reason)
    return hold


def active_hold(app):
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import Suspension

    return (Suspension.objects.filter(content_type=ContentType.objects.get_for_model(app), object_id=str(app.pk),
                                      lifted_at__isnull=True).order_by("-placed_at").first())


def lift_hold(app, actor, *, note=""):
    hold = active_hold(app)
    if hold is None:
        return None
    hold.lifted_by = actor
    hold.lifted_at = timezone.now()
    hold.save(update_fields=["lifted_by", "lifted_at", "updated_at"])
    instance = flow.instance_for(app, "onboarding")
    if instance is not None and instance.paused:
        if instance.paused_at:
            instance.paused_seconds += int((timezone.now() - instance.paused_at).total_seconds())
        instance.paused = False
        instance.paused_at = None
        instance.save(update_fields=["paused", "paused_at", "paused_seconds", "updated_at"])
    timeline.record(app.institution, "application.hold_lifted", subject=app, actor=actor, visibility="public",
                    public_text="The hold on your application was lifted.", internal_text=note)
    return hold


def _maybe_lift_ownership_hold(inst, user):
    """Lift an ownership hold automatically once nothing mandatory is missing."""
    from accreditation.models import Application

    app = (Application.unscoped.filter(institution=inst).exclude(status__in=("ssq_submitted", "withdrawn"))
           .order_by("-created_at").first())
    if app is None:
        return
    hold = active_hold(app)
    if hold is None or not hold.reason.startswith("Ownership changed"):
        return
    if checklist_summary(checklist(inst))["complete"]:
        lift_hold(app, hold.placed_by, note="Lifted automatically: every newly required document is in.")


# ---------------------------------------------------------------------------
# Portal accounts: credentials and impersonation
# ---------------------------------------------------------------------------
def credentials_cooldown_left(user):
    from django.core.cache import cache

    from qa_core.portal_views import _cooldown_key

    return bool(cache.get(_cooldown_key(user)))


def send_credentials(inst, *, actor, user=None, request=None):
    """Activate the account and e-mail a one-time set-password link (never a
    plaintext password). Respects qa.credentials.resend_cooldown_minutes."""
    from django.core.cache import cache
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from qa_core import mail
    from qa_core.hosts import portal_absolute, portal_reverse
    from qa_core.portal_tokens import link_valid_hours, portal_token_generator
    from qa_core.portal_views import _cooldown_key

    user = user or inst.primary_user()
    if user is None:
        raise ValidationError("This institution has no portal account yet. Add an administrator first.")
    if not inst.portal_enabled or inst.status == Institution.DELISTED:
        raise ValidationError("Portal access is switched off for this institution.")
    cooldown = cfg_int("qa.credentials.resend_cooldown_minutes", 30)
    if cooldown and cache.get(_cooldown_key(user)):
        raise ValidationError(f"Credentials were sent less than {cooldown} minutes ago. Try again later.")
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = portal_token_generator.make_token(user)
    link = portal_absolute(portal_reverse("qa_portal:password_set", uidb64=uid, token=token), request)
    mail.send("Set your NBTE Quality Assurance Portal password", [user.email], "emails/qa_set_password.html",
              {"user": user, "set_url": link, "valid_hours": link_valid_hours(), "institution": inst},
              category="reset")
    if cooldown:
        cache.set(_cooldown_key(user), timezone.now().isoformat(), cooldown * 60)
    InstitutionUser.unscoped.filter(user=user).update(credentials_last_sent_at=timezone.now())
    timeline.record(inst, "credentials.sent", actor=actor, visibility="internal",
                    internal_text=f"Set-password link e-mailed to {user.email}.")
    return user


def impersonation_target(inst):
    """The account an impersonation session views the portal as."""
    for link in members_of(inst):
        if link.user.is_active:
            return link.user
    return None


def start_impersonation(inst, *, staff, reason, write_mode=False):
    if len((reason or "").strip()) < 5:
        raise ValidationError("Give a reason for viewing as this institution.")
    view_as = impersonation_target(inst)
    if view_as is None:
        raise ValidationError("This institution has no active portal account to view as. Send its login "
                              "credentials first.")
    if write_mode and not (staff.is_superuser and cfg_bool("qa.impersonation.allow_write")):
        raise ValidationError("Write mode needs a superuser and qa.impersonation.allow_write.")
    minutes = max(1, cfg_int("qa.impersonation.max_minutes", 30))
    now = timezone.now()
    session = ImpersonationSession.unscoped.create(
        institution=inst, staff=staff, view_as=view_as, reason=reason.strip(), write_mode=bool(write_mode),
        token=secrets.token_urlsafe(32), started_at=now, expires_at=now + datetime.timedelta(minutes=minutes))
    audit.log("other", session, actor=staff, note=f"impersonation started ({minutes} min): {reason.strip()[:120]}")
    timeline.record(inst, "impersonation.started", subject=session, actor=staff, visibility="internal",
                    internal_text=f"{staff.get_full_name() or staff.email} viewed the portal as the institution "
                                  f"(read-only, {minutes} min). Reason: {reason.strip()}")
    return session


def end_impersonation(session, reason="stopped"):
    if session.ended_at is None:
        session.ended_at = timezone.now()
        session.end_reason = reason
        session.save(update_fields=["ended_at", "end_reason", "updated_at"])
        audit.log("other", session, actor=session.staff, note=f"impersonation ended ({reason}); "
                                                              f"{session.pages_viewed} page(s) viewed")
    return session


# ---------------------------------------------------------------------------
# Notifications to institution users
# ---------------------------------------------------------------------------
def institution_users(inst):
    from accounts.models import User

    return list(User.objects.filter(institution=inst, user_type="institution", is_active=True))


def notify_institution(inst, event, context, *, url=""):
    from qa_core import notify

    ctx = {"institution": inst.name, "code": inst.code, **context}
    notify.dispatch(event, ctx, {"institution_users": institution_users(inst)}, url=url)


def staff_in_department(dept, *, perm=None, roles=None):
    from accounts.models import User

    if dept is None:
        return []
    users = User.objects.filter(is_active=True, user_type="staff", department=dept).select_related("role")
    if roles:
        users = users.filter(role__code__in=roles)
    out = list(users[:200])
    if perm:
        out = [u for u in out if u.has_perm(perm)]
    return out


def notify_role(event, context, users, *, url=""):
    from qa_core import notify

    notify.dispatch(event, context, {"role": users}, url=url)


def impersonation_session_for_token(token):
    """Bind a one-time view-as token (portal host). Returns the session or None."""
    with transaction.atomic():
        session = (ImpersonationSession.unscoped.select_for_update().select_related("institution", "view_as")
                   .filter(token=token, token_used_at__isnull=True, ended_at__isnull=True).first())
        if session is None or not session.is_live:
            return None
        session.token_used_at = timezone.now()
        session.save(update_fields=["token_used_at", "updated_at"])
    return session


def ensure_unique_name(name, *, exclude=None):
    qs = Institution.unscoped.filter(name__iexact=(name or "").strip())
    if exclude:
        qs = qs.exclude(pk=exclude)
    if qs.exists():
        raise ValidationError("An institution with this name is already registered with NBTE. If it is yours, "
                              "contact NBTE instead of registering again.")


@transaction.atomic
def nominee_problems(inst, data):
    """§9 eligibility rules for institution nominees (v1.1, wired in Phase 5):

    * Institutional Admin — ``qa.institution.admin_required_department``
      (``any`` or a department name) against the department on the nomination;
    * Chief Executive — when ``qa.institution.chief_executive_required_position``
      is true, the nominee must be the institution's head-of-institution
      contact (same name, and same e-mail when the contact has one).

    Returns {field: message}. No override: change the setting instead."""
    import config

    problems = {}
    role = data.get("role")
    if role == "inst-admin":
        required = str(config.get("qa.institution.admin_required_department", "any") or "any").strip()
        given = (data.get("institution_department") or "").strip()
        if required.lower() != "any" and given.lower() != required.lower():
            problems["institution_department"] = (
                f"An Institutional Administrator must come from the {required} department "
                "(qa.institution.admin_required_department).")
    elif role == "inst-chief-executive":
        flag = config.get("qa.institution.chief_executive_required_position", True)
        if str(flag).strip().lower() in ("1", "true", "yes", "on") or flag is True:
            head = (InstitutionContact.unscoped.filter(institution=inst, kind="head_of_institution", is_current=True)
                    .order_by("-pk").first())
            full = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip().lower()
            email = (data.get("email") or "").strip().lower()
            if head is None or not (head.name or "").strip():
                problems["first_name"] = ("Record the head of the institution first (Institution → Contacts): the "
                                          "Chief Executive must be that person "
                                          "(qa.institution.chief_executive_required_position).")
            else:
                head_name = " ".join((head.name or "").lower().split())
                name_ok = head_name == " ".join(full.split()) or set(full.split()) <= set(head_name.split())
                email_ok = not ("@" in (head.value or "")) or head.value.strip().lower() == email
                if not name_ok or not email_ok:
                    problems["first_name"] = (
                        f"The Chief Executive must be the institution's head ({head.name}"
                        + (f", {head.value}" if "@" in (head.value or "") else "")
                        + ") — qa.institution.chief_executive_required_position.")
    return problems


def invite_user(inst, data, *, actor, request=None):
    """Add a portal user to the institution and e-mail a set-password link."""
    from accounts.models import Role, User

    problems = nominee_problems(inst, data)
    if problems:
        raise ValidationError(problems)
    email = data["email"].strip().lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError(f"{email} already has an account.")
    role = Role.objects.filter(code=data["role"]).first()
    user = User(email=email, first_name=data["first_name"].strip(), last_name=data["last_name"].strip(),
                user_type=User.INSTITUTION, institution=inst, role=role, is_active=True,
                must_change_password=False)
    user.set_unusable_password()
    user.save()
    InstitutionUser.unscoped.create(institution=inst, user=user, institution_role=role, is_primary_contact=False,
                                    job_title=(data.get("job_title") or "")[:120],
                                    institution_department=(data.get("institution_department") or "")[:120],
                                    nominated_grade_level=data.get("grade_level"), invited_at=timezone.now(),
                                    invited_by=actor if getattr(actor, "pk", None) else None)
    send_credentials(inst, actor=actor, user=user, request=request)
    timeline.record(inst, "users.invited", actor=actor, visibility="public",
                    public_text=f"{user.get_full_name()} was invited as {getattr(role, 'name', 'a user')}.")
    return user


def add_proposed_programme(inst, name, level, category=None, *, application=None, user=None):
    programme = Programme.unscoped.create(institution=inst, name=name, level=level, category=category,
                                          status="proposed", source_application=application)
    timeline.record(inst, "programme.proposed", subject=programme, actor=user, visibility="public",
                    public_text=f"Programme proposed: {programme.name} ({programme.level.name}).")
    return programme


def remove_proposed_programme(programme, *, user=None):
    inst = programme.institution
    label = f"{programme.name} ({programme.level.name})"
    programme.delete()
    timeline.record(inst, "programme.removed", actor=user, visibility="public",
                    public_text=f"Programme removed from the list: {label}.")
