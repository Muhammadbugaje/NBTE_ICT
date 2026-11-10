"""Professional bodies and joint QA (Phase 6).

* :func:`bodies_for_programme` — which active bodies regulate a programme
  (matched by programme name and level against ``ProgrammeBodyMapping``).
* :func:`refresh_joint_qa` — keeps ``QARequest.is_joint_qa`` and
  ``joint_bodies`` in step with the request's active programmes. Called when
  a request is created, when a programme is added, dropped or replaced, and
  at submission.
* :func:`notify_bodies_for_case` — when the SOP-03 case opens, e-mails each
  mapped body and notifies its portal accounts once, recording a
  ``ProfessionalBodyNotice``. Never raises into the caller: a failed notice
  must not stop the case from opening.
"""
# scoped-by-caller
from __future__ import annotations

import logging

from django.utils import timezone

from qa_core import timeline

from .models import ProfessionalBody, ProgrammeBodyMapping, programme_key

logger = logging.getLogger(__name__)


def bodies_for_programme(programme):
    """v2.1 R.4: the programme's own body (master list / chosen) first, plus
    any legacy name mappings (kept read-only for one release)."""
    found = set()
    if getattr(programme, "professional_body_id", None) and programme.professional_body.is_active:
        found.add(programme.professional_body)
    master = getattr(programme, "master", None) if getattr(programme, "master_id", None) else None
    if master is not None:
        found.update(master.other_bodies.filter(is_active=True))
    key = programme_key(programme.name)
    if key:
        rows = (ProgrammeBodyMapping.objects.filter(programme_key=key, is_active=True, body__is_active=True)
                .select_related("body"))
        found.update(m.body for m in rows if m.level_id is None or m.level_id == programme.level_id)
    return sorted(found, key=lambda b: b.name)


def bodies_by_programme(programmes):
    """{programme: [bodies]} for the programmes that have at least one body."""
    out = {}
    for programme in programmes:
        bodies = bodies_for_programme(programme)
        if bodies:
            out[programme] = bodies
    return out


def refresh_joint_qa(req):
    """Recompute the joint-QA flag from the active programmes. Returns the
    bodies now attached."""
    from accreditation.models import QARequestProgramme

    rows = (QARequestProgramme.unscoped.filter(qa_request=req, status=QARequestProgramme.ACTIVE)
            .select_related("programme"))
    bodies = set()
    for row in rows:
        bodies.update(bodies_for_programme(row.programme))
    was_joint = req.is_joint_qa
    req.joint_bodies.set(bodies)
    if req.is_joint_qa != bool(bodies):
        req.is_joint_qa = bool(bodies)
        type(req).unscoped.filter(pk=req.pk).update(is_joint_qa=req.is_joint_qa, updated_at=timezone.now())
    if bodies and not was_joint:
        timeline.record(req.institution, "qa_request.joint_qa", subject=req, visibility="public",
                        public_text=f"{req.reference} is a joint QA exercise with "
                                    f"{', '.join(b.name for b in sorted(bodies, key=lambda b: b.name))}.")
    return sorted(bodies, key=lambda b: b.name)


def notify_bodies_for_case(case, *, actor=None):
    """E-mail every body mapped to the case's programmes (once per body)."""
    from accreditation.models import ProfessionalBodyNotice, QARequestProgramme

    from qa_core import mail

    sent = []
    try:
        req = case.qa_request
        refresh_joint_qa(req)
        if not req.is_joint_qa:
            return sent
        rows = (QARequestProgramme.unscoped.filter(qa_request=req, status=QARequestProgramme.ACTIVE)
                .select_related("programme", "programme__level"))
        per_body = {}
        for row in rows:
            for body in bodies_for_programme(row.programme):
                per_body.setdefault(body, []).append(str(row.programme))
        for body, programmes in sorted(per_body.items(), key=lambda kv: kv[0].name):
            if ProfessionalBodyNotice.unscoped.filter(case=case, body=body).exists():
                continue
            portal_notified_at = None
            portal_recipient_count = 0
            from config import registry as config

            if body.portal_enabled and config.get("qa.pb.portal_enabled", True):
                from notifications.services import notify_many

                from institutions.pb_portal import body_users

                recipients = body_users(body)
                notifications = notify_many(
                    recipients,
                    title=f"Joint QA exercise opened: {case.institution.name}",
                    body=f"NBTE opened {case.reference} for {', '.join(programmes)}. "
                         "View the programme status on your NBTE professional-body portal.",
                    url="/pb/",
                )
                if notifications:
                    portal_notified_at = timezone.now()
                    portal_recipient_count = len(notifications)
            status = ProfessionalBodyNotice.NO_EMAIL
            if body.email:
                try:
                    mail.send(f"NBTE joint QA exercise: {case.institution.name}", [body.email],
                              "emails/professional_body_notice.html",
                              {"body": body, "case": case, "institution": case.institution,
                               "programmes": programmes, "qa_type": req.qa_type.name},
                              category="qa")
                    status = ProfessionalBodyNotice.SENT
                except Exception:
                    logger.exception("Joint QA notice to %s failed", body.pk)
                    status = ProfessionalBodyNotice.FAILED
            notice = ProfessionalBodyNotice.unscoped.create(
                case=case, body=body, email=body.email, programmes=programmes, status=status,
                sent_at=timezone.now() if status == ProfessionalBodyNotice.SENT else None,
                portal_notified_at=portal_notified_at, portal_recipient_count=portal_recipient_count)
            sent.append(notice)
            if status == ProfessionalBodyNotice.SENT:
                what = "notified by e-mail"
                if portal_notified_at:
                    what += " and in the professional-body portal"
            elif portal_notified_at:
                what = "notified in the professional-body portal; no e-mail is on file"
            else:
                what = "has no e-mail or portal contact on file: contact them directly"
            if status == ProfessionalBodyNotice.FAILED:
                what = ("e-mail delivery failed; notified in the professional-body portal"
                        if portal_notified_at else "could not be e-mailed: contact them directly")
            timeline.record(case.institution, "qa_case.body_notified", subject=case, actor=actor,
                            visibility="internal",
                            internal_text=f"Joint QA: {body.name} {what} ({', '.join(programmes)}).")
    except Exception:
        logger.exception("Joint QA notices failed for case %s", getattr(case, "pk", None))
    return sent


def active_bodies():
    return ProfessionalBody.objects.filter(is_active=True).order_by("name")
