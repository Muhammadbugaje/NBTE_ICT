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
from django.core.exceptions import PermissionDenied, ValidationError
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


# X49 slice (Phase 0): the state list says "FCT" while the bundled LGA file
# says "Federal Capital Territory"; these names all mean the capital.
FCT_ALIASES = ("fct", "f.c.t", "f.c.t.", "abuja", "fct abuja", "fct, abuja", "federal capital territory")


def _state_key(name):
    return (name or "").strip().lower().replace(" state", "")


def lgas_for(state):
    """LGA names of ``state`` (v2.1 X49: from the staff.LGA table, cached; the
    bundled file is the fallback until `seed_nbte --only geo` has run)."""
    from staff import geo

    return geo.lga_names(state)


def lgas_from_file(state):
    if state is None:
        return []
    name = state.name if hasattr(state, "name") else str(state)
    data = lga_map()
    wanted = _state_key(name)
    for key, values in data.items():
        if _state_key(key) == wanted:
            return list(values)
    if wanted in FCT_ALIASES or wanted.startswith("fct"):
        for key, values in data.items():
            if _state_key(key) in FCT_ALIASES:
                return list(values)
    return []


def lga_groups():
    """[(state name, [LGAs])] for the no-JS fallback (an <optgroup> per state)."""
    from staff import geo

    groups = geo.lga_groups()
    if groups is not None:
        return groups
    return sorted(((k, list(v)) for k, v in lga_map().items()), key=lambda x: x[0])


def validate_lga(state, lga):
    """The LGA must belong to ``state`` (case-insensitive). An LGA of another
    state is rejected even when the state has no list in the bundled file."""
    if not lga:
        return
    from staff import geo

    if geo._table_ready() and state is not None and hasattr(state, "pk"):
        geo.validate_lga(state, lga)
        return
    options = lgas_for(state)
    if options:
        if lga.strip().lower() not in {o.lower() for o in options}:
            raise ValidationError(f"'{lga}' is not an LGA of {state}. Choose one from the list.")
        return
    for key, values in lga_map().items():
        if lga.strip().lower() in {v.lower() for v in values}:
            raise ValidationError(f"'{lga}' is an LGA of {key}, not {state}.")


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
def create_institution(data, *, actor=None, origin="nbte", activate_admin=False, admin_password=None, request=None):
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
                    user_type=User.INSTITUTION, institution=inst,
                    role=Role.objects.filter(code=admin.get("role") if admin.get("role") in (
                        "inst-admin", CHIEF_EXECUTIVE_ROLE) else "inst-admin").first(),
                    is_active=bool(activate_admin), must_change_password=False)
        if admin_password:
            user.set_password(admin_password)
        else:
            user.set_unusable_password()
        user.save()
        role_code = getattr(user.role, "code", "")
        InstitutionUser.unscoped.create(institution=inst, user=user, institution_role=user.role,
                                        is_primary_contact=True, job_title=(admin.get("job_title") or "")[:120],
                                        institution_department=(admin.get("department") or "")[:120],
                                        invited_at=timezone.now(), invited_by=actor if getattr(actor, "pk", None) else None,
                                        account_kind=(InstitutionUser.CHIEF_EXECUTIVE if role_code == CHIEF_EXECUTIVE_ROLE
                                                      else InstitutionUser.ICT_HEAD))
        _record_management(inst, "head-of-ict" if role_code != CHIEF_EXECUTIVE_ROLE else "chief-executive",
                           full_name=user.get_full_name(), email=email, phone=admin.get("phone", ""), user=user,
                           actor=actor)
    ce = data.get("chief_executive") or {}
    if ce.get("email"):
        invite_chief_executive(inst, ce, actor=actor or user, request=request)
    label = {"nbte": "added by NBTE", "self": "self-registered on the portal", "import": "created by bulk import"}
    timeline.record(inst, "institution.created", actor=actor, visibility="public",
                    public_text=f"{inst.name} was registered as a proposed institution ({inst.code}).",
                    internal_text=f"Institution {label.get(origin, origin)}; handled by {dept.name}.")
    transaction.on_commit(lambda: notify_registration_reviewers(inst.pk))
    return inst


