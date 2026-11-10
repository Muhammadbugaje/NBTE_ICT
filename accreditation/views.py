"""Internal SOP-01 screens: onboarding applications and the Departmental
clearance queue (Legal, Finance, Academic Planning, Physical Planning).

A clearance officer sees the applications waiting for *their* department
(even though the institution is handled by NTIRD) and can open exactly those
applications' documents; nothing else of the institution leaks.
"""
from __future__ import annotations

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from institutions import services as inst_services
from institutions.models import Institution, InstitutionDocument
from qa_core import flow
from qa_core.permissions import can_view_applications, require, require_or_404
from qa_core.ui import paginate, render_qa, toast

from . import services
from .models import Application, DepartmentalClearance, OnboardingSSQ


def _impersonating(request):
    return bool(getattr(request, "is_impersonating", False))


def visible_applications(user):
    """Ids of applications the user may open: those of institutions in scope
    (§4) plus those with a clearance row for the user's department."""
    from .services import clearance_application_ids

    ids = set(Application.objects.for_user(user).values_list("pk", flat=True))
    if user.department_id:
        ids |= clearance_application_ids(user.department_id)
    import org

    if org.is_director(user, "ict"):
        ids |= set(Application.unscoped.filter(status="ssq_submitted").values_list("pk", flat=True))
    return ids


def _application_or_404(request, pk):
    if pk not in visible_applications(request.user):
        raise Http404
    from .services import load_application

    app = load_application(pk)
    if app is None:
        raise Http404
    return app


STAGE_GROUPS = [("", "All stages"), ("draft", "Draft / returned"), ("clearance", "In clearance"),
                ("fee", "Fee stage"), ("programmes", "Programme selection"), ("closed", "Closed")]


@require(can_view_applications)
@require_GET
def application_list(request):
    from .services import applications_by_ids

    ids = visible_applications(request.user)
    rows = applications_by_ids(ids)
    f = request.GET
    group = f.get("stage", "")
    groups = {"draft": ("draft",), "clearance": ("submitted", "clearance"),
              "fee": ("institutional_fee_due", "payment_pending_confirmation", "fee_confirmed"),
              "programmes": ("programme_selection", "programme_clearance", "programme_fee_due"),
              "closed": ("ssq_submitted", "withdrawn")}
    if group in groups:
        rows = [a for a in rows if a.status in groups[group]]
    q = (f.get("q") or "").strip().lower()
    if q:
        rows = [a for a in rows if q in a.reference.lower() or q in a.institution.name.lower()
                or q in a.institution.code.lower()]
    ownership = f.get("ownership", "")
    if ownership:
        rows = [a for a in rows if a.institution.ownership_id and a.institution.ownership.code == ownership]
    returned = f.get("returned", "")
    if returned == "yes":
        rows = [a for a in rows if a.return_remark]
    from .onboarding_ssq import approval_stage

    for a in rows:
        a.public = approval_stage(a)["label"] if a.status == "ssq_submitted" else services.public_status(a)
    page = paginate(request, rows)
    from institutions.models import InstitutionOwnership

    ctx = {"page": page, "filters": f, "stages": STAGE_GROUPS,
           "ownerships": InstitutionOwnership.objects.filter(is_active=True),
           "counts": {"total": len(ids), "clearance": sum(1 for a in applications_by_ids(ids)
                                                          if a.status in ("submitted", "clearance"))}}
    return render_qa(request, "accreditation/list.html", ctx, {"qa-panel": "accreditation/_list_panel.html",
                                                                "qa-rows": "accreditation/_rows.html"})


AGES = {"1d": 1, "3d": 3, "7d": 7}


@require(can_view_applications)
@require_GET
def clearance_queue(request):
    user = request.user
    from .services import clearance_departments, clearance_rows

    status = request.GET.get("status", "pending")
    dept = request.GET.get("dept", "")
    rows = clearance_rows(user, status=status, dept_code=dept)
    q = (request.GET.get("q") or "").strip().lower()
    if q:
        rows = [c for c in rows if q in c.application.reference.lower() or q in c.application.institution.name.lower()]
    age = request.GET.get("age", "")
    if age in AGES:
        cutoff = timezone.now() - datetime.timedelta(days=AGES[age])
        rows = [c for c in rows if c.opened_at <= cutoff]
    for c in rows:
        c.ok, c.why = services.can_decide(c, user)
        c.waiting_days = (timezone.now() - c.opened_at).days
    page = paginate(request, rows)
    ctx = {"page": page, "filters": request.GET, "status": status, "departments": clearance_departments(user),
           "my_department": user.department, "ages": AGES, "delegation": _delegation_context(user),
           "counts": {"pending": len(clearance_rows(user, status="pending", dept_code=dept))}}
    return render_qa(request, "accreditation/clearance.html", ctx,
                     {"qa-panel": "accreditation/_clearance_panel.html", "qa-rows": "accreditation/_clearance_rows.html"})


