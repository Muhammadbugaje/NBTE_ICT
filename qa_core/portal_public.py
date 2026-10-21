"""Public pages of the QA portal (Phase 6): landing, how-to, guide downloads.

* ``/`` — the landing page for visitors; signed-in users get their dashboard.
* ``/how-to/`` — how every kind of request works, with diagrams.
* ``/guides/<slug>/`` — the Word guides built by
  ``manage.py generate_institution_guides``.

Registration follows ``qa.portal.self_registration_enabled``. When it is off,
the pages say who to contact, using ``qa.portal.registration_contact_email`` /
``…_phone`` and falling back to the site's public contact details.
"""
from __future__ import annotations

import os

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

import config

GUIDES_SUBDIR = os.path.join("qa", "guides")

# slug, title, summary (the order they are listed on the how-to page)
GUIDES = [
    ("starter", "Starter guide", "Accounts, the dashboard, your timeline and how NBTE contacts you"),
    ("onboarding", "Onboarding guide (SOP-01)", "Registering a new institution and getting it approved"),
    ("qa-requests", "QA requests guide (SOP-02)", "Requesting accreditation and completing the SSQ"),
    ("payments", "Payments guide", "Bills, paying online or by bank, proof and receipts"),
]

# What each QA type is for, in plain words (keyed by QAType.code).
QA_TYPE_EXPLAINERS = {
    "resource_inspection": "The first visit to a new programme, to check you have the staff, facilities and "
                           "equipment to start it. A successful inspection gives approval to commence.",
    "accreditation": "The full assessment of a programme once it has run long enough for its first students to "
                     "graduate. Accreditation is granted for a fixed period.",
    "reaccreditation": "Renewing the accreditation of a programme before it expires, or after interim "
                       "accreditation once the gaps NBTE found have been fixed.",
    "validation": "Confirming that an institution's records and claims about a programme are accurate, usually "
                  "after a change or when NBTE needs to check something.",
    "verification": "A targeted check of particular facts about a programme or the institution, such as staffing "
                    "or student numbers.",
    "increase_capacity": "Asking to admit more students into a programme by approving additional streams.",
    "institutional_administration": "The assessment of the institution as a whole: governance, administration "
                                    "and the services that support every programme.",
}


def registration_open():
    return str(config.get("qa.portal.self_registration_enabled", True)).lower() in ("true", "1", "yes")


def contact_details(request=None):
    from config.context_processors import site_info

    info = site_info(request).get("site_info", {}) if request is not None else {}
    email = (config.get("qa.portal.registration_contact_email", "") or "").strip() or info.get("email", "")
    phone = (config.get("qa.portal.registration_contact_phone", "") or "").strip() or info.get("phone", "")
    return email, phone


def closed_message(email, phone):
    how = " or ".join(x for x in (email, phone) if x)
    if how:
        return (f"Institution registration is currently managed by NBTE. Please contact {how} to begin your "
                "registration.")
    return "Institution registration is currently managed by NBTE. Please contact NBTE to begin your registration."


def public_context(request, page):
    email, phone = contact_details(request)
    return {"page": page, "registration_open": registration_open(), "contact_email": email,
            "contact_phone": phone, "registration_closed_message": closed_message(email, phone),
            "portal_name": "NBTE Quality Assurance Portal",
            "maintenance_message": (config.get("qa.portal.maintenance_message", "") or "").strip()}


def guide_path(slug):
    return os.path.join(settings.MEDIA_ROOT, GUIDES_SUBDIR, f"nbte-qa-{slug}-guide.docx")


def _size(path):
    size = os.path.getsize(path)
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def guides():
    out = []
    for slug, title, summary in GUIDES:
        path = guide_path(slug)
        available = os.path.exists(path)
        out.append({"slug": slug, "title": title, "summary": summary, "available": available,
                    "size": _size(path) if available else "",
                    "url": reverse("qa_portal:guide", args=[slug]) if available else ""})
    return out


def qa_types():
    from accreditation.models import QAType

    return [{"name": t.name, "code": t.code, "explainer": QA_TYPE_EXPLAINERS.get(t.code) or t.description}
            for t in QAType.objects.filter(is_active=True).order_by("sort_order", "name")]


def professional_bodies():
    """Active professional bodies, for the joint QA section (public names only)."""
    from institutions.models import ProfessionalBody

    return list(ProfessionalBody.objects.filter(is_active=True).order_by("acronym", "name")
                .values("name", "acronym")[:12])


@require_GET
def home(request):
    if request.user.is_authenticated:
        from .portal_views import dashboard

        return dashboard(request)
    ctx = public_context(request, "home")
    ctx.update({"qa_types": qa_types(), "guides": guides(), "bodies": professional_bodies()})
    return render(request, "qa_portal/public/landing.html", ctx)


@require_GET
def how_to(request):
    ctx = public_context(request, "how_to")
    ctx.update({"qa_types": qa_types(), "guides": guides()})
    return render(request, "qa_portal/public/how_to.html", ctx)


@require_GET
def guide(request, slug):
    if slug not in {g[0] for g in GUIDES}:
        raise Http404
    path = guide_path(slug)
    if not os.path.exists(path):
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=os.path.basename(path),
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