def notify_registration_reviewers(institution_id):
    """Alert the NBTE leads responsible for new institution registrations."""
    import org
    from accounts.models import User
    from notifications.models import Notification
    from notifications.services import notify
    from qa_core.departments import qa_department

    inst = Institution.unscoped.select_related("handling_department").filter(pk=institution_id).first()
    if inst is None:
        return
    recipients = {}
    staff = User.objects.filter(is_active=True, user_type=User.STAFF).select_related("role", "department")
    for user in staff:
        if org.is_es(user) or org.is_super_admin(user):
            recipients[user.pk] = user
    ict = qa_department("ict")
    for dept in (ict, inst.handling_department):
        if dept and dept.director_id:
            user = User.objects.filter(pk=dept.director_id, is_active=True, user_type=User.STAFF).first()
            if user:
                recipients[user.pk] = user
    for user in recipients.values():
        notify(user, title="New proposed institution", body=f"{inst.name} ({inst.code}) was registered and needs "
               "NBTE oversight assignment.", url=f"/qa/institutions/{inst.code}/", category=Notification.QA)
    queue_ict_supervisor_task(institution_id)


def rotate_oversight_open(inst):
    """An ICT supervisor is already appointed (open oversight_ict work)."""
    from assignments.models import WorkAssignment

    return WorkAssignment.objects.filter(
        institution=inst, kind=WorkAssignment.OVERSIGHT_ICT, status__in=WorkAssignment.OPEN).exists()


def queue_ict_supervisor_task(institution_id):
    """X19: a new institution opens "Assign an ICT supervisor" for the ICT
    department — a task on the director's list with a rotation suggestion,
    linked in QA so it closes itself when the supervisor is appointed."""
    from .models import Institution

    inst = (Institution.unscoped.filter(pk=institution_id, status__in=("proposed", "under_review")).first())
    if inst is None or rotate_oversight_open(inst):
        return
    _open_oversight_task(inst)


def _open_oversight_task(inst):
    """Create (or reuse) the oversight task; suggested person from rotation."""
    from django.contrib.contenttypes.models import ContentType

    import assignments.rotation as rotation
    from accounts.models import User
    from assignments.models import WorkAssignment
    from qa_core.models import QATaskLink
    from qa_core.task_bridge import bridge_active, system_user
    from tasks import services as task_services
    from tasks.models import TaskAssignee, TaskCategory

    ct = ContentType.objects.get_for_model(Institution, for_concrete_model=True)
    if QATaskLink.objects.filter(content_type=ct, object_id=inst.pk, stage_code="oversight_ict",
                                 closed_at__isnull=True).exists():
        return
    ict = qa_department("ict")
    if ict is None:
        return
    director = (User.objects.filter(pk=ict.director_id, is_active=True, user_type=User.STAFF).first()
                if ict.director_id else None)
    suggested = ""
    try:
        s = rotation.suggest_ict_supervisor(inst, record=True)
        if s.suggested is not None:
            suggested = (s.suggested.user.get_full_name() or s.suggested.user.email) if getattr(
                s.suggested, "user", None) else s.suggested.name
    except Exception:
        pass
    sys_user = system_user()
    description = ("Assign an ICT supervisor for the new institution. Open the institution page in QA and "
                   "use the ICT oversight card."
                   + (f"\n\nRotation suggests: {suggested}." if suggested else ""))
    with bridge_active():
        task = task_services.create_task(
            created_by=sys_user, title=f"[QA] Assign an ICT supervisor — {inst.name}"[:200],
            description=description,
            category=TaskCategory.objects.filter(code="qa-onboarding").first(),
            owner_department=ict, priority="high", requires_review=False)
        if director is not None:
            if not TaskAssignee.objects.filter(task=task, user=director).exists():
                TaskAssignee.objects.create(task=task, user=director, state=TaskAssignee.ASSIGNED,
                                            assigned_by=sys_user)
                task_services.log_activity(task, "assignee_added", actor=sys_user,
                                           message=f"Assigned by QA to {director.get_full_name() or director.email}",
                                           metadata={"assignee_id": director.pk})
        QATaskLink.objects.create(task=task, content_type=ct, object_id=inst.pk, stage_code="oversight_ict",
                                  kind="stage_action")
        if director is not None:
            task_services.open_task(task, sys_user)
    audit.log("oversight_task_opened", inst, actor=None,
              changes={"task_reference": getattr(task, "reference", ""), "suggested": suggested})


