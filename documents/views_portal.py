"""Portal: the institution's issued documents (X25; design 27b).

The Letters page lists them (provisional badge, official copy when NBTE has
uploaded it). Opening a provisional document the first time shows the pop-up
("This is a provisional copy ... await the official document") until the user
presses "I understand" - remembered per user per document (DocumentOpen)."""
import base64

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from qa_core.portal_views import _shell_context, institution_required
from qa_core.ui import is_htmx

from . import issue as issuing
from . import services
from .models import IssuedDocument


def _doc(request, pk):
    doc = get_object_or_404(services.for_institution(request.user), pk=pk)
    if not doc.can_download(request.user):
        raise Http404
    return doc


def _qr_data_uri(doc):
    from .render import qr_png

    return "data:image/png;base64," + base64.b64encode(qr_png(issuing.verify_url(doc.token))).decode("ascii")


@institution_required
def document(request, pk):
    doc = _doc(request, pk)
    impersonating = bool(getattr(request, "is_impersonating", False))
    show_popup = services.opened(doc, request.user) if not impersonating else doc.provisional
    body = (doc.payload or {}).get("body") or []
    facts = (doc.payload or {}).get("facts") or []
    from .official_letters import portal_status

    ctx = _shell_context(request, "letters", doc=doc, official=doc.official_copy, show_popup=show_popup,
                         official_status=portal_status(doc),
                         popup_text=issuing.popup_text(), qr=_qr_data_uri(doc), body=body, facts=facts,
                         signers=doc.signatories or [])
    return render(request, "documents/portal/document.html", ctx)


@institution_required
@require_POST
def acknowledge(request, pk):
    doc = _doc(request, pk)
    if not getattr(request, "is_impersonating", False):
        services.acknowledge(doc, request.user)
    if is_htmx(request):
        return HttpResponse("")
    return redirect("qa_portal:issued_document", pk=doc.pk)


def letters_context(user):
    """Rows for the Letters page section (accreditation.programme_portal_views.letters)."""
    from .official_letters import portal_status

    rows = list(services.for_institution(user)[:100])
    for row in rows:
        row.official_status = portal_status(row)
    return {"issued_docs": rows}


__all__ = ["document", "acknowledge", "letters_context", "IssuedDocument"]
