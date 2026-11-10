"""Public pages of the QA portal (Phase 6): landing, how-to, guide downloads.

* ``/`` — the public landing page; institutions stay signed in there.
* ``/how-to/`` — how every kind of request works, with diagrams.
* ``/guides/<slug>/`` — each guide as a PDF (qa_core.guide_pdf, built from the
  same content as the Word version of ``manage.py generate_institution_guides``;
  ``?format=docx`` still serves that Word file when it exists).
* ``/guides/<slug>/read/`` — the same guide to read online.
  The how-to page is "How it works & guides": the process step by step and
  every guide (its old ``#downloads`` anchor still lands on the guides).

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
    ("onboarding", "Onboarding guide", "Registering a new institution and getting it approved"),
    ("qa-requests", "QA requests guide", "Requesting accreditation and completing the SSQ"),
    ("payments", "Payments guide", "Bills, paying online or by bank, proof and receipts"),
]
# Who each guide is for, and its card icon on the how-to page.
GUIDE_FOR = {
    "starter": ("Everyone at your institution", "start"),
    "onboarding": ("New institutions getting registered", "onboard"),
    "qa-requests": ("Approved institutions asking for a visit", "qa"),
    "payments": ("Registrars and bursars", "pay"),
}

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

    try:
        info = site_info(request).get("site_info", {})
    except Exception:
        info = {}
    email = (config.get("qa.portal.registration_contact_email", "") or "").strip() or info.get("email", "")
    phone = (config.get("qa.portal.registration_contact_phone", "") or "").strip() or info.get("phone", "")
    return email, phone


def closed_message(email, phone):
    how = " or ".join(x for x in (email, phone) if x)
    if how:
        return (f"Institution registration is currently managed by NBTE. Please contact {how} to begin your "
                "registration.")
    return "Institution registration is currently managed by NBTE. Please contact NBTE to begin your registration."


def info_name(request=None):
    try:
        return str(config.get("site.portal_name", "") or "").strip()
    except Exception:
        return ""


def public_context(request, page):
    email, phone = contact_details(request)
    return {"page": page, "registration_open": registration_open(), "contact_email": email,
            "contact_phone": phone, "registration_closed_message": closed_message(email, phone),
            "portal_name": (info_name(request) or "NBTE Quality Assurance Portal"),
            "maintenance_message": (config.get("qa.portal.maintenance_message", "") or "").strip()}


def guide_path(slug):
    return os.path.join(settings.MEDIA_ROOT, GUIDES_SUBDIR, f"nbte-qa-{slug}-guide.docx")


def _size(path):
    size = os.path.getsize(path)
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def guides():
    """The guides, each readable online and downloadable as PDF (built on demand)."""
    from .guide_pdf import guide_pdf_path
    from .guides_content import GUIDES as CONTENT

    out = []
    for n, (slug, title, summary) in enumerate(GUIDES, 1):
        audience, icon = GUIDE_FOR.get(slug, ("", "start"))
        pdf = guide_pdf_path(slug)
        content = CONTENT.get(slug, {})
        parts = sum(1 for b in content.get("blocks", []) if b[0] == "part")
        out.append({"n": n, "slug": slug, "title": title, "summary": summary, "audience": audience, "icon": icon,
                    "accent": "#" + (content.get("accent") or "1F9D5C"), "parts": parts, "available": True,
                    "size": _size(pdf) if os.path.exists(pdf) else "",
                    "url": reverse("qa_portal:guide", args=[slug]),
                    "read_url": reverse("qa_portal:guide_read", args=[slug])})
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
    if request.user.is_authenticated and getattr(request.user, "user_type", "") != "institution":
        from .portal_views import dashboard

        return dashboard(request)
    ctx = public_context(request, "home")
    ctx.update({"qa_types": qa_types(), "guides": guides(), "bodies": professional_bodies()})
    from config.context_processors import site_info as _site_info  # v2.1 A8
    from public.hero import hero_context

    ctx["hero"] = hero_context("portal.home", _site_info(request)["site_info"])
    return render(request, "qa_portal/public/landing.html", ctx)


@require_GET
def how_to(request):
    ctx = public_context(request, "how_to")
    ctx.update({"qa_types": qa_types(), "guides": guides()})
    return render(request, "qa_portal/public/how_to.html", ctx)


@require_GET
def guide_read(request, slug):
    """v2.2: read a guide online (the preview of the Word download)."""
    from .guide_render import render as render_guide
    from .guides_content import GUIDES as CONTENT

    if slug not in CONTENT:
        raise Http404
    g = CONTENT[slug]
    toc, html = render_guide(g)
    ctx = public_context(request, "how_to")
    all_guides = guides()
    this = next((x for x in all_guides if x["slug"] == slug), None)
    idx = all_guides.index(this) if this else 0
    ctx.update({"g": g, "slug": slug, "toc": toc, "body": html, "guides": all_guides, "this": this,
                "prev_guide": all_guides[idx - 1] if this and idx > 0 else None,
                "next_guide": all_guides[idx + 1] if this and idx + 1 < len(all_guides) else None,
                "download": reverse("qa_portal:guide", args=[slug])})
    return render(request, "qa_portal/public/guide_read.html", ctx)


@require_GET
def guide(request, slug):
    """Download a guide as PDF (``?format=docx`` for the legacy Word file;
    ``?inline=1`` opens the PDF in the browser instead of saving it)."""
    if slug not in {g[0] for g in GUIDES}:
        raise Http404
    if request.GET.get("format") == "docx":
        path = guide_path(slug)
        if not os.path.exists(path):
            raise Http404
        return FileResponse(open(path, "rb"), as_attachment=True, filename=os.path.basename(path),
                            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    from .guide_pdf import ensure_pdf

    try:
        path = ensure_pdf(slug)
    except Exception:  # never a 500 for a download: fall back to the Word file, else 404
        import logging

        logging.getLogger(__name__).exception("guide PDF build failed for %s", slug)
        path = guide_path(slug)
        if not os.path.exists(path):
            raise Http404
        return FileResponse(open(path, "rb"), as_attachment=True, filename=os.path.basename(path),
                            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    inline = request.GET.get("inline") == "1"
    return FileResponse(open(path, "rb"), as_attachment=not inline, filename=os.path.basename(path),
                        content_type="application/pdf")
