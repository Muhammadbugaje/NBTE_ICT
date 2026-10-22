"""Decisions at SOP-03 stages 14–16 and the stage-17 programme update — Phase 8
(Master Plan Phase 6; §5.4 Decision; B18, B20, B23, B24).

* Stage 14 (CQAC recommendation), 15 (CQAC Chairman) and 16 (ES final): one
  ``Decision`` per active programme of the case — outcome (approve / deny /
  return), approval type, approved streams, commencement date and weaknesses.
  Approving the stage needs a decision for every programme
  (``case_services.prerequisites``). Each stage starts from the previous one
  (stage 14 from the Directorate's recommendation at stage 13).
* The Chairman may approve CQAC recommendations in a batch when
  ``qa.cqac.batch_approval_enabled`` is on — only within one programme
  category per batch (B23).
* Stage 17 (``apply_final``): each ES decision is applied to the programme —
  status, approval type, approved streams, commencement date, expiry date
  (commencement + the approval type's duration, never typed) and the date
  re-accreditation opens (expiry − ``qa.programme.reassessment_window_months``)
  — then the outcome letters are issued, the timeline written (public and
  internal) and the institution notified.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

import config
from qa_core import timeline
from qa_core.seed_data_phase8 import NEW_PROGRAMME_APPROVAL, RECOMMENDATION_DEFAULTS

from .models import ApprovalType, Decision, QACase, QARequestProgramme, add_months

STAGE_OF_FLOW = {flow_code: stage for stage, flow_code in Decision.FLOW_STAGE.items()}
PREVIOUS = {Decision.CHAIR: Decision.CQAC, Decision.ES: Decision.CHAIR}
STAGE_PERMISSION = {Decision.CQAC: "accreditation.cqac_review", Decision.CHAIR: "accreditation.cqac_chair_decide",
                    Decision.ES: "accreditation.es_final_decide"}
FINAL_STATUSES = ("accredited", "interim", "approval_to_commence")


def _bool(key, default=False):
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def batch_enabled():
    return _bool("qa.cqac.batch_approval_enabled", True)


def is_decision_stage(stage_code):
    return stage_code in STAGE_OF_FLOW


def rows(case):
    return list(QARequestProgramme.unscoped.filter(qa_request=case.qa_request, status=QARequestProgramme.ACTIVE)
                .select_related("programme__level", "programme__category").order_by("programme__name"))


def decisions_at(case, stage):
    return {d.qa_request_programme_id: d for d in Decision.unscoped.filter(case=case, stage=stage)
            .select_related("approval_type", "decided_by")}


def all_decisions(case):
    return list(Decision.unscoped.filter(case=case).select_related("approval_type", "decided_by", "programme__level")
                .order_by("programme__name", "decided_at"))


def active_types():
    return list(ApprovalType.objects.filter(is_active=True).order_by("sort_order", "name"))


# ===========================================================================
# Suggestions (what the form starts with)
# ===========================================================================
def _report_for(case, row):
    from inspection.models import PostVisitReport

    return (PostVisitReport.unscoped.filter(inspection__case=case, qa_request_programme=row)
            .order_by("-pk").first())


def suggestion(case, row, stage):
    """Initial values for ``row`` at ``stage`` (never saved by itself)."""
    previous = PREVIOUS.get(stage)
    if previous:
        earlier = Decision.unscoped.filter(case=case, qa_request_programme=row, stage=previous) \
            .select_related("approval_type").first()
        if earlier is not None:
            return {"outcome": earlier.outcome, "approval_type": earlier.approval_type,
                    "approved_streams": earlier.approved_streams or row.requested_streams,
                    "commencement_date": earlier.commencement_date, "weaknesses": list(earlier.weaknesses or []),
                    "note": "", "from": earlier}
    outcome, type_code = "approve", "full"
    report = _report_for(case, row)
    if report is not None and report.recommendation in RECOMMENDATION_DEFAULTS:
        outcome, type_code = RECOMMENDATION_DEFAULTS[report.recommendation]
    if outcome == "approve" and (row.is_new_programme or row.programme.status == "proposed"):
        type_code = NEW_PROGRAMME_APPROVAL if type_code == "full" else type_code
    approval_type = ApprovalType.objects.filter(code=type_code, is_active=True).first() if type_code else None
    return {"outcome": outcome, "approval_type": approval_type, "approved_streams": row.requested_streams or 1,
            "commencement_date": timezone.localdate(), "weaknesses": [], "note": "", "from": report}


# ===========================================================================
# Recording
# ===========================================================================
def can_decide(case, user, stage):
    """(ok, reason): the holder of the stage's task with the stage permission."""
    from . import case_services as cs

    ok, why = cs.can_act(case, user, Decision.FLOW_STAGE[stage])
    if not ok:
        return ok, why
    if not (user.is_superuser or user.has_perm(STAGE_PERMISSION[stage])):
        return False, f"You do not hold {STAGE_PERMISSION[stage]}."
    return True, ""


