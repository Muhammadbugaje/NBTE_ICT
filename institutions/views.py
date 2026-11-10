"""Internal institution management screens (Master Plan §6, §13).

    Institutions list · Institution detail (tabs + timeline) · every §6 action
    form (initiate) · action launcher (pick an institution) · bulk import /
    export · Settings → document rules and lookups.

Confirming is the Phase 1 "Pending confirmations" page filtered by action
(the "Confirm X" menu items link there). Every read is scoped with
``.for_user(request.user)`` (§4); object routes answer 404, never 403.
"""
from __future__ import annotations

import datetime

from django.db import transaction
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core import actions
from qa_core.models import ActionPolicy, PendingAction, TimelineEvent
from qa_core.permissions import can_view_institutions, can_view_qa_settings, require, require_or_404
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import change_requests as change_requests_service
from . import forms, services
from .handlers import latest_impersonation
from .models import (
    DocumentRequirement,
    Institution,
    InstitutionChangeRequest,
    InstitutionCategory,
    InstitutionDocument,
    InstitutionImportBatch,
    InstitutionOwnership,
    InstitutionRequest,
    InstitutionType,
    Programme,
)

NI, TS = "New Institution", "TVET Support"


def _impersonating(request):
    return bool(getattr(request, "is_impersonating", False))


def _scoped(request):
    return Institution.objects.for_user(request.user).select_related(
        "ownership", "institution_type", "category", "state", "handling_department")


def _institution_or_404(request, code):
    inst = _scoped(request).filter(code=code.upper()).first()
    if inst is None:
        raise Http404("No such institution in your scope.")
    return inst


# ===========================================================================
# Menu data (sidebar + action menus) — driven by ActionPolicy rows (§6)
# ===========================================================================
def action_menu(user, institution=None):
    """[{group, items:[{code,label,url,confirm_url,enabled,reason,can_initiate}]}]"""
    groups = {}
    for policy in ActionPolicy.objects.filter(menu_group__in=(NI, TS)).order_by("menu_group", "label"):
        try:
            handler = actions.get_handler(policy.action_code)
        except ValidationError:
            continue
        can_initiate = bool(user.is_superuser or not policy.initiate_permission
                            or user.has_perm(policy.initiate_permission))
        can_confirm = bool(policy.requires_second_officer and (user.is_superuser or (
            policy.confirm_permission and user.has_perm(policy.confirm_permission))))
        if not (can_initiate or can_confirm):
            continue  # X47 / rule 5: actions you hold no permission for are not shown at all
        enabled, reason = policy.is_enabled, ("" if policy.is_enabled else policy.description)
        if enabled and institution is not None and hasattr(handler, "available_for"):
            enabled, reason = handler.available_for(institution)
        if policy.action_code == "institutions.add_tvet":
            url = reverse("qa_inst:add_tvet")
        elif policy.action_code == "institutions.bulk_import":
            url = reverse("qa_inst:import")
        elif institution is not None:
            url = reverse("qa_inst:action", args=[institution.code, policy.action_code])
        else:
            url = reverse("qa_inst:launcher", args=[policy.action_code])
        groups.setdefault(policy.menu_group, []).append({
            "code": policy.action_code, "label": policy.label, "url": url, "enabled": enabled and can_initiate,
            "reason": reason if enabled is False else ("" if can_initiate else "You cannot start this action."),
            "two_step": policy.requires_second_officer, "icon": getattr(handler, "icon", "edit"),
            "confirm_url": (reverse("qa:pending_list") + f"?status=pending&action={policy.action_code}")
            if policy.requires_second_officer else "",
            "summary": getattr(handler, "summary", "") or policy.description,
        })
    order = [NI, TS]
    return [{"group": g, "items": groups[g]} for g in order if g in groups]


# ===========================================================================
# List
# ===========================================================================
STATUS_CHOICES = list(Institution.STATUS_LABELS.items())


@require(can_view_institutions)
@require_GET
def institution_list(request):
    f = request.GET
    qs = services.filter_institutions(_scoped(request), q=f.get("q", ""), status=f.get("status", ""),
                                      ownership=f.get("ownership", ""), type_code=f.get("type", ""),
                                      dept=f.get("dept", ""), zone=f.get("zone", ""), sort=f.get("sort", "name"))
    page = paginate(request, qs)
    base = _scoped(request)
    from staff.models import Department, StateOfOrigin

    ctx = {
        "page": page, "filters": f, "statuses": STATUS_CHOICES,
        "ownerships": InstitutionOwnership.objects.filter(is_active=True),
        "types": InstitutionType.objects.filter(is_active=True),
        "departments": Department.objects.filter(pk__in=base.values_list("handling_department", flat=True)).order_by("name"),
        "zones": StateOfOrigin.ZONE_CHOICES,
        "counts": {"total": base.count(), "proposed": base.filter(status__in=("proposed", "under_review")).count(),
                   "approved": base.filter(status="approved").count(),
                   "pending": PendingAction.objects.filter(status="pending", policy__menu_group__in=(NI, TS))
                   .filter(pk__in=actions.visible_to(request.user).values("pk")).count()},
        "menu": action_menu(request.user),
        "can_export": request.user.has_perm("institutions.export_institutions") or request.user.is_superuser,
        "can_import": request.user.has_perm("institutions.initiate_bulk_import") or request.user.is_superuser,
        "can_add": request.user.has_perm("institutions.initiate_add_tvet") or request.user.is_superuser,
        "can_review_change_requests": (change_requests_service.is_lead(request.user)
                                       or change_requests_service.is_ict_reviewer(request.user)),
    }
    return render_qa(request, "institutions/list.html", ctx, {"qa-panel": "institutions/_list_panel.html",
                                                               "qa-rows": "institutions/_rows.html"})


