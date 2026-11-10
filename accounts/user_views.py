"""User Management — admin-side account operations.

Distinct from `force_password_change` (self-service, requires current password).
These views are for Super Admin / HR / anyone with the right permission:
  - View all users, filter by department / role / active state
  - Reset a user's password (custom or random)
  - Force change on next login
  - Activate / deactivate
  - Edit roles
"""
from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import password_validation
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

User = get_user_model()


def _users_visible_to(actor):
    """Review M7: user management is department-scoped like everything else.
    Phase B (B10/T-SEC-3): superusers and holders of `accounts.view_all_users`
    (ES, super admin, or an explicit grant) see every account; directors and
    everyone else see their own department only."""
    if not actor.is_authenticated:
        return User.objects.none()
    if actor.is_superuser or actor.has_perm("accounts.view_all_users"):
        return User.objects.all()
    if actor.department_id is None:
        return User.objects.none()
    return User.objects.filter(department_id=actor.department_id)


FILTER_KEYS = ("q", "department", "office", "rank", "role", "institution", "state", "kind", "type", "body",
               "active")


def filter_users(qs, params, segment=None):
    """Shared list contract (C14/C16): search + facets, always inside ONE
    segment (NBTE staff / institution users / resource persons & professional
    bodies) — the segment is a server-side filter, never just a CSS tab.
    Used by User management and Act as user."""
    from . import audience

    if segment is not None:
        qs = qs.filter(audience.segment_q(segment))
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    dept = params.get("department") or ""
    if dept.isdigit():
        qs = qs.filter(department_id=int(dept))
    elif dept == "none":
        qs = qs.filter(department__isnull=True)
    office = params.get("office") or ""
    if office.isdigit():
        qs = qs.filter(staff_profile__office_id=int(office))
    rank = params.get("rank") or ""
    if rank.isdigit():
        qs = qs.filter(staff_profile__designation_id=int(rank))
    role = params.get("role") or ""
    if role.isdigit():
        qs = qs.filter(role_id=int(role))
    elif role == "none":
        qs = qs.filter(role__isnull=True)
    inst = params.get("institution") or ""
    if inst.isdigit():
        qs = qs.filter(institution_id=int(inst))
    state = params.get("state") or ""
    if state.isdigit():
        qs = qs.filter(institution__state_id=int(state))
    kind = params.get("kind") or ""
    if kind in ("chief_executive", "ict_head", "staff_user"):
        qs = qs.filter(institution_membership__account_kind=kind)
    body = params.get("body") or ""
    if body.isdigit():
        qs = qs.filter(professional_body_id=int(body))
    utype = params.get("type") or ""
    if utype in {c for c, _ in User.USER_TYPE_CHOICES}:
        qs = qs.filter(user_type=utype)
    active = params.get("active") or ""
    if active == "1":
        qs = qs.filter(is_active=True)
    elif active == "0":
        qs = qs.filter(is_active=False)
    return qs


def facet_choices(visible, segment=None):
    """Facet options drawn from the accounts in ``visible`` (one segment)."""
    from staff.models import Department, Designation, Office

    from . import audience
    from .models import Role

    if segment is not None:
        visible = visible.filter(audience.segment_q(segment))
    seg = audience.clean_segment(segment or audience.NBTE)
    out = {
        "roles": Role.objects.filter(pk__in=visible.values("role_id")).order_by("name"),
        "user_types": [(c, l) for c, l in User.USER_TYPE_CHOICES if c in audience.SEGMENTS[seg]["types"]],
        "segment": seg,
    }
    if seg == audience.NBTE:
        out["departments"] = Department.objects.filter(pk__in=visible.values("department_id")).order_by("name")
        out["offices"] = Office.objects.filter(pk__in=visible.values("staff_profile__office_id")).order_by("name")
        out["ranks"] = Designation.objects.filter(
            pk__in=visible.values("staff_profile__designation_id")).order_by("name")
    elif seg == audience.INSTITUTION:
        from institutions.models import Institution
        from staff.models import StateOfOrigin

        out["institutions"] = Institution.unscoped.filter(pk__in=visible.values("institution_id")).order_by("name")
        out["states"] = StateOfOrigin.objects.filter(
            pk__in=visible.values("institution__state_id")).order_by("name")
        out["kinds"] = [("chief_executive", "Chief Executive"), ("ict_head", "Head of ICT"),
                        ("staff_user", "Other portal users")]
    else:
        from institutions.models import ProfessionalBody

        out["bodies"] = ProfessionalBody.objects.filter(pk__in=visible.values("professional_body_id")).order_by("name")
    return out


