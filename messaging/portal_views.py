"""QA portal: the institution's Memos inbox (v2.1 Phase E, M4).

External and general memos the ES sent (or approved) to the institution:
list, read (first open marks it read for the institution), acknowledge, and
download the signed letter personalised to the institution. Another
institution's memo answers 404. Services: messaging.memos.
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from qa_core.portal_views import _institution_for, _shell_context, institution_required

from . import memos as svc

logger = logging.getLogger(__name__)


def _inst(request):
    return _institution_for(request.user)


@institution_required
@require_GET
def memo_list(request):
    inst = _inst(request)
    rows = svc.institution_memos_for(inst)
    show = (request.GET.get("show") or "").strip()
    q = (request.GET.get("q") or "").strip()[:80]
    if show == "unread":
        rows = rows.filter(read_at__isnull=True)
    elif show == "to_ack":
        rows = rows.filter(memo__requires_acknowledgment=True, acknowledged_at__isnull=True)
    if q:
        from django.db.models import Q
        rows = rows.filter(Q(memo__subject__icontains=q) | Q(memo__reference__icontains=q))
    ctx = _shell_context(request, "memos", inst=inst, rows=list(rows[:200]), show=show, q=q,
                         unread=svc.unread_institution_memo_count(inst))
    return render(request, "qa_portal/memos/list.html", ctx)


@institution_required
@require_GET
def memo_detail(request, pk):
    inst = _inst(request)
    receipt = svc.get_institution_receipt(inst, pk)
    svc.open_institution_memo(receipt, request.user, impersonating=getattr(request, "is_impersonating", False))
    memo = receipt.memo
    from .memo_documents import _es_for_letter, external_letterhead, external_reference

    es = _es_for_letter(memo)
    sig = ""
    try:
        from documents.signature import data_uri_for
        sig = data_uri_for(memo.es_signatory, memo.es_signed_at) if memo.es_signatory_id else ""
    except Exception:
        sig = ""
    ctx = _shell_context(request, "memos", inst=inst, receipt=receipt, memo=memo,
                         letterhead=external_letterhead(), external_ref=external_reference(memo),
                         es_name=(es.get_full_name() or es.email) if es else "Executive Secretary",
                         es_signature=sig, attachments=list(memo.attachments.all()))
    return render(request, "qa_portal/memos/detail.html", ctx)


@institution_required
@require_POST
def memo_acknowledge(request, pk):
    inst = _inst(request)
    receipt = svc.get_institution_receipt(inst, pk)
    if getattr(request, "is_impersonating", False):
        raise PermissionDenied("Read-only while viewing as the institution.")
    svc.acknowledge_institution_memo(receipt, request.user)
    return redirect("qa_portal:memo", pk=pk)


@institution_required
@require_GET
def memo_pdf(request, pk):
    inst = _inst(request)
    receipt = svc.get_institution_receipt(inst, pk)
    from core import file_preview

    from .memo_documents import render_memo_pdf
    pdf = render_memo_pdf(receipt.memo, institution=inst)
    resp = HttpResponse(pdf, content_type="application/pdf")
    return file_preview.finish(resp, f"{receipt.memo.reference}.pdf", request.GET.get("download") != "1")