def _clean(row, *, outcome, approval_type, approved_streams, commencement_date, weaknesses, note):
    from .qa_services import max_streams

    if outcome not in dict(Decision.OUTCOME_CHOICES):
        raise ValidationError(f"{row.programme}: choose approve, deny or return.")
    weaknesses = [str(w).strip()[:500] for w in (weaknesses or []) if str(w).strip()][:40]
    if outcome == Decision.APPROVE:
        if approval_type is None or not approval_type.is_active:
            raise ValidationError(f"{row.programme}: choose the approval type.")
        try:
            approved_streams = int(approved_streams)
        except (TypeError, ValueError):
            raise ValidationError(f"{row.programme}: enter the approved streams.") from None
        if not 1 <= approved_streams <= max_streams():
            raise ValidationError(f"{row.programme}: approved streams must be 1 to {max_streams()}.")
        if commencement_date is None:
            raise ValidationError(f"{row.programme}: enter the commencement date.")
    else:
        approval_type, approved_streams, commencement_date = None, 0, None
        if not weaknesses and len((note or "").strip()) < 3:
            raise ValidationError(f"{row.programme}: list the weaknesses (or a note) behind a "
                                  f"{'denial' if outcome == Decision.DENY else 'return'}.")
    return approval_type, approved_streams, commencement_date, weaknesses


@transaction.atomic
def record(case, row, stage, *, user, outcome, approval_type=None, approved_streams=0, commencement_date=None,
           weaknesses=None, note="", batch_key="", check=True):
    if check:
        ok, why = can_decide(case, user, stage)
        if not ok:
            raise PermissionDenied(why)
    if row.qa_request_id != case.qa_request_id or row.status != QARequestProgramme.ACTIVE:
        raise ValidationError("That programme is not active on this case.")
    approval_type, approved_streams, commencement_date, weaknesses = _clean(
        row, outcome=outcome, approval_type=approval_type, approved_streams=approved_streams,
        commencement_date=commencement_date, weaknesses=weaknesses, note=note)
    decision, _created = Decision.unscoped.update_or_create(
        case=case, qa_request_programme=row, stage=stage,
        defaults={"programme": row.programme, "outcome": outcome, "approval_type": approval_type,
                  "approved_streams": approved_streams, "commencement_date": commencement_date,
                  "weaknesses": weaknesses, "note": (note or "").strip(), "batch_key": batch_key,
                  "decided_by": user if getattr(user, "pk", None) else None, "decided_at": timezone.now()})
    timeline.record(case.institution, f"decision.{stage}", subject=decision, actor=user, visibility="internal",
                    internal_text=f"{decision.get_stage_display()}: {row.programme} — {decision.get_outcome_display()}"
                                  + (f", {approval_type.name}, {approved_streams} stream(s) from "
                                     f"{commencement_date:%d %b %Y} (expires {decision.expiry_date:%d %b %Y})"
                                     if decision.outcome == Decision.APPROVE else "")
                                  + (f" [batch {batch_key}]" if batch_key else ""))
    return decision


def problems(case, stage_code):
    """What blocks *approve* at a decision stage (plain language)."""
    stage = STAGE_OF_FLOW.get(stage_code)
    if stage is None:
        return []
    have = decisions_at(case, stage)
    missing = [str(r.programme) for r in rows(case) if r.pk not in have]
    out = []
    if missing:
        out.append(f"Record the decision for: {', '.join(missing)}.")
    returned = [str(d.programme) for d in have.values() if d.outcome == Decision.RETURN]
    if returned:
        out.append(f"{', '.join(returned)} marked “return” — use “Return with remark” instead of approving.")
    return out