def _record_management(inst, role_code, *, full_name, email="", phone="", title="", user=None, actor=None):
    """X13: the CE / Head of ICT are also management staff (linked to their account)."""
    from .models import ManagementRole, ManagementStaff

    role = ManagementRole.objects.filter(code=role_code, is_active=True).first()
    if role is None or not (full_name or "").strip():
        return None
    ManagementStaff.unscoped.filter(institution=inst, role=role, is_current=True).update(
        is_current=False, end_date=timezone.localdate(), updated_at=timezone.now())
    return ManagementStaff.unscoped.create(institution=inst, role=role, title=(title or "")[:30],
                                           full_name=full_name.strip()[:150], email=(email or "").strip().lower(),
                                           phone=(phone or "").strip()[:30], is_current=True, user=user,
                                           start_date=timezone.localdate(),
                                           added_by=actor if getattr(actor, "pk", None) else None)


def _split_name(full_name):
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    return " ".join(parts[:-1]) or parts[0], parts[-1] if len(parts) > 1 else ""


def _make_set_password_link(user, request):
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from qa_core.hosts import portal_absolute, portal_reverse
    from qa_core.portal_tokens import link_valid_hours, portal_token_generator

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = portal_token_generator.make_token(user)
    path = portal_reverse("qa_portal:password_set", uidb64=uid, token=token)
    return portal_absolute(path, request), link_valid_hours()


@transaction.atomic
def invite_chief_executive(inst, ce, *, actor=None, request=None):
    """X12 (F.13): create the Chief Executive's account (no password) and
    e-mail a set-password invitation. Replaces an earlier, unused CE link."""
    from accounts.models import Role, User

    email = (ce.get("email") or "").strip().lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"ce_email": f"{email} already has an account."})
    first, last = _split_name(ce.get("full_name"))
    role = Role.objects.filter(code=CHIEF_EXECUTIVE_ROLE).first()
    user = User(email=email, first_name=first[:150], last_name=last[:150], user_type=User.INSTITUTION,
                institution=inst, role=role, is_active=True, must_change_password=False)
    user.set_unusable_password()
    user.save()
    InstitutionUser.unscoped.filter(institution=inst, account_kind=InstitutionUser.CHIEF_EXECUTIVE,
                                    is_active_link=True).update(is_active_link=False, updated_at=timezone.now())
    InstitutionUser.unscoped.create(institution=inst, user=user, institution_role=role, is_primary_contact=False,
                                    job_title="Chief Executive", invited_at=timezone.now(),
                                    invited_by=actor if getattr(actor, "pk", None) else None,
                                    account_kind=InstitutionUser.CHIEF_EXECUTIVE)
    _record_management(inst, "chief-executive", full_name=ce.get("full_name", ""), email=email,
                       phone=ce.get("phone", ""), title=ce.get("title", ""), user=user, actor=actor)
    # v2.2: the setup link is never shown to the registrant (Head of ICT) —
    # it goes only to the Chief Executive's own e-mail, automatically unless
    # NBTE switched that off; NBTE staff with access can send it again.
    if ce_auto_email():
        transaction.on_commit(lambda: _send_ce_invite(inst, user, actor, request, None))
    else:
        timeline.record(inst, "credentials.ce_manual", visibility="internal",
                        internal_text="Chief Executive set-up link held for NBTE to send (automatic e-mail is off).")
    timeline.record(inst, "users.invited", actor=actor if getattr(actor, "pk", None) else None, visibility="public",
                    public_text=f"{user.get_full_name()} was invited as the Chief Executive.")
    return user


def ce_auto_email():
    """v2.2 setting qa.onboarding.ce_auto_email (default on)."""
    value = cfg_value("qa.onboarding.ce_auto_email", True)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def cfg_value(key, default=None):
    import config
    return config.get(key, default)


def _send_ce_invite(inst, user, actor, request, prepared_link=None):
    try:
        send_credentials(inst, actor=actor, user=user, request=request, reveal_once=False,
                         prepared_link=prepared_link)
    except ValidationError:
        logger.warning("CE invitation for %s not sent (cool-down or portal off)", inst.code)