# ===========================================================================
# Detail (tabs)
# ===========================================================================
TABS = [("overview", "Overview"), ("programmes", "Programmes"), ("documents", "Documents"),
        ("issued", "Issued documents"), ("users", "Users"),
        ("applications", "Applications"), ("payments", "Payments"), ("changes", "Changes"),
        ("timeline", "Timeline")]


def _visible_document_checklist(request, inst):
    """v2.2: only the reviewing department's people see a document; others
    get a locked row (name and department only). ES, super admin and the
    Director of ICT see all; the assigned ICT officer sees all, read-only."""
    from . import document_access

    rows, _hidden = document_access.filter_rows(request.user, inst, services.checklist(inst))
    return rows


TAB_ICONS = {"overview": "home", "programmes": "layers", "documents": "file", "issued": "qr", "users": "users",
             "applications": "sheet", "payments": "card", "changes": "edit", "timeline": "clock"}


def _hero_progress(inst, variant):
    """v2.3: the onboarding journey for the hero (plain-words steps only; the
    SOP-03 stage name is shown to full-journey viewers)."""
    try:
        from qa_core import spotlight

        if inst.status not in spotlight.ONBOARDING_STATUSES:
            return None   # approved / delisted files show their status, not onboarding
        out = dict(spotlight.progress(inst))
    except Exception:
        return None
    if not out.get("steps"):
        return None
    if variant not in (None, "full"):
        out["case"] = ""
    return out


@require(can_view_institutions)
@require_GET
def institution_detail(request, code):
    inst = _institution_or_404(request, code)
    tab = request.GET.get("tab", "overview")
    if tab not in dict(TABS):
        tab = "overview"
    # v2.1 P.6 (X2/X3): full journey / your department's part / my work.
    from assignments import journey
    jctx = journey.context(request.user, inst, request=request)
    tabs = journey.allowed_tabs(request.user, inst, TABS, jctx.get("variant"))
    tabs = _with_issued_tab(request.user, inst, tabs)
    if tab not in dict(tabs):
        tab = "overview"
    ctx = {"inst": inst, "tab": tab, "tabs": tabs, "menu": action_menu(request.user, inst), "journey": jctx,
           "tab_icons": TAB_ICONS}
    if request.headers.get("HX-Target") not in ("qa-tab", "qa-prog-rows"):
        ctx["inst_progress"] = _hero_progress(inst, jctx.get("variant"))
        if jctx.get("variant") in (None, "full"):  # SOP-03 redesign: straight to the case cockpit
            try:
                from accreditation import case_services as qcs

                ctx["open_case"] = (qcs.visible_cases(request.user).filter(institution=inst)
                                    .exclude(status="closed").order_by("-pk").first())
            except Exception:  # noqa: BLE001 — never break the institution page over this link
                ctx["open_case"] = None
    ctx.update(_tab_context(request, inst, tab))
    if tab == "overview":
        ctx.update(_oversight_context(request.user, inst))
    return render_qa(request, "institutions/detail.html", ctx, {"qa-panel": "institutions/_detail_panel.html",
                                                                 "qa-tab": f"institutions/tabs/_{tab}.html",
                                                                 "qa-prog-rows": "institutions/tabs/_programme_rows.html"})


@require_or_404(can_view_institutions)
@require_POST
def chief_executive_action(request, code, verb):
    """v2.2: NBTE side of the Chief Executive's account — e-mail the set-up
    link to the CE, reveal it once to send another way, or issue a one-time
    password. ES, super admin, Director of ICT and the assigned ICT officer."""
    inst = _institution_or_404(request, code)
    if getattr(request, "is_impersonating", False) or not services.can_manage_ce_access(request.user, inst):
        raise PermissionDenied
    result, error = {}, ""
    try:
        if verb == "email":
            result = services.nbte_send_ce_link(inst, actor=request.user, request=request)
        elif verb == "reveal":
            result = services.nbte_send_ce_link(inst, actor=request.user, request=request, reveal=True)
        elif verb == "password":
            result = {"password": services.nbte_issue_ce_password(inst, actor=request.user)}
        else:
            raise Http404
    except ValidationError as exc:
        error = " ".join(exc.messages)
    response = render(request, "institutions/_ce_access.html", {
        "inst": inst, "ce": services.ce_status(inst), "can_manage_ce": True, "result": result, "verb": verb,
        "error": error, "ce_auto_email": services.ce_auto_email()})
    response["Cache-Control"] = "no-store, private"
    if error:
        return toast(response, "error", "Not done", error)
    titles = {"email": "Set-up link e-mailed to the Chief Executive", "reveal": "Set-up link ready (shown once)",
              "password": "One-time password issued (shown once)"}
    return toast(response, "success", titles[verb], "Recorded in the institution's audit trail.")


def _can_assign_oversight(user):
    import org
    from qa_core.departments import qa_department

    ict = qa_department("ict")
    return bool(ict and (user.is_superuser or org.is_es(user) or org.is_super_admin(user)
                         or org.is_director(user, ict)))


def _oversight_context(user, inst):
    from accounts.models import User
    from assignments.models import WorkAssignment
    from qa_core.departments import qa_department

    ict = qa_department("ict")
    can_assign = bool(inst.status in ("proposed", "under_review") and ict and _can_assign_oversight(user))
    assignments = WorkAssignment.objects.filter(
        institution=inst, kind=WorkAssignment.OVERSIGHT_ICT, status__in=WorkAssignment.OPEN)
    if not _can_assign_oversight(user):
        assignments = assignments.filter(assignee=user)
    current = assignments.select_related("assignee").order_by("-assigned_at").first()
    candidates = []
    suggestion = None
    if can_assign:
        from accounts.audience import nbte_staff_users_q

        candidates = list(User.objects.filter(nbte_staff_users_q())
                          .filter(Q(department=ict) | Q(staff_profile__department=ict))
                          .select_related("role").distinct().order_by("first_name", "last_name", "email"))
        if current is None:  # X19: the rotation suggests the next ICT officer
            try:
                import assignments.rotation as rotation

                s = rotation.suggest_ict_supervisor(inst, record=False)
                suggestion = s.suggested.user if s.suggested is not None else None
            except Exception:
                suggestion = None
    return {"oversight_assignment": current, "oversight_candidates": candidates, "oversight_suggestion": suggestion,
            "can_assign_oversight": can_assign}


