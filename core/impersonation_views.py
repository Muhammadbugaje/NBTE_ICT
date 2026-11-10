"""Views for starting / stopping impersonation.

Strict separation (NBTE staff vs institution users):

* The picker lists ONE segment at a time, chosen with ``?segment=``:
  ``nbte`` (default — NBTE staff only), ``institution`` (institution portal
  users, grouped and filterable by institution, state and account kind) or
  ``external`` (resource persons and professional bodies). The segment is a
  server-side filter; the lists are never mixed.
* Acting as an NBTE staff member swaps the user on the internal system (the
  session key read by core.middleware.ImpersonationMiddleware).
* Acting as an institution user goes to the QA portal: an audited, time-boxed
  ``ImpersonationSession`` bound to that exact account is opened with a
  one-time token on the portal host (read-only unless write mode is allowed,
  as for "View as institution"). The internal session is left untouched, so
  the internal system never runs as an institution account.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.models import User

from .impersonation import IMPERSONATE_SESSION_KEY


@login_required
def pick_user(request):
    """C16 / T-PERF-4: a superuser picks someone to act as — search and the
    facets of one segment, 25 per page (Show all capped); HTMX swaps the list."""
    if not request.user.is_superuser:
        raise PermissionDenied("Only superusers can impersonate.")
    from accounts import audience
    from accounts.user_views import _filter_state, facet_choices, filter_users, segment_counts
    from qa_core import ui

    segment = audience.clean_segment(request.GET.get("segment"))
    active = User.objects.filter(is_active=True).exclude(pk=request.user.pk)
    params = request.GET.copy()
    params.pop("active", None)
    users = filter_users(active.select_related("role", "department", "institution", "institution__state",
                                               "professional_body"), params, segment=segment)
    if segment == audience.INSTITUTION:
        # Grouped by institution; the Chief Executive and Head of ICT first.
        users = users.select_related("institution_membership").order_by(
            "institution__name", "institution_membership__account_kind", "first_name", "last_name", "email")
    else:
        users = users.order_by("first_name", "last_name", "email")
    page = ui.paginate(request, users)
    ctx = {"page": page, "f": _filter_state(params), "segment": segment, "seg": audience.SEGMENTS[segment],
           "segment_query": "" if segment == audience.NBTE else f"?segment={segment}",
           "segment_counts": segment_counts(active), **facet_choices(active, segment)}
    return ui.render_qa(request, "core/impersonate.html", ctx,
                        partials={"actas-list": "core/_impersonate_list.html"})


def _start_portal_session(request, target):
    """Open a portal view-as session bound to ``target`` (an institution user)."""
    import datetime
    import secrets

    from django.utils import timezone

    from config import get as cfg
    from core import audit
    from institutions.models import ImpersonationSession
    from qa_core.hosts import portal_url

    inst = target.institution
    try:
        minutes = max(1, int(cfg("qa.impersonation.max_minutes", 30) or 30))
    except (TypeError, ValueError):
        minutes = 30
    allow_write = str(cfg("qa.impersonation.allow_write", False)).strip().lower() in ("1", "true", "yes", "on")
    now = timezone.now()
    reason = "Act as user (superuser support/testing)"
    session = ImpersonationSession.unscoped.create(
        institution=inst, staff=request.user, view_as=target, reason=reason,
        write_mode=bool(allow_write and request.user.is_superuser), token=secrets.token_urlsafe(32),
        started_at=now, expires_at=now + datetime.timedelta(minutes=minutes))
    audit.log("other", session, actor=request.user,
              note=f"act-as started on the QA portal ({minutes} min) as {target.email}")
    return HttpResponseRedirect(portal_url(request, f"/impersonate/{session.token}/"))


@login_required
@require_POST
def start(request, pk):
    if not request.user.is_superuser:
        raise PermissionDenied("Only superusers can impersonate.")
    target = get_object_or_404(User, pk=pk, is_active=True)
    if target.pk == request.user.pk:
        messages.error(request, "You are already yourself.")
        return redirect("core:impersonate_pick")

    if target.user_type == User.INSTITUTION:
        if target.institution_id is None:
            messages.error(request, "This institution account is not linked to an institution.")
            from django.urls import reverse
            return redirect(f"{reverse('core:impersonate_pick')}?segment=institution")
        # Institution users act on the QA portal, never on the internal system.
        request.session.pop(IMPERSONATE_SESSION_KEY, None)
        return _start_portal_session(request, target)

    request.session[IMPERSONATE_SESSION_KEY] = target.pk
    messages.success(
        request,
        f"Now acting as {target.get_full_name() or target.email}. "
        f"Use the banner at the top to return."
    )
    return redirect("core:home")


@login_required
@require_POST
def stop(request):
    request.session.pop(IMPERSONATE_SESSION_KEY, None)
    messages.success(request, "Returned to your own account.")
    return redirect("core:home")