def resend_ce_invitation(inst, *, actor, request=None):
    """X12: the Head of ICT resends the CE's invitation (rate-limited by
    qa.onboarding.ce_invite_resend_minutes; the credentials cool-down also applies)."""
    from django.core.cache import cache

    if not getattr(actor, "pk", None) or not (InstitutionUser.unscoped.filter(
            institution=inst, user=actor, account_kind=InstitutionUser.ICT_HEAD, is_active_link=True).exists()
            or can_manage_ce_access(actor, inst)):
        raise PermissionDenied("Only the institution's Head of ICT or NBTE staff with access can resend it.")

    link = (InstitutionUser.unscoped.filter(institution=inst, account_kind=InstitutionUser.CHIEF_EXECUTIVE,
                                            is_active_link=True).select_related("user").first())
    if link is None:
        raise ValidationError("There is no Chief Executive account to invite.")
    if link.user.has_usable_password() and link.user.last_login:
        raise ValidationError("The Chief Executive has already signed in.")
    minutes = cfg_int("qa.onboarding.ce_invite_resend_minutes", 15)
    key = f"qa:ce-invite:{inst.pk}"
    if minutes and cache.get(key):
        raise ValidationError(f"The invitation was sent less than {minutes} minutes ago. Try again later.")
    from django.core.cache import cache as _c
    from qa_core.portal_views import _cooldown_key

    _c.delete(_cooldown_key(link.user))  # this rate limit replaces the generic credentials cool-down
    # v2.2: e-mailed to the Chief Executive only; never shown to the Head of ICT.
    send_credentials(inst, actor=actor, user=link.user, request=request, reveal_once=False)
    if minutes:
        cache.set(key, timezone.now().isoformat(), minutes * 60)
    InstitutionUser.unscoped.filter(pk=link.pk).update(invited_at=timezone.now(), updated_at=timezone.now())
    return link.user


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
    previous = (InstitutionDocument.unscoped.select_for_update()
                .filter(institution=inst, kind=kind).order_by("-version").first())
    was_returned = previous is not None and previous.status == InstitutionDocument.RETURNED
    if application is not None and not was_returned:
        from accreditation import onboarding_v3

        if onboarding_v3.document_upload_blocked(application):
            raise ValidationError("Required uploads unlock after NBTE Finance confirms the institutional and "
                                  "processing fees.")
    validate_upload(upload, extensions=kind.extensions(), max_mb=max_document_mb(kind), label="document")
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
    doc.routed_department = None
    if was_returned:
        # O8/O9 (first slice of X17): a re-upload of a returned document goes
        # straight back to the department that returned it — no resubmission.
        from accreditation.services import on_document_reuploaded

        doc.routed_department = on_document_reuploaded(doc, previous, user=user)
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


def send_credentials(inst, *, actor, user=None, request=None, reveal_once=False, prepared_link=None):
    """Activate the account and e-mail a one-time set-password link (never a
    plaintext password). Respects qa.credentials.resend_cooldown_minutes."""
    from django.core.cache import cache

    from qa_core import mail
    from qa_core.portal_tokens import link_valid_hours
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
    link, valid_hours = _make_set_password_link(user, request) if prepared_link is None else (
        prepared_link, link_valid_hours())
    if reveal_once and request is not None and getattr(actor, "pk", None) and prepared_link is None \
            and getattr(actor, "user_type", "") == "staff":
        request.session["qa_ce_activation_link_once"] = {
            "url": link, "email": user.email, "institution": inst.pk, "issuer_id": actor.pk,
            "valid_hours": valid_hours,
        }
    delivery_failed = False
    try:
        mail.send("Set your NBTE Quality Assurance Portal password", [user.email], "emails/qa_set_password.html",
                  {"user": user, "set_url": link, "valid_hours": valid_hours, "institution": inst},
                  category="reset")
    except Exception:
        if not (getattr(actor, "user_type", "") == "staff" or _is_registration_actor(inst, actor)):
            raise
        delivery_failed = True
        logger.exception("Could not deliver the CE password setup email for institution %s; the one-time link is available to the issuer", inst.pk)
    if cooldown:
        cache.set(_cooldown_key(user), timezone.now().isoformat(), cooldown * 60)
    InstitutionUser.unscoped.filter(user=user).update(credentials_last_sent_at=timezone.now())
    timeline.record(inst, "credentials.delivery_failed" if delivery_failed else "credentials.sent",
                    actor=actor, visibility="internal",
                    internal_text=(f"Set-password email delivery failed for {user.email}; the one-time link was issued."
                                   if delivery_failed else f"Set-password invitation prepared for {user.email}."))
    return user


