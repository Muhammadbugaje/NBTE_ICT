"""v2.1 F.16 (X16–X18, design 23): the department's document review workspace.

``/qa/applications/documents/``           queue: the director's department's documents (and an
                                           analyst's own), newest re-uploads first
``/qa/applications/documents/<pk>/``      one document: assign (rotation suggestion),
                                           recommend (analyst), approve / send back / return (director)
``/qa/applications/verification/<pk>/``   Director of ICT hands a physical verification to an ICT officer

Scoping: ``institutions.document_review.visible_reviews`` (department / own
assignment). TODO(pp-access): use ``assignments.access.can_see``.
"""
from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from institutions import document_review as dr
from institutions.models import DocumentReview
from qa_core.permissions import is_qa_staff, require, require_or_404
from qa_core.ui import render_qa

STATUS_FILTERS = [("open", "Open"), ("awaiting_assignment", "Needs an officer"), ("assigned", "With officer"),
                  ("recommended", "Awaiting your decision"), ("returned_to_institution", "With institution"),
                  ("approved", "Approved"), ("all", "All")]
RETURN_REASONS = ["Unreadable page", "Not signed", "Expired", "Wrong document", "Not certified"]


def _queue(user, status="open"):
    qs = dr.visible_reviews(user).exclude(status=DocumentReview.SUPERSEDED)
    if status == "open":
        qs = qs.filter(status__in=DocumentReview.OPEN + (DocumentReview.RETURNED,))
    elif status and status != "all":
        qs = qs.filter(status=status)
    rows = list(qs.order_by("-updated_at")[:300])
    order = {DocumentReview.RECOMMENDED: 0, DocumentReview.AWAITING: 1, DocumentReview.SENT_BACK: 2,
             DocumentReview.ASSIGNED: 3, DocumentReview.RETURNED: 4, DocumentReview.APPROVED: 5}
    rows.sort(key=lambda r: (order.get(r.status, 9), -(r.round or 1)))
    return rows


def _pv_rows(user):
    """Physical verifications waiting for the Director of ICT (X18)."""
    import org
    import config

    from assignments.models import WorkAssignment

    ict = org.department(config.get("qa.privacy.ict_department", "ict") or "ict")
    if ict is None or not (org.is_director(user, ict) or org.is_super_admin(user)):
        return []
    return list(WorkAssignment.objects.filter(kind=WorkAssignment.PHYSICAL_VERIFICATION, status=WorkAssignment.OFFERED,
                                              meta__awaiting_officer=True).select_related("institution", "assignee"))


def pv_excluded_ids(institution):
    """Physical verification must be done by someone independent: not the ICT
    officer overseeing the institution online (open oversight assignment) and
    not anyone who reviewed one of its self-studies (SSQ / onboarding SSQ)."""
    from assignments.models import WorkAssignment

    rows = WorkAssignment.objects.filter(institution=institution).filter(
        kind__in=[WorkAssignment.OVERSIGHT_ICT, WorkAssignment.SSQ_REVIEW]).exclude(
        status=getattr(WorkAssignment, "CANCELLED", "cancelled")).values_list("kind", "status", "assignee_id")
    out = set()
    for kind, status, uid in rows:
        if kind == WorkAssignment.SSQ_REVIEW or status in WorkAssignment.OPEN:
            out.add(uid)
    if getattr(institution, "ict_supervisor_id", None):
        out.add(institution.ict_supervisor_id)
    return out


def pv_officers_for(institution):
    excluded = pv_excluded_ids(institution)
    return [u for u in _ict_officers() if u.pk not in excluded]


def _pv_rows_with_officers(user):
    rows = _pv_rows(user)
    for w in rows:
        w.pv_officers = pv_officers_for(w.institution)
    return rows


def _ict_officers():
    import org
    import config

    from accounts.models import User

    ict = org.department(config.get("qa.privacy.ict_department", "ict") or "ict")
    director = org.director_of(ict) if ict else None
    qs = User.objects.filter(is_active=True, user_type="staff", department=ict).order_by("first_name")
    return [u for u in qs if director is None or u.pk != director.pk]


