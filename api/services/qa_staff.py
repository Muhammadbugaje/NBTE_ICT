"""QA workspace for NBTE staff (mobile API, staff door) — data shaping only.

Every rule comes from the website's own modules:

* who sees which section      qa_core.context_processors.qa_nav (the sidebar),
                              qa_core.permissions, accreditation.case_services
                              .can_view_cases, complaints.views.can_open,
                              accreditation.qa_views.can_view_qa_requests
* cases                       accreditation.case_services (visible_cases,
                              my_stage_links, stage_actions, stepper, act …)
* confirmations               qa_core.actions (visible_to, can_decide, review,
                              confirm, reject, withdraw)
* finance queue               payments.services (queue_items, item_rights,
                              first_pass, second_pass, final_decision)
* institutions                institutions.services + Institution.objects.for_user
* applications (clearance)    accreditation.services (clearance_rows,
                              can_decide, documents_for, decide)
* QA requests / SSQ           accreditation.qa_services
* complaints                  complaints.services (queue_rows, detail_rights,
                              add_message, act, escalate_manually)
* inspections                 inspection.services.visible_inspections / team

This module turns those objects into plain dicts for the serializers in
``api/serializers_qa_staff.py``.
"""
from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from .home import money

# Restricted files the QA endpoints link to; ``qa/files/<label>/<pk>/``
# serves only these, after the row's own ``can_download(user)``.
FILE_MODELS = (
    "accreditation.evidence", "accreditation.outcomelettercopy", "accreditation.response",
    "complaints.complaintattachment", "institutions.institutiondocument", "payments.paymentadvice",
    "payments.proofofpayment", "tasks.taskattachment",
)


# ---------------------------------------------------------------------------
# Small shapes
# ---------------------------------------------------------------------------
def dt(value):
    return timezone.localtime(value).isoformat() if value else None


def day(value):
    return value.isoformat() if value else None


def person(user):
    if user is None:
        return None
    return {"id": user.pk, "name": user.get_full_name() or user.email}


def inst_ref(inst):
    if inst is None:
        return None
    return {"id": inst.pk, "code": inst.code, "name": inst.name}


def dept_ref(dept):
    if dept is None:
        return None
    return {"id": dept.pk, "name": dept.name}


def file_dict(obj, request, *, name=None):
    label = obj._meta.label_lower
    url = reverse("api:qa_file", args=[label, obj.pk])
    size = getattr(obj, "size_bytes", None)
    if size is None:
        try:
            size = obj.file.size
        except Exception:
            size = 0
    display = name or getattr(obj, "display_name", None) or getattr(obj, "original_name", "") \
        or obj.file.name.rsplit("/", 1)[-1]
    return {"id": obj.pk, "kind": label, "name": display, "size": size or 0,
            "download_url": request.build_absolute_uri(url) if request is not None else url}


def money_pair(value, prefix="amount"):
    amount, display = money(value or 0)
    return {prefix: amount, f"{prefix}_display": display}


def flow_events(instance):
    from qa_core import flow

    return [{"at": dt(e.at), "action": e.action_code, "from_stage": e.from_stage or None, "to_stage": e.to_stage,
             "remark": e.remark, "actor": person(e.actor)} for e in flow.history(instance)]


# ===========================================================================
# Summary (the QA home)
# ===========================================================================
def sections(user):
    """Which QA areas ``user`` may open (the same checks as the sidebar)."""
    from accreditation import case_services as cs
    from accreditation.qa_views import can_view_qa_requests
    from complaints.views import can_open as can_open_complaints
    from qa_core.permissions import can_view_applications, can_view_finance, can_view_institutions, can_view_pending

    return {"cases": cs.can_view_cases(user), "confirmations": can_view_pending(user),
            "finance": can_view_finance(user), "institutions": can_view_institutions(user),
            "applications": can_view_applications(user), "requests": can_view_qa_requests(user),
            "complaints": can_open_complaints(user), "inspections": cs.can_view_cases(user)}


def my_inspections(user):
    """Visits ``user`` leads or is on the team of (inside their visible set)."""
    from django.db.models import Q

    from inspection import services as insp

    return insp.visible_inspections(user).filter(Q(team_lead=user) | Q(team__staff__user=user)).distinct()


