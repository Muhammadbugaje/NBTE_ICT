"""Linear onboarding screens (namespace ``qa_app``, under /qa/applications/).

``self-study/<pk>/``                      one programme self-study: what the institution submitted
                                          and the bare-minimum counter over it (``onboarding_check``,
                                          read first), then where it is and the one step that is the
                                          viewer's (assign / send to director / clear · return to
                                          officer · return to institution). Sending a self-study that
                                          is below the bare minimum to the director needs the override
                                          and its reason on the POST.
``self-study/<pk>/assign|send|clear|return-officer|return-institution/``   POST steps
``<pk>/approve-institution/``             POST: the handling director initiates the approval
``approval/<pk>/confirm|reject/``         POST: the ES confirms / rejects (pending-action path)

Every rule is checked again in ``accreditation.onboarding_linear``; the counter itself is read-only
(``accreditation.onboarding_check``).
"""
from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from qa_core.permissions import is_qa_staff, require_or_404
from qa_core.ui import render_qa

from . import onboarding_linear as linear
from .models import Application, OnboardingSSQ


def _study_or_404(request, pk):
    study = OnboardingSSQ.unscoped.select_related(
        "application", "application__institution", "programme", "template", "submitted_by").filter(pk=pk).first()
    if study is None or not linear.can_view(request.user, study):
        raise Http404
    return study


def _back(request, default):
    nxt = request.POST.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) and nxt.startswith("/"):
        return redirect(nxt)
    return redirect(default)


def _fail(request, exc, default):
    messages.error(request, " ".join(getattr(exc, "messages", None) or [str(exc)]))
    return _back(request, default)


@require_or_404(is_qa_staff)
@require_GET
def self_study(request, pk):
    study = _study_or_404(request, pk)
    user = request.user
    review = linear.review_of(study)
    state = linear._state(study, review)
    from . import onboarding_check
    verdict = onboarding_check.check(study)
    ctx = {"study": study, "app": study.application, "inst": study.application.institution, "review": review,
           "stage": linear.review_stage(study), "state": state,
           "snapshot_valid": study.status != OnboardingSSQ.SUBMITTED or study.verify_submission(),
           "can_direct": linear.can_direct(user, study, review), "can_assess": linear.can_assess(user, study, review),
           "recommendations": linear.RECOMMENDATIONS, "history": list(reversed((review.history or [])[-20:]))
           if review is not None else [],
           # The smart counter: does this self-study reach the bare minimum? Read first, act below.
           "check": verdict, "check_labels": onboarding_check.STATUS_LABELS,
           "check_override": onboarding_check.latest_override(review)}
    if state["code"] == linear.AWAITING_ASSIGNMENT and ctx["can_direct"]:
        from assignments import rotation_queue as rq
        job = rq.Job(rq.ONBOARDING, study, state["department"], ctx["inst"], str(study.programme), "",
                     study.submitted_at)
        rows, suggested = rq.candidates(job) if state["department"] is not None else ([], None)
        ctx["candidates"], ctx["suggested"] = [r for r in rows if r["eligible"]], suggested
    return render_qa(request, "accreditation/onboarding/self_study.html", ctx)


def _post_step(request, pk, fn, ok):
    study = _study_or_404(request, pk)
    default = linear.study_url(study)
    try:
        fn(study)
    except (ValidationError, PermissionDenied) as exc:
        return _fail(request, exc, default)
    messages.success(request, ok(study) if callable(ok) else ok)
    return _back(request, default)


@require_or_404(is_qa_staff)
@require_POST
def self_study_assign(request, pk):
    from accounts.models import User
    officer = None
    if request.POST.get("officer"):
        officer = User.objects.filter(pk=request.POST["officer"], is_active=True).first()
    return _post_step(request, pk, lambda s: linear.assign(s, by=request.user, officer=officer,
                                                           reason=request.POST.get("reason", "")),
                      lambda s: f"Assigned to {linear._name(officer)}. They have been notified.")


@require_or_404(is_qa_staff)
@require_POST
def self_study_send(request, pk):
    return _post_step(request, pk, lambda s: linear.send_to_director(
        s, officer=request.user, recommendation=request.POST.get("recommendation", ""),
        remark=request.POST.get("remark", ""),
        override=request.POST.get("override") == "1",
        override_reason=request.POST.get("override_reason", "")), "Sent to the director.")


@require_or_404(is_qa_staff)
@require_POST
def self_study_clear(request, pk):
    def ok(s):
        if linear.ready_for_approval(s.application):
            return f"Programme cleared. {s.application.institution.name} has cleared everything — approve it now."
        return "Programme cleared."
    return _post_step(request, pk, lambda s: linear.clear(s, by=request.user, remark=request.POST.get("remark", "")),
                      ok)


@require_or_404(is_qa_staff)
@require_POST
def self_study_return_officer(request, pk):
    return _post_step(request, pk, lambda s: linear.return_to_officer(s, by=request.user,
                                                                      remark=request.POST.get("remark", "")),
                      "Sent back to the officer.")


@require_or_404(is_qa_staff)
@require_POST
def self_study_return_institution(request, pk):
    return _post_step(request, pk, lambda s: linear.return_to_institution(s, by=request.user,
                                                                          remark=request.POST.get("remark", "")),
                      "Returned to the institution for correction.")


@require_or_404(is_qa_staff)
@require_POST
def approve_institution(request, pk):
    app = Application.unscoped.select_related("institution").filter(pk=pk).first()
    if app is None:
        raise Http404
    default = linear.application_url(app)
    try:
        pa = linear.approve_institution(app, by=request.user, remark=request.POST.get("remark", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _fail(request, exc, default)
    if pa.status == pa.PENDING:
        messages.success(request, f"Approval sent to the ES for confirmation ({pa.reference}).")
    else:
        messages.success(request, f"{app.institution.name}: {pa.get_status_display().lower()}.")
    return _back(request, default)


def _pa_or_404(pk):
    from qa_core.models import PendingAction
    pa = PendingAction.objects.select_related("policy", "initiated_by").filter(pk=pk,
                                                                                policy_id=linear.APPROVE_POLICY).first()
    if pa is None:
        raise Http404
    return pa


@require_or_404(is_qa_staff)
@require_POST
def approval_confirm(request, pk):
    pa = _pa_or_404(pk)
    try:
        pa = linear.confirm_approval(pa, by=request.user, remark=request.POST.get("remark", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _fail(request, exc, "/qa/")
    if pa.status == pa.APPLIED:
        messages.success(request, f"{pa.target_label} is approved. The institution has been told and its formal "
                                  "QA request has started.")
    elif pa.status == pa.FAILED:
        messages.error(request, f"Could not apply: {pa.failure_detail}")
    else:
        messages.success(request, "Confirmed.")
    return _back(request, "/qa/")


@require_or_404(is_qa_staff)
@require_POST
def approval_reject(request, pk):
    pa = _pa_or_404(pk)
    try:
        linear.reject_approval(pa, by=request.user, remark=request.POST.get("remark", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _fail(request, exc, "/qa/")
    messages.success(request, "Approval rejected; the director has been told.")
    return _back(request, "/qa/")
