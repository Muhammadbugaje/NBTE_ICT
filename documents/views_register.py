"""Issued-documents register for staff (/qa/documents/; G5.g.4): search by
reference, short code, institution or title; filters; upload the official
copy; revoke with a reason; the verification history of each document."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from qa_core.ui import paginate, render_qa

from . import services, verify
from .models import KIND_CHOICES, IssuedDocument, OfficialCopy


def _require_view(user):
    if not services.can_view_register(user):
        raise PermissionDenied("The issued-documents register needs documents.view_issued_documents.")


def _filtered(request):
    qs = IssuedDocument.unscoped.select_related("institution").order_by("-issued_at", "-id")
    q = (request.GET.get("q") or "").strip()
    if q:
        from .codes import normalise_short_code

        code = normalise_short_code(q)
        cond = (Q(reference__icontains=q) | Q(title__icontains=q) | Q(institution__name__icontains=q)
                | Q(institution__code__iexact=q))
        if code:
            cond |= Q(short_code=code)
        qs = qs.filter(cond)
    kind = request.GET.get("kind") or ""
    if kind in dict(KIND_CHOICES):
        qs = qs.filter(kind=kind)
    status = request.GET.get("status") or ""
    if status in dict(IssuedDocument.STATUS_CHOICES):
        qs = qs.filter(status=status)
    elif status == "provisional":
        qs = qs.filter(provisional=True, official_copies__isnull=True)
    return qs, {"q": q, "kind": kind, "status": status}


@login_required
def register(request):
    _require_view(request.user)
    qs, filters = _filtered(request)
    base = IssuedDocument.unscoped
    kpis = {"total": base.count(), "valid": base.filter(status="valid").count(),
            "provisional": base.filter(provisional=True, official_copies__isnull=True, status="valid").count(),
            "revoked": base.filter(status="revoked").count()}
    ctx = {"page": paginate(request, qs), "filters": filters, "kinds": KIND_CHOICES,
           "statuses": IssuedDocument.STATUS_CHOICES, "kpis": kpis}
    return render_qa(request, "documents/register/page.html", ctx, {"doc-panel": "documents/register/_list.html"})


@login_required
def detail(request, pk):
    _require_view(request.user)
    doc = get_object_or_404(IssuedDocument.unscoped.select_related("institution", "revoked_by", "issued_by"), pk=pk)
    ctx = {"doc": doc, "card": verify.card_for(doc, user=request.user),
           "can_upload": services.can_upload_official(request.user, doc) and doc.status != "revoked",
           "can_revoke": services.can_revoke(request.user) and doc.status == "valid",
           "can_log": services.can_view_log(request.user),
           "log": list(doc.verifications.select_related("user").order_by("-at")[:25]),
           "copies": list(OfficialCopy.unscoped.filter(document=doc).select_related("uploaded_by"))}
    return render_qa(request, "documents/register/detail.html", ctx)


@login_required
@require_POST
def revoke(request, pk):
    doc = get_object_or_404(IssuedDocument.unscoped, pk=pk)
    try:
        services.revoke(doc, user=request.user, reason=request.POST.get("reason", ""))
        messages.success(request, f"{doc.reference} is revoked. Anyone who scans it now sees “Revoked”.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("documents_register:detail", pk=doc.pk)


@login_required
@require_POST
def upload_official(request, pk):
    doc = get_object_or_404(IssuedDocument.unscoped, pk=pk)
    try:
        services.upload_official(doc, user=request.user, upload=request.FILES.get("file"),
                                 note=request.POST.get("note", ""))
        messages.success(request, "Official copy uploaded. The institution has been told and the verify page "
                                  "now shows it.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("documents_register:detail", pk=doc.pk)