def summary(user, request):
    from accreditation import case_services as cs
    from qa_core.context_processors import qa_nav

    nav = qa_nav(request)["qa_nav"]  # the sidebar's own numbers (one build)
    access = sections(user)
    now = timezone.now()
    counts = {k: None for k in ("my_stages", "my_stages_overdue", "confirmations_waiting", "finance_queue",
                                "finance_ready_for_final", "complaints_assigned", "applications_to_clear",
                                "ssq_waiting", "inspections_upcoming", "my_work", "my_work_overdue")}
    if any(access.values()):  # v2.1 N1: the same numbers as the web QA home
        from qa_core.home import my_work_counts

        work = my_work_counts(user)
        counts["my_work"], counts["my_work_overdue"] = work["open"], work["overdue"]
    if access["cases"]:
        links = cs.my_stage_links(user)
        counts["my_stages"] = len(links)
        counts["my_stages_overdue"] = sum(1 for link in links if link.due_at and link.due_at < now)
        counts["inspections_upcoming"] = my_inspections(user).filter(
            scheduled_end__gte=timezone.localdate()).exclude(status="cancelled").count()
    if nav.get("pending") or nav.get("institutions"):
        counts["confirmations_waiting"] = nav.get("awaiting", 0)
    if access["finance"]:
        from payments import services as pay

        counts["finance_queue"] = nav.get("finance_open", 0)
        if pay.is_final_approver(user):
            counts["finance_ready_for_final"] = sum(1 for item in pay.queue_items(user) if item.ready)
    if access["complaints"]:
        counts["complaints_assigned"] = nav.get("complaints_mine", 0)
    if access["applications"]:
        counts["applications_to_clear"] = nav.get("clearance_waiting", 0)
    if access["requests"]:
        counts["ssq_waiting"] = nav.get("ssq_waiting", 0)
    return {"sections": access, "counts": counts}


# ===========================================================================
# Cases
# ===========================================================================
def case_row(case):
    """``case`` decorated by case_services.decorate_cases."""
    return {"id": case.pk, "reference": case.reference, "institution": inst_ref(case.institution),
            "owning_department": dept_ref(case.owning_department), "status": case.status,
            "stage_label": case.stage_label, "stage_no": case.stage_no,
            "holders": [person(u) for u in case.holders], "due_at": dt(case.due), "overdue": case.overdue,
            "needs_reassignment": case.needs_reassignment, "programme_count": case.programme_count,
            "updated_at": dt(case.updated_at)}


def filter_case_list(user, *, q="", stage="", overdue=False, institution="", department="", mine=False):
    from accreditation import case_services as cs

    qs = cs.filter_cases(cs.visible_cases(user), q=q, department=department)
    if institution:
        qs = qs.filter(institution__code__iexact=institution)
    if stage:
        groups = {key: codes for key, _label, codes in cs.STAGE_GROUPS}
        if stage == "new":
            qs = qs.filter(status__in=("", "awaiting_sop03"))
        elif stage in groups:
            qs = qs.filter(status__in=groups[stage])
        else:
            qs = qs.filter(status=stage)
    cases = cs.decorate_cases(list(qs[:600]))
    if mine:
        cases = [c for c in cases if user in c.holders]
    if overdue:
        cases = [c for c in cases if c.overdue]
    return cases


def stage_link_row(link):
    case = link.case
    return {"link_id": link.pk, "case": {"id": case.pk, "reference": case.reference,
                                         "institution": inst_ref(case.institution)},
            "stage_code": link.stage_code, "stage_label": link.stage_label, "stage_no": link.stage_no,
            "task": {"id": link.task_id, "reference": link.task.reference},
            "opened_at": dt(link.opened_at), "due_at": dt(link.due_at), "overdue": link.overdue}


def case_documents(case, user, request):
    """Files on the case's stage tasks and its outcome letters that ``user``
    may download (each row's own can_download)."""
    from accreditation import case_services as cs
    from accreditation import letters
    from tasks.models import TaskAttachment

    out = []
    links = {link.task_id: link for link in cs.case_links(case)}
    for att in TaskAttachment.objects.filter(task_id__in=list(links)).select_related("uploaded_by", "task"):
        if att.can_download(user):
            link = links[att.task_id]
            out.append({**file_dict(att, request), "source": "stage_task", "stage_code": link.stage_code,
                        "description": att.description, "uploaded_by": person(att.uploaded_by),
                        "uploaded_at": dt(att.created_at)})
    for letter in letters.letters_of_case(case):
        for copy in letter.copies.all():
            if copy.can_download(user):
                out.append({**file_dict(copy, request, name=f"{letter.reference} ({copy.get_kind_display()})"),
                            "source": "outcome_letter", "stage_code": None,
                            "description": f"{letter.programme} · {letter.decision.get_outcome_display()}"
                                           + ("" if letter.is_current else " · replaced"),
                            "uploaded_by": person(letter.generated_by), "uploaded_at": dt(letter.generated_at)})
    return out


