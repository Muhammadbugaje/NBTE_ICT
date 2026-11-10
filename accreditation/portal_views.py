"""Onboarding application pages on the portal (SOP-01): tracker, document
checklist summary, clearance status, submit, and programme selection."""
from __future__ import annotations

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MaxValueValidator
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from institutions import services as inst_services
from institutions.forms import QAForm
from institutions.models import (Institution, ProfessionalBody, Programme, ProgrammeCategory, ProgrammeLevel,
                                 ProgrammeMaster)
from qa_core import portal_journey
from institutions.profile import profile_gate
from qa_core.portal_views import _shell_context, institution_required
from qa_core.ui import htmx_fragment, is_htmx, toast

from . import services
from .models import Application, DepartmentalClearance, OnboardingSSQ


def _inst(request):
    return Institution.objects.for_user(request.user).select_related("ownership", "institution_type",
                                                                      "category", "state").first()


def _app(request, inst):
    return (Application.objects.for_user(request.user).filter(institution=inst).exclude(status="withdrawn")
            .order_by("-created_at").first())


def _context(request, inst, app, **extra):
    rows = inst_services.checklist(inst)
    clearances = []
    if app is not None and app.round:
        clearances = list(DepartmentalClearance.objects.for_user(request.user)
                          .filter(application=app, round=app.round).select_related("department")
                          .order_by("branch_code"))
    ctx = {
        "inst": inst, "app": app, "summary": inst_services.checklist_summary(rows), "rows": rows,
        "gaps": services.profile_gaps(inst) if app is None or app.status == "draft" else [],
        "blockers": services.submission_blockers(app) if app is not None and app.status == "draft" else [],
        # v2.1 X6: milestones only (qa.portal.milestones), never internal stages.
        "board": portal_journey.portal_board(app) if app is not None else [],
        "public_status": ((str(services.public_status(app)) if app.status == "ssq_submitted" else
                           portal_journey.portal_status("onboarding", portal_journey.application_stage(app),
                                                        str(services.public_status(app))))
                          if app is not None else "Not started"),
        "show_departments": portal_journey.show_department_names(),
        "clearances": clearances, "hold": inst_services.active_hold(app) if app is not None else None,
        "programmes": list(Programme.objects.for_user(request.user).filter(institution=inst)
                           .select_related("level", "category").order_by("name")),
        "obligations": _obligations(request, app),
    }
    if app is not None and app.status == "ssq_submitted":
        from .onboarding_ssq import approval_stage

        ctx["self_study_stage"] = approval_stage(app)
        if ctx["self_study_stage"]["code"] == "approved":  # the self-study milestone is behind them
            ctx["board"] = [dict(m, state="done") if m.get("state") == "current" else m for m in ctx["board"]]
    ctx["estimate"] = _programme_estimate(inst, ctx["programmes"])
    # v2.1 F.15 (X14/X15): pay to submit — four-step header, Declare and pay (CE only).
    from . import onboarding_v2

    from . import onboarding_v3

    ctx["v2"] = app is None or onboarding_v2.is_v2(app)
    ctx["v3"] = onboarding_v3.is_v3(app) if app is not None else onboarding_v3.latest_flow_is_v3()
    if ctx["v2"]:
        ctx["head"] = onboarding_v2.header(app, inst)
        ctx["pay_blockers"] = onboarding_v2.pay_blockers(app) if app is not None and app.status == "draft" else []
        ctx["blockers"] = []
        if app is not None:
            from institutions.models import DocumentReview

            ctx["returned_reviews"] = list(DocumentReview.objects.for_user(request.user).filter(
                application=app, status=DocumentReview.RETURNED).select_related("document__kind"))
    if ctx["v3"] and app is not None:
        ctx["document_blockers"] = onboarding_v3.submission_blockers(app) if app.status == "document_upload" else []
        ctx["can_submit_documents"] = app.status == "document_upload"
    if ctx["v3"] and app is not None and app.status in ("programme_fee_confirmed", "ssq_in_progress",
                                                        "ssq_submitted"):
        from . import onboarding_ssq

        ctx["self_studies"] = list(OnboardingSSQ.objects.for_user(request.user).filter(application=app)
                                   .select_related("programme", "template").order_by("programme__name"))
        for study in ctx["self_studies"]:
            study.problems = onboarding_ssq.submission_problems(study)
        ctx["self_study_problem_count"] = sum(len(study.problems) for study in ctx["self_studies"])
        from . import onboarding_v4

        # A cleared programme without its questionnaire (e.g. an older application): offer to open it.
        ctx["ssq_missing"] = bool(ctx["self_studies"]) and bool(onboarding_v4.stalled_step(app))
        ctx["can_submit_self_study"] = (
            app.status == "ssq_in_progress" and bool(ctx["self_studies"])
            and all(study.status == OnboardingSSQ.DRAFT and not study.problems for study in ctx["self_studies"])
            and inst_services.is_chief_executive(request.user)
        )
    ctx["is_ce"] = inst_services.is_chief_executive(request.user)
    ctx["selecting"] = bool(app is not None and app.status == "programme_selection"
                            and not getattr(request, "is_impersonating", False))
    if ctx["selecting"]:
        ctx["form"] = ProgrammeForm()
    if app is not None and (app.status.startswith("programme_") or app.programme_round):
        from . import onboarding_programmes as op

        ctx["prog"] = op.summary(app, request.user)
        ctx["prog_states"] = {r["programme"].pk: r for r in ctx["prog"]["rows"]}
    # v2.3 portal redesign: the plain-language "what is happening / what next" card.
    head = ctx.get("head")
    ctx["state"] = journey_state(inst, app, head=head, obligations=ctx["obligations"], is_ce=ctx["is_ce"],
                                 returned=len(ctx.get("returned_reviews") or []), docs=ctx["summary"],
                                 self_studies=ctx.get("self_studies"), hold=ctx["hold"], v3=ctx["v3"])
    ctx["fee_timing"] = (head or {}).get("fee_timing")
    ctx.update(extra)
    return _shell_context(request, "application", **ctx)