def _is_registration_actor(inst, actor):
    """The Head of ICT registering: a mail failure must not undo the
    registration; NBTE resends from the institution page."""
    return bool(getattr(actor, "pk", None) and InstitutionUser.unscoped.filter(
        institution=inst, user=actor, account_kind=InstitutionUser.ICT_HEAD).exists())


# ---------------------------------------------------------------------------
# v2.2 — Chief Executive account access, NBTE side
# ---------------------------------------------------------------------------
def chief_executive_link(inst):
    return (InstitutionUser.unscoped.filter(institution=inst, account_kind=InstitutionUser.CHIEF_EXECUTIVE,
                                            is_active_link=True).select_related("user").first())


def can_manage_ce_access(user, inst):
    """ES, super admin / superuser, the Director of ICT, and the ICT officer
    assigned to oversee this institution."""
    if not getattr(user, "is_authenticated", False) or not user.is_active \
            or getattr(user, "user_type", "staff") != "staff":
        return False
    import org
    from assignments import access

    if user.is_superuser or org.is_es(user) or org.is_super_admin(user):
        return True
    if org.is_director(user, access.ict_department_code()):
        return True
    if access.is_ict_supervisor(user, inst):
        return True
    from assignments.models import WorkAssignment

    return WorkAssignment.objects.filter(institution=inst, assignee=user, kind=WorkAssignment.OVERSIGHT_ICT,
                                         status__in=WorkAssignment.OPEN).exists()


def ce_status(inst):
    link = chief_executive_link(inst)
    if link is None:
        return {"state": "none", "user": None, "link": None}
    u = link.user
    state = "active" if (u.has_usable_password() and u.last_login) else (
        "password_set" if u.has_usable_password() else "invited")
    return {"state": state, "user": u, "link": link}


@transaction.atomic
def nbte_send_ce_link(inst, *, actor, request=None, reveal=False):
    """E-mail a fresh set-up link to the Chief Executive's own address. With
    ``reveal`` the link is also returned once to the NBTE officer (to send by
    another channel when e-mail fails). Audited either way."""
    if not can_manage_ce_access(actor, inst):
        raise PermissionDenied("You cannot manage this institution's Chief Executive account.")
    link = chief_executive_link(inst)
    if link is None:
        raise ValidationError("This institution has no Chief Executive account yet.")
    url, hours = _make_set_password_link(link.user, request)
    sent = True
    try:
        from qa_core import mail

        mail.send("Set your NBTE Quality Assurance Portal password", [link.user.email], "emails/qa_set_password.html",
                  {"user": link.user, "set_url": url, "valid_hours": hours, "institution": inst}, category="reset")
    except Exception:
        sent = False
        logger.exception("CE link e-mail failed for %s", inst.code)
    InstitutionUser.unscoped.filter(pk=link.pk).update(credentials_last_sent_at=timezone.now(),
                                                       invited_at=timezone.now(), updated_at=timezone.now())
    from core import audit

    audit.log("other", link.user, actor=actor,
              note=(f"CE set-up link {'e-mailed' if sent else 'e-mail FAILED'} to {link.user.email}"
                    + ("; link shown once to the NBTE officer" if reveal else ""))[:255])
    timeline.record(inst, "credentials.sent", actor=actor, visibility="internal",
                    internal_text=f"NBTE ({actor.get_full_name()}) sent the Chief Executive set-up link to "
                                  f"{link.user.email}{'' if sent else ' — e-mail failed'}"
                                  f"{'; link also shown to the officer' if reveal else ''}.")
    return {"url": url if reveal else None, "hours": hours, "sent": sent, "email": link.user.email}