def case_detail(case, user, request):
    from accreditation import case_services as cs
    from accreditation import decisions
    from accreditation.models import QARequestProgramme

    current = cs.stage_objects(case)
    labels = {s.code: s for s in cs.flow_stages(case)}
    stage = labels.get(case.status)
    monitor = cs.is_monitor(user)
    stage_rows = []
    for row in cs.stage_actions(case, user, current):
        link = row["link"]
        can = row["can"] and not monitor
        stage_rows.append({
            "code": row["stage"].code, "label": row["stage"].label, "stage_no": row["stage"].sop_stage_no,
            "holders": [person(u) for u in row["holders"]],
            "due_at": dt(link.due_at) if link else None,
            "overdue": bool(link and link.due_at and link.due_at < timezone.now()),
            "can_act": can, "why_not": "" if can else (row["why"] or "Read-only access."),
            "problems": row["problems"], "approve_label": row["approve_label"],
            "can_return": can and row["returns"], "can_attach": can and link is not None,
            "web_tab": cs.DATA_STAGES.get(row["stage"].code, "overview"),
        })
    bundle = cs.case_bundle(case)
    sheet = bundle["sheet"]
    visit = bundle["visit"]
    programmes = (QARequestProgramme.unscoped.filter(qa_request=case.qa_request)
                  .select_related("programme__level", "programme__category").order_by("status", "programme__name"))
    timeline = cs.case_timeline(case, user)[:60]
    return {
        "id": case.pk, "reference": case.reference, "status": case.status,
        "stage_label": stage.label if stage else ("Not started" if case.status in ("", "awaiting_sop03")
                                                  else case.status.replace("_", " ").capitalize()),
        "stage_no": stage.sop_stage_no if stage else None,
        "started": cs.instance_of(case) is not None, "read_only": monitor,
        "institution": inst_ref(case.institution), "owning_department": dept_ref(case.owning_department),
        "qa_request": {"id": case.qa_request_id, "reference": case.qa_request.reference,
                       "qa_type": case.qa_request.qa_type.name},
        "programme_count": case.programme_count, "needs_reassignment": case.needs_reassignment,
        "started_at": dt(case.started_at), "completed_at": dt(case.completed_at),
        "programmes": [{"id": r.pk, "programme_id": r.programme_id, "name": r.programme.name,
                        "level": r.programme.level.name if r.programme.level_id else None,
                        "status": r.status, "is_new_programme": r.is_new_programme} for r in programmes],
        "progress": cs.stepper(case, current),
        "current_stages": stage_rows,
        "team": {
            "desk_officer": person(case.desk_officer),
            "supervising_audit_officer": person(case.supervising_audit_officer),
            "supervising_finance_officer": person(case.supervising_finance_officer),
            "tracking_ict_officers": [person(u) for u in bundle["tracking_ict"]],
            "personnel": [{"id": e.pk, "name": str(e.staff), "duty": e.duty, "duty_label": e.get_duty_display(),
                           "status": e.status} for e in bundle["entries"]],
        },
        "visit": None if visit is None else {
            "planned_start": day(visit.planned_start), "planned_end": day(visit.planned_end),
            "team_lead": str(visit.team_lead) if visit.team_lead_id else None, "status": visit.status},
        "cost": None if sheet is None else {"version": sheet.version, "status": sheet.status,
                                            **money_pair(sheet.total, "total")},
        "decisions": [{"id": d.pk, "programme": str(d.programme), "stage": d.stage, "stage_label": d.get_stage_display(),
                       "outcome": d.outcome, "outcome_label": d.get_outcome_display(),
                       "approval_type": d.approval_type.name if d.approval_type_id else None,
                       "approved_streams": d.approved_streams, "commencement_date": day(d.commencement_date),
                       "expiry_date": day(d.expiry_date), "note": d.note, "decided_by": person(d.decided_by),
                       "decided_at": dt(d.decided_at)} for d in decisions.all_decisions(case)],
        "documents": case_documents(case, user, request),
        "timeline": [{"at": dt(r["at"]), "kind": r["kind"], "title": r["title"], "detail": r["detail"],
                      "remark": r["remark"], "actor": person(r["actor"])} for r in timeline],
    }


# ===========================================================================
# Confirmations (qa_core PendingAction)
# ===========================================================================
CONFIRMATION_TABS = {
    "pending": {"status": "pending"}, "started_by_me": {"mine": True}, "expired": {"status": "expired"},
    "applied": {"status": "applied"}, "rejected": {"status": "rejected"}, "failed": {"status": "failed"},
    "withdrawn": {"status": "withdrawn"}, "all": {},
}


def confirmation_row(pa, user):
    from qa_core import actions

    can, why = actions.can_decide(pa, user)
    return {"id": pa.pk, "reference": pa.reference, "action": {"code": pa.policy_id, "label": pa.policy.label},
            "target_label": pa.target_label, "reason": pa.reason, "status": pa.status,
            "status_label": pa.get_status_display(), "initiated_by": person(pa.initiated_by),
            "initiated_at": dt(pa.initiated_at), "expires_at": dt(pa.expires_at), "overdue": pa.is_overdue,
            "department": dept_ref(pa.scope_department), "decided_by": person(pa.decided_by),
            "decided_at": dt(pa.decided_at), "can_decide": can, "decide_reason": why,
            # X47: which layer it waits for, and the trail (same as the website)
            "stage": pa.stage, "layers": actions.layers_for(pa.policy), "confirmed_by": person(pa.confirmed_by),
            "confirmed_at": dt(pa.confirmed_at),
            "trail": [{"at": dt(when), "by": person(who), "what": what} for when, who, what in actions.trail(pa)]}


