"""SOP-01 programme self-studies and the institution-approval hand-off."""
from __future__ import annotations

import hashlib
import json
import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from institutions import services as inst_services
from institutions.models import InstitutionDocument
from qa_core import flow, timeline

from . import ssq as engine
from . import services as accreditation_services
from .models import Application, OnboardingProgrammeReview, OnboardingSSQ, QAType, SSQTemplate


def _template():
    qa_type = QAType.objects.filter(code="onboarding_ssq").select_related(
        "ssq_template").first()
    template = qa_type.ssq_template if qa_type is not None else None
    if template is None or template.status != SSQTemplate.PUBLISHED:
        raise ValidationError(
            "NBTE has not published the programme self-study template yet. Contact the QA administrator."
        )
    return template


def _accepted_documents(institution, field):
    qs = InstitutionDocument.unscoped.filter(institution=institution, status=InstitutionDocument.ACCEPTED)
    if field.get("document_kind"):
        qs = qs.filter(kind__code=field["document_kind"])
    return list(qs.select_related("kind").order_by("kind__name", "-version"))


def _evidence_by_field(ssq):
    mapping = (ssq.meta or {}).get("documents", {})
    out = {}
    for field in ssq.template.field_map().values():
        if field["type"] != "file":
            continue
        ids = mapping.get(field["key"], [])
        docs = InstitutionDocument.unscoped.filter(
            pk__in=ids, institution=ssq.application.institution, status=InstitutionDocument.ACCEPTED
        ).select_related("kind").order_by("kind__name", "-version")
        out[field["key"]] = list(docs)
    return out


def evidence_counts(ssq):
    return {key: len(rows) for key, rows in _evidence_by_field(ssq).items()}


def submission_problems(ssq):
    return engine.required_problems(ssq.template, ssq.data or {}, evidence_counts(ssq))


def cleared_reviews(app):
    """The newest review of every programme on the application's list.

    A programme cleared in an earlier round stays cleared (and is not sent
    again) when NBTE returns another one, so the latest round alone does not
    hold every programme: look at each programme's newest review instead.
    Raises ValidationError unless every programme is cleared."""
    from .onboarding_programmes import latest_reviews, programmes_of

    latest = latest_reviews(app)
    programmes = programmes_of(app)
    reviews = [latest.get(p.pk) for p in programmes]
    if not programmes or any(row is None or row.status != OnboardingProgrammeReview.CLEARED
                             or row.programme.institution_id != app.institution_id for row in reviews):
        raise ValidationError("Every programme must be cleared by NBTE before self-studies can open.")
    return reviews


@transaction.atomic
def open_for_application(app):
    """Create missing per-programme SSQs after the programme fee is confirmed."""
    app = Application.unscoped.select_for_update().select_related("institution").get(pk=app.pk)
    if app.status != "ssq_in_progress":
        raise ValidationError("The application is not at the self-study stage.")
    reviews = cleared_reviews(app)

    programme_ids = {row.programme_id for row in reviews}
    existing = set(OnboardingSSQ.unscoped.filter(application=app).values_list("programme_id", flat=True))
    missing = programme_ids - existing
    if not missing:
        return list(OnboardingSSQ.unscoped.filter(application=app).select_related(
            "programme", "template").order_by("programme__name"))

    template = _template()
    for review in reviews:
        if review.programme_id not in missing:
            continue
        data, prefilled_docs, prefilled = engine.compute_prefill(template, app.institution, review.programme)
        document_map = {}
        for field in template.field_map().values():
            if field["type"] != "file":
                continue
            doc = prefilled_docs.get(field["key"])
            docs = [doc] if doc is not None else _accepted_documents(app.institution, field)
            document_map[field["key"]] = [item.pk for item in docs]
        OnboardingSSQ.unscoped.get_or_create(
            application=app, programme=review.programme,
            defaults={
                "template": template, "data": data,
                "meta": {"prefilled": prefilled, "edited": [], "sections": {}, "documents": document_map},
            },
        )
    timeline.record(
        app.institution, "onboarding.ssq_opened", subject=app, visibility="public",
        public_text=f"Complete the programme self-study questionnaires for application {app.reference}.",
    )
    transaction.on_commit(lambda: inst_services.notify_institution(
        app.institution, "onboarding.ssq_opened", {"reference": app.reference}, url="/application/"
    ))
    return list(OnboardingSSQ.unscoped.filter(application=app).select_related(
        "programme", "template").order_by("programme__name"))