@transaction.atomic
def nbte_issue_ce_password(inst, *, actor):
    """Generate a one-time password for the Chief Executive (shown once to
    the NBTE officer, who passes it on). The CE must change it at first
    sign-in. Audited; the password itself is never stored or logged."""
    import secrets
    import string

    if not can_manage_ce_access(actor, inst):
        raise PermissionDenied("You cannot manage this institution's Chief Executive account.")
    link = chief_executive_link(inst)
    if link is None:
        raise ValidationError("This institution has no Chief Executive account yet.")
    alphabet = string.ascii_letters + string.digits
    password = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)) + "#"
    user = link.user
    user.set_password(password)
    user.must_change_password = True
    user.is_active = True
    user.save(update_fields=["password", "must_change_password", "is_active"])
    from core import audit

    audit.log("other", user, actor=actor, note=f"One-time CE password issued by NBTE for {user.email}")
    timeline.record(inst, "credentials.password_issued", actor=actor, visibility="internal",
                    internal_text=f"{actor.get_full_name()} issued a one-time password for the Chief Executive "
                                  f"({user.email}); they must change it at first sign-in.")
    return password


def impersonation_target(inst):
    """The account an impersonation session views the portal as."""
    for link in members_of(inst):
        if link.user.is_active:
            return link.user
    return None