def board(case, stage_code, user):
    """Rows for the decision form at ``stage_code``: programme, the saved
    decision (if any), the starting values and the earlier stages' decisions."""
    stage = STAGE_OF_FLOW.get(stage_code)
    if stage is None:
        return []
    saved = decisions_at(case, stage)
    from . import capacity

    history = {}
    for d in Decision.unscoped.filter(case=case).select_related("approval_type", "decided_by").order_by("pk"):
        history.setdefault(d.qa_request_programme_id, []).append(d)
    out = []
    for row in rows(case):
        decision = saved.get(row.pk)
        start = suggestion(case, row, stage) if decision is None else {
            "outcome": decision.outcome, "approval_type": decision.approval_type,
            "approved_streams": decision.approved_streams or row.requested_streams,
            "commencement_date": decision.commencement_date or timezone.localdate(),
            "weaknesses": list(decision.weaknesses or []), "note": decision.note, "from": None}
        report = _report_for(case, row)
        out.append({"row": row, "programme": row.programme, "decision": decision, "start": start,
                    "weaknesses_text": "\n".join(start["weaknesses"]),
                    "history": [d for d in history.get(row.pk, []) if d.stage != stage],
                    "report": report, "capacity": capacity.overall(row)})
    return out


def parse_form(post, row):
    """The decision fields for ``row`` from a POST (prefix d<row pk>-)."""
    p = f"d{row.pk}-"
    approval = ApprovalType.objects.filter(pk=post.get(p + "approval_type") or 0).first()
    try:
        start = datetime.date.fromisoformat((post.get(p + "commencement_date") or "").strip())
    except ValueError:
        start = None
    weaknesses = [line.strip(" -•\t") for line in (post.get(p + "weaknesses") or "").splitlines()]
    return {"outcome": post.get(p + "outcome", ""), "approval_type": approval,
            "approved_streams": post.get(p + "approved_streams") or 0, "commencement_date": start,
            "weaknesses": [w for w in weaknesses if w], "note": post.get(p + "note", "")}


@transaction.atomic
def record_from_post(case, stage_code, post, *, user):
    stage = STAGE_OF_FLOW.get(stage_code)
    if stage is None:
        raise ValidationError("This stage does not record decisions.")
    ok, why = can_decide(case, user, stage)
    if not ok:
        raise PermissionDenied(why)
    saved = []
    for row in rows(case):
        if post.get(f"d{row.pk}-outcome") is None:
            continue
        saved.append(record(case, row, stage, user=user, check=False, **parse_form(post, row)))
    if not saved:
        raise ValidationError("Nothing to save.")
    return saved


# ===========================================================================
# B23: the Chairman's batch approval (one programme category per batch)
# ===========================================================================
def batch_candidates(user):
    """Programmes waiting for the Chairman (stage 15) that ``user`` holds,
    with the CQAC recommendation, grouped by programme category."""
    from . import case_services as cs

    groups = {}
    for case in cs.visible_cases(user).filter(status=Decision.FLOW_STAGE[Decision.CHAIR]).select_related(
            "institution", "qa_request__qa_type"):
        if not can_decide(case, user, Decision.CHAIR)[0]:
            continue
        cqac = decisions_at(case, Decision.CQAC)
        chair = decisions_at(case, Decision.CHAIR)
        for row in rows(case):
            if row.pk in chair:
                continue
            rec = cqac.get(row.pk)
            category = row.programme.category
            key = category.pk if category is not None else 0
            group = groups.setdefault(key, {"category": category, "items": []})
            group["items"].append({"case": case, "row": row, "recommendation": rec,
                                   "batchable": rec is not None and rec.outcome == Decision.APPROVE})
    return sorted(groups.values(), key=lambda g: g["category"].name if g["category"] else "~")