def _filter_state(params):
    state = {k: (params.get(k) or "").strip() for k in FILTER_KEYS}
    state["any"] = any(state.values())
    return state


def segment_counts(qs):
    """{segment: count} for the segment switcher."""
    from django.db.models import Count

    from . import audience

    by_type = dict(qs.values_list("user_type").annotate(n=Count("id")).values_list("user_type", "n"))
    return {key: sum(by_type.get(t, 0) for t in seg["types"]) for key, seg in audience.SEGMENTS.items()}


def _segment_stats(qs, segment):
    """Hero numbers for one segment's list."""
    from . import audience

    stats = {"total": qs.count(), "active": qs.filter(is_active=True).count()}
    stats["inactive"] = stats["total"] - stats["active"]
    if segment == audience.NBTE:
        stats["departments"] = qs.exclude(department__isnull=True).values("department_id").distinct().count()
        stats["no_role"] = qs.filter(role__isnull=True).count()
    elif segment == audience.INSTITUTION:
        stats["institutions"] = qs.values("institution_id").distinct().count()
        stats["ce"] = qs.filter(institution_membership__account_kind="chief_executive",
                                institution_membership__is_active_link=True).count()
        stats["ict"] = qs.filter(institution_membership__account_kind="ict_head",
                                 institution_membership__is_active_link=True).count()
    else:
        stats["resource"] = qs.filter(user_type=User.RESOURCE_PERSON).count()
        stats["bodies"] = qs.filter(user_type=User.PROFESSIONAL_BODY).count()
    return stats


SEGMENT_URLS = {"nbte": "accounts:user_list", "institution": "accounts:user_list_institution",
                "external": "accounts:user_list_external"}


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def user_list(request, segment="nbte"):
    """C13-C15 + strict separation: three separate lists — NBTE staff
    (default, the old /accounts/users/ URL), institution users, and resource
    persons & professional bodies. Each has its own facets and hero counts;
    a row from another segment can never appear. HTMX swaps #users-list."""
    from qa_core import ui

    from . import audience

    # Old links such as ?type=institution land on the right section.
    legacy = request.GET.get("type") or ""
    if segment == audience.NBTE and legacy and legacy != User.STAFF \
            and legacy in {c for c, _ in User.USER_TYPE_CHOICES}:
        params = request.GET.copy()
        target = audience.segment_of_type(legacy)
        if target == audience.INSTITUTION:
            params.pop("type", None)
        from django.urls import reverse
        return redirect(f"{reverse(SEGMENT_URLS[target])}?{params.urlencode()}")

    visible = _users_visible_to(request.user)
    in_segment = visible.filter(audience.segment_q(segment))
    qs = filter_users(in_segment.select_related("department", "role", "institution", "professional_body"),
                      request.GET).order_by("first_name", "last_name", "email")
    if segment == audience.NBTE:
        qs = qs.select_related("staff_profile__designation", "staff_profile__office")
    elif segment == audience.INSTITUTION:
        qs = qs.select_related("institution_membership", "institution__state")
    page = ui.paginate(request, qs)
    ctx = {"page": page, "f": _filter_state(request.GET), "total": page["total"],
           "segment": segment, "seg": audience.SEGMENTS[segment],
           "segment_counts": segment_counts(visible), "stats": _segment_stats(in_segment, segment),
           "list_url_name": SEGMENT_URLS[segment],
           "can_add": request.user.has_perm("accounts.add_user") and segment != audience.EXTERNAL,
           **facet_choices(visible, segment)}
    return ui.render_qa(request, "accounts/user_list.html", ctx,
                        partials={"users-list": "accounts/users/_list.html"})


def user_list_institution(request):
    return user_list(request, segment="institution")


def user_list_external(request):
    return user_list(request, segment="external")


