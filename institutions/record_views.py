"""Institution exports (Phase 8), namespace "qa_inst":

    /qa/institutions/<code>/timeline/export/?format=pdf|csv  (the tab's filters carry over)
    /qa/institutions/<code>/record/                          the institutional record (PDF)
    /qa/institutions/<code>/record/zip/                      the record PDF + every file, in folders
    /qa/institutions/<code>/payments/statement/?format=pdf|csv  the payment statement

Permission: staff who can see the institution (out of scope → 404). Every
download is audit-logged.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core import audit
from qa_core.pdf_report import Report, csv_response, pdf_response
from qa_core.permissions import can_view_institutions, require

from . import record
from .models import Institution


def _inst_or_404(request, code):
    inst = (Institution.objects.for_user(request.user)
            .select_related("ownership", "institution_type", "category", "state", "handling_department")
            .filter(code=code.upper()).first())
    if inst is None:
        raise Http404("No such institution in your scope.")
    return inst


def _stamp():
    return timezone.localdate().isoformat()


@require(can_view_institutions)
@require_GET
def timeline_export(request, code):
    inst = _inst_or_404(request, code)
    vis = request.GET.get("vis", "")
    tq = (request.GET.get("tq") or "").strip()
    qs = record.timeline_queryset(request.user, inst, vis=vis, tq=tq)
    fmt = request.GET.get("format", "pdf")
    filters = ", ".join(x for x in (f"visibility {vis}" if vis else "", f"search “{tq}”" if tq else "") if x)
    audit.log("other", inst, actor=request.user, note=f"exported the timeline ({fmt}{'; ' + filters if filters else ''})")
    name = f"{inst.code}-timeline-{_stamp()}"
    if fmt == "csv":
        return csv_response(f"{name}.csv", record.TIMELINE_HEADERS, record.timeline_rows(qs))
    return pdf_response(f"{name}.pdf", record.timeline_pdf(inst, request.user, qs, filters=filters))


@require(can_view_institutions)
@require_GET
def record_pdf(request, code):
    inst = _inst_or_404(request, code)
    audit.log("other", inst, actor=request.user, note="downloaded the institutional record (PDF)")
    return pdf_response(f"{inst.code}-institutional-record-{_stamp()}.pdf", record.record_pdf(inst, request.user))


@require(can_view_institutions)
@require_GET
def record_zip(request, code):
    inst = _inst_or_404(request, code)
    audit.log("other", inst, actor=request.user, note="downloaded the institutional record with all files (ZIP)")
    response = HttpResponse(record.record_zip(inst, request.user), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{inst.code}-institutional-record-{_stamp()}.zip"'
    return response


@require(can_view_institutions)
@require_GET
def payment_statement(request, code):
    """Every bill of the institution with what was paid, the balance and the
    advices — a statement Finance or the desk can hand over."""
    inst = _inst_or_404(request, code)
    if not record.can_see_payments(request.user):
        raise PermissionDenied("Payment statements need payments.view_obligation.")
    from payments.models import PaymentAdvice, PaymentObligation

    obligations = list(PaymentObligation.objects.for_user(request.user).filter(institution=inst)
                       .order_by("created_at"))
    advices = {}
    for a in PaymentAdvice.objects.for_user(request.user).filter(obligation__institution=inst):
        advices.setdefault(a.obligation_id, []).append(a.reference)
    fmt = request.GET.get("format", "pdf")
    audit.log("other", inst, actor=request.user, note=f"exported the payment statement ({fmt})")
    rows = [[o.reference, o.created_at.date().isoformat() if o.created_at else "", o.description, o.amount,
             o.amount_paid, o.balance, o.status.replace("_", " "), ", ".join(advices.get(o.pk, []))]
            for o in obligations]
    name = f"{inst.code}-payment-statement-{_stamp()}"
    headers = ["Reference", "Issued", "Description", "Amount (NGN)", "Paid (NGN)", "Balance (NGN)", "Status",
               "Payment advice"]
    if fmt == "csv":
        return csv_response(f"{name}.csv", headers, rows)
    total = sum((o.amount for o in obligations if o.status != "cancelled"), 0)
    paid = sum((o.amount_paid for o in obligations), 0)
    balance = sum((o.balance for o in obligations if o.status != "cancelled"), 0)
    rep = Report("Payment statement", subject=f"{inst.name} ({inst.code})", generated_by=request.user,
                 facts=[("Institution", inst.name), ("Bills", len(obligations)), ("Billed", f"NGN {total:,.2f}"),
                        ("Paid", f"NGN {paid:,.2f}"), ("Outstanding", f"NGN {balance:,.2f}")])
    rep.h1("Bills and payments")
    rep.table(headers, [[r[0], r[1], r[2], f"{r[3]:,.2f}", f"{r[4]:,.2f}", f"{r[5]:,.2f}", r[6], r[7]] for r in rows],
              widths=[0.14, 0.1, 0.24, 0.11, 0.11, 0.11, 0.09, 0.1], empty="No bills.")
    rep.para(f"Billed NGN {total:,.2f} · paid NGN {paid:,.2f} · outstanding NGN {balance:,.2f} "
             "(cancelled bills excluded from the totals).", "small")
    return pdf_response(f"{name}.pdf", rep.build())
