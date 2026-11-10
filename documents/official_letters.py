"""Official letters pipeline: provisional documents that still need their
signed official copy (approval letters, accreditation letters, ... - any
IssuedDocument issued ``provisional`` because its rule/template says an
official copy follows).

Who acts
* the handling department's Director (``Department.director``) - only the
  institutions their department handles;
* the Executive Secretary and super admins - everything.

Flow (``OfficialLetterJob``)::

    waiting --assign--> assigned --draft--> draft_ready --upload--> done
                ^                               |
                +------------ send back --------+ (returned)

The Director (or ES) may upload the official copy at any step; uploading it
from the register closes the job too (``services.upload_official`` calls
``on_official_uploaded``). Reminders: ``send_reminders`` (run by
``manage.py qa_stage_reminders`` and ``manage.py qa_official_letters``)
reminds the Director/assignee once the letter has waited
``qa.documents.official_copy_sla_days`` days and escalates it to the ES.
"""
from __future__ import annotations

import datetime
import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

import config
import org

logger = logging.getLogger(__name__)

SLA_KEY = "qa.documents.official_copy_sla_days"
DEFAULT_SLA_DAYS = 7
MAX_DRAFT_MB = 15
DRAFT_EXTENSIONS = (".pdf", ".doc", ".docx", ".odt", ".rtf")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def sla_days():
    try:
        return max(1, int(config.get(SLA_KEY, DEFAULT_SLA_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_SLA_DAYS


def show_department_names():
    raw = config.get("qa.portal.show_department_names", False)
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


# ---------------------------------------------------------------------------
# Who may act
# ---------------------------------------------------------------------------
def _staff(user):
    return bool(user is not None and getattr(user, "is_authenticated", False) and user.is_active
                and getattr(user, "user_type", "staff") == "staff")


def is_full(user):
    """ES and super admins see and act on every institution's letters."""
    return _staff(user) and (org.is_super_admin(user) or org.is_es(user))


def directed_department(user):
    return org.directed_department(user) if _staff(user) else None


def can_open_queue(user):
    return is_full(user) or directed_department(user) is not None


def _dept_id(doc):
    inst = doc.institution if doc.institution_id else None
    return inst.handling_department_id if inst is not None else None


def can_manage(user, doc):
    """Upload the official copy, assign, review drafts for ``doc``."""
    if not _staff(user):
        return False
    if is_full(user):
        return True
    dept_id = _dept_id(doc)
    return bool(dept_id and org.is_director(user, dept_id))


def can_open(user, doc, job=None):
    """See the letter's page (managers, and the staff member preparing it)."""
    if can_manage(user, doc):
        return True
    if job is None:
        job = job_for(doc, create=False)
    return bool(job is not None and _staff(user) and job.assignee_id == user.pk and job.is_open)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def needs_official_q(prefix=""):
    p = prefix
    return (Q(**{f"{p}provisional": True}) & Q(**{f"{p}status": "valid"}) & Q(**{f"{p}institution__isnull": False})
            & ~Q(**{f"{p}audience": "internal"}))


def pending_documents():
    """Every provisional document still waiting for its official copy."""
    from .models import IssuedDocument

    return (IssuedDocument.unscoped.filter(needs_official_q()).filter(official_copies__isnull=True)
            .select_related("institution", "institution__handling_department").order_by("issued_at", "id"))


def scoped(user, qs=None):
    qs = pending_documents() if qs is None else qs
    if is_full(user):
        return qs
    dept = directed_department(user)
    if dept is None:
        return qs.none()
    return qs.filter(institution__handling_department=dept)


def job_for(doc, *, create=True):
    from .models import OfficialLetterJob

    job = OfficialLetterJob.objects.filter(document=doc).first()
    if job is None and create:
        inst = doc.institution if doc.institution_id else None
        job, _ = OfficialLetterJob.objects.get_or_create(
            document=doc, defaults={"department_id": getattr(inst, "handling_department_id", None)})
    return job


def days_waiting(doc, now=None):
    now = now or timezone.now()
    return max(0, (timezone.localdate(now) - timezone.localdate(doc.issued_at)).days)


def letter_label(doc):
    """'Registration approval letter' - the template title when there is one."""
    if doc.template_id and doc.template:
        return doc.template.title
    return doc.title


STATE_TONES = {"waiting": "warn", "assigned": "info", "returned": "warn", "draft_ready": "ok", "done": "ok",
               "cancelled": "plain"}


def row_for(doc, job=None, now=None, sla=None):
    from .models import OfficialLetterJob

    sla = sla or sla_days()
    waited = days_waiting(doc, now)
    state = job.state if job is not None else OfficialLetterJob.WAITING
    label = dict(OfficialLetterJob.STATE_CHOICES).get(state, state)
    if state == OfficialLetterJob.ASSIGNED and job and job.assignee_id:
        label = f"With {job.assignee.get_full_name() or job.assignee.email}"
    return {"doc": doc, "job": job, "institution": doc.institution, "letter": letter_label(doc),
            "days": waited, "overdue": waited >= sla, "state": state, "state_label": label,
            "tone": STATE_TONES.get(state, "info"),
            "due_on": timezone.localdate(doc.issued_at) + datetime.timedelta(days=sla),
            "url": reverse("documents_register:official_letter", args=[doc.pk])}


def rows(user, *, q="", state="", department=None, overdue=False, now=None):
    """Queue rows for ``user`` (already scoped), oldest first."""
    from .models import OfficialLetterJob

    qs = scoped(user)
    if department:
        qs = qs.filter(institution__handling_department_id=department)
    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(title__icontains=q) | Q(institution__name__icontains=q)
                       | Q(institution__code__iexact=q))
    docs = list(qs.select_related("template")[:500])
    jobs = {j.document_id: j for j in OfficialLetterJob.objects.filter(document__in=docs)
            .select_related("assignee", "assigned_by")}
    sla = sla_days()
    out = [row_for(d, jobs.get(d.pk), now, sla) for d in docs]
    if state == "with_staff":
        out = [r for r in out if r["state"] in OfficialLetterJob.WITH_STAFF]
    elif state:
        out = [r for r in out if r["state"] == state]
    if overdue:
        out = [r for r in out if r["overdue"]]
    return out


def summary(all_rows):
    from .models import OfficialLetterJob

    return {"total": len(all_rows),
            "waiting": sum(1 for r in all_rows if r["state"] == OfficialLetterJob.WAITING),
            "with_staff": sum(1 for r in all_rows if r["state"] in OfficialLetterJob.WITH_STAFF),
            "draft_ready": sum(1 for r in all_rows if r["state"] == OfficialLetterJob.DRAFT_READY),
            "overdue": sum(1 for r in all_rows if r["overdue"])}


def my_letters_to_prepare(user):
    from .models import OfficialLetterJob

    if not _staff(user):
        return []
    return list(OfficialLetterJob.objects.filter(assignee=user, state__in=OfficialLetterJob.WITH_STAFF,
                                                 document__status="valid")
                .select_related("document", "document__institution", "document__template")
                .order_by("due_date", "assigned_at"))


def assignable_staff(user, doc):
    """Staff of the handling department (any active staff for the ES/admin
    when the institution has no handling department)."""
    from accounts.models import User

    from accounts.audience import nbte_staff_users_q

    qs = User.objects.filter(nbte_staff_users_q()).exclude(pk=user.pk)
    dept_id = _dept_id(doc)
    if dept_id:
        qs = qs.filter(Q(department_id=dept_id) | Q(staff_profile__department_id=dept_id)).distinct()
    elif not is_full(user):
        return qs.none()
    return qs.order_by("first_name", "last_name", "email")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def _notify(users, title, body, url, actor=None, level=None):
    try:
        from notifications.models import Notification
        from notifications.services import notify

        seen = set()
        for u in users:
            if u is None or not getattr(u, "pk", None) or u.pk in seen:
                continue
            if actor is not None and u.pk == getattr(actor, "pk", None):
                continue
            seen.add(u.pk)
            notify(u, title=title[:200], body=body[:2000], url=url, category=Notification.QA,
                   level=level or Notification.INFO, actor=actor)
    except Exception:  # notifications never break the action
        logger.exception("official letters: notification failed")


def _job_url(doc):
    return reverse("documents_register:official_letter", args=[doc.pk])


def _directors_for(job_or_doc):
    doc = getattr(job_or_doc, "document", job_or_doc)
    dept_id = _dept_id(doc)
    director = org.director_of(dept_id) if dept_id else None
    return [director] if director is not None else []


def _es_users():
    return list(org.users_with_role(org.ES_ROLE, staff_only=True))


# ---------------------------------------------------------------------------
# Tasks (assignee's to-do in the Tasks app)
# ---------------------------------------------------------------------------
def is_linked_task(task):
    from .models import OfficialLetterJob

    return OfficialLetterJob.objects.filter(task_id=task.pk).exists()


def _open_task(job, actor):
    """A normal Tasks-app task for the assignee (bell + 'Assigned to me').
    Completed from the letter page, never from the Tasks app."""
    try:
        from tasks import services as task_services
        from tasks.models import Task, TaskAssignee

        doc = job.document
        task = task_services.create_task(
            created_by=actor, title=f"Prepare the official {letter_label(doc).lower()} — {doc.institution.name}"[:200],
            description=(f"Prepare the official signed copy of {doc.reference} ({doc.title}) for "
                         f"{doc.institution.name}, then send the draft back on the letter page: "
                         f"{_job_url(doc)}\n\n{job.assign_note}").strip(),
            owner_department=job.department, due_date=job.due_date, requires_review=False)
        TaskAssignee.objects.create(task=task, user=job.assignee, state=TaskAssignee.ASSIGNED, assigned_by=actor)
        task_services.log_activity(task, "assignee_added", actor=actor,
                                   message=f"Assigned to {job.assignee.get_full_name() or job.assignee.email}",
                                   metadata={"assignee_id": job.assignee_id})
        task.state = Task.OPEN
        task.save(update_fields=["state", "updated_at"])
        task_services.log_activity(task, "opened", actor=actor, message="Task opened (official letter).")
        return task
    except Exception:
        logger.exception("official letters: could not create the task for job %s", job.pk)
        return None


def _close_task(job, *, completed, actor=None):
    if not job.task_id:
        return
    try:
        from tasks import services as task_services
        from tasks.models import Task, TaskAssignee

        task = Task.objects.filter(pk=job.task_id).first()
        if task is None or task.state in Task.CLOSED_STATES:
            return
        now = timezone.now()
        if completed:
            TaskAssignee.objects.filter(task=task).exclude(state=TaskAssignee.DONE).update(
                state=TaskAssignee.DONE, completed_at=now)
            task.state, task.completed_at = Task.COMPLETED, now
            task.save(update_fields=["state", "completed_at", "updated_at"])
            task_services.log_activity(task, "completed", actor=actor, message="Official copy uploaded.")
        else:
            task.state, task.cancelled_at = Task.CANCELLED, now
            task.save(update_fields=["state", "cancelled_at", "updated_at"])
            task_services.log_activity(task, "cancelled", actor=actor, message="Official letter reassigned or closed.")
    except Exception:
        logger.exception("official letters: could not close task %s", job.task_id)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _require_open(doc, job):
    if doc.status != doc.VALID:
        raise ValidationError("This document is no longer valid; nothing to prepare.")
    if job.state == job.DONE or doc.official_copy is not None:
        raise ValidationError("The official copy is already uploaded.")


@transaction.atomic
def assign(doc, *, actor, assignee, due_date=None, note=""):
    from core import audit

    if not can_manage(actor, doc):
        raise PermissionDenied("Only the handling department's Director, the ES or a super admin can assign this.")
    job = job_for(doc)
    _require_open(doc, job)
    if assignee is None or not assignable_staff(actor, doc).filter(pk=assignee.pk).exists():
        raise ValidationError("Choose a staff member of the handling department.")
    today = timezone.localdate()
    if due_date is None:
        due_date = today + datetime.timedelta(days=min(3, sla_days()))
    if due_date < today:
        raise ValidationError("The due date cannot be in the past.")
    if job.task_id and job.assignee_id != assignee.pk:
        _close_task(job, completed=False, actor=actor)
        job.task = None
    previous = job.assignee
    job.assignee, job.assigned_by, job.assigned_at = assignee, actor, timezone.now()
    job.due_date, job.assign_note = due_date, (note or "").strip()[:500]
    job.state, job.review_comment, job.assignee_reminded_at = job.ASSIGNED, "", None
    if job.department_id is None:
        job.department_id = _dept_id(doc)
    job.log("reassigned" if previous and previous.pk != assignee.pk else "assigned", actor,
            f"{assignee.get_full_name() or assignee.email}, due {due_date:%d %b %Y}" + (f" — {note}" if note else ""))
    job.save()
    if job.task_id is None:
        task = _open_task(job, actor)
        if task is not None:
            job.task = task
            job.save(update_fields=["task", "updated_at"])
    audit.log("other", doc, actor=actor, note=f"official letter assigned to user #{assignee.pk}")
    who = actor.get_full_name() or actor.email
    url = _job_url(doc)
    transaction.on_commit(lambda: _notify(
        [assignee], f"Prepare the official letter: {doc.institution.name}",
        f"{who} asked you to prepare the official {letter_label(doc).lower()} ({doc.reference}) by "
        f"{due_date:%d %b %Y}. Upload your draft on the letter page." + (f" Note: {note}" if note else ""),
        url, actor=actor))
    return job


@transaction.atomic
def submit_draft(doc, *, actor, upload, note=""):
    from core import audit

    from .models import OfficialLetterDraft

    job = job_for(doc, create=False)
    if job is None or job.assignee_id != getattr(actor, "pk", None) or job.state not in job.WITH_STAFF:
        raise PermissionDenied("Only the staff member preparing this letter can send the draft.")
    _require_open(doc, job)
    if upload is None:
        raise ValidationError("Choose the draft file.")
    name = getattr(upload, "name", "") or "draft"
    if not name.lower().endswith(DRAFT_EXTENSIONS):
        raise ValidationError("Send the draft as a PDF or Word document.")
    if getattr(upload, "size", 0) > MAX_DRAFT_MB * 1024 * 1024:
        raise ValidationError(f"The file is larger than {MAX_DRAFT_MB} MB.")
    draft = OfficialLetterDraft(job=job, uploaded_by=actor, note=(note or "").strip()[:1000],
                                original_name=name[:255])
    draft.file.save(name, upload, save=False)
    draft.save()
    job.state = job.DRAFT_READY
    job.log("draft_sent", actor, note)
    job.save()
    audit.log("other", doc, actor=actor, note="official letter draft sent for review")
    who = actor.get_full_name() or actor.email
    recipients = _directors_for(doc) + ([job.assigned_by] if job.assigned_by_id else [])
    url = _job_url(doc)
    transaction.on_commit(lambda: _notify(
        recipients, f"Draft ready – review & upload: {doc.institution.name}",
        f"{who} sent the draft of the official {letter_label(doc).lower()} ({doc.reference}). Review it and upload "
        f"the signed official copy." + (f" Note: {note}" if note else ""), url, actor=actor))
    return draft


@transaction.atomic
def send_back(doc, *, actor, comment):
    from core import audit

    if not can_manage(actor, doc):
        raise PermissionDenied("Only the handling department's Director, the ES or a super admin can send it back.")
    job = job_for(doc, create=False)
    if job is None or job.state != job.DRAFT_READY:
        raise ValidationError("There is no draft waiting for review.")
    comment = (comment or "").strip()
    if len(comment) < 3:
        raise ValidationError("Say what needs to change (a short comment).")
    job.state, job.review_comment = job.RETURNED, comment[:1000]
    job.log("sent_back", actor, comment)
    job.save()
    audit.log("other", doc, actor=actor, note="official letter draft sent back")
    who = actor.get_full_name() or actor.email
    assignee = job.assignee
    url = _job_url(doc)
    transaction.on_commit(lambda: _notify(
        [assignee], f"Draft sent back: {doc.institution.name}",
        f"{who} sent your draft of the official {letter_label(doc).lower()} ({doc.reference}) back: {comment}",
        url, actor=actor))
    return job


def upload_official(doc, *, actor, upload, note=""):
    """The Director / ES / admin uploads the signed copy (existing flow)."""
    from . import services

    if not can_manage(actor, doc):
        raise PermissionDenied("Only the handling department's Director, the ES or a super admin can upload "
                               "the official copy.")
    return services.upload_official(doc, user=actor, upload=upload, note=note)


def on_official_uploaded(doc, actor=None):
    """Called by ``services.upload_official`` (any route): close the job."""
    job = job_for(doc, create=doc.provisional and doc.institution_id is not None)
    if job is None or job.state == job.DONE:
        return job
    job.state, job.completed_at = job.DONE, timezone.now()
    job.completed_by = actor if getattr(actor, "pk", None) else None
    job.log("official_uploaded", actor)
    job.save()
    _close_task(job, completed=True, actor=actor)
    if job.assignee_id and getattr(actor, "pk", None) != job.assignee_id:
        assignee, inst = job.assignee, doc.institution
        transaction.on_commit(lambda: _notify(
            [assignee], f"Official copy uploaded: {inst.name}",
            f"The signed official copy of {doc.reference} is uploaded. Thank you — your task is closed.",
            _job_url(doc), actor=actor))
    return job


def on_revoked(doc, actor=None):
    job = job_for(doc, create=False)
    if job is None or not job.is_open:
        return
    job.state = job.CANCELLED
    job.log("closed", actor, "Document revoked")
    job.save()
    _close_task(job, completed=False, actor=actor)


# ---------------------------------------------------------------------------
# Reminders and escalation (hourly/daily sweep)
# ---------------------------------------------------------------------------
def send_reminders(now=None):
    """Remind once the letter has waited the SLA, and escalate it to the ES.
    Also remind an assignee once when their due date has passed. Returns a
    list of (document pk, kind)."""
    from .models import OfficialLetterJob

    now = now or timezone.now()
    sla = sla_days()
    sent = []
    es_users = None
    for doc in pending_documents():
        waited = days_waiting(doc, now)
        job = OfficialLetterJob.objects.filter(document=doc).select_related("assignee").first()
        overdue = waited >= sla
        assignee_late = bool(job and job.state in job.WITH_STAFF and job.due_date
                             and job.due_date < timezone.localdate(now) and job.assignee_reminded_at is None)
        if not overdue and not assignee_late:
            continue
        if job is None:
            job = job_for(doc)
        url = _job_url(doc)
        inst = doc.institution.name
        label = letter_label(doc).lower()
        changed = []
        if overdue and job.reminded_at is None:
            recipients = _directors_for(doc)
            if job.state in job.WITH_STAFF and job.assignee_id:
                recipients.append(job.assignee)
            _notify(recipients, f"Official letter overdue: {inst}",
                    f"The official {label} ({doc.reference}) has waited {waited} days (target {sla}). "
                    f"Upload the signed copy or follow up with the staff preparing it.", url)
            job.reminded_at = now
            changed.append("reminded_at")
            sent.append((doc.pk, "reminder"))
        if overdue and job.escalated_at is None:
            if es_users is None:
                es_users = _es_users()
            _notify(es_users, f"Escalated: official letter overdue — {inst}",
                    f"The official {label} ({doc.reference}) has waited {waited} days for its signed copy "
                    f"(target {sla} days).", url)
            job.escalated_at = now
            changed.append("escalated_at")
            sent.append((doc.pk, "escalation"))
        if assignee_late:
            _notify([job.assignee], f"Official letter draft overdue: {inst}",
                    f"Your draft of the official {label} ({doc.reference}) was due on {job.due_date:%d %b %Y}.", url)
            job.assignee_reminded_at = now
            changed.append("assignee_reminded_at")
            sent.append((doc.pk, "assignee"))
        if changed:
            job.save(update_fields=changed + ["updated_at"])
    return sent


# ---------------------------------------------------------------------------
# Institution portal: plain-words status line
# ---------------------------------------------------------------------------
def portal_status(doc):
    """None for a document that is not provisional; otherwise
    {"ready": bool, "since": date, "by": "NBTE" | department name, "copy": OfficialCopy}."""
    if not doc.provisional:
        return None
    copy = doc.official_copy
    if copy is not None:
        return {"ready": True, "copy": copy, "since": copy.uploaded_at, "by": "NBTE"}
    if doc.status != doc.VALID:
        return None
    by = "NBTE"
    if show_department_names() and doc.institution_id and doc.institution.handling_department_id:
        by = f"NBTE ({doc.institution.handling_department.name})"
    return {"ready": False, "copy": None, "since": doc.issued_at, "by": by}


# ---------------------------------------------------------------------------
# Staff institution page: every document issued to the institution
# ---------------------------------------------------------------------------
PAYMENT_KINDS = ("invoice", "payment_slip", "receipt", "payment_advice")


def institution_documents(user, inst):
    """Rows for the institution's 'Issued documents' tab. Register keepers
    see everything; others see the documents they may download (issued or
    signed by them) and, with payments access, payment documents."""
    from institutions.services import can_see_payments

    from . import issue as issuing
    from . import services
    from .models import IssuedDocument, OfficialCopy, OfficialLetterJob

    qs = (IssuedDocument.unscoped.filter(institution=inst).select_related("template", "issued_by")
          .order_by("-issued_at", "-id"))
    register = services.can_view_register(user)
    see_all = register or is_full(user) or bool(
        inst.handling_department_id and _staff(user) and org.is_director(user, inst.handling_department_id))
    payments_ok = can_see_payments(user)
    docs = []
    for d in qs[:500]:
        if see_all or d.can_download(user) or (payments_ok and d.kind in PAYMENT_KINDS):
            docs.append(d)
    copies = {}
    for c in OfficialCopy.unscoped.filter(document__in=docs).select_related("uploaded_by").order_by("-uploaded_at"):
        copies.setdefault(c.document_id, c)
    jobs = {j.document_id: j for j in OfficialLetterJob.objects.filter(document__in=docs).select_related("assignee")}
    sla = sla_days()
    out = []
    for d in docs:
        copy = copies.get(d.pk)
        if d.status == d.REVOKED:
            status, tone = "Revoked", "danger"
        elif d.status == d.SUPERSEDED:
            status, tone = "Superseded", "warn"
        elif d.provisional and copy is not None:
            status, tone = "Official copy uploaded", "success"
        elif d.provisional:
            status, tone = "Provisional", "pending"
        else:
            status, tone = "Valid", "success"
        file_ok = d.can_download(user)  # the protected download asks the same question
        job = jobs.get(d.pk)
        pending = bool(d.provisional and copy is None and d.status == d.VALID and d.audience != "internal")
        out.append({"doc": d, "copy": copy, "copy_ok": file_ok and copy is not None, "status": status, "tone": tone, "pending": pending,
                    "job": job, "days": days_waiting(d) if pending else None,
                    "overdue": pending and days_waiting(d) >= sla,
                    "can_download": file_ok and bool(d.file), "verify_url": issuing.verify_url(d.token),
                    "register_url": reverse("documents_register:detail", args=[d.pk]) if register else "",
                    "letter_url": _job_url(d) if pending and can_open(user, d, job) else ""})
    return out


def institution_counts(rows_):
    return {"total": len(rows_), "pending": sum(1 for r in rows_ if r["pending"]),
            "official": sum(1 for r in rows_ if r["copy"] is not None),
            "revoked": sum(1 for r in rows_ if r["doc"].status == "revoked")}