# ---------------------------------------------------------------------------
# Create (one form per segment — a mixed account cannot be made)
# ---------------------------------------------------------------------------
@login_required
@permission_required("accounts.add_user", raise_exception=True)
def user_create(request, segment="nbte"):
    """NBTE section: always user_type=staff, a department is required and only
    NBTE roles are offered. Institution section: always user_type=institution,
    an institution and an institution portal role are required (created
    through institutions.services.invite_user, which links the account to the
    institution and sends the set-password e-mail)."""
    from . import audience
    from .forms import InstitutionUserCreateForm, NBTEUserCreateForm

    if segment not in (audience.NBTE, audience.INSTITUTION):
        raise PermissionDenied("Accounts of this kind are created from their own workflow.")
    form_cls = NBTEUserCreateForm if segment == audience.NBTE else InstitutionUserCreateForm
    form = form_cls(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            user = form.save(request)
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)
        else:
            messages.success(request, f"Account created for {user.get_full_name() or user.email}.")
            return redirect("accounts:user_detail", pk=user.pk)
    return render(request, "accounts/user_create.html", {
        "form": form, "segment": segment, "seg": audience.SEGMENTS[segment],
        "list_url_name": SEGMENT_URLS[segment],
    })


def user_create_institution(request):
    return user_create(request, segment="institution")


def _seg_ctx(user):
    from . import audience

    segment = audience.segment_of(user)
    return {"segment": segment, "seg": audience.SEGMENTS[segment], "list_url_name": SEGMENT_URLS[segment]}


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def user_detail(request, pk):
    from . import audience

    user = get_object_or_404(_users_visible_to(request.user), pk=pk)
    segment = audience.segment_of(user)
    return render(request, "accounts/user_detail.html", {
        "u": user, "segment": segment, "seg": audience.SEGMENTS[segment],
        "list_url_name": SEGMENT_URLS[segment],
        "role_audience": {"staff": "NBTE staff", "institution": "institution portal",
                          "resource_person": "resource-person", "professional_body": "professional-body"}[
            audience.role_audience(user.role)] if user.role_id else "",
    })

import logging
import secrets

logger = logging.getLogger(__name__)


@login_required
@permission_required("accounts.change_user", raise_exception=True)
def user_reset_password(request, pk):
    user = get_object_or_404(_users_visible_to(request.user), pk=pk)
    # Review H3: never act on a superuser or someone who outranks you.
    from .permissions import can_act_on_user
    if not can_act_on_user(request.user, user):
        raise PermissionDenied("You cannot reset the password of this account.")

    if request.method == "POST":
        action = request.POST.get("action") or "generate"
        force_change = request.POST.get("force_change") == "on"

        # ---- Default-on: only skip if the operator explicitly unchecked it.
        raw_send = request.POST.get("send_email")
        # Checkboxes that are checked submit "on"; unchecked submit nothing.
        # If the key is missing entirely, we treat it as "checked by default"
        # because the form has it ticked by default.
        if raw_send is None:
            send_email = True
        else:
            send_email = raw_send == "on"

        # ---- Password choice
        # Review H3: the new password is never shown on screen (flash
        # messages live in a cookie). A generated password can only be
        # delivered by email; a typed one is known to the operator already.
        if action == "set":
            new_password = (request.POST.get("password") or "").strip()
            try:
                password_validation.validate_password(new_password, user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("accounts:user_reset_password", pk=pk)
        else:
            if not (send_email and user.email):
                messages.error(
                    request,
                    "A generated password can only be delivered by email. "
                    "Tick 'Email the new credentials' (the account needs an email "
                    "address), or choose 'Set a specific password'.",
                )
                return redirect("accounts:user_reset_password", pk=pk)
            from .services import generate_temporary_password
            new_password = generate_temporary_password()

        # ---- Apply
        user.set_password(new_password)
        if hasattr(user, "must_change_password"):
            user.must_change_password = force_change
        user.save()
        logger.info(
            "Password reset for %s by %s (send_email=%s, force_change=%s)",
            user.email, request.user.email, send_email, force_change,
        )

        # ---- Email
        if not send_email or not user.email:
            messages.success(request, "Password reset. No email was sent; give the user the password you set.")
            return redirect("accounts:user_detail", pk=pk)

        sent = _send_reset_email(request, user, new_password, force_change)
        if sent:
            messages.success(request, f"Password reset — credentials email queued for {user.email}.")
        else:
            messages.warning(
                request,
                "Password reset, but the email could not be queued. "
                "Check the server log, then reset again.",
            )
        return redirect("accounts:user_detail", pk=pk)

    return render(request, "accounts/user_reset_password.html", {"u": user, **_seg_ctx(user)})


def _send_reset_email(request, user, new_password, force_change):
    """Queue credentials through the configured provider engine."""
    login_url = request.build_absolute_uri("/accounts/login/")
    try:
        from core.email_utils import send_templated_email
        email_log = send_templated_email(
            subject="Your NBTE ICT password has been reset",
            recipients=[user.email],
            template_name="emails/password_reset_by_admin.html",
            context={
                "user": user,
                "temp_password": new_password,
                "force_change": force_change,
                "login_url": login_url,
            },
            category="credentials",
        )
        if email_log is None:
            logger.error("Password-reset credentials email could not be queued.")
            return False
        logger.info("Password-reset credentials email queued log_id=%s", email_log.pk)
        return True
    except Exception as exc:
        from notifications.engine import safe_error_message

        logger.error(
            "Password-reset credentials email could not be queued: %s",
            safe_error_message(exc),
        )
        return False


@login_required
@permission_required("accounts.change_user", raise_exception=True)
@require_POST
def user_toggle_active(request, pk):
    user = get_object_or_404(_users_visible_to(request.user), pk=pk)
    from .permissions import can_act_on_user
    if not can_act_on_user(request.user, user):
        raise PermissionDenied("You cannot change this account.")
    if user.pk == request.user.pk:
        messages.error(request, "You can't deactivate your own account.")
        return redirect("accounts:user_detail", pk=pk)
    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])
    text = f"{user.email} is now {'active' if user.is_active else 'inactive'}."
    from qa_core import ui
    if ui.is_htmx(request):          # C18: swap the status block, no reload
        resp = render(request, "accounts/users/_status.html", {"u": user})
        return ui.toast(resp, "success", text)
    messages.success(request, text)
    return redirect("accounts:user_detail", pk=pk)


