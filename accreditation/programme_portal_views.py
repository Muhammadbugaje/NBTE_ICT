"""Portal: Programmes (status board with expiry countdown and the
"Request re-accreditation" call to action) and Outcome letters — Phase 8
(Master Plan Phase 6 "Portal: Outcome letters, programme status board with
expiry countdown").

Everything is read through ``Model.objects.for_user(user)`` (Master Plan §4);
another institution's rows answer 404.
"""
from __future__ import annotations

from django.db.models import Prefetch
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from institutions.models import Institution, Programme
from qa_core.portal_views import _shell_context, institution_required

from . import qa_services as svc
from .models import OutcomeLetter, OutcomeLetterCopy, QAType

RENEWAL_TYPE = "reaccreditation"


def _copies(user):
    return Prefetch("copies", queryset=OutcomeLetterCopy.objects.for_user(user).order_by("kind"))


def _inst(request):
    return Institution.objects.for_user(request.user).first()


def programme_cards(user, inst, *, today=None):
    """Each programme with its countdown and what the institution can do."""
    today = today or timezone.localdate()
    renewal = QAType.objects.filter(code=RENEWAL_TYPE, is_active=True).first()
    can_request = (inst is not None and inst.status == Institution.APPROVED and svc.can_manage_requests(user, inst)
                   and renewal is not None and svc.type_availability(inst, renewal)[0])
    letters = {}
    for letter in (OutcomeLetter.objects.for_user(user).filter(is_current=True)
                   .prefetch_related(_copies(user)).order_by("-generated_at")):
        letters.setdefault(letter.programme_id, letter)
    cards = []
    for p in (Programme.objects.for_user(user).filter(institution=inst).exclude(status="dropped")
              .select_related("level", "category", "approval_type").order_by("name")):
        days = (p.expiry_date - today).days if p.expiry_date else None
        opens = svc.window_opens(p) if p.expiry_date else None
        window_open = bool(p.status == "expired" or (opens is not None and today >= opens))
        eligible = False
        if renewal is not None and p.status in (renewal.eligible_programme_statuses or []):
            eligible = svc.programme_eligibility(p, renewal, today=today)[0]
        cards.append({
            "programme": p, "days_left": days, "opens": opens, "window_open": window_open,
            "can_renew": bool(can_request and eligible and window_open), "letter": letters.get(p.pk),
            "urgency": ("expired" if p.status == "expired" or (days is not None and days < 0) else
                        "soon" if days is not None and days <= 90 else "ok" if days is not None else ""),
        })
    return cards


@institution_required
@require_GET
def programmes(request):
    inst = _inst(request)
    cards = programme_cards(request.user, inst)
    renewal = QAType.objects.filter(code=RENEWAL_TYPE).first()
    ctx = _shell_context(request, "programmes", inst=inst, cards=cards, renewal=renewal,
                         window=svc.window_months(), today=timezone.localdate())
    return render(request, "qa_portal/programmes/page.html", ctx)


@institution_required
@require_GET
def letters(request):
    inst = _inst(request)
    rows = list(OutcomeLetter.objects.for_user(request.user)
                .select_related("programme__level", "decision__approval_type", "case")
                .prefetch_related(_copies(request.user)).order_by("-generated_at"))
    ctx = _shell_context(request, "letters", inst=inst, letters=rows,
                         kinds=dict(OutcomeLetterCopy.KIND_CHOICES))
    return render(request, "qa_portal/letters/page.html", ctx)