def _returned_for_correction(ssq):
    """NBTE returned this one self-study for correction (``onboarding_linear``)."""
    if ssq.status != OnboardingSSQ.DRAFT or ssq.application.status != "ssq_submitted":
        return False
    from .onboarding_linear import is_returned
    return is_returned(ssq)


def _open_for_editing(ssq):
    return ssq.status == OnboardingSSQ.DRAFT and (
        ssq.application.status == "ssq_in_progress" or _returned_for_correction(ssq))


def _assert_editable(ssq, user):
    if getattr(user, "user_type", "") != "institution" or user.institution_id != ssq.application.institution_id:
        raise PermissionDenied("Only users of this institution may edit its self-study.")
    if not _open_for_editing(ssq):
        raise ValidationError("This self-study is read-only.")


def _posted_value(post, field, programme):
    key = f"f__{field['key']}"
    if field["type"] == "file":
        return None
    if field["type"] == "catalogue":
        return {
            str(item.pk): post.get(f"{key}__{item.pk}", "")
            for item in engine.catalogue_items(field, programme)
        }
    if field["type"] == "table":
        columns = field.get("columns", [])
        indices = set()
        prefix = f"{key}__"
        for name in post.keys():
            match = re.fullmatch(re.escape(prefix) + r"(\d+)__.+", name)
            if match:
                indices.add(int(match.group(1)))
        return [
            {column["key"]: post.get(f"{key}__{index}__{column['key']}", "") for column in columns}
            for index in sorted(indices)
        ]
    if field["type"] == "multi_choice":
        return post.getlist(key)
    return post.get(key, "")


@transaction.atomic
def save_fields(ssq, post, *, user):
    ssq = OnboardingSSQ.unscoped.select_for_update().select_related(
        "application", "application__institution", "programme", "template"
    ).get(pk=ssq.pk)
    _assert_editable(ssq, user)
    data = dict(ssq.data or {})
    errors = {}
    edited = set((ssq.meta or {}).get("edited", []))
    prefilled = set((ssq.meta or {}).get("prefilled", []))
    for field in ssq.template.field_map().values():
        if field["type"] == "file":
            continue
        raw = _posted_value(post, field, ssq.programme)
        if raw is None:
            continue
        try:
            value = engine.clean_value(field, raw, programme=ssq.programme)
        except ValidationError as exc:
            errors[field["key"]] = " ".join(exc.messages)
            continue
        if data.get(field["key"]) != value:
            data[field["key"]] = value
            if field["key"] in prefilled:
                edited.add(field["key"])

    now = timezone.now()
    meta = dict(ssq.meta or {})
    meta["edited"] = sorted(edited)
    meta["sections"] = {
        section["key"]: {"saved_at": now.isoformat(), "by": user.pk}
        for section in ssq.template.sections()
    }
    ssq.data, ssq.meta, ssq.last_saved_at, ssq.last_saved_by = data, meta, now, user
    ssq.save(update_fields=["data", "meta", "last_saved_at", "last_saved_by", "updated_at"])
    return errors


def workspace(ssq, user):
    """Build the dynamic, institution-safe questionnaire workspace."""
    sections = ssq.template.sections()
    progress = engine.section_progress(ssq.template, ssq.data or {}, evidence_counts(ssq))
    documents = _evidence_by_field(ssq)
    prefilled = set((ssq.meta or {}).get("prefilled", [])) - set((ssq.meta or {}).get("edited", []))
    fields_by_section = {}
    for section in sections:
        fields = []
        for field in section.get("fields", []):
            item = dict(field)
            item["value"] = (ssq.data or {}).get(field["key"])
            item["choices"] = engine.option_pairs(field)
            item["prefilled"] = field["key"] in prefilled
            if field["type"] == "catalogue":
                qty = item["value"] or {}
                item["items"] = [{"item": catalogue, "quantity": qty.get(str(catalogue.pk), "")}
                                 for catalogue in engine.catalogue_items(field, ssq.programme)]
            elif field["type"] == "table":
                item["rows"] = list(item["value"] or [])
            elif field["type"] == "file":
                item["documents"] = documents.get(field["key"], [])
            fields.append(item)
        fields_by_section[section["key"]] = fields
    editable = (getattr(user, "user_type", "") == "institution"
                and user.institution_id == ssq.application.institution_id
                and _open_for_editing(ssq))
    returned = _returned_for_correction(ssq)
    return {
        "ssq": ssq, "app": ssq.application, "programme": ssq.programme, "template": ssq.template,
        "sections": sections, "progress": progress, "fields_by_section": fields_by_section, "editable": editable,
        "problems": submission_problems(ssq), "submitted": ssq.status == OnboardingSSQ.SUBMITTED,
        "can_submit": inst_services.is_chief_executive(user),
        # Linear onboarding: NBTE returned this one self-study; the CE resubmits it
        # (POST /application/ssq/<pk>/resubmit/, onboarding_linear.resubmit).
        "returned_for_correction": returned, "review_stage": review_stage(ssq),
    }