def confirmation_list(user, *, tab="pending", action="", q=""):
    from django.db.models import Q

    from qa_core import actions

    qs = actions.visible_to(user)
    spec = CONFIRMATION_TABS.get(tab, CONFIRMATION_TABS["pending"])
    if spec.get("status"):
        qs = qs.filter(status=spec["status"])
    if spec.get("mine"):
        qs = qs.filter(initiated_by=user)
    if action:
        qs = qs.filter(policy_id=action)
    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(target_label__icontains=q) | Q(reason__icontains=q)
                       | Q(initiated_by__email__icontains=q) | Q(initiated_by__first_name__icontains=q)
                       | Q(initiated_by__last_name__icontains=q))
    return qs.order_by("expires_at" if tab == "pending" else "-initiated_at", "-id")


def confirmation_counts(user):
    from qa_core import actions
    from qa_core.models import PendingAction

    visible = actions.visible_to(user)
    return {"pending": visible.filter(status=PendingAction.PENDING).count(),
            "mine": visible.filter(status=PendingAction.PENDING, initiated_by=user).count(),
            "overdue": visible.filter(status=PendingAction.PENDING, expires_at__lte=timezone.now()).count()}


def confirmation_detail(pa, user):
    from core.models import AuditLogEntry
    from qa_core import actions

    data = actions.review(pa)
    history = AuditLogEntry.objects.filter(model_label="qa_core.PendingAction", object_pk=str(pa.pk)) \
        .select_related("actor").order_by("timestamp")[:30]
    return {
        **confirmation_row(pa, user),
        "diff": [{"label": r.label, "before": str(r.before), "after": str(r.after), "changed": r.changed}
                 for r in data["diff"]],
        "stale": data["stale"],
        "confirm_options": [{"name": o.get("name"), "label": o.get("label", o.get("name")),
                             "help": o.get("help", ""), "default": bool(o.get("default", False))}
                            for o in data["confirm_options"]],
        "decision_remark": pa.decision_remark, "failure_detail": pa.failure_detail,
        "applied_at": dt(pa.applied_at),
        "can_withdraw": pa.is_open and pa.initiated_by_id == user.pk,
        "history": [{"at": dt(h.timestamp), "label": (h.note or h.get_action_display()).capitalize(),
                     "actor": person(h.actor)} for h in history],
    }


# ===========================================================================
# Finance queue
# ===========================================================================
def finance_row(item):
    ob = item.obligation
    return {"id": item.pk, "obligation_id": ob.pk, "reference": ob.reference, "institution": inst_ref(ob.institution),
            "description": ob.description, **money_pair(ob.balance, "balance"),
            "source": item.source, "source_label": item.get_source_display(),
            "opened_at": dt(item.opened_at), "sla_due_at": dt(item.sla_due_at), "sla_pct": item.sla_pct,
            "overdue": bool(item.overdue), "decision": item.decision or None,
            "decision_label": item.get_decision_display() if item.decision else None,
            "second_decision": item.second_decision or None, "needs_second_check": bool(item.needs_second),
            "ready_for_final": item.ready, "ready_why": item.ready_why}


def finance_hero(user):
    from payments import services as pay
    from payments.models import PaymentObligation, PaymentVerification

    open_items = PaymentVerification.objects.for_user(user).filter(is_open=True)
    return {"first_pass": open_items.filter(decision="").count(),
            "ready_for_final": sum(1 for i in open_items.exclude(decision="").select_related("obligation")
                                   if pay.ready_for_final(i)[0]),
            "overdue": PaymentObligation.objects.for_user(user).filter(status="finance_confirmation_overdue").count(),
            "role": "director" if pay.is_final_approver(user) else
            ("officer" if user.has_perm("payments.verify_payment") else "viewer")}


def _pass(decision, label, by, at, reason, **extra):
    if not decision:
        return {"decision": None, "decision_label": None, "by": None, "at": None, "reason": "", **extra}
    return {"decision": decision, "decision_label": label, "by": person(by), "at": dt(at), "reason": reason, **extra}