def start_impersonation(inst, *, staff, reason, write_mode=False):
    if len((reason or "").strip()) < 5:
        raise ValidationError("Give a reason for viewing as this institution.")
    # v2.1 P.4 (S-08): impersonation never widens scope — the portal shows
    # the institution's documents, payments and SSQs, so only people who
    # already see this institution's whole journey may view as it.
    from assignments import access
    if not access.can_see(staff, inst, "full_journey" if access.assignment_mode() else "summary"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("You can only view as an institution whose whole journey you can already see.")
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


# ---------------------------------------------------------------------------
# X12 slice (Phase 0): only the Chief Executive submits to NBTE
# ---------------------------------------------------------------------------
CHIEF_EXECUTIVE_ROLE = "inst-chief-executive"
CE_ONLY_MESSAGE = "Only the Chief Executive of the institution can submit {what} to NBTE."


def is_chief_executive(user):
    """The institution's Chief Executive: the typed account (X12, F.13) or,
    for older rows, the inst-chief-executive role."""
    if user is None or getattr(user, "user_type", "") != "institution":
        return False
    if getattr(getattr(user, "role", None), "code", "") == CHIEF_EXECUTIVE_ROLE:
        return True
    return InstitutionUser.unscoped.filter(user=user, account_kind=InstitutionUser.CHIEF_EXECUTIVE,
                                           is_active_link=True).exists()


def require_chief_executive(user, what="this"):
    """Service guard for every institution → NBTE submission (application,
    programmes, QA requests, SSQs). Phase F replaces it with portal
    permissions (spec G3)."""
    if not is_chief_executive(user):
        raise PermissionDenied(CE_ONLY_MESSAGE.format(what=what))


def institution_team_locked(inst):
    """Lock direct profile, management, and user changes after submission."""
    from accreditation.models import Application
    from django.db.models import Q

    if inst.status != Institution.PROPOSED:
        return True
    return Application.unscoped.filter(institution=inst).filter(
        Q(submitted_at__isnull=False) | ~Q(status__in=("draft", "withdrawn"))
    ).exists()


def chief_executives(inst):
    import org
    from accounts.models import User

    return list(User.objects.filter(org.role_q(CHIEF_EXECUTIVE_ROLE), institution=inst, user_type="institution",
                                    is_active=True))


def staff_in_department(dept, *, perm=None, roles=None):
    from accounts.models import User

    if dept is None:
        return []
    users = User.objects.filter(is_active=True, user_type="staff", department=dept).select_related("role")
    if roles:
        import org
        users = users.filter(org.role_q(*roles))
    out = list(users[:200])
    if perm:
        out = [u for u in out if u.has_perm(perm)]
    if roles is None:
        from assignments import access
        if access.assignment_mode():
            # X1/X3/X4: institution notices reach the department's leads (who
            # assign the work), not every member — staff hear once assigned.
            out = [u for u in out if access.is_department_lead(u)]
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


def invite_user(inst, data, *, actor, request=None, approved_request=False):
    """Add a portal user to the institution and e-mail a set-password link."""
    from accounts.models import Role, User

    if (getattr(actor, "user_type", "") == User.INSTITUTION and institution_team_locked(inst)
            and not approved_request):
        raise PermissionDenied("Institution users are locked after application submission. Submit a change request.")
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


def add_proposed_programme(inst, name, level, category=None, *, application=None, user=None, streams=1):
    programme = Programme.unscoped.create(institution=inst, name=name, level=level, category=category,
                                          status="proposed", source_application=application,
                                          requested_streams=max(1, int(streams or 1)))
    timeline.record(inst, "programme.proposed", subject=programme, actor=user, visibility="public",
                    public_text=f"Programme proposed: {programme.name} ({programme.level.name}), "
                                f"{programme.requested_streams} stream(s).")
    return programme


def set_programme_streams(programme, streams, *, user=None):
    """Phase 8: the streams asked for on a proposed programme (SOP-01)."""
    streams = max(1, int(streams))
    if programme.requested_streams != streams:
        programme.requested_streams = streams
        programme.save(update_fields=["requested_streams", "updated_at"])
        timeline.record(programme.institution, "programme.streams", subject=programme, actor=user,
                        visibility="public", public_text=f"{programme.name}: {streams} stream(s) asked for.")
    return programme


def remove_proposed_programme(programme, *, user=None):
    inst = programme.institution
    label = f"{programme.name} ({programme.level.name})"
    programme.delete()
    timeline.record(inst, "programme.removed", actor=user, visibility="public",
                    public_text=f"Programme removed from the list: {label}.")


# ---------------------------------------------------------------------------
# Detail-page read rules (Phase 9: shared by the website and the mobile API)
# ---------------------------------------------------------------------------
def can_see_payments(user):
    """The Payments tab of an institution (bills)."""
    return bool(user.is_superuser or user.has_perm("payments.view_obligation"))


def can_see_internal_timeline(user, inst):
    return bool(user.is_superuser or user.has_perm("qa_core.view_timeline_internal")
                or user.department_id == inst.handling_department_id)


def timeline_for(user, inst, *, vis="", text=""):
    """The institution's timeline as ``user`` may read it, newest first."""
    from django.db.models import Q

    from qa_core.models import TimelineEvent

    qs = TimelineEvent.objects.for_user(user).filter(institution=inst).select_related("actor")
    if vis in ("public", "internal"):
        qs = qs.filter(visibility=vis)
    if not can_see_internal_timeline(user, inst):
        qs = qs.filter(visibility="public")
    text = (text or "").strip()
    if text:
        qs = qs.filter(Q(public_text__icontains=text) | Q(internal_text__icontains=text) | Q(event_code__icontains=text))
    return qs.order_by("-at", "-id")


INSTITUTION_SORTS = {"name": "name", "code": "code", "recent": "-created_at", "status": "status"}


def filter_institutions(qs, *, q="", status="", ownership="", type_code="", dept="", zone="", sort="name"):
    """The Institutions list filters (Phase 9: shared with the mobile API)."""
    from django.db.models import Q

    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(lga__icontains=q)
                       | Q(former_names__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if ownership:
        qs = qs.filter(ownership__code=ownership)
    if type_code:
        qs = qs.filter(institution_type__code=type_code)
    if dept:
        qs = qs.filter(handling_department__code=dept)
    if zone:
        qs = qs.filter(zone=zone)
    return qs.order_by(INSTITUTION_SORTS.get(sort or "name", "name"))


def programme_name_choices(limit=500):
    """Distinct programme names offered anywhere (names only, no institution),
    for datalists. v2.1 P.3: views never call ``.unscoped``."""
    return sorted({n for n in Programme.unscoped.values_list("name", flat=True) if n})[:limit]


def lga_in_use(lga):
    """True when any institution or institution address uses ``lga`` (Settings → LGAs)."""
    from .models import Institution, InstitutionAddress
    return (Institution.unscoped.filter(lga_ref=lga).exists()
            or InstitutionAddress.unscoped.filter(lga_ref=lga).exists())