@require_or_404(can_view_applications)
@require_GET
def application_detail(request, pk):
    app = _application_or_404(request, pk)
    return render_qa(request, "accreditation/detail.html", _detail_context(request, app),
                     {"qa-panel": "accreditation/_detail_panel.html"})


def _detail_context(request, app, **extra):
    user = request.user
    inst = app.institution
    instance = services.instance_of(app)
    clearances = services.clearances_for_round(app) if app.round else []
    mine = next((c for c in clearances if c.department_id == user.department_id
                 and c.status == DepartmentalClearance.PENDING), None)
    my_rows = services.documents_for(mine) if mine else []
    can, why = services.can_decide(mine, user) if mine else (False, "")
    rows = inst_services.checklist(inst)
    by_dept = {}
    for row in rows:
        key = row["department"].name if row["department"] else "Other"
        by_dept.setdefault(key, []).append(row)
    self_studies = list(OnboardingSSQ.unscoped.filter(application=app).select_related(
        "programme", "template", "submitted_by"
    ).order_by("programme__name"))
    from . import onboarding_linear

    for study in self_studies:
        study.snapshot_valid = study.status != OnboardingSSQ.SUBMITTED or study.verify_submission()
        study.review = onboarding_linear.review_stage(study)  # linear onboarding: where each one is
    from payments.models import PaymentObligation
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(Application)
    obligations = []
    if user.is_superuser or user.has_perm("payments.view_obligation") or user.department_id == inst.handling_department_id:
        from payments.services import stage_label

        obligations = list(PaymentObligation.objects.for_user(user).filter(source_type=ct, source_id=str(app.pk)))
        for ob in obligations:
            ob.stage = stage_label(ob)
    ctx = {
        "app": app, "inst": inst, "instance": instance, "public_status": services.public_status(app),
        "history": flow.history(instance), "branches": flow.branch_states(instance) if instance else {},
        "clearances": clearances, "mine": mine, "my_rows": my_rows, "can_decide": can, "decide_reason": why,
        "docs_by_dept": sorted(by_dept.items()), "contacts": inst_services.contacts_of(inst),
        "addresses": inst_services.addresses_of(inst), "board": services.stage_board(app),
        "hold": inst_services.active_hold(app), "obligations": obligations,
        "can_lift": user.is_superuser or user.has_perm("qa_core.lift_suspension"),
        "earlier_rounds": [c for r in range(1, app.round or 0) for c in services.clearances_for_round(app, r)],
        "self_studies": self_studies,
    }
    if app.status == "ssq_submitted":  # where the submitted self-studies really are (approval)
        from .onboarding_ssq import approval_stage

        ctx["self_study_stage"] = approval_stage(app)
        ctx["public_status"] = ctx["self_study_stage"]["label"]
    if app.status == "ssq_submitted":  # linear onboarding: the step that is this person's
        from . import journey

        ctx["journey_actions"] = [a for a in journey.next_actions(user) if a["application_id"] == app.pk]
        ctx["ready_for_approval"] = onboarding_linear.ready_for_approval(app)
    from . import onboarding_v4

    ctx["stalled"] = onboarding_v4.stalled_step(app)
    ctx["can_resume"] = bool(ctx["stalled"]) and _can_resume(user)
    ctx["summary"] = _detail_summary(ctx)
    ctx.update(extra)
    return ctx


def _detail_summary(ctx):
    """Figures for the page header and the "before you decide" checklist."""
    docs = [r for _dept, rows in ctx["docs_by_dept"] for r in rows if r.get("mandatory", True)]
    docs_ok = sum(1 for r in docs if r.get("state") == "accepted")
    studies = ctx["self_studies"]
    studies_sent = [s for s in studies if s.status == OnboardingSSQ.SUBMITTED]
    studies_ok = sum(1 for s in studies if (getattr(s, "review", None) or {}).get("code") in ("cleared", "approved"))
    fees = ctx["obligations"]
    fees_ok = sum(1 for ob in fees if getattr(ob, "status", "") == "confirmed")
    clearance_done = any(s["code"] == "clearance" and s["state"] == "done" for s in ctx["board"])
    decisions = len(ctx.get("journey_actions") or []) + (1 if ctx["mine"] else 0)
    return {
        "docs_total": len(docs), "docs_ok": docs_ok, "docs_all_ok": bool(docs) and docs_ok == len(docs),
        "studies_total": len(studies), "studies_sent": len(studies_sent), "studies_ok": studies_ok,
        "studies_all_ok": bool(studies) and studies_ok == len(studies),
        "fees_total": len(fees), "fees_ok": fees_ok, "fees_all_ok": bool(fees) and fees_ok == len(fees),
        "clearance_done": clearance_done, "decisions": decisions,
    }