def finance_detail(item, user, request):
    from payments import services as pay
    from payments.models import BankStatementLine, PaymentAdvice, PaymentAttempt, ProofOfPayment

    ob = item.obligation
    proofs = ProofOfPayment.objects.for_user(user).filter(obligation=ob).select_related("uploaded_by") \
        .order_by("-created_at")
    attempts = PaymentAttempt.objects.for_user(user).filter(obligation=ob).order_by("-created_at")
    lines = BankStatementLine.objects.filter(matched_obligation=ob).select_related("statement")
    ready, why = pay.ready_for_final(item)
    needs_second = pay.needs_second_check(item)
    rights = pay.item_rights(item, user)
    return {
        "id": item.pk, "source": item.source, "source_label": item.get_source_display(), "is_open": item.is_open,
        "institution": inst_ref(ob.institution),
        "bill": {"id": ob.pk, "reference": ob.reference, "description": ob.description,
                 "for_reference": ob.source_reference or None, "status": ob.status, "stage_label": pay.stage_label(ob),
                 **money_pair(ob.amount, "amount"), **money_pair(ob.balance, "balance"),
                 **money_pair(ob.amount_paid, "amount_paid"), "issued_at": dt(ob.created_at), "due_at": dt(ob.due_at),
                 "formula": {"expr": str((ob.formula_snapshot or {}).get("expr", "")),
                             "result": str((ob.formula_snapshot or {}).get("result", ""))}},
        "proofs": [{"id": p.pk, "file": file_dict(p, request) if p.file and p.can_download(user) else None,
                    "kind": p.kind, "kind_label": p.get_kind_display(), **money_pair(p.amount_claimed, "amount_claimed"),
                    "paid_on": day(p.paid_on), "bank_name": p.bank_name, "teller_number": p.teller_number,
                    "uploaded_by": person(p.uploaded_by), "uploaded_at": dt(p.created_at),
                    "same_file_as_another_bill": p.fraud_flag} for p in proofs],
        "provider_reports": [{"id": a.pk, "provider": a.provider, "provider_label": a.get_provider_display(),
                              "provider_reference": a.provider_reference, "status": a.status,
                              "status_label": a.get_status_display(), **money_pair(a.amount, "amount"),
                              "verified_paid": bool((a.verify_result or {}).get("paid")),
                              "verified_amount": str((a.verify_result or {}).get("amount") or "") or None,
                              "signature_checked": a.webhook_verified}
                             for a in attempts if a.status == "provider_reported_paid" or a.verify_result],
        "statement_lines": [{"id": line.pk, "statement_id": line.statement_id, "row_number": line.row_number,
                             "value_date": day(line.value_date), **money_pair(line.amount, "amount"),
                             "narration": line.narration, "match_status": line.match_status}
                            for line in lines],
        "first_pass": _pass(item.decision, item.get_decision_display() if item.decision else None,
                            item.verified_by, item.verified_at, item.reason,
                            amount_confirmed=money(item.amount_confirmed)[0] if item.amount_confirmed is not None
                            else None),
        "second_check": _pass(item.second_decision,
                              item.get_second_decision_display() if item.second_decision else None,
                              item.second_verifier, item.second_verified_at, item.second_reason)
        if needs_second else None,
        "final": _pass(item.final_decision, item.get_final_decision_display() if item.final_decision else None,
                       item.final_approver, item.final_decided_at, item.final_reason),
        "needs_second_check": needs_second,
        # v2.1 F.4 / F.18
        "verified_via": item.verified_via, "auto_first_pass": bool(item.auto_first_pass),
        "provider_amount": money(item.provider_amount)[0] if item.provider_amount is not None else None,
        "amount_matches": item.amount_matches,
        "approvals_required": pay.approvals_required(item), "approvals_have": len(pay.approvals(item)),
        "dual_verify_threshold_display": money(pay._decimal("qa.payment.dual_verify_threshold", "5000000"))[1],
        "ready_for_final": ready, "ready_why": why,
        "rights": rights,
        "advices": [{**file_dict(a, request), "reference": a.reference}
                    for a in PaymentAdvice.objects.for_user(user).filter(obligation=ob) if a.can_download(user)],
        "history": flow_events(pay.instance_of(ob)),
    }


# ===========================================================================
# Institutions
# ===========================================================================
def institution_row(inst):
    return {"id": inst.pk, "code": inst.code, "name": inst.name, "status": inst.status,
            "status_label": str(inst.status_label),
            "ownership": inst.ownership.name if inst.ownership_id else None,
            "type": inst.institution_type.name if inst.institution_type_id else None,
            "state": inst.state.name if inst.state_id else None, "lga": inst.lga, "zone": inst.zone,
            "handling_department": dept_ref(inst.handling_department)}


