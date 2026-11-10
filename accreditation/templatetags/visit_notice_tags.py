"""``{% visit_notices case as vn %}`` — the data of the "Invitations and
notices" panel on the case Visit tab (templates/accreditation/case/
_external_notices.html), so the case views need no change.

``vn`` = {invitations, expected_invitees, summary, can_manage, joint_notices, visit_approved,
channels, outcomes, vias}. Never raises: a problem shows an empty panel.
"""
import logging

from django import template

register = template.Library()
logger = logging.getLogger(__name__)


@register.simple_tag(takes_context=True)
def visit_notices(context, case):
    from accreditation import visit_invitations as vi
    from accreditation.models import ProfessionalBodyNotice, VisitDetail
    from accreditation.visit_invitation_models import VisitInvitation, VisitInvitationContact

    request = context.get("request")
    user = getattr(request, "user", None)
    try:
        invitations = vi.for_case(case)
        for inv in invitations:
            inv.log = list(VisitInvitationContact.unscoped.filter(invitation=inv).select_related("by")[:8])
        visit = VisitDetail.unscoped.filter(case=case).first()
        notices = list(ProfessionalBodyNotice.unscoped.filter(case=case).select_related("body").order_by("body__name"))
        manual_outcomes = [(v, label) for v, label in VisitInvitationContact.OUTCOME_CHOICES
                           if v in VisitInvitationContact.MANUAL_OUTCOMES]
        return {
            "invitations": invitations,
            "expected_invitees": vi.invitees(case),
            "summary": vi.summary(invitations),
            "can_manage": vi.can_manage(user, case),
            "can_word": vi.can_download_word(user, case),
            "joint_notices": notices,
            "visit_approved": bool(visit and visit.status == "es_approved"),
            "enabled": vi.enabled(),
            "channels": [(v, label) for v, label in VisitInvitationContact.CHANNEL_CHOICES if v != "system"],
            "outcomes": manual_outcomes,
            "vias": [(v, label) for v, label in VisitInvitation.VIA_CHOICES if v not in ("portal", "link")],
            "reminder_days": vi.reminder_days(),
        }
    except Exception:
        logger.exception("Visit notices panel failed for case %s", getattr(case, "pk", None))
        return {"invitations": [], "expected_invitees": [], "summary": {"total": 0},
                "can_manage": False, "joint_notices": [],
                "visit_approved": False, "enabled": False, "channels": [], "outcomes": [], "vias": [],
                "reminder_days": [], "error": True}


_TONES = {"queued": "pending", "sent": "pending", "failed": "danger", "no_contact": "warn",
          "acknowledged": "success", "declined": "danger", "withdrawn": "neutral"}


@register.filter
def invite_tone(status):
    return "qa-tone-" + _TONES.get(str(status or ""), "neutral")


@register.simple_tag
def institution_visit_invites(inst):
    """Upcoming / current visits of the institution with their invitation counts
    (institution page, Overview). [{case, visit_start, visit_end, summary}]."""
    from django.utils import timezone

    from accreditation import visit_invitations as vi
    from accreditation.visit_invitation_models import VisitInvitation

    try:
        today = timezone.localdate()
        rows = (VisitInvitation.unscoped.filter(case__institution=inst, visit_end__gte=today)
                .select_related("case").order_by("visit_start", "case_id"))
        by_case = {}
        for inv in rows:
            by_case.setdefault(inv.case_id, []).append(inv)
        out = []
        for invs in by_case.values():
            first = invs[0]
            out.append({"case": first.case, "visit_start": first.visit_start, "visit_end": first.visit_end,
                        "summary": vi.summary(invs)})
        return out
    except Exception:
        logger.exception("Institution visit invitations failed for %s", getattr(inst, "pk", None))
        return []