def _can_resume(user):
    """ES, super admins and holders of the lift-hold permission."""
    from org import services as org_services

    return bool(user.is_superuser or user.has_perm("qa_core.lift_suspension") or org_services.is_es(user)
                or org_services.is_super_admin(user))


@require_or_404(can_view_applications)
@require_POST
def clearance_decide(request, pk):
    from .services import load_clearance

    clearance = load_clearance(pk)
    if clearance is None or clearance.application_id not in visible_applications(request.user):
        raise Http404
    decision = request.POST.get("decision", "")
    remark = request.POST.get("remark", "")
    error = ""
    try:
        services.decide(clearance, user=request.user, decision=decision, remark=remark,
                        accept_documents=request.POST.get("accept_documents", "on") == "on",
                        impersonating=_impersonating(request))
    except (ValidationError, PermissionDenied) as exc:
        error = " ".join(getattr(exc, "messages", [str(exc)]))
    app = services.load_application(clearance.application_id)
    response = render(request, "accreditation/_detail_panel.html", _detail_context(request, app, error=error,
                                                                                  remark=remark if error else ""),
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not done", error)
    if decision == "clear":
        title = f"{clearance.department.name}: cleared"
        body = ("All four departments have cleared it — the institutional fee was issued."
                if app.status not in ("clearance", "submitted") else "Waiting for the other departments.")
    else:
        title, body = "Returned to the institution", "The other departments' reviews were closed for this round."
    return toast(response, "success", title, body)


@require_or_404(can_view_applications)
@require_POST
def document_review(request, pk):
    from .services import load_document

    doc = load_document(pk)
    if doc is None or doc.application_id not in visible_applications(request.user):
        raise Http404
    app = services.load_application(doc.application_id)
    clearance = next((c for c in services.clearances_for_round(app) if c.department_id == request.user.department_id
                      and c.status == DepartmentalClearance.PENDING), None)
    error = ""
    if clearance is None or not services.can_decide(clearance, request.user)[0]:
        error = "Only the reviewing department can review this document now."
    else:
        rows = {r["kind"].pk for r in services.documents_for(clearance)}
        if doc.kind_id not in rows:
            error = "This document is reviewed by another department."
        else:
            try:
                inst_services.review_document(doc, request.POST.get("decision", ""), user=request.user,
                                              remark=request.POST.get("remark", ""))
            except ValidationError as exc:
                error = " ".join(exc.messages)
    response = render(request, "accreditation/_detail_panel.html", _detail_context(request, app, error=error))
    if error:
        return toast(response, "error", "Not done", error)
    return toast(response, "success", f"{doc.kind.name}: {'accepted' if request.POST.get('decision') == 'accept' else 'returned'}")


@require_or_404(can_view_applications)
@require_POST
def hold_lift(request, pk):
    app = _application_or_404(request, pk)
    if not (request.user.is_superuser or request.user.has_perm("qa_core.lift_suspension")):
        raise Http404
    inst_services.lift_hold(app, request.user, note=request.POST.get("note", ""))
    response = render(request, "accreditation/_detail_panel.html", _detail_context(request, app))
    return toast(response, "success", "Hold lifted")


@require_or_404(can_view_applications)
@require_POST
def application_resume(request, pk):
    """Retry an automatic step that did not run (e.g. the self-studies did not
    open after the programme fee was confirmed). Idempotent; ES / super admin."""
    from . import onboarding_v4

    app = _application_or_404(request, pk)
    if not _can_resume(request.user):
        raise Http404
    try:
        with transaction.atomic():
            message = onboarding_v4.resume(app, request.user)
    except ValidationError as exc:
        text = " ".join(exc.messages)
        response = render(request, "accreditation/_detail_panel.html", _detail_context(request, app, error=text))
        return toast(response, "error", "Could not resume", text)
    app.refresh_from_db()
    response = render(request, "accreditation/_detail_panel.html", _detail_context(request, app))
    return toast(response, "success", "Resumed", message)


# ---------------------------------------------------------------------------
# Phase 0 (Q2): onboarding programme clearance (NTIRD or the setting's department)
# ---------------------------------------------------------------------------
def _can_open_programme_queue(user):
    return can_view_applications(user) or (getattr(user, "user_type", "") == "staff"
                                          and user.has_perm("accreditation.clear_qa_programmes"))


@require(_can_open_programme_queue)
@require_GET
def programme_queue(request):
    from . import onboarding_programmes as op

    status = request.GET.get("status", "pending")
    if status not in ("pending", "all"):
        status = "pending"
    items = op.queue(request.user, status=status)
    q = (request.GET.get("q") or "").strip().lower()
    if q:
        items = [i for i in items if q in i["app"].reference.lower() or q in i["app"].institution.name.lower()]
    page = paginate(request, items)
    ctx = {"page": page, "status": status, "filters": request.GET, "my_department": request.user.department,
           "counts": {"pending": len(op.queue(request.user, status="pending"))}}
    return render_qa(request, "accreditation/programmes.html", ctx,
                     {"qa-panel": "accreditation/_programmes_panel.html",
                      "qa-rows": "accreditation/_programmes_rows.html"})


@require_or_404(_can_open_programme_queue)
@require_POST
def programme_decide(request, pk):
    from . import onboarding_programmes as op

    review = op.load_review(pk)
    if review is None:
        raise Http404
    decision = request.POST.get("decision", "")
    error = ""
    try:
        op.decide(review, user=request.user, decision=decision, remark=request.POST.get("remark", ""),
                  impersonating=_impersonating(request))
    except (ValidationError, PermissionDenied) as exc:
        error = " ".join(getattr(exc, "messages", [str(exc)]))
    item = next((i for i in op.queue(request.user, status="all") if i["app"].pk == review.application_id), None)
    if item is None:
        raise Http404
    response = render(request, "accreditation/_programme_app.html", {"item": item, "error": error},
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not done", error)
    item["app"].refresh_from_db()
    if item["app"].status == "programme_fee_due":
        return toast(response, "success", "All programmes cleared", "The programme processing fee was issued.")
    if item["app"].status == "programme_selection":
        return toast(response, "success", "Returned to the institution",
                     "The returned programmes reopen; cleared ones stay cleared.")
    return toast(response, "success", f"{review.programme.name}: {'cleared' if decision == 'clear' else 'returned'}",
                 "The institution has been told.")


# ---------------------------------------------------------------------------
# Phase 0 (O10): the director names clearance delegates for the department
# ---------------------------------------------------------------------------
def _delegation_context(user, error=""):
    from accounts.models import User

    from .clearance_access import can_manage_delegates, live_delegations

    dept = getattr(user, "department", None)
    if dept is None or not can_manage_delegates(user, dept):
        return None
    current = list(live_delegations(dept).order_by("user__first_name"))
    taken = {d.user_id for d in current} | {user.pk}
    candidates = list(User.objects.filter(is_active=True, user_type="staff", department=dept)
                      .exclude(pk__in=taken).order_by("first_name", "last_name")[:200])
    return {"department": dept, "delegates": current, "candidates": candidates, "error": error}


def _delegates_response(request, error="", ok=None):
    response = render(request, "accreditation/_delegates.html", {"delegation": _delegation_context(request.user, error)},
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not done", error)
    return toast(response, "success", *ok) if ok else response


@require_or_404(can_view_applications)
@require_POST
def delegate_add(request):
    from accounts.models import User

    from . import clearance_access

    if _impersonating(request) or _delegation_context(request.user) is None:
        raise Http404
    person = User.objects.filter(pk=request.POST.get("user") or 0, user_type="staff", is_active=True).first()
    if person is None:
        return _delegates_response(request, "Choose a staff member of your department.")
    try:
        clearance_access.add_delegate(request.user.department, person, granted_by=request.user,
                                      note=(request.POST.get("note") or "")[:255])
    except (ValidationError, PermissionDenied) as exc:
        return _delegates_response(request, " ".join(getattr(exc, "messages", [str(exc)])))
    return _delegates_response(request, ok=("Delegate added", f"{person.get_full_name() or person.email} can now "
                                                              "clear or return applications for the department."))


@require_or_404(can_view_applications)
@require_POST
def delegate_revoke(request, pk):
    from . import clearance_access
    from .models import ClearanceDelegate

    if _impersonating(request) or _delegation_context(request.user) is None:
        raise Http404
    delegate = ClearanceDelegate.objects.filter(pk=pk, department=request.user.department, active=True).first()
    if delegate is None:
        raise Http404
    try:
        clearance_access.revoke_delegate(delegate, by=request.user)
    except PermissionDenied as exc:
        return _delegates_response(request, str(exc))
    return _delegates_response(request, ok=("Delegation removed",))