def institution_detail(inst, user, request):
    from accreditation import case_services as cs
    from institutions import services as isvc
    from institutions.models import Programme

    programmes = Programme.objects.for_user(user).filter(institution=inst).select_related("level", "category") \
        .order_by("name")
    cases = cs.decorate_cases(list(cs.visible_cases(user).filter(institution=inst).exclude(status="closed")
                                   .select_related("institution", "owning_department")))
    bills = None
    if isvc.can_see_payments(user):
        from payments.models import PaymentObligation
        from payments.services import stage_label

        bills = [{"id": ob.pk, "reference": ob.reference, "description": ob.description, "status": ob.status,
                  "stage_label": stage_label(ob), **money_pair(ob.amount, "amount"),
                  **money_pair(ob.balance, "balance"), "due_at": dt(ob.due_at), "issued_at": dt(ob.created_at)}
                 for ob in PaymentObligation.objects.for_user(user).filter(institution=inst).order_by("-created_at")]
    documents = []
    for row in isvc.checklist(inst):
        doc = row["document"]
        documents.append({"kind": row["kind"].name, "mandatory": bool(row.get("mandatory")), "state": row["state"],
                          "department": row["department"].name if row.get("department") else None,
                          "file": file_dict(doc, request) if doc is not None and doc.file and doc.can_download(user)
                          else None})
    from qa_core import actions
    from django.contrib.contenttypes.models import ContentType

    from institutions.models import Institution

    pending = actions.visible_to(user).filter(content_type=ContentType.objects.get_for_model(Institution),
                                              object_id=str(inst.pk), status="pending").count()
    primary = inst.primary_user()
    return {
        **institution_row(inst),
        "former_names": list(inst.former_names or []), "year_established": inst.year_established,
        "website": inst.website or None, "portal_enabled": inst.portal_enabled,
        "category": inst.category.name if inst.category_id else None,
        "primary_user": person(primary) if primary is not None else None,
        "contacts": [{"kind": c.kind, "kind_label": c.get_kind_display(), "value": c.value, "name": c.name,
                      "is_primary": c.is_primary} for c in isvc.contacts_of(inst)],
        "addresses": [{"kind": a.kind, "kind_label": a.get_kind_display(), "address": str(a),
                       "town": a.town, "lga": a.lga, "state": a.state.name if a.state_id else None, "gps": a.gps or None}
                      for a in isvc.addresses_of(inst)],
        "programmes": [{"id": p.pk, "name": p.name, "level": p.level.name if p.level_id else None,
                        "category": p.category.name if p.category_id else None, "status": p.status,
                        "status_label": p.get_status_display(), "approved_streams": p.approved_streams,
                        "commencement_date": day(p.commencement_date), "expiry_date": day(p.expiry_date)}
                       for p in programmes],
        "open_cases": [case_row(c) for c in cases],
        "bills": bills,
        "documents": documents,
        "pending_changes": pending,
        "timeline": [{"at": dt(t.at), "event": t.event_code, "visibility": t.visibility,
                      "text": (t.public_text or t.internal_text) if t.visibility == "public"
                      else (t.internal_text or t.public_text), "actor": person(t.actor)}
                     for t in isvc.timeline_for(user, inst)[:30]],
    }


# ===========================================================================
# Applications (departmental clearance)
# ===========================================================================
def clearance_row(c, user):
    from accreditation import services as asvc

    ok, why = asvc.can_decide(c, user)
    app = c.application
    return {"id": c.pk, "department": dept_ref(c.department), "status": c.status,
            "status_label": c.get_status_display(), "round": c.round, "opened_at": dt(c.opened_at),
            "waiting_days": (timezone.now() - c.opened_at).days, "remark": c.remark,
            "decided_by": person(c.decided_by), "decided_at": dt(c.decided_at),
            "application": {"id": app.pk, "reference": app.reference, "status": app.status,
                            "public_status": str(asvc.public_status(app)), "round": app.round,
                            "submitted_at": dt(app.submitted_at), "institution": inst_ref(app.institution)},
            "can_decide": ok, "decide_reason": why}


def clearance_detail(c, user, request):
    from accreditation import services as asvc
    from institutions import services as isvc

    app = c.application
    rows = asvc.documents_for(c)
    return {
        **clearance_row(c, user),
        "documents": [{"kind": r["kind"].name, "mandatory": bool(r.get("mandatory")), "state": r["state"],
                       "file": file_dict(r["document"], request)
                       if r["document"] is not None and r["document"].file and r["document"].can_download(user)
                       else None} for r in rows],
        "contacts": [{"kind": x.kind, "kind_label": x.get_kind_display(), "value": x.value, "name": x.name}
                     for x in isvc.contacts_of(app.institution)],
        "other_departments": [{"department": dept_ref(o.department), "status": o.status,
                               "status_label": o.get_status_display()}
                              for o in asvc.clearances_for_round(app) if o.pk != c.pk],
        "history": flow_events(asvc.instance_of(app)),
    }


# ===========================================================================
# QA requests and SSQs
# ===========================================================================
def request_row(req, user):
    from accreditation import qa_services as svc
    from accreditation.models import QARequestProgramme

    return {"id": req.pk, "reference": req.reference, "institution": inst_ref(req.institution),
            "qa_type": {"code": req.qa_type.code, "name": req.qa_type.name}, "status": req.status,
            "public_status": str(svc.public_status(req)),
            "programme_count": QARequestProgramme.objects.for_user(user).filter(qa_request=req, status="active").count(),
            "fee_note": req.fee_note, "created_at": dt(req.created_at), "submitted_at": dt(req.submitted_at)}