@login_required
@permission_required("accounts.change_user", raise_exception=True)
def user_edit_roles(request, pk):
    """Review M6: assign the user's Role (accounts.Role), which is what
    RolePermissionBackend reads. B.8: goes through staff.role_ui.role_assign
    (swapped to org.assign_role at the pb-org merge); HTMX posts get a toast."""
    from qa_core import ui
    from staff.role_ui import RoleAssignError, grantable_roles, role_assign

    from .permissions import can_act_on_user

    user = get_object_or_404(_users_visible_to(request.user), pk=pk)
    if not can_act_on_user(request.user, user):
        raise PermissionDenied("You cannot change this account's role.")
    roles = grantable_roles(request.user, user)

    if request.method == "POST":
        raw = (request.POST.get("role") or "").strip()
        if "role" not in request.POST:
            error = "Choose a role."
        else:
            new_role = None if raw == "" else (roles.filter(pk=raw).first() if raw.isdigit() else None)
            error = "Choose a valid role." if raw and new_role is None else ""
        if not error:
            try:
                changed = user.role_id != getattr(new_role, "pk", None)
                role_assign(user, new_role, request.user, reason=request.POST.get("reason", ""))
            except RoleAssignError as exc:
                error = str(exc)
        if ui.is_htmx(request):
            resp = render(request, "accounts/users/_status.html", {"u": user})
            if error:
                resp = ui.toast(resp, "error", error)
                resp.status_code = 422
                return resp
            return ui.toast(resp, "success", f"Role set to {user.role.name if user.role else 'none'}.")
        if error:
            messages.error(request, error)
            return redirect("accounts:user_edit_roles", pk=pk)
        if changed:
            messages.success(request, f"Role set to {user.role.name if user.role else 'none'}.")
        else:
            messages.info(request, "Role unchanged.")
        return redirect("accounts:user_detail", pk=pk)

    return render(request, "accounts/user_edit_roles.html", {
        "u": user, "roles": roles, **_seg_ctx(user),
        "can_override": __import__("org").is_es(request.user) or request.user.is_superuser,
    })
