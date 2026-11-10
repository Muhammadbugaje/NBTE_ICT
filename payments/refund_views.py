"""v2.5 — cancellation & refund screens.

Staff (internal host, namespace ``qa_fin``):
    refunds/                          Refunds tab: cancellation refunds + overpayment refunds
    refund-requests/<pk>/             one refund: quote, bank details, Finance / ES actions
    refund-requests/<pk>/<verb>/      verify · return · approve · reject · paid
    withdraw/<kind>/<pk>/             NBTE withdraws a QA request / application (100% band)

Portal (namespace ``qa_portal``):
    qa-requests/<pk>/cancel-with-refund/   cancel a QA request (quote, reason, declaration)
    application/cancel/                    cancel the onboarding application
    refunds/                               the institution's refunds; bank details form
    refunds/<pk>/bank-details/
    refund-policy/                         the current policy in plain English
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core.permissions import require, require_or_404
from qa_core.portal_views import _shell_context, institution_required
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import cancellations as svc
from . import refund_policy as policy
from .models import Refund, RefundRequest
from .money import naira


def _msg(exc):
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


def _impersonating(request):
    return bool(getattr(request, "is_impersonating", False))


def _bank(post):
    return {k: post.get(k, "") for k in ("bank_name", "account_name", "account_number")}


# ===========================================================================
# Staff
# ===========================================================================
TABS = [("open", "Open"), (RefundRequest.FINANCE, "To verify"), (RefundRequest.APPROVAL, "Awaiting approval"),
        (RefundRequest.APPROVED, "To pay"), (RefundRequest.DETAILS, "Waiting for bank details"),
        (RefundRequest.REFUNDED, "Refunded"), ("closed", "Rejected / lapsed"), ("all", "All")]


def _qs(user):
    from qa_core.permissions import can_view_finance

    # Refunds are a Finance / ES queue across all institutions (like the bill approvals).
    whole_queue = svc.is_approver(user) or user.is_superuser or svc.can_verify(user) or can_view_finance(user)
    base = RefundRequest.unscoped.all() if whole_queue else RefundRequest.objects.for_user(user)
    return base.select_related("institution", "cancellation", "verified_by", "decided_by", "paid_by")


def _hero(request):
    qs = _qs(request.user)
    user = request.user
    return {
        "to_verify": qs.filter(status=RefundRequest.FINANCE).count(),
        "awaiting_approval": qs.filter(status=RefundRequest.APPROVAL).count(),
        "to_pay": qs.filter(status=RefundRequest.APPROVED).count(),
        "refunded_total": qs.filter(status=RefundRequest.REFUNDED).aggregate(s=Sum("refund_amount"))["s"] or 0,
        "is_approver": svc.is_approver(user), "can_verify": svc.can_verify(user),
        "requires_es": policy.requires_es_approval(),
    }


def _list_ctx(request, **extra):
    svc.lapse_overdue()
    qs = _qs(request.user)
    hero = _hero(request)
    default = RefundRequest.APPROVAL if hero["is_approver"] and not hero["can_verify"] else "open"
    status = request.GET.get("status") or default
    counts = dict(qs.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    counts["open"] = sum(counts.get(s, 0) for s in RefundRequest.OPEN)
    counts["closed"] = counts.get(RefundRequest.REJECTED, 0) + counts.get(RefundRequest.LAPSED, 0)
    rows = qs
    if status == "open":
        rows = rows.filter(status__in=RefundRequest.OPEN)
    elif status == "closed":
        rows = rows.filter(status__in=(RefundRequest.REJECTED, RefundRequest.LAPSED))
    elif status != "all":
        rows = rows.filter(status=status)
    q = (request.GET.get("q") or "").strip()
    if q:
        rows = rows.filter(Q(reference__icontains=q) | Q(institution__name__icontains=q)
                           | Q(cancellation__subject_reference__icontains=q) | Q(institution__code__iexact=q))
    legacy = Refund.objects.for_user(request.user).select_related("obligation", "obligation__institution") \
        .order_by("-created_at")[:20]
    ctx = {"page": paginate(request, rows.order_by("-created_at")), "status": status, "tabs": TABS,
           "counts": counts, "q": q, "hero": hero, "fin_tab": "refunds", "legacy": list(legacy),
           "policy": policy.policy_rows()}
    ctx.update(extra)
    return ctx


@require(svc.can_view)
@require_GET
def refunds(request):
    return render_qa(request, "payments/refunds/list.html", _list_ctx(request),
                     {"qa-panel": "payments/refunds/_list_panel.html"})


def _detail_ctx(request, rr, **extra):
    can_decide, why = svc.can_decide(rr, request.user, impersonating=_impersonating(request))
    order = [RefundRequest.DETAILS, RefundRequest.FINANCE, RefundRequest.APPROVAL, RefundRequest.APPROVED,
             RefundRequest.REFUNDED]
    at = order.index(rr.status) if rr.status in order else (3 if rr.status == RefundRequest.REJECTED else 0)
    approver = "Executive Secretary" if rr.approver_kind == "es" else "Director of Finance"
    labels = [("Bank details", rr.details_at), ("Finance verified", rr.verified_at),
              (f"{approver} approved", rr.decided_at), ("Refund paid", rr.paid_at)]
    steps = [{"label": lbl, "when": when, "done": i + 1 <= at or (rr.status == RefundRequest.REFUNDED),
              "now": i + 1 == at + 1 and rr.status in RefundRequest.OPEN} for i, (lbl, when) in enumerate(labels)]
    ctx = {"rr": rr, "c": rr.cancellation, "hero": _hero(request), "fin_tab": "refunds", "steps": steps,
           "can_decide": can_decide, "decide_why": why, "approver": approver,
           "can_verify": svc.can_verify(request.user) and rr.status == RefundRequest.FINANCE,
           "can_pay": svc.can_record_payment(request.user) and rr.status == RefundRequest.APPROVED}
    ctx.update(extra)
    return ctx


@require(svc.can_view)
@require_GET
def refund_request_detail(request, pk):
    rr = get_object_or_404(_qs(request.user), pk=pk)
    return render_qa(request, "payments/refunds/detail.html", _detail_ctx(request, rr),
                     {"qa-panel": "payments/refunds/_detail_panel.html"})


VERBS = {
    "verify": ("Verified — sent for approval", "The approver has been notified."),
    "return": ("Sent back to the institution", "They have been asked to correct the bank details."),
    "approve": ("Refund approved", "Finance has been told to pay it; the institution has been notified."),
    "reject": ("Refund rejected", "The institution has been told why."),
    "paid": ("Refund recorded as paid", "The institution has been notified with the bank reference."),
}


@require(svc.can_view)
@require_POST
def refund_request_action(request, pk, verb):
    if verb not in VERBS:
        raise Http404
    rr = get_object_or_404(_qs(request.user), pk=pk)
    post, user, imp = request.POST, request.user, _impersonating(request)
    try:
        if verb == "verify":
            rr = svc.verify(rr, actor=user, note=post.get("note", ""), impersonating=imp)
        elif verb == "return":
            rr = svc.return_to_institution(rr, actor=user, note=post.get("note", ""))
        elif verb == "approve":
            rr = svc.approve(rr, actor=user, remark=post.get("remark", ""), impersonating=imp)
        elif verb == "reject":
            rr = svc.reject(rr, actor=user, remark=post.get("remark", ""), impersonating=imp)
        else:
            rr = svc.mark_refunded(rr, actor=user, paid_reference=post.get("paid_reference", ""))
    except (ValidationError, PermissionDenied) as exc:
        msg = _msg(exc)
        rr.refresh_from_db()
        response = render_qa(request, "payments/refunds/detail.html",
                             _detail_ctx(request, rr, flash={"kind": "danger", "title": "Not done", "message": msg}),
                             {"qa-panel": "payments/refunds/_detail_panel.html"},
                             status=403 if isinstance(exc, PermissionDenied) else 422)
        return toast(response, "error", "Not done", msg[:200])
    title, body = VERBS[verb]
    ctx = _detail_ctx(request, rr, flash={"kind": "danger" if verb == "reject" else "success", "title": title,
                                          "message": body})
    response = render_qa(request, "payments/refunds/detail.html", ctx,
                         {"qa-panel": "payments/refunds/_detail_panel.html"})
    return toast(response, "success", title, body)


def _subject(kind, pk):
    from accreditation.models import Application, QARequest

    model = {"qa_request": QARequest, "application": Application}.get(kind)
    if model is None:
        raise Http404
    return get_object_or_404(model.unscoped.select_related("institution"), pk=pk)


def _staff_subject_url(kind, subject):
    return f"/qa/requests/{subject.pk}/" if kind == "qa_request" else f"/qa/applications/{subject.pk}/"


@require_or_404(svc.can_withdraw_for_nbte)
@require_http_methods(["GET", "POST"])
def withdraw(request, kind, pk):
    subject = _subject(kind, pk)
    ctx = {"subject": subject, "kind": kind, "hero": _hero(request), "fin_tab": "refunds",
           "quote": policy.refund_quote(subject, by_nbte=True), "policy": policy.policy_rows(),
           "back_url": _staff_subject_url(kind, subject), "done": svc.cancellation_of(subject)}
    if request.method == "POST":
        try:
            c = svc.cancel(subject, actor=request.user, reason=request.POST.get("reason", ""), by_nbte=True,
                           declaration=request.POST.get("declaration") == "on")
        except (ValidationError, PermissionDenied) as exc:
            msg = _msg(exc)
            ctx["flash"] = {"kind": "danger", "title": "Not withdrawn", "message": msg}
            response = render_qa(request, "payments/refunds/withdraw.html", ctx,
                                 {"qa-panel": "payments/refunds/_withdraw_panel.html"}, status=422)
            return toast(response, "error", "Not withdrawn", msg[:200])
        rr = RefundRequest.unscoped.filter(cancellation=c).first()
        title = f"{subject.reference} withdrawn by NBTE"
        body = (f"Full refund {rr.reference} of {naira(rr.refund_amount)} opened; the institution was asked for "
                "bank details." if rr else "Nothing had been paid, so no refund is due.")
        ctx.update(done=c, quote=c.quote, flash={"kind": "success", "title": title, "message": body})
        response = render_qa(request, "payments/refunds/withdraw.html", ctx,
                             {"qa-panel": "payments/refunds/_withdraw_panel.html"})
        return toast(response, "success", title, body)
    return render_qa(request, "payments/refunds/withdraw.html", ctx,
                     {"qa-panel": "payments/refunds/_withdraw_panel.html"})


# ===========================================================================
# Portal (institutions)
# ===========================================================================
def _portal_req(request, pk):
    from accreditation.models import QARequest

    req = QARequest.objects.for_user(request.user).select_related("qa_type", "institution").filter(pk=pk).first()
    if req is None:
        raise Http404
    return req


def _portal_app(request):
    from accreditation.models import Application

    app = Application.objects.for_user(request.user).select_related("institution").order_by("-created_at").first()
    if app is None:
        raise Http404
    return app


def _cancel_page(request, subject, *, kind, back_url, action_url, title):
    if not svc.can_cancel_as_institution(request.user, subject):
        raise Http404
    quote = policy.refund_quote(subject)
    ctx = {"subject": subject, "kind": kind, "quote": quote, "policy": policy.policy_rows(), "back_url": back_url,
           "action_url": action_url, "page_title": title, "done": svc.cancellation_of(subject),
           "cancellable": svc.is_cancellable(subject), "form": {}}
    if request.method == "POST":
        ctx["form"] = request.POST
        try:
            c = svc.cancel(subject, actor=request.user, reason=request.POST.get("reason", ""),
                           declaration=request.POST.get("declaration") == "on", bank=_bank(request.POST))
        except (ValidationError, PermissionDenied) as exc:
            msg = _msg(exc)
            ctx["error"] = msg
            response = render(request, "qa_portal/refunds/cancel.html",
                              _shell_context(request, "qa_requests" if kind == "qa_request" else "application",
                                             **ctx), status=422)
            return toast(response, "error", "Not cancelled", msg[:200])
        rr = RefundRequest.unscoped.filter(cancellation=c).first()
        if rr is None:
            body = "Any unpaid bill was voided. " + ("No refund is due under the policy." if c.quote.get("paid") not in
                                                     (None, "0", "0.00") else "Nothing had been paid.")
        elif rr.status == RefundRequest.DETAILS:
            body = f"Refund {rr.reference} of {naira(rr.refund_amount)} opened. Add your bank details on Refunds."
        else:
            body = f"Refund {rr.reference} of {naira(rr.refund_amount)} is with NBTE Finance."
        ctx.update(done=c, cancellable=False, ok={"title": f"{subject.reference} cancelled", "message": body},
                   refund=rr)
        response = render(request, "qa_portal/refunds/cancel.html",
                          _shell_context(request, "qa_requests" if kind == "qa_request" else "application", **ctx))
        return toast(response, "success", f"{subject.reference} cancelled", body)
    return render(request, "qa_portal/refunds/cancel.html",
                  _shell_context(request, "qa_requests" if kind == "qa_request" else "application", **ctx))


@institution_required
@require_http_methods(["GET", "POST"])
def portal_cancel_request(request, pk):
    req = _portal_req(request, pk)
    return _cancel_page(request, req, kind="qa_request", back_url=reverse("qa_portal:qa_request", args=[req.pk]),
                        action_url=reverse("qa_portal:qa_request_cancel_refund", args=[req.pk]),
                        title=f"Cancel {req.reference}")


@institution_required
@require_http_methods(["GET", "POST"])
def portal_cancel_application(request):
    app = _portal_app(request)
    return _cancel_page(request, app, kind="application", back_url=reverse("qa_portal:application"),
                        action_url=reverse("qa_portal:application_cancel"), title="Cancel your application")


def _portal_refunds_ctx(request, **extra):
    svc.lapse_overdue()
    rows = list(RefundRequest.objects.for_user(request.user).select_related("cancellation").order_by("-created_at"))
    from .models import Cancellation

    cancelled = list(Cancellation.objects.for_user(request.user).filter(refund_request__isnull=True)
                     .order_by("-created_at")[:20])
    from accreditation.qa_services import can_manage_requests
    from institutions.services import is_chief_executive

    inst = request.user.institution
    ctx = {"rows": rows, "cancelled": cancelled, "policy": policy.policy_rows(),
           "can_manage": inst is not None and can_manage_requests(request.user, inst)
           and is_chief_executive(request.user)}
    ctx.update(extra)
    return _shell_context(request, "payments", **ctx)


@institution_required
@require_GET
def portal_refunds(request):
    return render(request, "qa_portal/refunds/list.html", _portal_refunds_ctx(request))


@institution_required
@require_POST
def portal_refund_bank_details(request, pk):
    rr = RefundRequest.objects.for_user(request.user).filter(pk=pk).first()
    if rr is None:
        raise Http404
    try:
        svc.submit_bank_details(rr, actor=request.user, bank=_bank(request.POST))
    except (ValidationError, PermissionDenied) as exc:
        msg = _msg(exc)
        response = render(request, "qa_portal/refunds/list.html",
                          _portal_refunds_ctx(request, error=msg, error_for=rr.pk, form=request.POST), status=422)
        return toast(response, "error", "Bank details not saved", msg[:200])
    title, body = "Bank details sent", f"NBTE Finance will verify refund {rr.reference} and keep you posted."
    response = render(request, "qa_portal/refunds/list.html",
                      _portal_refunds_ctx(request, ok={"title": title, "message": body}))
    return toast(response, "success", title, body)


@institution_required
@require_GET
def portal_refund_policy(request):
    return render(request, "qa_portal/refunds/policy.html",
                  _shell_context(request, "payments", policy=policy.policy_rows()))