def request_detail(req, user):
    from accreditation import qa_services as svc
    from accreditation import ssq as engine
    from accreditation.models import QACase, QARequestProgramme, Query, SSQ
    from django.contrib.contenttypes.models import ContentType
    from payments.models import PaymentObligation
    from payments.services import stage_label

    rows = list(QARequestProgramme.objects.for_user(user).filter(qa_request=req)
                .select_related("programme__level").order_by("status", "programme__name"))
    ssqs = []
    for row in rows:
        if row.status != "active":
            continue
        cur = SSQ.objects.for_user(user).filter(qa_request_programme=row, is_current=True).select_related(
            "template").first()
        if cur is None:
            continue
        progress = engine.section_progress(cur.template, cur.data or {}, svc.evidence_counts(cur))
        ssqs.append({"id": cur.pk, "programme": row.programme.name, "status": cur.status,
                     "public_status": str(svc.ssq_public_status(cur)), "version": cur.version,
                     "fields_done": sum(p["filled"] for p in progress), "fields_total": sum(p["total"] for p in progress),
                     "submitted_at": dt(cur.submitted_at)})
    obligations = PaymentObligation.objects.for_user(user).filter(
        source_type=ContentType.objects.get_for_model(type(req)), source_id=str(req.pk)).order_by("-created_at")
    case = QACase.objects.for_user(user).filter(qa_request=req).first()
    can_clear, why = svc.can_clear(req, user)
    return {
        **request_row(req, user),
        "programmes": [{"id": r.pk, "programme_id": r.programme_id, "name": r.programme.name,
                        "level": r.programme.level.name if r.programme.level_id else None, "status": r.status,
                        "is_new_programme": r.is_new_programme, "clearance_status": r.clearance_status or None,
                        "clearance_remark": r.clearance_remark, "requested_streams": r.requested_streams}
                       for r in rows],
        "ssqs": ssqs,
        "bills": [{"id": ob.pk, "reference": ob.reference, "description": ob.description, "status": ob.status,
                   "stage_label": stage_label(ob), **money_pair(ob.amount, "amount"),
                   **money_pair(ob.balance, "balance"), "due_at": dt(ob.due_at), "issued_at": dt(ob.created_at)}
                  for ob in obligations],
        "queries": [{"id": q.pk, "reference": q.reference, "title": q.title, "status": q.status,
                     "due_at": dt(q.due_at), "created_at": dt(q.created_at)}
                    for q in Query.objects.for_user(user).filter(qa_request=req).order_by("-created_at")],
        "case": {"id": case.pk, "reference": case.reference} if case is not None else None,
        "can_clear": can_clear, "clear_reason": why,
        "history": flow_events(svc.request_instance(req)),
    }


def ssq_row(ssq):
    from accreditation import qa_services as svc

    rp = ssq.qa_request_programme
    req = rp.qa_request
    return {"id": ssq.pk, "request": {"id": req.pk, "reference": req.reference},
            "institution": inst_ref(req.institution), "programme": rp.programme.name,
            "level": rp.programme.level.name if rp.programme.level_id else None,
            "qa_type": req.qa_type.name, "status": ssq.status, "public_status": str(svc.ssq_public_status(ssq)),
            "version": ssq.version, "is_current": ssq.is_current, "submitted_at": dt(ssq.submitted_at),
            "waiting_days": (timezone.now() - ssq.submitted_at).days if ssq.submitted_at else None}


def ssq_detail(ssq, user, request):
    from accreditation import qa_services as svc
    from accreditation import ssq as engine
    from accreditation.models import Evidence, SSQ, Submission

    submission = Submission.objects.for_user(user).filter(ssq=ssq).order_by("-version").first()
    sections = submission.rendered_snapshot if submission is not None else engine.render_snapshot(
        ssq.template, ssq.data or {}, programme=ssq.qa_request_programme.programme)
    evidence = Evidence.objects.for_user(user).filter(ssq=ssq).exclude(status="removed")
    ok, why = svc.can_review(ssq, user)
    return {
        **ssq_row(ssq),
        "sections": sections,
        "evidence": [{"field_key": ev.field_key, "status": ev.status,
                      "file": file_dict(ev, request) if ev.file and ev.can_download(user) else None}
                     for ev in evidence],
        "versions": [{"id": v.pk, "version": v.version, "status": v.status, "is_current": v.is_current}
                     for v in SSQ.objects.for_user(user).filter(qa_request_programme=ssq.qa_request_programme)
                     .order_by("-version")],
        "submission_checksum_ok": submission.verify() if submission is not None else None,
        "review_remark": ssq.review_remark, "reviewed_by": person(ssq.reviewed_by), "reviewed_at": dt(ssq.reviewed_at),
        "can_review": ok, "review_reason": why,
        "history": flow_events(svc.ssq_instance(ssq)),
    }


# ===========================================================================
# Complaints
# ===========================================================================
def complaint_row(c):
    sla = getattr(c, "sla", None) or {"state": "none"}
    return {"id": c.pk, "reference": c.reference, "subject": c.subject,
            "layer": {"code": c.layer.code, "name": c.layer.name}, "priority": c.priority,
            "priority_label": c.get_priority_display(), "status": c.status,
            "status_label": (c.status or "").replace("_", " ").capitalize(), "is_open": c.is_open,
            "institution": inst_ref(c.institution), "raised_by": person(c.raised_by), "raiser_type": c.raiser_type,
            "current_department": dept_ref(c.current_department), "current_assignee": person(c.current_assignee),
            "assigned_officer": person(c.assigned_officer),
            "sla": {"state": sla.get("state", "none"), "pct": sla.get("pct", 0), "left": sla.get("left", ""),
                    "due": dt(sla.get("due"))},
            "created_at": dt(c.created_at)}