def _open_bill(obligations, prefer=None):
    """The bill the institution should pay now (the given one first)."""
    payable = ("awaiting_payment", "rejected", "partially_paid", "issued")
    if prefer is not None and getattr(prefer, "status", "") in payable:
        return prefer
    return next((o for o in obligations or [] if o.status in payable), None)


def journey_state(inst, app, *, head=None, obligations=None, is_ce=False, returned=0, docs=None,
                  self_studies=None, hold=None, v3=False):
    """v2.3 portal redesign: one plain-language card for the application —
    what is happening, who is acting now, and the single next thing the
    institution must do. Display only; it never changes the workflow.

    Returns {tone, icon, title, body, who, mine, cta_label, cta_url, cta_icon,
    step, steps}. ``mine`` is True when the institution has something to do.
    Institutions never see officers' names or internal terms."""
    from django.urls import reverse

    def out(title, body, *, who="NBTE", mine=False, tone="blue", icon="clock", cta_label="", cta_url="",
            cta_icon="arrow"):
        return {"title": title, "body": body, "who": who, "mine": mine, "tone": tone, "icon": icon,
                "cta_label": cta_label, "cta_url": cta_url, "cta_icon": cta_icon}

    app_url = reverse("qa_portal:application")
    docs_url = reverse("qa_portal:documents")
    status = app.status if app is not None else ""
    timing = (head or {}).get("fee_timing")
    ob = (head or {}).get("obligation")
    bill = _open_bill(obligations, prefer=ob)

    def pay_cta(default_label="Pay now"):
        if bill is None:
            return {"cta_label": "Open Payments", "cta_url": reverse("qa_portal:payments"), "cta_icon": "card"}
        from payments.money import naira

        return {"cta_label": f"Pay {naira(bill.balance)}" if bill.balance else default_label,
                "cta_url": reverse("qa_portal:payment", args=[bill.pk]), "cta_icon": "card"}

    finance_wait = ("You told NBTE you have paid. NBTE Finance is confirming the payment — online payments "
                    "usually confirm within minutes, bank transfers within a few working days. Nothing to do now; "
                    "you will be notified.")
    if hold is not None:
        state = out("Your application is on hold", getattr(hold, "reason", "") or
                    "NBTE has paused your application. You will be told when it continues.", tone="red",
                    icon="alert")
    elif app is None:
        if inst is not None and inst.status == "approved":
            state = out("Your institution is on NBTE's register",
                        "Ask NBTE for accreditation, add programmes or renew one from QA requests.", who="You",
                        mine=True, tone="green", icon="check", cta_label="Open QA requests",
                        cta_url=reverse("qa_portal:qa_requests"))
        else:
            state = out("Start your onboarding application",
                        "Open your application to register with NBTE. It takes a minute; nothing is sent to NBTE "
                        "until your Chief Executive signs the declaration.", who="You", mine=True, tone="gold",
                        icon="plus", cta_label="Start application", cta_url=app_url, cta_icon="plus")
    elif status == "draft":
        if app.return_remark:
            state = out("NBTE returned your application", app.return_remark, who="You", mine=True, tone="red",
                        icon="alert", cta_label="Fix and resubmit", cta_url=app_url + "#qa-next")
        elif timing == "after_clearance":
            body = ("Your Chief Executive signs the declaration to open document uploads. No payment is needed "
                    "now — the institutional fee is paid once NBTE has approved all your documents.")
            state = out("Sign the declaration and start", body if is_ce else
                        "Waiting for your Chief Executive to sign in and sign the declaration. " + body.split(". ", 1)[1],
                        who="Your Chief Executive", mine=True, tone="gold", icon="edit",
                        cta_label="Sign the declaration" if is_ce else "See what is needed",
                        cta_url=app_url + "#qa-next", cta_icon="edit")
        else:
            body = ("Your Chief Executive signs the declaration and pays the institutional processing fee. "
                    + ("Document uploads open once NBTE Finance confirms the payment." if (timing or v3) else
                       "Your application submits itself once the payment is confirmed."))
            state = out("Declare and pay", body, who="Your Chief Executive", mine=True, tone="gold", icon="card",
                        cta_label="Declare and pay" if is_ce else "See what is needed", cta_url=app_url + "#qa-next",
                        cta_icon="card")
    elif status in ("fee_due", "cleared_fee_due", "approved_fee_due", "programme_fee_due", "institutional_fee_due"):
        titles = {"fee_due": "Pay the institutional processing fee",
                  "institutional_fee_due": "Pay the institutional fee",
                  "cleared_fee_due": "All your documents are approved — pay the institutional processing fee "
                                     "to continue",
                  "approved_fee_due": "All your documents are approved — pay the institutional processing fee "
                                      "to continue",
                  "programme_fee_due": "Pay the programme processing fee"}
        bodies = {"fee_due": "Pay online (Remita or Paystack) or by bank transfer. Document uploads open as soon as "
                             "NBTE Finance confirms the payment.",
                  "institutional_fee_due": "Your documents are cleared. Pay the fee to move on.",
                  "cleared_fee_due": "NBTE's departments have approved every document. Pay the institutional "
                                     "processing fee; then you choose the programmes you will offer.",
                  "approved_fee_due": "NBTE's departments have approved every document. Pay the institutional "
                                      "processing fee; then you choose the programmes you will offer.",
                  "programme_fee_due": "NBTE cleared your programmes. Pay the programme processing fee; your "
                                       "self-study questionnaires open once it is confirmed."}
        state = out(titles[status], bodies[status], who="You", mine=True,
                    tone="green" if status in ("cleared_fee_due", "approved_fee_due") else "gold", icon="card", **pay_cta())
    elif status in ("payment_pending_confirmation", "cleared_payment_pending", "approved_payment_pending",
                    "programme_payment_pending"):
        nxt = {"payment_pending_confirmation": "Next: upload your documents." if (timing or v3) else
               "Your application submits itself the moment it is confirmed.",
               "cleared_payment_pending": "Next: choose the programmes you will offer.",
               "approved_payment_pending": "Next: choose the programmes you will offer.",
               "programme_payment_pending": "Next: your self-study questionnaires open."}[status]
        state = out("Payment received — NBTE Finance is confirming it", f"{finance_wait} {nxt}", who="NBTE Finance",
                    icon="shield")
    elif status == "fee_confirmed":
        state = out("Payment confirmed", "Your application is being submitted to NBTE automatically.",
                    tone="green", icon="check")
    elif status == "document_upload":
        ready = docs or {}
        missing = max((ready.get("mandatory") or 0) - (ready.get("ready") or 0), 0)
        if missing:
            state = out("Upload your required documents",
                        f"{missing} required document{'s' if missing != 1 else ''} still to upload. When all are "
                        "in, your Chief Executive submits them to NBTE for review.", who="You", mine=True,
                        tone="gold", icon="upload", cta_label="Upload documents", cta_url=docs_url,
                        cta_icon="upload")
        else:
            state = out("Submit your documents to NBTE",
                        "Every required document is uploaded. " + (
                            "Submit them now — each one goes to the NBTE department that reviews it." if is_ce else
                            "Ask your Chief Executive to sign in and submit them."),
                        who="Your Chief Executive", mine=True, tone="gold", icon="arrow",
                        cta_label="Submit documents" if is_ce else "See what is needed",
                        cta_url=app_url + "#qa-next")
    elif status in ("document_review", "submitted", "clearance"):
        if returned:
            state = out(f"Upload {returned} returned document{'s' if returned != 1 else ''} again",
                        "NBTE asked for a change. Upload a new file for just that document — it goes straight back "
                        "for review. You never resubmit the whole application.", who="You", mine=True, tone="red",
                        icon="upload", cta_label="Re-upload", cta_url=docs_url + "?state=returned",
                        cta_icon="upload")
        else:
            state = out("NBTE departments are reviewing your documents",
                        "Each document is checked by the NBTE department responsible for it. Nothing to do now — "
                        "if a document needs a change, only that one comes back and you are notified.",
                        icon="search")
    elif status == "approval_pending":  # legacy v4 stage (Gate A removed): documents are all approved
        state = out("All your documents are approved",
                    "NBTE is preparing your next step. Nothing to do now — you will be notified.", tone="green",
                    icon="check")
    elif status == "programme_selection":
        state = out("Choose the programmes you will offer",
                    "Add each programme and its number of streams. When the list is ready, your Chief Executive "
                    "submits it to NBTE for clearance.", who="You", mine=True, tone="gold", icon="layers",
                    cta_label="Add programmes", cta_url=app_url + "#qa-prog-card", cta_icon="plus")
    elif status == "programme_clearance":
        state = out("NBTE is clearing your programmes",
                    "NBTE checks each programme you listed. Nothing to do now — you are told after each decision, "
                    "and the programme fee is issued when all are cleared.", icon="search")
    elif status == "programme_fee_confirmed":
        state = out("Programme fee confirmed — open your self-studies",
                    "NBTE Finance confirmed your programme fee. Open the self-study questionnaires, one per cleared "
                    "programme.", who="You", mine=True, tone="gold", icon="sheet",
                    cta_label="Open self-studies", cta_url=app_url + "#qa-ssq")
    elif status == "ssq_in_progress":
        studies = self_studies or []
        state = out("Complete your self-study for each programme",
                    "Fill one self-study questionnaire (SSQ) per cleared programme. Your work saves as you go. When "
                    "all are complete, your Chief Executive submits them together." if studies else
                    "Open your self-study questionnaires — one per cleared programme.", who="You", mine=True,
                    tone="gold", icon="sheet", cta_label="Open self-studies" if studies else "Open questionnaires",
                    cta_url=app_url + "#qa-ssq")
    elif status == "ssq_submitted":
        if inst is not None and inst.status == "approved":
            state = out("Your institution is approved",
                        "Next: ask NBTE for a Resource Inspection of your programmes.", who="You", mine=True,
                        tone="green", icon="check", cta_label="Request a Resource Inspection",
                        cta_url=reverse("qa_portal:qa_request_new") + "?type=resource_inspection")
        else:
            from .onboarding_ssq import approval_stage

            stage = approval_stage(app) if app is not None else {"code": ""}
            if stage["code"] == "approval_pending":
                state = out("Approval in progress",
                            "NBTE has reviewed your self-studies and your institution's approval is waiting for "
                            "final confirmation. Nothing to do now — you will be notified.", tone="green",
                            icon="shield")
            else:
                state = out("Self-studies under review by NBTE",
                            "NBTE is reviewing your self-studies before it approves your institution. Nothing to "
                            "do now — you will be notified.", tone="green", icon="check")
    else:
        state = out("Your application is with NBTE", "You will be notified when something changes.")
    steps = (head or {}).get("steps") or []
    current = next((i for i, s in enumerate(steps) if s.get("state") == "current"), None)
    state["step"] = (current + 1) if current is not None else (len(steps) if steps else 0)
    state["steps"] = len(steps)
    state["pct"] = round(100 * sum(1 for s in steps if s.get("state") == "done") / len(steps)) if steps else 0
    return state