def _context(request, review=None, **extra):
    user = request.user
    status = request.GET.get("status", "open")
    rows = _queue(user, status)
    ctx = {"rows": rows, "status": status, "filters": STATUS_FILTERS, "review": review,
           "pv_rows": _pv_rows_with_officers(user), "ict_officers": [],
           "return_reasons": RETURN_REASONS, "directed": None}
    import org

    ctx["directed"] = org.directed_department(user)
    # v2.3: counts per filter pill, and what a director may approve as a group.
    from django.db.models import Count

    by_status = {r["status"]: r["n"] for r in dr.visible_reviews(user).exclude(status=DocumentReview.SUPERSEDED)
                 .order_by().values("status").annotate(n=Count("pk"))}
    open_codes = set(DocumentReview.OPEN) | {DocumentReview.RETURNED}
    counts = {"open": sum(n for st, n in by_status.items() if st in open_codes), "all": sum(by_status.values())}
    counts.update(by_status)
    ctx["filter_pills"] = [{"code": v, "label": lbl, "count": counts.get(v, 0)} for v, lbl in STATUS_FILTERS]
    ctx["status_counts"] = counts
    directed = ctx["directed"]
    ctx["bulk_ids"] = [r.pk for r in rows if directed is not None and r.department_id == directed.pk
                       and r.status == DocumentReview.RECOMMENDED and r.recommendation == "accept"]
    if review is not None:
        inst = review.document.institution
        ctx["siblings"] = [r for r in dr.visible_reviews(user).filter(document__institution=inst,
                                                                     department=review.department)
                           .exclude(status=DocumentReview.SUPERSEDED).order_by("document__kind__name")]
        ctx["is_director"] = dr.is_director_for(user, review.department)
        ctx["is_analyst"] = review.analyst_id == user.pk
        ctx["trail"] = dr.trail(review)
        ctx["sibling_bulk"] = [r.pk for r in ctx["siblings"] if ctx["is_director"]
                               and r.status == DocumentReview.RECOMMENDED and r.recommendation == "accept"]
        from core import preview_gate   # v2.3: decide only after opening the file

        ctx["gate_on"] = preview_gate.enabled()
        ctx["opened"] = preview_gate.has_opened(user, review.document)
        ctx["can_act_here"] = (ctx["is_director"] and review.status == DocumentReview.RECOMMENDED) or (
            ctx["is_analyst"] and review.status in (DocumentReview.ASSIGNED, DocumentReview.SENT_BACK))
        if review.status == DocumentReview.AWAITING and ctx["is_director"]:
            s = dr.suggestion(review)
            ctx["suggested"] = s.suggested if s is not None else None
            ctx["candidates"] = dr.candidates(review)
    ctx.update(extra)
    return ctx


def _review_or_404(request, pk):
    review = dr.visible_reviews(request.user).filter(pk=pk).first()
    if review is None:
        raise Http404
    return review


@require(is_qa_staff)
@require_GET
def queue(request):
    return render_qa(request, "accreditation/docreview/page.html", _context(request),
                     {"qa-panel": "accreditation/docreview/_panel.html"})


@require_or_404(is_qa_staff)
@require_GET
def detail(request, pk):
    review = _review_or_404(request, pk)
    return render_qa(request, "accreditation/docreview/page.html", _context(request, review),
                     {"qa-panel": "accreditation/docreview/_panel.html"})


def _done(request, review, ok_message):
    messages.success(request, ok_message)
    return redirect("qa_app:docrev_detail", review.pk)


def _fail(request, review, exc):
    msgs = getattr(exc, "messages", None) or [str(exc)]
    messages.error(request, " ".join(msgs))
    return redirect("qa_app:docrev_detail", review.pk)