def complaint_message(m, request):
    return {"id": m.pk, "author": person(m.author), "body": m.body, "visibility": m.visibility,
            "is_system": m.is_system, "created_at": dt(m.created_at),
            "attachments": [file_dict(a, request) for a in m.attachments.all()]}


def complaint_detail(c, user, request):
    from complaints import services as csvc
    from complaints.models import Escalation

    csvc.decorate([c])
    rights = csvc.detail_rights(user, c)
    stages = c.stages.select_related("assigned_department", "assigned_to").order_by("entered_at", "id")
    actions = [{"code": t.action_code, "label": t.label, "requires_remark": t.requires_remark,
                "needs_referral_target": t.action_code == "refer",
                "needs_officer": t.action_code == "assign"} for t in rights["actions"]]
    return {
        **complaint_row(c),
        "body": c.body,
        "answers": [{"label": f.get("label") or f.get("key"), "value": c.answers.get(f.get("key"))}
                    for f in (c.layer.custom_fields or []) if c.answers.get(f.get("key"))],
        "related": {"type": c.related_type.model, "id": c.related_id, "label": c.related_label}
        if c.related_type_id else None,
        "resolution": c.resolution, "resolved_at": dt(c.resolved_at), "closed_at": dt(c.closed_at),
        "reopened_count": c.reopened_count,
        "messages": [complaint_message(m, request) for m in csvc.messages_for(user, c, internal=rights["internal"])],
        "stages": [{"code": s.stage_code, "label": s.stage_label, "department": dept_ref(s.assigned_department),
                    "assigned_to": person(s.assigned_to), "entered_at": dt(s.entered_at), "due_at": dt(s.due_at),
                    "left_at": dt(s.left_at), "breached_at": dt(s.breached_at),
                    "escalation_level": s.escalation_level} for s in stages],
        "watchers": [person(u) for u in c.watchers.all()],
        "escalations": [{"level": e.level, "to": person(e.escalated_to), "by": person(e.by), "reason": e.reason,
                         "at": dt(e.at)} for e in Escalation.objects.filter(complaint_stage__complaint=c)
                        .select_related("escalated_to", "by").order_by("at")],
        "rights": {"can_write": rights["can_write"], "can_write_internal": rights["internal"],
                   "can_act": rights["can_act"], "act_why": rights["act_why"], "actions": actions,
                   "can_escalate": rights["can_escalate"], "read_only": rights["read_only"],
                   "watch_reason": rights["watch_reason"], "is_director": rights["is_director"],
                   "is_officer": rights["is_officer"], "officers": [person(u) for u in rights["officers"]]},
    }


# ===========================================================================
# Inspections
# ===========================================================================
def inspection_row(insp, user):
    from inspection.models import InspectionTeamMember

    role = None
    if insp.team_lead_id == user.pk:
        role = "Team lead"
    else:
        member = InspectionTeamMember.unscoped.filter(inspection=insp, staff__user=user).first()
        if member is not None:
            role = member.role_on_team or "Member"
    case = insp.case
    return {"id": insp.pk, "reference": insp.reference, "kind": insp.kind, "kind_label": insp.get_kind_display(),
            "status": insp.status, "case": {"id": case.pk, "reference": case.reference},
            "institution": inst_ref(case.institution), "scheduled_start": day(insp.scheduled_start),
            "scheduled_end": day(insp.scheduled_end), "actual_start": day(insp.actual_start),
            "actual_end": day(insp.actual_end), "team_lead": person(insp.team_lead), "my_role": role}


def _member_name(m):
    if m.staff_id:
        return str(m.staff)
    mapping = m.qa_mapping
    if mapping is not None:
        if mapping.resource_person_id:
            return str(mapping.resource_person)
        if mapping.qa_personnel_id:
            return str(mapping.qa_personnel.staff)
    return "—"


def inspection_detail(insp, user):
    from inspection import services as svc

    team = svc.team(insp)
    reports = svc.visible_reports(user).filter(inspection=insp).select_related("qa_request_programme__programme")
    return {
        **inspection_row(insp, user),
        "team": [{"name": _member_name(m), "role_on_team": m.role_on_team,
                  "kind": "staff" if m.staff_id else ("resource_person" if m.qa_mapping_id and
                                                      m.qa_mapping.resource_person_id else "qa_personnel"),
                  "programme": m.qa_mapping.qa_request_programme.programme.name
                  if m.qa_mapping_id and m.qa_mapping.qa_request_programme_id else None,
                  "confirmed": m.confirmed_by_division_director} for m in team],
        "attendance": [{"name": str(a.staff or a.qa_personnel or a.resource_person), "role_on_team": a.role_on_team,
                        "duty": a.duty, "planned": a.planned, "attended": a.attended}
                       for a in svc.attendance(insp)],
        "reports": [{"id": r.pk, "programme": r.qa_request_programme.programme.name, "status": r.status}
                    for r in reports],
    }


def upcoming_filter(qs, when):
    today = timezone.localdate()
    if when == "upcoming":
        return qs.filter(scheduled_end__gte=today)
    if when == "past":
        return qs.filter(scheduled_end__lt=today)
    return qs

