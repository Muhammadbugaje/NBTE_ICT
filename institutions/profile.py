"""v2.1 X13 (F.14): the institution's setup checklist and the friendly
profile gate (spec_v21 G3.d).

``setup_checklist(inst)`` drives the /setup/ page and the gate;
``@profile_gate`` sends institution users to /setup/ from the pages that
need a complete profile (application, documents, QA requests …) until every
step is done. No 403s: the setup page explains what is left.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone

from core import audit
from qa_core import timeline

from . import services
from .models import Institution, InstitutionAddress, InstitutionContact, InstitutionUser, ManagementRole, ManagementStaff


@dataclass
class Step:
    code: str
    label: str
    ok: bool
    detail: str = ""
    action_label: str = ""
    action_url: str = ""
    hidden: bool = False
    missing: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Management roles and staff
# ---------------------------------------------------------------------------
def active_roles():
    return list(ManagementRole.objects.filter(is_active=True).order_by("sort_order", "name"))


def mandatory_roles(inst=None):
    return [r for r in active_roles() if r.is_mandatory]


def current_staff(inst):
    return list(ManagementStaff.unscoped.filter(institution=inst, is_current=True).select_related("role")
                .order_by("role__sort_order", "role__name", "id"))


def holder_problems(role, row):
    out = []
    if role.requires_email and not (row.email or "").strip():
        out.append("e-mail")
    if role.requires_phone and not (row.phone or "").strip():
        out.append("phone")
    return out


def role_board(inst):
    """[{role, holders, ok, problems}] for every active role."""
    staff = current_staff(inst)
    out = []
    for role in active_roles():
        holders = [s for s in staff if s.role_id == role.pk]
        problems = []
        for h in holders:
            problems += holder_problems(role, h)
        out.append({"role": role, "holders": holders, "filled": bool(holders),
                    "ok": bool(holders) and not problems, "problems": problems})
    return out


@transaction.atomic
def set_holder(inst, role, data, *, actor, replace=True, approved_request=False):
    """Add (or replace) the holder of ``role``. The previous holder is closed,
    never deleted (history). Returns the new ManagementStaff row."""
    if (getattr(actor, "user_type", "") == "institution"
            and services.institution_team_locked(inst) and not approved_request):
        raise PermissionDenied("Institution management is locked after application submission. Submit a change request.")
    if not role.is_active:
        raise ValidationError("This role is no longer in use.")
    name = (data.get("full_name") or "").strip()
    if len(name) < 3:
        raise ValidationError({"full_name": "Enter the person's full name."})
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    if role.requires_email and not email:
        raise ValidationError({"email": f"NBTE needs an e-mail address for the {role.name}."})
    if role.requires_phone and not phone:
        raise ValidationError({"phone": f"NBTE needs a phone number for the {role.name}."})
    current = list(ManagementStaff.unscoped.select_for_update().filter(institution=inst, role=role, is_current=True))
    today = timezone.localdate()
    if len(current) >= role.max_holders:
        if not replace:
            raise ValidationError(f"The {role.name} already has {role.max_holders} holder(s). Replace one instead.")
        for old in current[: len(current) - role.max_holders + 1]:
            old.is_current, old.end_date = False, today
            old.save(update_fields=["is_current", "end_date", "updated_at"])
    row = ManagementStaff.unscoped.create(
        institution=inst, role=role, title=(data.get("title") or "").strip()[:30], full_name=name[:150],
        email=email, phone=phone[:30], start_date=data.get("start_date") or today, is_current=True,
        added_by=actor if getattr(actor, "pk", None) else None)
    audit.log(audit._action("create"), row, actor=actor, note=f"management staff: {role.code}")
    timeline.record(inst, "institution.management_staff", subject=row, actor=actor, visibility="public",
                    public_text=f"{role.name}: {row.display_name} recorded.")
    return row


# ---------------------------------------------------------------------------
# Accounts (X12): the Chief Executive and the Head of ICT
# ---------------------------------------------------------------------------
def account_link(inst, kind):
    """The active InstitutionUser link of kind ``chief_executive`` / ``ict_head``."""
    return (InstitutionUser.unscoped.filter(institution=inst, account_kind=kind, is_active_link=True,
                                            user__is_active=True)
            .select_related("user").order_by("-id").first())


def account_state(link):
    if link is None:
        return "missing"
    return "active" if link.user.has_usable_password() and link.user.last_login else "invited"


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------
def _contact(inst, kind):
    return InstitutionContact.unscoped.filter(institution=inst, kind=kind, is_current=True).exclude(value="").exists()


def _address(inst):
    return InstitutionAddress.unscoped.filter(institution=inst, is_current=True).exclude(line1="").exists()


def setup_checklist(inst):
    """Ordered steps with state; ``(steps, percent)``. Cached per institution; any write to the institution's
    own data (core.fastcache.INST) refreshes it at once."""
    from core import fastcache

    return fastcache.cached(fastcache.INST, f"checklist:{inst.pk}", lambda: _setup_checklist(inst), ttl=120)


def _setup_checklist(inst):
    missing = []
    if not inst.ownership_id:
        missing.append("ownership")
    if not inst.institution_type_id:
        missing.append("institution type")
    if not inst.state_id or not (inst.lga_ref_id or inst.lga):
        missing.append("state and LGA")
    if not _contact(inst, "official_email"):
        missing.append("official e-mail")
    if not _contact(inst, "phone"):
        missing.append("phone")
    details = Step("details", "Institution details", ok=not missing, missing=missing,
                   detail=(", ".join(filter(None, [inst.name, inst.ownership.name if inst.ownership_id else "",
                                                   inst.state_name])) if not missing
                           else "Still needed: " + ", ".join(missing) + "."),
                   action_label="Complete details", action_url="/institution/")
    has_addr = _address(inst)
    addresses = Step("addresses", "Physical address", ok=has_addr,
                     detail="We post official letters here." if has_addr else "Add the campus address.",
                     action_label="Add address", action_url="/institution/")
    ce, ict = account_link(inst, "chief_executive"), account_link(inst, "ict_head")
    acc_missing = [label for label, link in (("Chief Executive", ce), ("Head of ICT", ict)) if link is None]
    accounts = Step("accounts", "Chief Executive and Head of ICT accounts", ok=not acc_missing, missing=acc_missing,
                    detail=("Both accounts are set up." if not acc_missing
                            else "Add the " + " and ".join(acc_missing) + " account."),
                    action_label="Open users", action_url="/institution/users/")
    board = role_board(inst)
    need = [b for b in board if b["role"].is_mandatory and not b["ok"]]
    mand = [b for b in board if b["role"].is_mandatory]
    management = Step("management", "Management team", ok=not need, missing=[b["role"].name for b in need],
                      detail=(f"{len(mand) - len(need)} of {len(mand)} roles filled."
                              + (f" {need[0]['role'].name} still needed." if need else "")),
                      action_label=f"Add {need[0]['role'].name}" if need else "Open team",
                      action_url="/institution/management/")
    public = inst.is_public
    legal = Step("legal", "CAC registration", ok=public or bool((inst.cac_rc_number or "").strip()), hidden=public,
                 detail="Private institutions give their CAC RC number.", action_label="Add CAC number",
                 action_url="/institution/")
    steps = [details, addresses, accounts, management, legal]
    shown = [s for s in steps if not s.hidden]
    done = sum(1 for s in shown if s.ok)
    percent = int(round(100 * done / len(shown))) if shown else 100
    return steps, percent


def profile_complete(inst):
    steps, _p = setup_checklist(inst)
    return all(s.ok for s in steps if not s.hidden)


def next_step(inst):
    steps, _p = setup_checklist(inst)
    return next((s for s in steps if not s.hidden and not s.ok), None)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def gate_enabled():
    import config

    v = config.get("qa.onboarding.profile_gate_enabled", True)
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def profile_gate(view):
    """Institution pages that need a complete profile. Incomplete → /setup/
    (HTMX: ``HX-Redirect``). Use under ``@institution_required``."""
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if gate_enabled() and getattr(user, "user_type", "") == "institution" and user.institution_id:
            inst = Institution.unscoped.filter(pk=user.institution_id).select_related("ownership").first()
            if inst is not None and not profile_complete(inst):
                if request.headers.get("HX-Request"):
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = "/setup/"
                    return response
                return redirect("/setup/")
        return view(request, *args, **kwargs)
    return wrapped


def require_profile_complete(inst):
    """Service-level guard (API and web reuse it)."""
    if gate_enabled() and not profile_complete(inst):
        raise PermissionDenied("Complete your institution's setup first (profile and management team).")
