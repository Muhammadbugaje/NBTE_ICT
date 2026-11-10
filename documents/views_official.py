"""Official letters queue (/qa/documents/official-letters/): provisional
documents waiting for their signed official copy. Directors see their
department's institutions, the ES and super admins see all. See
``documents.official_letters`` for the rules."""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from qa_core.ui import render_qa

from . import issue as issuing
from . import official_letters as ol
from .models import IssuedDocument, OfficialCopy, OfficialLetterJob

STATE_FILTERS = [("", "All open"), ("waiting", "Waiting – no one assigned"), ("with_staff", "With staff"),
                 ("draft_ready", "Draft ready – review & upload")]


@login_required
def queue(request):
    user = request.user
    if not ol.can_open_queue(user):
        raise PermissionDenied("The official letters queue is for the handling department's Director, the ES "
                               "and super admins.")
    full = ol.is_full(user)
    state = request.GET.get("state") or ""
    if state not in dict(STATE_FILTERS):
        state = ""
    dept = request.GET.get("dept") or ""
    dept_id = int(dept) if (full and dept.isdigit()) else None
    q = (request.GET.get("q") or "").strip()
    overdue = request.GET.get("overdue") == "1"
    all_rows = ol.rows(user)
    shown = ol.rows(user, q=q, state=state, department=dept_id, overdue=overdue) \
        if (q or state or dept_id or overdue) else all_rows
    departments = []
    if full:
        seen = {}
        for r in all_rows:
            d = r["institution"].handling_department
            if d is not None:
                seen[d.pk] = d.name
        departments = sorted(seen.items(), key=lambda kv: kv[1])
    ctx = {"rows": shown, "stats": ol.summary(all_rows), "full": full, "directed": ol.directed_department(user),
           "filters": {"q": q, "state": state, "dept": dept_id, "overdue": overdue}, "state_filters": STATE_FILTERS,
           "departments": departments, "sla": ol.sla_days(),
           "filtered": bool(q or state or dept_id or overdue)}
    return render_qa(request, "documents/official/queue.html", ctx, {"ol-panel": "documents/official/_rows.html"})


def _doc_or_404(request, pk):
    doc = get_object_or_404(IssuedDocument.unscoped.select_related("institution", "institution__handling_department",
                                                                   "template", "issued_by"), pk=pk)
    if not doc.provisional or not doc.institution_id:
        raise Http404
    job = ol.job_for(doc, create=False)
    if not ol.can_open(request.user, doc, job):
        raise Http404
    return doc, job


@login_required
def detail(request, pk):
    doc, job = _doc_or_404(request, pk)
    user = request.user
    manage = ol.can_manage(user, doc)
    copies = list(OfficialCopy.unscoped.filter(document=doc).select_related("uploaded_by"))
    open_ = doc.status == doc.VALID and not copies
    state = job.state if job else OfficialLetterJob.WAITING
    is_assignee = bool(job and job.assignee_id == user.pk)
    drafts = list(job.drafts.select_related("uploaded_by")) if job else []
    staff = list(ol.assignable_staff(user, doc)[:300]) if (manage and open_) else []
    sla = ol.sla_days()
    waited = ol.days_waiting(doc)
    steps = [
        {"label": "Provisional letter issued", "done": True, "when": doc.issued_at},
        {"label": "Assigned to prepare", "done": bool(job and job.assigned_at), "when": job.assigned_at if job else None,
         "optional": True},
        {"label": "Draft ready for review", "done": bool(drafts), "when": drafts[0].uploaded_at if drafts else None,
         "optional": True},
        {"label": "Official copy uploaded", "done": bool(copies), "when": copies[0].uploaded_at if copies else None},
    ]
    history = []
    for h in reversed((job.history or []) if job else []):
        try:
            at = datetime.datetime.fromisoformat(h.get("at"))
        except (TypeError, ValueError):
            at = None
        history.append({**h, "at_dt": at})
    ctx = {"doc": doc, "job": job, "state": state, "state_label": dict(OfficialLetterJob.STATE_CHOICES)[state],
           "tone": ol.STATE_TONES.get(state, "info"), "manage": manage, "is_assignee": is_assignee,
           "open": open_, "copies": copies, "drafts": drafts, "staff": staff, "letter": ol.letter_label(doc),
           "days": waited, "overdue": open_ and waited >= sla, "sla": sla,
           "can_draft": is_assignee and open_ and state in OfficialLetterJob.WITH_STAFF,
           "can_review": manage and open_ and state == OfficialLetterJob.DRAFT_READY,
           "default_due": timezone.localdate() + datetime.timedelta(days=min(3, sla)),
           "today": timezone.localdate(), "verify_url": issuing.verify_url(doc.token),
           "steps": steps, "history": history, "can_queue": ol.can_open_queue(user)}
    return render_qa(request, "documents/official/detail.html", ctx)


def _back(doc):
    return redirect("documents_register:official_letter", pk=doc.pk)


def _errors(request, exc):
    messages.error(request, " ".join(getattr(exc, "messages", None) or [str(exc)]))


@login_required
@require_POST
def assign(request, pk):
    doc, _job = _doc_or_404(request, pk)
    from accounts.models import User

    assignee = User.objects.filter(pk=request.POST.get("assignee") or 0).first()
    due = None
    raw = (request.POST.get("due_date") or "").strip()
    if raw:
        try:
            due = datetime.date.fromisoformat(raw)
        except ValueError:
            messages.error(request, "Enter the due date as a date.")
            return _back(doc)
    try:
        job = ol.assign(doc, actor=request.user, assignee=assignee, due_date=due, note=request.POST.get("note", ""))
        messages.success(request, f"Assigned to {job.assignee.get_full_name() or job.assignee.email}, due "
                                  f"{job.due_date:%d %b %Y}. They have been notified and it is in their tasks.")
    except PermissionDenied:
        raise
    except ValidationError as exc:
        _errors(request, exc)
    return _back(doc)


@login_required
@require_POST
def submit_draft(request, pk):
    doc, _job = _doc_or_404(request, pk)
    try:
        ol.submit_draft(doc, actor=request.user, upload=request.FILES.get("file"), note=request.POST.get("note", ""))
        messages.success(request, "Draft sent. The Director has been told it is ready to review.")
    except ValidationError as exc:
        _errors(request, exc)
    return _back(doc)


@login_required
@require_POST
def send_back(request, pk):
    doc, _job = _doc_or_404(request, pk)
    try:
        ol.send_back(doc, actor=request.user, comment=request.POST.get("comment", ""))
        messages.success(request, "Draft sent back with your comments. The staff member has been notified.")
    except ValidationError as exc:
        _errors(request, exc)
    return _back(doc)


@login_required
@require_POST
def upload(request, pk):
    doc, _job = _doc_or_404(request, pk)
    try:
        ol.upload_official(doc, actor=request.user, upload=request.FILES.get("file"), note=request.POST.get("note", ""))
        messages.success(request, f"Official copy uploaded. {doc.institution.name} has been told their official "
                                  f"letter is ready.")
    except ValidationError as exc:
        _errors(request, exc)
        return _back(doc)
    if request.POST.get("next") == "queue" and ol.can_open_queue(request.user):
        return redirect("documents_register:official_letters")
    return _back(doc)