def _obligations(request, app):
    if app is None:
        return []
    from django.contrib.contenttypes.models import ContentType

    from payments.models import PaymentObligation

    ct = ContentType.objects.get_for_model(Application)
    return list(PaymentObligation.objects.for_user(request.user).filter(source_type=ct, source_id=str(app.pk))
                .order_by("-created_at"))


@institution_required
@profile_gate  # v2.1 X13
def application(request):
    inst = _inst(request)
    app = _app(request, inst)
    return render(request, "qa_portal/application/page.html", _context(request, inst, app))


def _ssq_context(request, ssq, **extra):
    from . import onboarding_ssq

    ctx = onboarding_ssq.workspace(ssq, request.user)
    ctx.update(extra)
    return _shell_context(request, "application", **ctx)


@institution_required
@profile_gate
@require_POST
def application_ssq_open(request):
    if getattr(request, "is_impersonating", False):
        raise PermissionDenied
    inst = _inst(request)
    app = _app(request, inst)
    if app is None:
        raise Http404
    try:
        from qa_core import flow

        from . import onboarding_ssq

        with transaction.atomic():
            if app.status == "programme_fee_confirmed":  # the automatic step did not run: take it now
                instance = services.instance_of(app)
                if instance is not None and flow.can(instance, "auto_open_ssq"):
                    flow.transition(instance, "auto_open_ssq", None)
                app.refresh_from_db()
            onboarding_ssq.open_for_application(app)
    except ValidationError as exc:
        message = " ".join(exc.messages)
        response = render(request, "qa_portal/application/page.html",
                          _context(request, inst, app, error=message), status=422)
        return toast(response, "error", "Self-study unavailable", message)
    return redirect("qa_portal:application")