@require_or_404(is_qa_staff)
@require_POST
def assign(request, pk):
    from accounts.models import User

    review = _review_or_404(request, pk)
    officer = None
    if request.POST.get("officer"):
        officer = User.objects.filter(pk=request.POST["officer"], is_active=True).first()
    try:
        dr.assign(review, director=request.user, officer=officer,
                  accept_suggestion=request.POST.get("accept_suggestion") == "1",
                  reason=request.POST.get("reason", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _fail(request, review, exc)
    review.refresh_from_db()
    return _done(request, review, f"Assigned to {review.analyst.get_full_name()}.")


@require_or_404(is_qa_staff)
@require_POST
def recommend(request, pk):
    review = _review_or_404(request, pk)
    try:
        from core import preview_gate

        preview_gate.require_opened(request.user, review.document)   # v2.3: no blind recommendations
        dr.recommend(review, analyst=request.user, recommendation=request.POST.get("recommendation", ""),
                     remark=request.POST.get("remark", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _fail(request, review, exc)
    return _done(request, review, "Recommendation sent to your director.")


@require_or_404(is_qa_staff)
@require_POST
def decide(request, pk):
    review = _review_or_404(request, pk)
    decision = request.POST.get("decision", "")
    try:
        from core import preview_gate

        preview_gate.require_opened(request.user, review.document)   # v2.3: no blind decisions
        dr.decide(review, director=request.user, decision=decision, remark=request.POST.get("remark", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _fail(request, review, exc)
    return _done(request, review, {"approve": "Document approved.", "send_back": "Sent back to the officer.",
                                   "return_to_institution": "Returned to the institution. Only this document "
                                                            "goes back."}.get(decision, "Saved."))


@require(is_qa_staff)
@require_POST
def verification_assign(request, pk):
    """X18: the Director of ICT hands the physical verification to an ICT officer."""
    from accounts.models import User
    from assignments.models import WorkAssignment

    wa = next((w for w in _pv_rows(request.user) if w.pk == pk), None)
    if wa is None:
        raise Http404
    officer = User.objects.filter(pk=request.POST.get("officer") or 0, is_active=True).first()
    if officer is None or officer not in _ict_officers():
        messages.error(request, "Choose an ICT officer.")
        return redirect("qa_app:docrev_queue")
    if officer.pk in pv_excluded_ids(wa.institution):
        messages.error(request, f"{officer.get_full_name()} oversees this institution or reviewed its self-study. "
                                "Physical verification needs a different ICT officer.")
        return redirect("qa_app:docrev_queue")
    from assignments import work

    # Through the work API: the server re-checks who may assign whom (X4) and
    # the officer gets the time-boxed grants of the kind (X5).
    try:
        with transaction.atomic():
            work.cancel(wa, request.user, "reassigned")
            new = work.assign(wa.institution, WorkAssignment.PHYSICAL_VERIFICATION, officer, subject=wa.subject,
                              department=wa.department, assigned_by=request.user, role_on_work="lead",
                              payable=False,  # X32 covers SSQ/document items only: PV is paid through the cost sheet
                              meta={"programme_count": (wa.meta or {}).get("programme_count")})
            work.start(new, officer)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", None) or [str(exc)]))
        return redirect("qa_app:docrev_queue")
    messages.success(request, f"Physical verification of {wa.institution.name} assigned to {officer.get_full_name()}.")
    return redirect("qa_app:docrev_queue")


@require(is_qa_staff)
@require_POST
def bulk_approve(request):
    """v2.3: a director approves, in one go, documents his officers already
    reviewed and recommended for acceptance (the officers opened them)."""
    ids = [int(x) for x in request.POST.getlist("review") if str(x).isdigit()]
    done, skipped = 0, []
    for review in dr.visible_reviews(request.user).filter(pk__in=ids, status=DocumentReview.RECOMMENDED,
                                                          recommendation="accept"):
        try:
            dr.decide(review, director=request.user, decision="approve",
                      remark="Approved in bulk on the officer's recommendation")
            done += 1
        except (ValidationError, PermissionDenied) as exc:
            skipped.append(f"{review.document.kind.name}: {' '.join(getattr(exc, 'messages', [str(exc)]))}")
    if done:
        messages.success(request, f"Approved {done} document{'s' if done != 1 else ''} on your officers' "
                                  "recommendation.")
    if skipped:
        messages.warning(request, "Not approved: " + "; ".join(skipped[:5]))
    if not done and not skipped:
        messages.info(request, "Nothing to approve: only documents an officer recommended for acceptance can be "
                               "approved in bulk.")
    nxt = request.POST.get("next") or ""
    from core.urlsafe import safe_next

    return redirect(safe_next(request, nxt, "/qa/applications/documents/"))