@transaction.atomic
def batch_approve(user, row_ids, *, category_id):
    """Adopt the CQAC recommendation as the Chairman's decision for every
    chosen programme — all of one programme category — then approve stage
    15 on each case whose programmes are all decided. Returns (batch key,
    decisions, cases moved on)."""
    from . import case_services as cs

    if not batch_enabled():
        raise ValidationError("Batch approval is switched off (qa.cqac.batch_approval_enabled).")
    if not (user.is_superuser or user.has_perm("accreditation.batch_decide")):
        raise PermissionDenied("Batch approval needs accreditation.batch_decide.")
    ids = sorted({int(i) for i in row_ids if str(i).isdigit()})
    if not ids:
        raise ValidationError("Choose at least one programme.")
    chosen = list(QARequestProgramme.unscoped.filter(pk__in=ids).select_related("programme__category", "qa_request"))
    if len(chosen) != len(ids):
        raise ValidationError("One of the programmes is no longer available.")
    categories = {r.programme.category_id for r in chosen}
    if len(categories) != 1 or (category_id is not None and categories != {category_id}):
        raise ValidationError("A batch may hold programmes of one programme category only (B23). "
                              "Approve each category separately.")
    key = uuid.uuid4().hex[:12]
    decisions, cases = [], {}
    for row in chosen:
        case = QACase.unscoped.filter(qa_request=row.qa_request).first()
        if case is None or case.status != Decision.FLOW_STAGE[Decision.CHAIR]:
            raise ValidationError(f"{row.programme}: the case is not waiting for the Chairman.")
        if not cs.visible_cases(user).filter(pk=case.pk).exists():
            raise PermissionDenied
        ok, why = can_decide(case, user, Decision.CHAIR)
        if not ok:
            raise PermissionDenied(f"{case.reference}: {why}")
        rec = Decision.unscoped.filter(case=case, qa_request_programme=row, stage=Decision.CQAC).first()
        if rec is None or rec.outcome != Decision.APPROVE:
            raise ValidationError(f"{row.programme}: only a CQAC recommendation to approve can be batch-approved.")
        decisions.append(record(case, row, Decision.CHAIR, user=user, check=False, outcome=Decision.APPROVE,
                                approval_type=rec.approval_type, approved_streams=rec.approved_streams,
                                commencement_date=rec.commencement_date, weaknesses=rec.weaknesses,
                                note=f"Batch approval {key}", batch_key=key))
        cases[case.pk] = case
    moved = []
    for case in cases.values():
        if not problems(case, Decision.FLOW_STAGE[Decision.CHAIR]):
            cs.act(case, user=user, stage_code=Decision.FLOW_STAGE[Decision.CHAIR], action="approve",
                   remark=f"Batch approval {key}")
            moved.append(case)
    return key, decisions, moved


# ===========================================================================
# Stage 17: apply the ES decisions to the programmes
# ===========================================================================
def reassessment_opens(expiry):
    from .qa_services import window_months

    return add_months(expiry, -window_months()) if expiry else None


def _apply_one(decision, actor):
    from institutions.models import Programme

    programme = Programme.unscoped.select_for_update().get(pk=decision.programme_id)
    before = programme.get_status_display()
    if decision.outcome == Decision.APPROVE:
        programme.status = decision.approval_type.programme_status
        programme.approval_type = decision.approval_type
        programme.approved_streams = decision.approved_streams
        programme.commencement_date = decision.commencement_date
        programme.expiry_date = decision.approval_type.expiry_for(decision.commencement_date)  # B18: derived
        programme.eligible_for_reassessment_at = reassessment_opens(programme.expiry_date)
        programme.expired_at = None
        programme.renewal_reminders_sent = []
        public = (f"{programme} — {decision.approval_type.name}: {programme.approved_streams} stream(s) from "
                  f"{programme.commencement_date:%d %b %Y}; expires {programme.expiry_date:%d %b %Y}.")
    else:
        programme.status = "denied"
        public = f"{programme} — not approved."
    programme.save()
    decision.applied_at = timezone.now()
    decision.save(update_fields=["applied_at", "updated_at"])
    inst = programme.institution
    timeline.record(inst, "programme.decided", subject=programme, actor=actor, visibility="public",
                    public_text=f"Decision issued: {public}",
                    internal_text=f"Stage 17: {programme} {before} → {programme.get_status_display()} "
                                  f"(decision {decision.pk}, case {decision.case.reference}).")
    return programme, public


@transaction.atomic
def apply_final(case, *, actor=None):
    """Stage 17 (system): idempotent — a decision already applied is skipped."""
    from institutions.services import notify_institution

    from . import letters
    from . import qa_services as svc

    finals = list(Decision.unscoped.filter(case=case, stage=Decision.ES, applied_at__isnull=True)
                  .select_related("approval_type", "programme__level", "qa_request_programme"))
    issued = []
    for decision in finals:
        programme, public = _apply_one(decision, actor)
        letter = letters.issue(decision, actor=actor)
        issued.append(letter)
        detail = public.split(" — ", 1)[-1]
        outcome = (decision.approval_type.name if decision.outcome == Decision.APPROVE else "Not approved")
        transaction.on_commit(lambda p=programme, o=outcome, d=detail: notify_institution(
            p.institution, "programme.decided", {"programme": str(p), "outcome": o, "detail": d},
            url="/letters/"))
    if finals:
        timeline.record(case.institution, "qa_case.decided", subject=case, actor=actor, visibility="internal",
                        internal_text=f"Stage 17 applied {len(finals)} decision(s) and issued "
                                      f"{', '.join(l.reference for l in issued)}.")
        req = case.qa_request
        instance = svc.request_instance(req)
        if instance is not None and "in_workflow" in (instance.current_stages or []):
            from qa_core import flow

            try:
                flow.apply_confirmed(instance, "decide", actor, remark=f"Decided on case {case.reference}")
            except ValidationError:
                pass
    return issued