@require(can_view_institutions)
@require_POST
def assign_oversight(request, code):
    from accounts.models import User
    from assignments import work
    from assignments.models import WorkAssignment
    from qa_core.departments import qa_department

    inst = _institution_or_404(request, code)
    if not _can_assign_oversight(request.user) or inst.status not in ("proposed", "under_review"):
        raise Http404
    ict = qa_department("ict")
    target = User.objects.filter(pk=request.POST.get("assignee"), is_active=True, user_type=User.STAFF).filter(
        Q(department=ict) | Q(staff_profile__department=ict)).first()
    if target is None:
        messages.error(request, "Choose an active ICT staff member to supervise this institution.")
        from core.urlsafe import safe_next

        return redirect(safe_next(request, request.POST.get("next"),
                                  f"{reverse('qa_inst:detail', args=[inst.code])}?tab=overview"))
    existing = (WorkAssignment.objects.filter(
        institution=inst, kind=WorkAssignment.OVERSIGHT_ICT, status__in=WorkAssignment.OPEN)
        .order_by("-assigned_at").first())
    if existing is not None and existing.assignee_id == target.pk:
        messages.info(request, f"{target.get_full_name() or target.email} is already supervising {inst.name}.")
    elif existing is not None:
        work.reassign(existing, target, request.user)
        _close_oversight_task(request, inst)
        messages.success(request, f"{target.get_full_name() or target.email} is assigned to supervise {inst.name}.")
    else:
        work.assign(inst, WorkAssignment.OVERSIGHT_ICT, target, subject=inst, department=ict,
                    assigned_by=request.user, role_on_work="supervisor",
                    meta={"registration_oversight": True})
        _close_oversight_task(request, inst)
        messages.success(request, f"{target.get_full_name() or target.email} is assigned to supervise {inst.name}.")
    nxt = request.POST.get("next") or ""
    from django.utils.http import url_has_allowed_host_and_scheme

    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)   # v2.2: assigned from the dashboard spotlight
    return redirect(f"{reverse('qa_inst:detail', args=[inst.code])}?tab=overview")


def _close_oversight_task(request, inst):
    """X19: the "Assign an ICT supervisor" task closes itself in QA."""
    from qa_core import task_bridge

    task_bridge.close_for_stage(inst, "oversight_ict", reason="supervisor appointed", actor=request.user)


@require(lambda user: can_view_institutions(user) or change_requests_service.is_ict_reviewer(user))
def change_request_list(request):
    if change_requests_service.is_lead(request.user):
        rows = InstitutionChangeRequest.unscoped.select_related("institution", "requested_by", "assigned_to")
    elif change_requests_service.is_ict_reviewer(request.user):
        rows = InstitutionChangeRequest.unscoped.filter(assigned_to=request.user).select_related(
            "institution", "requested_by", "assigned_to")
    else:
        raise Http404
    return render_qa(request, "institutions/change_requests.html",
                     {"requests": rows, "can_assign": change_requests_service.is_lead(request.user)})


