"""v2.3 — one "Your work" list that opens the task itself.

Every row says what to do, for which institution, and links straight to the
place where it is done (the document review, the SSQ, the case stage, the
approval page). "View institution" is a second link. Used by My work, the
QA home and the main dashboards.

Row: {kind, verb, title, institution, url, inst_url, due, overdue, tone, icon, group}
"""
from __future__ import annotations

import datetime

from django.urls import NoReverseMatch, reverse
from django.utils import timezone


def _url(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


def _inst_url(inst):
    return _url("qa_inst:detail", inst.code) if inst is not None else ""


def task_url(wa):
    """Where a WorkAssignment is actually done (falls back to the institution)."""
    from .models import WorkAssignment

    kind = wa.kind
    subject_model = getattr(wa.subject_ct, "model", "") if wa.subject_ct_id else ""
    if kind == WorkAssignment.DOCUMENT_ANALYSIS and subject_model == "institutiondocument":
        from institutions.models import DocumentReview

        review = DocumentReview.unscoped.filter(document_id=wa.subject_id).order_by("-pk").first()
        if review is not None:
            return _url("qa_app:docrev_detail", review.pk), "Review document"
    if kind == WorkAssignment.SSQ_REVIEW and subject_model == "ssq":
        return _url("qa_req:ssq", wa.subject_id), "Review SSQ"
    if kind == WorkAssignment.SSQ_REVIEW and subject_model == "onboardingssq":
        return _url("qa_app:onboarding_ssq", wa.subject_id), "Assess self-study"
    if subject_model == "qacase":
        return _url("qa_case:detail", wa.subject_id), "Open case"
    if kind == WorkAssignment.COMPLAINT_TASK and subject_model == "complaint":
        return _url("qa_complaints:detail", wa.subject_id), "Handle complaint"
    if kind in (WorkAssignment.CLEARANCE, WorkAssignment.PROGRAMME_CLEARANCE):
        return _url("qa_app:clearance"), "Clear programmes"
    if kind == WorkAssignment.PAYMENT_REVIEW:
        return _url("qa_fin:queue"), "Check payment"
    if kind == WorkAssignment.HONORARIA_SCHEDULE:
        if subject_model == "honorariumfile":  # v2.3: the institution's honorarium file
            return _url("honoraria_sched:file", wa.subject_id), "Prepare payment"
        return _url("honoraria_sched:roller"), "Prepare payment"
    if kind == WorkAssignment.OVERSIGHT_ICT:
        # SOP-03 redesign: an institution with an open QA case opens its case cockpit.
        if wa.institution_id:
            try:
                from accreditation.models import QACase

                case = (QACase.unscoped.filter(institution_id=wa.institution_id).exclude(status="closed")
                        .order_by("-pk").values_list("pk", flat=True).first())
            except Exception:  # noqa: BLE001 — fall back to the institution page
                case = None
            if case:
                return _url("qa_case:detail", case), "Open case cockpit"
        return (_inst_url(wa.institution) + "?tab=overview") if wa.institution_id else "", "Track progress"
    return _inst_url(wa.institution) if wa.institution_id else "", "Open"


ICONS = {"approval": "★", "doc_decide": "✓", "doc_assign": "↗", "doc_analyse": "📄", "ssq": "📝", "case": "◆",
         "work": "•", "payment": "₦"}


def inbox(user, limit=40):
    """Everything waiting for ``user``, most urgent first."""
    if user is None or not getattr(user, "is_authenticated", False) or \
            getattr(user, "user_type", "staff") != "staff":
        return []
    now = timezone.now()
    rows = []

    # 2. Document reviews
    try:
        import org
        from institutions import document_review as dr
        from institutions.models import DocumentReview

        directed = org.directed_department(user)
        qs = dr.visible_reviews(user).select_related("document__kind", "document__institution", "analyst")
        if directed is not None:
            for r in qs.filter(department=directed, status=DocumentReview.RECOMMENDED).order_by("recommended_at")[:30]:
                rows.append({"kind": "doc_decide", "group": "Decide", "verb": "Decide",
                             "title": f"{r.document.kind.name}",
                             "sub": f"{r.analyst.get_full_name() if r.analyst else 'Officer'} recommends "
                                    f"{r.get_recommendation_display().lower() if r.recommendation else '—'}",
                             "institution": r.document.institution, "url": _url("qa_app:docrev_detail", r.pk),
                             "inst_url": _inst_url(r.document.institution), "due": None, "overdue": False,
                             "tone": "green" if r.recommendation == "accept" else "red", "rank": 1,
                             "when": r.recommended_at, "bulk_id": r.pk if r.recommendation == "accept" else None})
            for r in qs.filter(department=directed, status=DocumentReview.AWAITING).order_by("created_at")[:30]:
                rows.append({"kind": "doc_assign", "group": "Assign", "verb": "Assign an officer",
                             "title": r.document.kind.name, "sub": "New document for your department",
                             "institution": r.document.institution, "url": _url("qa_app:docrev_detail", r.pk),
                             "inst_url": _inst_url(r.document.institution), "due": None, "overdue": False,
                             "tone": "blue", "rank": 2, "when": r.created_at})
        for r in qs.filter(analyst=user, status__in=(DocumentReview.ASSIGNED, DocumentReview.SENT_BACK)
                           ).order_by("assigned_at")[:30]:
            rows.append({"kind": "doc_analyse", "group": "Review", "verb": "Review document",
                         "title": r.document.kind.name,
                         "sub": "Sent back by your director" if r.status == DocumentReview.SENT_BACK
                         else "Assigned to you",
                         "institution": r.document.institution, "url": _url("qa_app:docrev_detail", r.pk),
                         "inst_url": _inst_url(r.document.institution), "due": None, "overdue": False,
                         "tone": "red" if r.status == DocumentReview.SENT_BACK else "green", "rank": 2,
                         "when": r.assigned_at})
    except Exception:
        pass

    # 3. SOP-03 case stages held by this user
    try:
        from accreditation import case_services as cs

        for link in cs.my_stage_links(user)[:30]:
            rows.append({"kind": "case", "group": "SOP-03", "verb": cs.STAGE_TITLES.get(link.stage_code, "Act")
                         if hasattr(cs, "STAGE_TITLES") else "Act on stage",
                         "title": cs.stage_label(link.stage_code), "sub": link.case.reference,
                         "institution": link.case.institution, "url": _url("qa_case:detail", link.case.pk),
                         "inst_url": _inst_url(link.case.institution), "due": link.due_at,
                         "overdue": bool(link.due_at and link.due_at < now), "tone": "violet", "rank": 3,
                         "when": link.created_at if hasattr(link, "created_at") else None})
    except Exception:
        pass

    # 4. Other open assignments (SSQ, oversight, complaints, …)
    try:
        from .models import WorkAssignment

        seen_docs = {r.get("url") for r in rows}
        for wa in (WorkAssignment.objects.filter(assignee=user, status__in=WorkAssignment.OPEN)
                   .select_related("institution", "subject_ct").order_by("due_at", "-assigned_at")[:60]):
            if wa.kind == WorkAssignment.DOCUMENT_ANALYSIS:
                url, verb = task_url(wa)
                if url in seen_docs:
                    continue
            url, verb = task_url(wa)
            offered = wa.status in ("offered", "suggested")
            rows.append({"kind": "ssq" if wa.kind == WorkAssignment.SSQ_REVIEW else "work",
                         "group": wa.get_kind_display(), "verb": "Accept or decline" if offered else verb,
                         "title": wa.subject_label or wa.get_kind_display(),
                         "sub": wa.get_kind_display(), "institution": wa.institution,
                         "url": _url("assignments:my_work") if offered else url,
                         "inst_url": _inst_url(wa.institution) if wa.institution_id else "",
                         "due": wa.due_at, "overdue": bool(wa.due_at and wa.due_at < now),
                         "tone": "gold" if offered else "blue", "rank": 2 if offered else 4,
                         "when": wa.assigned_at})
    except Exception:
        pass

    # 5. Linear onboarding: assign / clear / approve (directors) and confirm (ES).
    # The officer's assessment is already listed above as an open assignment.
    try:
        from accreditation import journey

        for a in journey.next_actions(user):
            if a.get("kind") == "assess":
                continue
            rows.append({"kind": "approval" if a["kind"] in ("approve", "confirm") else
                         ("doc_assign" if a["kind"] == "assign" else "doc_decide"),
                         "group": "Onboarding", "verb": a["button_label"], "title": a["title"],
                         "sub": a["detail"], "institution": a["institution"], "url": a["url"],
                         "inst_url": _inst_url(a["institution"]), "due": a["due"], "overdue": a["overdue"],
                         "tone": "gold" if a["kind"] in ("approve", "confirm") else "blue",
                         "rank": 1 if a["kind"] in ("approve", "confirm") else 2, "when": a.get("since")})
    except Exception:
        pass

    rows.sort(key=lambda r: (not r["overdue"], r["rank"], r["due"] or now + datetime.timedelta(days=3650)))
    for r in rows:
        r["icon"] = ICONS.get(r["kind"], "•")
        r["fkey"] = FILTER_OF.get(r["kind"], "other")
    return rows[:limit]


# v2.3: filter pills on "Your work" (key, label); a row's key comes from its kind.
FILTERS = [("decide", "Decide"), ("assign", "Assign"), ("review", "Review"),
           ("sop03", "SOP-03"), ("other", "Other")]
FILTER_OF = {"doc_decide": "decide", "doc_assign": "assign", "doc_analyse": "review",
             "ssq": "review", "case": "sop03", "work": "other", "payment": "other", "approval": "decide"}


def summary(rows):
    """Counts for the header chips."""
    out = {"total": len(rows), "overdue": sum(1 for r in rows if r["overdue"]),
           "approve": sum(1 for r in rows if r["kind"] == "approval"),
           "decide": sum(1 for r in rows if r["kind"] == "doc_decide"),
           "bulk": [r["bulk_id"] for r in rows if r.get("bulk_id")]}
    counts = {}
    for r in rows:
        key = r.get("fkey") or FILTER_OF.get(r.get("kind"), "other")
        counts[key] = counts.get(key, 0) + 1
    out["filters"] = [{"key": k, "label": lbl, "count": counts[k]} for k, lbl in FILTERS if counts.get(k)]
    return out


def context(user):
    """{"work_rows", "work_summary"} for templates (never raises)."""
    try:
        rows = inbox(user)
    except Exception:  # pragma: no cover - the dashboard must render regardless
        rows = []
    return {"work_rows": rows, "work_summary": summary(rows)}