def snapshot(study):
    """The immutable submission fields of one self-study (data, rendering,
    evidence and their checksum)."""
    rendered = engine.render_snapshot(study.template, study.data or {}, programme=study.programme)
    evidence = [
        {"field": key, "document_id": doc.pk, "name": doc.display_name, "sha256": doc.sha256}
        for key, docs in _evidence_by_field(study).items() for doc in docs
    ]
    snapshot_data = dict(study.data or {})
    raw = json.dumps(
        {"data": snapshot_data, "rendered": rendered, "evidence": evidence},
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return {"submitted_data": snapshot_data, "rendered_snapshot": rendered, "evidence_snapshot": evidence,
            "checksum": hashlib.sha256(raw).hexdigest()}


def review_stage(ssq):
    """Where one submitted programme self-study is on the linear review path.

    ``{"code", "label", "public", "holder", "assigned_officer", "director",
    "days_at_stage"}`` — see ``accreditation.onboarding_linear``."""
    from .onboarding_linear import review_stage as _review_stage
    return _review_stage(ssq)


@transaction.atomic
def submit_application(app, *, user, declaration=False):
    inst_services.require_chief_executive(user, "submitting the onboarding self-study")
    app = Application.unscoped.select_for_update().select_related("institution").get(pk=app.pk)
    if app.status != "ssq_in_progress":
        raise ValidationError("The application is not waiting for self-study submission.")
    if not declaration:
        raise ValidationError("Confirm the self-study declaration before submitting.")
    studies = list(OnboardingSSQ.unscoped.select_for_update().filter(application=app).select_related(
        "programme", "template"))
    if not studies:
        raise ValidationError("The self-study questionnaires have not been opened yet.")
    try:
        reviews = cleared_reviews(app)
    except ValidationError:
        raise ValidationError("Every programme must be cleared by NBTE before self-studies can be submitted.")
    expected = {review.programme_id for review in reviews}
    if {study.programme_id for study in studies} != expected:
        raise ValidationError("Every NBTE-cleared programme must have a self-study questionnaire.")
    if any(study.status != OnboardingSSQ.DRAFT for study in studies):
        raise ValidationError("A self-study has already been submitted; its record cannot be changed.")
    issues = []
    for study in studies:
        problems = submission_problems(study)
        if problems:
            issues.append(f"{study.programme}: complete {len(problems)} required item(s).")
    if issues:
        raise ValidationError(issues)

    now = timezone.now()
    for study in studies:
        snap = snapshot(study)
        study.status = OnboardingSSQ.SUBMITTED
        study.submitted_by = user
        study.submitted_at = now
        study.submitted_data = snap["submitted_data"]
        study.rendered_snapshot = snap["rendered_snapshot"]
        study.evidence_snapshot = snap["evidence_snapshot"]
        study.checksum = snap["checksum"]
        study.save(update_fields=[
            "status", "submitted_by", "submitted_at", "submitted_data", "rendered_snapshot",
            "evidence_snapshot", "checksum", "updated_at",
        ])

    instance = accreditation_services.instance_of(app)
    if instance is None or not flow.can(instance, "submit_ssq"):
        raise ValidationError("The application workflow cannot accept the self-study submission.")
    flow.transition(instance, "submit_ssq", user)
    timeline.record(
        app.institution, "onboarding.ssq_submitted", subject=app, actor=user, visibility="public",
        public_text=f"The Chief Executive submitted the programme self-studies for {app.reference}. "
                    "NBTE institution approval is now required before formal QA requests can begin.",
    )
    # Linear onboarding: each self-study goes to the handling department's
    # director to assign an officer (the ES hears only when asked to confirm).
    from . import onboarding_linear

    onboarding_linear.route_on_submit(app, user)
    transaction.on_commit(lambda: inst_services.notify_institution(
        app.institution, "onboarding.ssq_submitted", {"reference": app.reference}, url="/application/"
    ))
    return Application.unscoped.get(pk=app.pk)


def _notify_approval_reviewers(application_id):
    import org
    from accounts.models import User
    from notifications.models import Notification
    from notifications.services import notify
    from qa_core.departments import qa_department

    app = Application.unscoped.select_related("institution", "institution__handling_department").filter(
        pk=application_id, status="ssq_submitted"
    ).first()
    if app is None:
        return
    recipients = {}
    staff = User.objects.filter(is_active=True, user_type=User.STAFF).select_related("role", "department")
    for user in staff:
        if org.is_es(user) or org.is_super_admin(user):
            recipients[user.pk] = user
    ict = qa_department("ict")
    for department in (ict, app.institution.handling_department):
        if department and department.director_id:
            user = User.objects.filter(pk=department.director_id, is_active=True, user_type=User.STAFF).first()
            if user:
                recipients[user.pk] = user
    for user in recipients.values():
        notify(
            user, title="Institution approval required",
            body=f"{app.institution.name} submitted its onboarding self-study and is awaiting NBTE approval.",
            url=f"/qa/applications/{app.pk}/", category=Notification.QA,  # staff host: the portal URLconf has no qa_app
        )


# ---------------------------------------------------------------------------
# Where the submitted self-studies stand (the application stays at its
# terminal "ssq_submitted" stage; the institution approval moves on).
# ---------------------------------------------------------------------------
def approval_stage(app):
    """The real stage of submitted onboarding self-studies.

    ``Application.status`` stays ``ssq_submitted`` (an end stage) while NBTE
    approves the institution through the two-officer action
    ``institutions.approve_institution`` (initiated by e.g. the Director of ICT
    or the handling director, confirmed by the ES). Its label alone ("submitted
    to NBTE") therefore never moved. Returns ``{"code", "label", "public",
    "holder", "initiated_by", "decided_by", "at"}``:

        under_review       submitted; nobody has started the approval yet
        approval_pending   approval initiated; waiting for the confirming officer (ES)
        approved           the institution is approved
        not_approved       the last approval request was rejected (back under review)
    """
    import org
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import PendingAction

    inst = app.institution
    out = {"code": "under_review", "holder": "", "initiated_by": None, "decided_by": None, "at": None}
    if getattr(app, "status", "") != "ssq_submitted":
        out.update(code="", label="", public="")
        return out
    ct = ContentType.objects.get_for_model(type(inst))
    actions = PendingAction.objects.filter(policy_id="institutions.approve_institution", content_type=ct,
                                           object_id=str(inst.pk)).select_related(
        "initiated_by", "decided_by", "confirmed_by").order_by("-initiated_at", "-id")
    last = actions.first()
    dept = inst.handling_department
    if inst.status == "approved":
        done = actions.filter(status__in=(PendingAction.APPLIED, PendingAction.CONFIRMED)).first()
        who = (done.decided_by or done.confirmed_by) if done is not None else None
        out.update(code="approved", label="Approved — institution approved by NBTE", public="Approved by NBTE",
                   decided_by=who, initiated_by=getattr(done, "initiated_by", None),
                   at=inst.approved_at or getattr(done, "decided_at", None))
    elif last is not None and last.status == PendingAction.PENDING:
        es = org.users_with_role("executive-secretary", staff_only=True).first()
        name = (last.initiated_by.get_full_name() or last.initiated_by.email) if last.initiated_by_id else "NBTE"
        out.update(code="approval_pending",
                   label=f"Approval recommended by {name} — awaiting confirmation"
                         + (f" by {es.get_full_name() or es.email}" if es is not None else " (ES)"),
                   public="Approval in progress at NBTE", initiated_by=last.initiated_by,
                   holder=(es.get_full_name() or es.email) if es is not None else "Executive Secretary",
                   at=last.initiated_at)
    elif last is not None and last.status == PendingAction.REJECTED:
        out.update(code="not_approved", label="Approval request rejected — back under review",
                   public="Under review by NBTE", decided_by=last.decided_by, at=last.decided_at,
                   holder=getattr(dept, "name", ""))
    else:
        ict = org.director_of("ict")
        holders = [n for n in (getattr(dept, "name", ""),
                               ("Director of ICT" + (f" ({ict.get_full_name()})" if ict else ""))) if n]
        out.update(code="under_review", label="Self-studies under review by NBTE — " + " / ".join(holders),
                   public="Self-studies under review by NBTE", holder=" / ".join(holders))
    from qa_core import portal_journey

    if out["code"] in ("under_review", "not_approved") and dept is not None and portal_journey.show_department_names():
        out["public"] = f"{out['public']} — {dept.name}"
    return out