@require(lambda user: can_view_institutions(user) or change_requests_service.is_ict_reviewer(user))
@require_http_methods(["GET", "POST"])
def change_request_detail(request, pk):
    row = InstitutionChangeRequest.unscoped.select_related(
        "institution", "requested_by", "assigned_to", "assigned_by", "decided_by").filter(pk=pk).first()
    if row is None:
        raise Http404
    is_lead = change_requests_service.is_lead(request.user)
    if not is_lead and row.assigned_to_id != request.user.pk:
        raise Http404
    if request.method == "POST":
        try:
            if request.POST.get("action") == "assign" and is_lead:
                assignee = change_requests_service.reviewer_candidates().filter(
                    pk=request.POST.get("assignee")).first()
                if assignee is None:
                    raise ValidationError({"assignee": "Choose an active ICT staff member."})
                change_requests_service.assign(row, actor=request.user, assignee=assignee)
                messages.success(request, f"{row.reference} was assigned to {assignee.get_full_name() or assignee.email}.")
            elif request.POST.get("action") in ("review", "approve", "reject"):
                decision = request.POST["action"]
                change_requests_service.decide(row, actor=request.user, decision=decision,
                                              note=request.POST.get("note", ""))
                if decision == "review":
                    messages.success(request, f"{row.reference} was sent for senior NBTE approval.")
                else:
                    messages.success(request, f"{row.reference} was {('approved' if decision == 'approve' else 'not approved')}.")
            else:
                raise PermissionDenied("You cannot perform that action on this request.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        return redirect("qa_inst:change_request_detail", pk=pk)
    reviewers = change_requests_service.reviewer_candidates() if is_lead else []
    if row.change_type == InstitutionChangeRequest.PROFILE and is_lead:
        reviewers = [user for user in reviewers if not change_requests_service.is_lead(user)]
    profile_request = row.change_type == InstitutionChangeRequest.PROFILE
    return render_qa(request, "institutions/change_request_detail.html",
                     {"change_request": row, "can_assign": is_lead,
                      "can_review_profile": (profile_request and row.status == InstitutionChangeRequest.ASSIGNED
                                             and row.assigned_to_id == request.user.pk),
                      "can_final_profile_decide": (profile_request and row.status == InstitutionChangeRequest.REVIEWED
                                                    and is_lead),
                      "can_decide": (not profile_request and row.status == InstitutionChangeRequest.ASSIGNED
                                     and (is_lead or row.assigned_to_id == request.user.pk)),
                      "reviewers": reviewers})


def _with_issued_tab(user, inst, tabs):
    """Issued documents: also for the handling department's Director / the ES
    even when the journey variant hides it."""
    keys = [k for k, _label in tabs]
    if "issued" in keys:
        return tabs
    from documents import official_letters, services as doc_services

    if not (doc_services.can_view_register(user) or official_letters.is_full(user)
            or (inst.handling_department_id and official_letters.directed_department(user) is not None
                and official_letters.directed_department(user).pk == inst.handling_department_id)):
        return tabs
    out = list(tabs)
    at = keys.index("documents") + 1 if "documents" in keys else len(out)
    out.insert(at, ("issued", "Issued documents"))
    return out


def _tab_context(request, inst, tab):
    user = request.user
    ctx = {}
    lifecycle = services.lifecycle(inst)
    ctx["lifecycle_label"] = inst.status_label
    if tab == "overview":
        checklist = _visible_document_checklist(request, inst)
        ctx.update({
            "contacts": services.contacts_of(inst), "addresses": services.addresses_of(inst),
            "primary_user": inst.primary_user(),
            "programme_count": Programme.objects.for_user(user).filter(institution=inst).exclude(status="dropped").count(),
            "doc_summary": services.checklist_summary(checklist),
            "pending_here": _pending_for(request, inst)[:5],
            "recent": list(TimelineEvent.objects.for_user(user).filter(institution=inst)[:6]),
            "impersonation": latest_impersonation(user, inst),
        })
    elif tab == "programmes":
        ctx.update(_programme_tab(request, inst))
    elif tab == "documents":
        from . import document_access

        rows = _visible_document_checklist(request, inst)
        ctx.update({"rows": rows, "locked_count": sum(1 for r in rows if r.get("locked")),
                    "doc_watcher": document_access.is_watcher(user, inst)
                    and not document_access.is_full_viewer(user),
                    "history": list(document_access.filter_documents(
                        user, inst, InstitutionDocument.objects.for_user(user).filter(institution=inst))
                        .select_related("kind", "uploaded_by", "reviewed_by")
                        .order_by("kind__name", "-version")[:200])})
    elif tab == "issued":
        from documents import official_letters

        issued = official_letters.institution_documents(user, inst)
        ctx.update({"issued": issued, "issued_counts": official_letters.institution_counts(issued),
                    "issued_sla": official_letters.sla_days(),
                    "outcome_letters": _outcome_letters(user, inst)})
    elif tab == "users":
        from .models import ManagementStaff

        team = list(ManagementStaff.unscoped.filter(institution=inst, is_current=True).select_related("role")
                    .order_by("role__sort_order", "role__name"))
        phones = {t.user_id: t.phone for t in team if t.user_id and t.phone}
        emails = {t.email.lower(): t.phone for t in team if t.email and t.phone}
        members = services.members_of(inst)
        for m in members:
            m.phone = phones.get(m.user_id) or emails.get((m.user.email or "").lower(), "")
        ctx.update({"members": members, "team": team,
                    "contacts": services.contacts_of(inst),
                    "contact_history": services.contact_history(inst),
                    "address_history": services.addresses_of(inst, current=False),
                    "ce": services.ce_status(inst),
                    "can_manage_ce": services.can_manage_ce_access(user, inst),
                    "ce_auto_email": services.ce_auto_email()})
    elif tab == "applications":
        from accreditation.models import Application

        ctx["applications"] = list(Application.objects.for_user(user).filter(institution=inst).order_by("-created_at"))
        ctx["requests"] = list(InstitutionRequest.objects.for_user(user).filter(institution=inst)
                               .select_related("request_type").order_by("-created_at"))
    elif tab == "payments":
        from payments.models import PaymentObligation
        from payments.services import stage_label

        can_see = services.can_see_payments(user)
        rows = list(PaymentObligation.objects.for_user(user).filter(institution=inst).order_by("-created_at")) \
            if can_see else []
        for ob in rows:
            ob.stage = stage_label(ob)
        ctx.update({"obligations": rows, "can_see_payments": can_see})
    elif tab == "changes":
        ctx["pending"] = _pending_for(request, inst, all_statuses=True)
    elif tab == "timeline":
        vis = request.GET.get("vis", "")
        if vis not in ("public", "internal"):
            vis = ""
        tq = (request.GET.get("tq") or "").strip()
        qs = services.timeline_for(user, inst, vis=vis, text=tq)
        ctx.update({"page": paginate(request, qs), "vis": request.GET.get("vis", ""), "tq": tq})
    return ctx


def _outcome_letters(user, inst):
    """Programme decision letters (original + duplicate) the user may open."""
    try:
        from accreditation.models import OutcomeLetter

        rows = list(OutcomeLetter.objects.for_user(user).filter(case__institution=inst)
                    .select_related("programme", "decision").prefetch_related("copies").order_by("-generated_at")[:100])
    except Exception:
        return []
    for row in rows:
        row.visible_copies = [c for c in row.copies.all() if c.can_download(user)]
    return rows


# v2.3: programme status groups for the filter pills (code, label, chip tone).
PROGRAMME_GROUPS = [("approved", "Approved / accredited", "green"),
                    ("commence", "Approval to commence", "teal"),
                    ("interim", "Interim", "violet"),
                    ("pending", "Pending", "gold"),
                    ("denied", "Denied", "red"),
                    ("expired", "Expired", "amber"),
                    ("dropped", "Dropped", "plain")]
PROGRAMME_GROUP_STATUSES = {"approved": ("accredited", "approved_pending_body_letter"),
                            "commence": ("approval_to_commence",), "interim": ("interim",),
                            "pending": ("proposed", "resource_inspected"), "denied": ("denied",),
                            "expired": ("expired",), "dropped": ("dropped",)}
PROGRAMME_STATUS_CHIP = {"accredited": "green", "approved_pending_body_letter": "green",
                         "approval_to_commence": "blue", "interim": "violet", "proposed": "gold",
                         "resource_inspected": "gold", "denied": "red", "expired": "red", "dropped": "plain"}


def _programme_tab(request, inst):
    """Phase 8: every programme of the institution — search, filters (status,
    level, category, professional body, expiring within N days, in an open
    request), 25 a page with "Show all", and the request / case each one is
    in now."""
    from . import programme_info
    from .models import ProfessionalBody, ProgrammeCategory, ProgrammeLevel

    f = request.GET
    base = Programme.objects.for_user(request.user).filter(institution=inst).select_related(
        "level", "category", "category__discipline")
    qs = base
    q = (f.get("pq") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(category__name__icontains=q))
    if f.get("pstatus"):
        qs = qs.filter(status=f["pstatus"])
    pgroup = f.get("pgroup", "")
    if pgroup in PROGRAMME_GROUP_STATUSES:
        qs = qs.filter(status__in=PROGRAMME_GROUP_STATUSES[pgroup])
    else:
        pgroup = ""
    if f.get("level"):
        qs = qs.filter(level__code=f["level"])
    if f.get("category"):
        qs = qs.filter(category_id=f["category"]) if f["category"].isdigit() else qs.none()
    expiring = (f.get("expiring") or "").strip()
    if expiring.isdigit():
        today = timezone.localdate()
        qs = qs.filter(expiry_date__gte=today, expiry_date__lte=today + datetime.timedelta(days=int(expiring)))
    programmes = list(qs.order_by("name", "level__sort_order"))
    bodies = programme_info.bodies_map(programmes)
    work = programme_info.open_work(programmes, request.user)
    if f.get("body"):
        wanted = f["body"]
        programmes = [p for p in programmes if any(str(b.pk) == wanted for b in bodies.get(p.pk, []))] \
            if wanted != "none" else [p for p in programmes if not bodies.get(p.pk)]
    if f.get("open") == "1":
        programmes = [p for p in programmes if p.pk in work]
    elif f.get("open") == "0":
        programmes = [p for p in programmes if p.pk not in work]
    page = paginate(request, programmes)
    for p in page["rows"]:
        p.bodies = bodies.get(p.pk, [])
        p.work = work.get(p.pk)
        p.expiry_info = programme_info.expiry(p)
        p.approval_type_name = programme_info.approval_type_name(p)
        p.chip = PROGRAMME_STATUS_CHIP.get(p.status, "plain")
    all_ids = list(base.values_list("pk", flat=True))
    # v2.3: status groups and levels with counts (pills, stat strip and bar).
    by_status = {r["status"]: r["n"] for r in base.order_by().values("status").annotate(n=Count("pk"))}
    total = len(all_ids)

    def link(**changes):
        params = request.GET.copy()
        for key in ("page", "all", "pstatus"):
            params.pop(key, None)
        params["tab"] = "programmes"
        for key, value in changes.items():
            params.pop(key, None)
            if value:
                params[key] = value
        return f"?{params.urlencode()}"

    groups = [{"code": "", "label": "All", "count": total, "url": link(pgroup=""), "tone": "all"}]
    for code, label, tone in PROGRAMME_GROUPS:
        n = sum(by_status.get(st, 0) for st in PROGRAMME_GROUP_STATUSES[code])
        groups.append({"code": code, "label": label, "count": n, "url": link(pgroup=code), "tone": tone,
                       "pct": round(100 * n / total, 1) if total else 0})
    level_counts = sorted((r["level__code"] or "", r["n"]) for r in
                          base.order_by().values("level__code").annotate(n=Count("pk")))
    level_pills = [{"code": "", "label": "All levels", "count": total, "url": link(level="")}] + [
        {"code": code, "label": code.upper(), "count": n, "url": link(level=code)} for code, n in level_counts if code]
    return {"page": page, "filters": f, "pstatus": f.get("pstatus", ""), "pgroup": pgroup,
            "programme_groups": groups, "level_pills": level_pills if len(level_pills) > 2 else [],
            "advanced_filter": any(f.get(k) for k in ("category", "body", "expiring", "open")),
            "programme_statuses": Programme.STATUS_CHOICES,
            "levels": ProgrammeLevel.objects.filter(is_active=True),
            "categories": ProgrammeCategory.objects.filter(programmes__in=all_ids).distinct().order_by("name"),
            "bodies": ProfessionalBody.objects.filter(is_active=True).order_by("name"),
            "programme_total": len(all_ids), "any_filter": any(f.get(k) for k in (
                "pq", "pstatus", "pgroup", "level", "category", "body", "expiring", "open"))}


def _pending_for(request, inst, all_statuses=False):
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(Institution)
    qs = actions.visible_to(request.user).filter(content_type=ct, object_id=str(inst.pk))
    if not all_statuses:
        qs = qs.filter(status="pending")
    return list(qs.order_by("-initiated_at")[:50])


# ===========================================================================
# Actions (initiate)
# ===========================================================================
def _policy_or_404(code):
    policy = ActionPolicy.objects.filter(action_code=code, menu_group__in=(NI, TS)).first()
    if policy is None:
        raise Http404
    try:
        handler = actions.get_handler(code)
    except ValidationError:
        raise Http404 from None
    return policy, handler


def _form_for(handler, request, institution, data=None):
    cls = getattr(handler, "form_class", forms.InstitutionActionForm)
    if cls is forms.AddTvetForm:
        return cls(data, request.FILES or None) if data is not None else cls()
    initial = None
    if data is None and request.GET:  # v2.1 R.7 X48: inline Edit / Drop pre-select the programme
        initial = {k: (request.GET.getlist(k) if k == "programmes" else request.GET.get(k)) for k in request.GET}
    return cls(data, institution=institution, user=request.user, initial=initial)


@require(can_view_institutions)
@require_http_methods(["GET", "POST"])
def action(request, code, action_code):
    inst = _institution_or_404(request, code)
    policy, handler = _policy_or_404(action_code)
    return _action_page(request, policy, handler, inst)


@require(can_view_institutions)
@require_http_methods(["GET", "POST"])
def add_tvet(request):
    policy, handler = _policy_or_404("institutions.add_tvet")
    return _action_page(request, policy, handler, None)


def _action_page(request, policy, handler, inst):
    user = request.user
    allowed = user.is_superuser or not policy.initiate_permission or user.has_perm(policy.initiate_permission)
    if not allowed:
        # The page itself is refused (not only the POST): no form, no hint of the action.
        raise PermissionDenied("You cannot start this action.")
    available, why = True, ""
    if not policy.is_enabled:
        available, why = False, policy.description
    elif inst is not None and hasattr(handler, "available_for"):
        available, why = handler.available_for(inst)
    form = _form_for(handler, request, inst, request.POST or None)
    error = ""
    if request.method == "POST":
        if not allowed or not available:
            raise PermissionDenied(why or "You cannot start this action.")
        if form.is_valid():
            payload = form.payload()
            reason = (form.cleaned_data.get("reason") or "").strip()
            payload["reason"] = reason
            target = handler.resolve_target(inst, form) if hasattr(handler, "resolve_target") else inst
            try:
                pa = actions.initiate(policy.action_code, actor=user, target=target, payload=payload,
                                      reason=reason, impersonating=_impersonating(request))
            except (ValidationError, PermissionDenied) as exc:
                error = " ".join(getattr(exc, "messages", [str(exc)]))
            else:
                return _after_initiate(request, policy, pa, inst)
    preview = []
    if form.is_bound and form.is_valid() and not error:
        preview = []
    lgas = []
    if inst is not None:
        lgas = services.lgas_for(inst.state)
    if form.is_bound and form.data.get("state"):
        from staff.models import StateOfOrigin

        lgas = services.lgas_for(StateOfOrigin.objects.filter(pk=form.data.get("state")).first())
    ctx = {"inst": inst, "policy": policy, "handler": handler, "form": form, "allowed": allowed,
           "available": available, "why": why, "error": error, "lgas": lgas, "preview": preview,
           "open_pending": _open_pending(request, policy, inst),
           "confirm_url": reverse("qa:pending_list") + f"?status=pending&action={policy.action_code}"}
    response = render_qa(request, "institutions/action.html", ctx, {"qa-panel": "institutions/_action_panel.html"},
                         status=422 if (form.is_bound and (error or form.errors)) else 200)
    if error:
        toast(response, "error", "Not started", error)
    return response


def _open_pending(request, policy, inst):
    if inst is None:
        return None
    from django.contrib.contenttypes.models import ContentType

    return (PendingAction.objects.filter(policy=policy, status="pending",
                                         content_type=ContentType.objects.get_for_model(Institution),
                                         object_id=str(inst.pk)).first())


def _after_initiate(request, policy, pa, inst):
    if pa.status == PendingAction.APPLIED and policy.action_code == "institutions.impersonate":
        session = latest_impersonation(request.user, inst)
        from qa_core.hosts import portal_absolute, portal_reverse

        url = portal_absolute(portal_reverse("qa_portal:impersonation_start", token=session.token), request)
        if is_htmx(request):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = url
            return response
        return HttpResponseRedirect(url)
    if pa.status == PendingAction.APPLIED:
        target = reverse("qa_inst:detail", args=[inst.code]) if inst is not None else reverse("qa_inst:list")
        messages.success(request, f"{policy.label}: done.")
    elif pa.status == PendingAction.FAILED:
        messages.error(request, f"{policy.label} could not be applied: {pa.failure_detail}")
        target = reverse("qa:pending_detail", args=[pa.pk])
    else:
        messages.success(request, f"{pa.reference} is waiting for a second officer.")
        target = reverse("qa:pending_detail", args=[pa.pk])
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = target
        return response
    return redirect(target)


@require(can_view_institutions)
@require_GET
def launcher(request, action_code):
    """Choose the institution for a TVET Support action (sidebar entry)."""
    policy, handler = _policy_or_404(action_code)
    user = request.user
    if not (user.is_superuser or not policy.initiate_permission or user.has_perm(policy.initiate_permission)):
        raise PermissionDenied("You cannot start this action.")
    qs = _scoped(request)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    f = request.GET
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("ownership"):
        qs = qs.filter(ownership__code=f["ownership"])
    if f.get("dept"):
        qs = qs.filter(handling_department__code=f["dept"])
    page = paginate(request, qs.order_by("name"))
    for inst in page["rows"]:
        inst.available, inst.why = handler.available_for(inst) if hasattr(handler, "available_for") else (True, "")
    from staff.models import Department

    base = _scoped(request)
    ctx = {"policy": policy, "handler": handler, "page": page, "filters": f, "statuses": STATUS_CHOICES,
           "ownerships": InstitutionOwnership.objects.filter(is_active=True),
           "departments": Department.objects.filter(pk__in=base.values_list("handling_department", flat=True)),
           "confirm_url": reverse("qa:pending_list") + f"?status=pending&action={policy.action_code}"}
    return render_qa(request, "institutions/launcher.html", ctx, {"qa-panel": "institutions/_launcher_panel.html",
                                                                   "qa-rows": "institutions/_launcher_rows.html"})


@require(can_view_institutions)
@require_GET
def lga_options(request):
    from staff.models import StateOfOrigin

    state = StateOfOrigin.objects.filter(pk=request.GET.get("state") or 0).first()
    if request.GET.get("select"):  # v2.1 R.7: <select> options for the Add TVET LGA dropdown
        return render(request, "qa_portal/_lga_select_options.html", {"lgas": services.lgas_for(state),
                                                                      "state": state})
    return render(request, "qa_core/partials/_lga_options.html", {"lgas": services.lgas_for(state)})


@require(can_view_institutions)
@require_GET
def code_suggestion(request):
    name = request.GET.get("name", "")
    try:
        code = services.allocate_code(name) if len(name.strip()) >= 4 else ""
    except ValidationError:
        code = ""
    return HttpResponse(code, content_type="text/plain")


# ===========================================================================
# Bulk import / export
# ===========================================================================
def _can_import(user):
    return user.is_superuser or user.has_perm("institutions.initiate_bulk_import")


@require(lambda u: can_view_institutions(u) and _can_import(u))
@require_http_methods(["GET", "POST"])
def import_page(request):
    from .importexport import COLUMNS, stage_batch

    form = forms.ImportUploadForm(request.POST or None, request.FILES or None)
    batch = None
    if request.method == "POST" and form.is_valid():
        try:
            batch = stage_batch(form.cleaned_data["file"], form.cleaned_data["kind"], user=request.user)
        except ValidationError as exc:
            form.add_error("file", exc)
        else:
            if is_htmx(request):
                response = render(request, "institutions/import/_batch.html", _batch_context(request, batch))
                kind = "success" if batch.status == "validated" else "error"
                return toast(response, kind, "Sheet checked" if kind == "success" else "The sheet has errors",
                             f"{batch.summary.get('total', 0)} row(s) read.")
            return redirect("qa_inst:import_batch", pk=batch.pk)
    recent = InstitutionImportBatch.objects.filter(created_by=request.user).order_by("-created_at")[:8]
    ctx = {"form": form, "batch": batch, "recent": recent, "columns": COLUMNS,
           "can_export": request.user.has_perm("institutions.export_institutions") or request.user.is_superuser}
    status = 422 if form.is_bound and form.errors else 200
    if is_htmx(request) and request.method == "POST":
        return render(request, "institutions/import/_upload.html", ctx, status=status)
    return render_qa(request, "institutions/import/page.html", ctx, {"qa-panel": "institutions/import/_panel.html"},
                     status=status)


def _batch_context(request, batch):
    pa = (PendingAction.objects.filter(policy_id="institutions.bulk_import", object_id=str(batch.pk))
          .order_by("-initiated_at").first())
    ctx = {"batch": batch, "pa": pa, "rows": batch.rows[:500], "errors": batch.errors[:500]}
    if batch.kind == "legacy":
        ctx["sheet_counts"] = _sheet_counts(batch)
        if batch.status == "applied":
            from .migration import pending_invites

            codes = (batch.result or {}).get("institution_codes") or []
            ctx["awaiting_invite"] = pending_invites(list(_scoped(request).filter(code__in=codes)))
            ctx["invites"] = (batch.result or {}).get("invites") or {}
            ctx["can_send_credentials"] = (request.user.is_superuser
                                           or request.user.has_perm("institutions.send_login_credentials"))
    return ctx


def _sheet_counts(batch):
    out = {}
    for row in batch.rows or []:
        sheet = {"institution": "Institutions", "programme": "Programmes", "history": "History"}.get(
            row.get("_sheet"), "Rows")
        item = out.setdefault(sheet, {"create": 0, "update": 0, "unchanged": 0})
        item[row.get("_action", "unchanged")] = item.get(row.get("_action", "unchanged"), 0) + 1
    return out


@require_or_404(lambda u: can_view_institutions(u) and (u.is_superuser
                                                         or u.has_perm("institutions.send_login_credentials")))
@require_POST
def import_invites(request, pk):
    """Send Login Credentials, in bulk, to every institution of an applied
    migration whose portal administrator has not had a link yet."""
    from .migration import pending_invites

    batch = InstitutionImportBatch.objects.filter(pk=pk, kind="legacy", status="applied").first()
    if batch is None or not (batch.created_by_id == request.user.pk or request.user.is_superuser
                             or request.user.has_perm("institutions.confirm_bulk_import")
                             or request.user.has_perm("institutions.send_login_credentials")):
        raise Http404
    codes = (batch.result or {}).get("institution_codes") or []
    sent, failed = 0, []
    for inst in pending_invites(list(_scoped(request).filter(code__in=codes))):
        try:
            pa = actions.initiate("institutions.send_login_credentials", actor=request.user, target=inst,
                                  payload={"reason": f"Migration batch #{batch.pk}"},
                                  reason=f"Migration batch #{batch.pk}", impersonating=_impersonating(request))
        except (ValidationError, PermissionDenied) as exc:
            failed.append(f"{inst.code}: {' '.join(getattr(exc, 'messages', [str(exc)]))}")
            continue
        if pa.status == PendingAction.APPLIED:
            sent += 1
        else:
            failed.append(f"{inst.code}: {pa.failure_detail or pa.get_status_display()}")
    result = dict(batch.result or {})
    invites = dict(result.get("invites") or {})
    invites.update({line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in failed})
    result["invites"] = invites
    InstitutionImportBatch.objects.filter(pk=batch.pk).update(result=result)
    batch.refresh_from_db()
    response = render_qa(request, "institutions/import/batch.html", _batch_context(request, batch),
                         {"qa-panel": "institutions/import/_batch.html", "qa-batch": "institutions/import/_batch.html"})
    if failed and not sent:
        return toast(response, "error", "No links sent", "; ".join(failed)[:300])
    return toast(response, "success", f"{sent} set-password link(s) sent", "; ".join(failed)[:300])


@require_or_404(lambda u: can_view_institutions(u) and _can_import(u))
@require_http_methods(["GET", "POST"])
def import_batch(request, pk):
    batch = InstitutionImportBatch.objects.filter(pk=pk).first()
    if batch is None or not (batch.created_by_id == request.user.pk or request.user.is_superuser
                             or request.user.has_perm("institutions.confirm_bulk_import")):
        raise Http404
    if request.method == "POST":
        if batch.created_by_id != request.user.pk or batch.status != "validated":
            raise Http404
        reason = (request.POST.get("reason") or "").strip()
        try:
            if len(reason) < 5:
                raise ValidationError("Say what this import is for (at least 5 characters).")
            # Mark the batch pending *before* initiating, in one transaction: the
            # pending action records the batch's version, and saving the batch
            # afterwards would make every confirmation fail as stale.
            with transaction.atomic():
                batch.status = "pending"
                batch.save(update_fields=["status", "updated_at"])
                payload = {"reason": reason}
                if batch.kind == "legacy":
                    payload["send_invites"] = request.POST.get("send_invites") == "1"
                pa = actions.initiate("institutions.bulk_import", actor=request.user, target=batch,
                                      payload=payload, reason=reason,
                                      impersonating=_impersonating(request))
                if pa.status != PendingAction.PENDING:
                    batch.refresh_from_db()
        except (ValidationError, PermissionDenied) as exc:
            batch.refresh_from_db()
            response = render(request, "institutions/import/_batch.html",
                              {**_batch_context(request, batch), "error": " ".join(getattr(exc, "messages", [str(exc)]))})
            return toast(response, "error", "Not sent", " ".join(getattr(exc, "messages", [str(exc)])))
        response = render(request, "institutions/import/_batch.html", _batch_context(request, batch))
        return toast(response, "success", f"{pa.reference} sent for confirmation",
                     "A second officer must confirm before anything is created or changed.")
    return render_qa(request, "institutions/import/batch.html", _batch_context(request, batch),
                     {"qa-panel": "institutions/import/_batch.html"})


@require(lambda u: can_view_institutions(u) and _can_import(u))
@require_GET
def import_template(request, kind):
    from .importexport import template_xlsx

    if kind not in ("institutions", "programmes", "legacy"):
        raise Http404
    if kind == "legacy":
        from .migration import template_xlsx as migration_template

        data = migration_template()
        kind = "migration"
    else:
        data = template_xlsx(kind)
    response = HttpResponse(data,
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="nbte-qa-{kind}-template.xlsx"'
    return response


@require(lambda u: can_view_institutions(u) and (u.is_superuser or u.has_perm("institutions.export_institutions")))
@require_GET
def export(request):
    from .importexport import export_institutions_xlsx, export_programmes_xlsx

    kind = request.GET.get("kind", "institutions")
    stamp = timezone.localdate().isoformat()
    if kind == "programmes":
        rows = (Programme.objects.for_user(request.user).select_related("institution", "level", "category")
                .order_by("institution__code", "name"))
        data = export_programmes_xlsx(rows)
    else:
        data = export_institutions_xlsx(_scoped(request).order_by("code"))
    from core import audit

    audit.log("other", request.user, actor=request.user, note=f"exported QA {kind}")
    response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="nbte-qa-{kind}-{stamp}.xlsx"'
    return response


# ===========================================================================
# Settings → QA → Document rules and lookups (§14 tables)
# ===========================================================================
def _can_edit_docs(user):
    return user.is_superuser or user.has_perm("institutions.manage_document_requirements")


def _can_edit_lookups(user):
    return user.is_superuser or user.has_perm("institutions.manage_institution_lookups")


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def settings_documents(request):
    from qa_core.views import _settings_context

    editable = _can_edit_docs(request.user)
    edit_id = request.GET.get("edit") or request.POST.get("id")
    instance = DocumentRequirement.objects.filter(pk=edit_id).first() if edit_id else None
    form = forms.DocumentRequirementForm(request.POST or None, instance=instance)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            response = render(request, "institutions/settings/_documents_panel.html",
                              _settings_context(request, "documents", **_docs_ctx(request, editable)))
            return toast(response, "success", "Document rule saved")
    ctx = _settings_context(request, "documents", **_docs_ctx(request, editable, form=form, editing=instance))
    return render_qa(request, "institutions/settings/documents.html", ctx,
                     {"qa-panel": "institutions/settings/_documents_panel.html"},
                     status=422 if form.is_bound and form.errors else 200)


def _docs_ctx(request, editable, form=None, editing=None):
    rules = DocumentRequirement.objects.select_related("ownership", "institution_type", "document_kind",
                                                       "reviewing_department")
    process = request.GET.get("process", "")
    if process:
        rules = rules.filter(process=process)
    return {"rules": list(rules), "editable": editable, "form": form or forms.DocumentRequirementForm(),
            "editing": editing, "process": process,
            "processes": sorted(set(DocumentRequirement.objects.values_list("process", flat=True)))}


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def settings_lookups(request, kind="ownership"):
    from qa_core.views import _settings_context

    if kind not in forms.LOOKUP_FORMS:
        raise Http404
    model, form_cls, title = forms.LOOKUP_FORMS[kind]
    editable = _can_edit_lookups(request.user)
    edit_id = request.GET.get("edit") or request.POST.get("id")
    instance = model.objects.filter(pk=edit_id).first() if edit_id else None
    form = form_cls(request.POST or None, instance=instance)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            response = render(request, "institutions/settings/_lookups_panel.html",
                              _settings_context(request, "lookups", **_lookup_ctx(kind, editable)))
            return toast(response, "success", f"{title}: saved")
    ctx = _settings_context(request, "lookups", **_lookup_ctx(kind, editable, form=form, editing=instance))
    return render_qa(request, "institutions/settings/lookups.html", ctx,
                     {"qa-panel": "institutions/settings/_lookups_panel.html"},
                     status=422 if form.is_bound and form.errors else 200)


def _lookup_ctx(kind, editable, form=None, editing=None):
    model, form_cls, title = forms.LOOKUP_FORMS[kind]
    return {"kind": kind, "title": title, "rows": list(model.objects.all()), "editable": editable,
            "form": form or form_cls(), "editing": editing,
            "kinds": [(k, v[2]) for k, v in forms.LOOKUP_FORMS.items()],
            "fields": [f for f in form_cls._meta.fields]}