@institution_required
@profile_gate
@require_http_methods(["GET"])
def application_ssq(request, pk):
    ssq = OnboardingSSQ.objects.for_user(request.user).select_related(
        "application", "application__institution", "programme", "template"
    ).filter(pk=pk).first()
    if ssq is None:
        raise Http404
    return render(request, "qa_portal/application/ssq.html", _ssq_context(request, ssq))


@institution_required
@profile_gate
@require_POST
def application_ssq_save(request, pk):
    if getattr(request, "is_impersonating", False):
        raise PermissionDenied
    ssq = OnboardingSSQ.objects.for_user(request.user).select_related(
        "application", "application__institution", "programme", "template"
    ).filter(pk=pk).first()
    if ssq is None:
        raise Http404
    from . import onboarding_ssq

    try:
        errors = onboarding_ssq.save_fields(ssq, request.POST, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        messages = getattr(exc, "messages", [str(exc)])
        response = render(request, "qa_portal/application/ssq.html",
                          _ssq_context(request, ssq, error=" ".join(messages)), status=422)
        return toast(response, "error", "Could not save", " ".join(messages))
    if errors:
        response = render(request, "qa_portal/application/ssq.html",
                          _ssq_context(request, ssq, field_errors=errors), status=422)
        return toast(response, "error", "Some answers need attention",
                     " ".join(f"{key}: {value}" for key, value in errors.items()))
    from django.contrib import messages as flash

    flash.success(request, "Self-study saved. You can come back and continue at any time.")
    return redirect("qa_portal:application_ssq", pk=ssq.pk)


@institution_required
@profile_gate
@require_POST
def application_ssq_submit(request):
    if getattr(request, "is_impersonating", False):
        raise PermissionDenied
    inst = _inst(request)
    app = _app(request, inst)
    if app is None:
        raise Http404
    try:
        from . import onboarding_ssq

        onboarding_ssq.submit_application(
            app, user=request.user, declaration=request.POST.get("declaration") == "on"
        )
    except (ValidationError, PermissionDenied) as exc:
        messages = getattr(exc, "messages", [str(exc)])
        response = render(request, "qa_portal/application/page.html",
                          _context(request, inst, app, error=" ".join(messages)), status=422)
        return toast(response, "error", "Self-study not submitted", " ".join(messages))
    response = render(request, "qa_portal/application/page.html", _context(request, inst, _app(request, inst)))
    return toast(response, "success", "Self-study submitted",
                 "NBTE must approve the institution before the Chief Executive can start a Resource Inspection request.")


@institution_required
@profile_gate
@require_POST
def application_ssq_resubmit(request, pk):
    """Linear onboarding: the Chief Executive resubmits one self-study NBTE returned for correction."""
    if getattr(request, "is_impersonating", False):
        raise PermissionDenied
    ssq = OnboardingSSQ.objects.for_user(request.user).select_related(
        "application", "application__institution", "programme", "template"
    ).filter(pk=pk).first()
    if ssq is None:
        raise Http404
    from django.contrib import messages as flash

    from . import onboarding_linear

    try:
        onboarding_linear.resubmit(ssq, user=request.user, declaration=request.POST.get("declaration") == "on")
    except (ValidationError, PermissionDenied) as exc:
        messages = getattr(exc, "messages", [str(exc)])
        response = render(request, "qa_portal/application/ssq.html",
                          _ssq_context(request, ssq, error=" ".join(messages)), status=422)
        return toast(response, "error", "Not resubmitted", " ".join(messages))
    flash.success(request, "Corrected self-study submitted to NBTE.")
    return redirect("qa_portal:application")


@institution_required
@profile_gate  # v2.1 X13
@require_POST
def application_start(request):
    inst = _inst(request)
    if getattr(request, "is_impersonating", False):
        raise PermissionDenied
    try:
        services.start_application(inst, user=request.user)
    except ValidationError as exc:
        response = render(request, "qa_portal/application/page.html",
                          _context(request, inst, _app(request, inst), error=" ".join(exc.messages)))
        return toast(response, "error", "Could not start", " ".join(exc.messages))
    response = redirect("qa_portal:application")
    if is_htmx(request):
        app = _app(request, inst)
        from . import onboarding_v3

        response = render(request, "qa_portal/application/page.html", _context(request, inst, app))
        toast(response, "success", "Application opened",
              "The Chief Executive pays first; uploads unlock after Finance confirms payment."
              if onboarding_v3.is_v3(app) else "Upload your documents, then submit.")
    return response


@institution_required
@profile_gate  # v2.1 X13
@require_POST
def application_submit(request):
    inst = _inst(request)
    app = _app(request, inst)
    if app is None:
        raise Http404
    try:
        from . import onboarding_v3

        if onboarding_v3.is_v3(app):
            onboarding_v3.submit_documents(app, user=request.user)
        else:
            services.submit(app, user=request.user, declaration=request.POST.get("declaration") == "on")
    except (ValidationError, PermissionDenied) as exc:
        msgs = getattr(exc, "messages", [str(exc)])
        response = render(request, "qa_portal/application/page.html",
                          _context(request, inst, app, error=" ".join(msgs)), status=422)
        return toast(response, "error", "Not submitted", " ".join(msgs))
    app.refresh_from_db()
    response = render(request, "qa_portal/application/page.html", _context(request, inst, app, submitted=True))
    if onboarding_v3.is_v3(app):
        return toast(response, "success", "Documents submitted",
                     "NBTE departments will review each document routed to them.")
    return toast(response, "success", "Application submitted",
                 "Legal, Finance, Academic Planning and Physical Planning will review it at the same time.")


@institution_required
@profile_gate  # v2.1 X13
@require_POST
def application_declare(request):
    """v2.1 F.15 (X12, X15): the Chief Executive signs the declaration and the
    processing fee is issued; the application submits itself once paid."""
    from . import onboarding_v2

    inst = _inst(request)
    app = _app(request, inst)
    if app is None or getattr(request, "is_impersonating", False):
        raise Http404
    try:
        app = onboarding_v2.declare_and_pay(app, user=request.user,
                                            declaration=request.POST.get("declaration") == "on")
    except (ValidationError, PermissionDenied) as exc:
        msgs = getattr(exc, "messages", [str(exc)])
        response = render(request, "qa_portal/application/page.html",
                          _context(request, inst, app, error=" ".join(msgs)), status=422)
        return toast(response, "error", "Not yet", " ".join(msgs))
    ob = onboarding_v2.obligation_of(app)
    if ob is not None:
        response = redirect("qa_portal:payment", ob.pk)
        if is_htmx(request):
            from django.http import HttpResponse

            response = HttpResponse(status=204)
            response["HX-Redirect"] = f"/payments/{ob.pk}/"
        return response
    return redirect("qa_portal:application")


class ProgrammeForm(QAForm):
    """v2.1 R.4 (X44): a master-list pick (``master``) or a typed programme
    with its professional body (``name``, ``level``, ``category``, ``body``)."""
    master = forms.ModelChoiceField(ProgrammeMaster.objects.filter(is_active=True), required=False,
                                    widget=forms.HiddenInput)
    name = forms.CharField(max_length=200, label="Programme name", required=False)
    level = forms.ModelChoiceField(ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin"),
                                   required=False)
    category = forms.ModelChoiceField(ProgrammeCategory.objects.filter(is_active=True), required=False)
    body = forms.ModelChoiceField(ProfessionalBody.objects.filter(is_active=True), required=False,
                                  label="Professional body", empty_label="Choose the body that regulates it")
    streams = forms.IntegerField(label="Number of streams", min_value=1, initial=1,
                                 help_text="How many streams (parallel classes) you plan to admit.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .qa_services import max_streams

        top = max_streams()
        self.fields["streams"].max_value = top
        self.fields["streams"].validators.append(MaxValueValidator(top))
        self.fields["streams"].widget.attrs.update({"min": 1, "max": top, "inputmode": "numeric"})
        self.fields["streams"].help_text = f"How many streams (parallel classes) you plan to admit: 1 to {top}."


def _programme_estimate(inst, programmes, pending=None):
    """Phase 8: the programme processing fee for the listed programmes (and
    the one being typed, if any), one line per programme."""
    from payments.services import fee_estimate

    pairs = [(p, p.requested_streams) for p in programmes if p.status != "dropped"]
    if pending is not None:
        pairs.append(pending)
    if not pairs:
        return None
    return fee_estimate(inst, "programme_processing", pairs)


@institution_required
@profile_gate  # v2.1 X13
@require_http_methods(["GET", "POST"])
@htmx_fragment("qa_portal:application", "#qa-programmes")
def programmes(request):
    """Programme selection (onboarding stage `programme_selection`). Phase 2
    lets the institution list its programmes; programme clearance and fees
    open in Phase 3."""
    inst = _inst(request)
    app = _app(request, inst)
    selecting = app is not None and app.status == "programme_selection" and not getattr(
        request, "is_impersonating", False)
    form = ProgrammeForm(request.POST or None)
    if request.method == "POST":
        if not selecting:
            raise Http404
        if form.is_valid():
            d = form.cleaned_data
            from institutions.programme_master import add_programme

            try:
                add_programme(inst, user=request.user, master=d.get("master"), typed_name=d.get("name") or "",
                              level=d.get("level"), category=d.get("category"), body=d.get("body"),
                              application=app, streams=d["streams"])
            except ValidationError as exc:
                field = "body" if "body" in " ".join(exc.messages).lower() and not d.get("master") else None
                form.add_error(field, " ".join(exc.messages))
            else:
                response = render(request, "qa_portal/application/_programmes.html",
                                  _context(request, inst, app, form=ProgrammeForm(), selecting=selecting))
                return toast(response, "success", "Programme added")
        return render(request, "qa_portal/application/_programmes.html",
                      _context(request, inst, app, form=form, selecting=selecting,
                               picked=form.cleaned_data.get("master") if hasattr(form, "cleaned_data") else None,
                               manual_open=not request.POST.get("master")), status=422)
    return render(request, "qa_portal/application/_programmes.html",
                  _context(request, inst, app, form=form, selecting=selecting))


def _locked(app, programme):
    from . import onboarding_programmes as op

    return op.is_locked(app, programme)


@institution_required
@profile_gate  # v2.1 X13
@require_POST
def programmes_submit(request):
    """Q1: the Chief Executive submits the programme list to NBTE (X12)."""
    from . import onboarding_programmes as op

    inst = _inst(request)
    app = _app(request, inst)
    if app is None:
        raise Http404
    try:
        op.submit_programmes(app, user=request.user, impersonating=getattr(request, "is_impersonating", False))
    except (ValidationError, PermissionDenied) as exc:
        msgs = getattr(exc, "messages", [str(exc)])
        response = render(request, "qa_portal/application/page.html",
                          _context(request, inst, app, error=" ".join(msgs)), status=422)
        return toast(response, "error", "Not submitted", " ".join(msgs))
    app.refresh_from_db()
    response = render(request, "qa_portal/application/page.html", _context(request, inst, app))
    if app.status == "programme_clearance":
        return toast(response, "success", "Programmes submitted to NBTE",
                     f"Waiting for clearance (expected {op.EXPECTED_TIME}). You will be told after each decision.")
    return toast(response, "success", "Programmes submitted", "Your programme processing fee is on Payments.")


@institution_required
@require_POST
@htmx_fragment("qa_portal:application", "#qa-programmes")
def programme_remove(request, pk):
    inst = _inst(request)
    app = _app(request, inst)
    programme = Programme.objects.for_user(request.user).filter(pk=pk, institution=inst).first()
    if programme is None or app is None or app.status != "programme_selection" or programme.status != "proposed" \
            or getattr(request, "is_impersonating", False) or _locked(app, programme):
        raise Http404
    inst_services.remove_proposed_programme(programme, user=request.user)
    response = render(request, "qa_portal/application/_programmes.html",
                      _context(request, inst, app, form=ProgrammeForm(), selecting=True))
    return toast(response, "info", "Programme removed")


@institution_required
@require_POST
@htmx_fragment("qa_portal:application", "#qa-programmes")
def programme_streams(request, pk):
    """Phase 8: change the streams of a proposed programme while selecting."""
    from .qa_services import clean_streams

    inst = _inst(request)
    app = _app(request, inst)
    programme = Programme.objects.for_user(request.user).filter(pk=pk, institution=inst).first()
    if programme is None or app is None or app.status != "programme_selection" or programme.status != "proposed" \
            or getattr(request, "is_impersonating", False) or _locked(app, programme):
        raise Http404
    try:
        n = clean_streams(request.POST.get("streams"))
    except ValidationError as exc:
        response = render(request, "qa_portal/application/_programmes.html",
                          _context(request, inst, app, form=ProgrammeForm(), selecting=True,
                                   stream_error=" ".join(exc.messages)), status=422)
        return toast(response, "error", "Not saved", " ".join(exc.messages))
    inst_services.set_programme_streams(programme, n, user=request.user)
    response = render(request, "qa_portal/application/_programmes.html",
                      _context(request, inst, app, form=ProgrammeForm(), selecting=True))
    return toast(response, "success", f"{programme.name}: {n} stream{'s' if n != 1 else ''}")


@institution_required
@htmx_fragment("qa_portal:application", "#qa-programmes")
def programme_estimate(request):
    """Phase 8: live programme-fee estimate while the institution types (HTMX)."""
    from .qa_services import clean_streams

    inst = _inst(request)
    app = _app(request, inst)
    if app is None:
        raise Http404
    programmes = list(Programme.objects.for_user(request.user).filter(institution=inst))
    pending, problems = None, []
    name = (request.GET.get("name") or "").strip()
    raw = request.GET.get("streams")
    if name or raw:
        try:
            n = clean_streams(raw or 1)
        except ValidationError as exc:
            problems.append(" ".join(exc.messages))
            n = 1
        pending = (Programme(name=name or "New programme", status="proposed"), n)
    return render(request, "qa_portal/qa_requests/_fee_estimate.html",
                  {"estimate": _programme_estimate(inst, programmes, pending), "problems": problems,
                   "qa_type": True, "count": len(programmes)})


@institution_required
def programme_master_search(request):
    """v2.1 R.4 (X44): type-ahead over the programme master list (HTMX listbox)."""
    from institutions.programme_master import search_master

    q = request.GET.get("q", "")
    results = search_master(q)
    return render(request, "qa_portal/application/_master_options.html",
                  {"results": results, "q": q, "searched": len(q.strip()) >= 2})


@institution_required
def programme_master_pick(request, pk):
    """The chosen master row as a read-only chip + hidden input (pk 0 clears it)."""
    picked = ProgrammeMaster.objects.filter(pk=pk, is_active=True).select_related(
        "level", "professional_body").first() if pk else None
    return render(request, "qa_portal/application/_master_pick.html", {"picked": picked})
