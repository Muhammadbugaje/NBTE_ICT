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
from collections import Counter

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

import config
from qa_core import timeline
from qa_core.seed_data_phase8 import NEW_PROGRAMME_APPROVAL, RECOMMENDATION_DEFAULTS

from .models import ApprovalType, CQACVote, Decision, QACase, QARequestProgramme, add_months

STAGE_OF_FLOW = {flow_code: stage for stage, flow_code in Decision.FLOW_STAGE.items()}
PREVIOUS = {Decision.CHAIR: Decision.CQAC, Decision.ES: Decision.CHAIR}
STAGE_PERMISSION = {Decision.CQAC: "accreditation.cqac_review", Decision.CHAIR: "accreditation.cqac_chair_decide",
                    Decision.ES: "accreditation.es_final_decide"}
FINAL_STATUSES = ("accredited", "interim", "approval_to_commence", "approved_pending_body_letter")


def _bool(key, default=False):
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def batch_enabled():
    return _bool("qa.cqac.batch_approval_enabled", True)


def recommendation_mode():
    """v2.3 Phase 2 (owner decision 10): every CQAC member always votes on their own.
    The old "shared" mode (one record any member could overwrite) is gone; the
    setting ``qa.cqac.recommendation_mode`` was removed (accreditation 0030)."""
    return "individual"


# v2.3 Phase 2 (owner decision 4): every approver decides on an empty form.
# The Chairman sees the panel's votes but his own form starts blank and sees
# the panel's recommendation only after saving his own.
# v2.9: the ES is no longer blind — before he decides at stage 16 he sees
# everything (reports, the Directorate's recommendation, every CQAC vote and
# comment, the Chairman's decision and remark, every remark): ``decision_brief``.
# His form still starts empty.
BLIND_STAGES = (Decision.CHAIR,)


def reveal_earlier(case, stage, user):
    """May ``user`` see the earlier decision stages of this case now?

    Everyone may, except the person who must still decide at a blind stage:
    the Chairman sees earlier decisions only after saving his own for every
    programme of the case (v2.9: the ES is not blind)."""
    if stage not in BLIND_STAGES:
        return True
    ok, _why = can_decide(case, user, stage)
    if not ok:
        return True
    saved = decisions_at(case, stage)
    return all(row.pk in saved for row in rows(case))


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
    """Starting values for ``row`` at ``stage``: always an empty form (v2.3).

    Nothing is pre-selected at any decision stage: not the panel's view for
    the Chairman, not the Chairman's for the ES. ``report`` is still returned
    so the form can link the evidence."""
    return {"outcome": "", "approval_type": None, "approved_streams": "", "commencement_date": None,
            "weaknesses": [], "note": "", "from": _report_for(case, row)}


def legacy_suggestion(case, row, stage):
    """The pre-v2.3 carried-forward values (used only by the demo seeder)."""
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
    if stage == Decision.ES and outcome == Decision.APPROVE and getattr(user, "pk", None):
        from documents.issue import precheck  # v2.1 X23: the ES signs the approval-to-commence letter

        precheck("qa_case.decision_approved", user)
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


def _cqac_panel(case):
    from . import case_services as cs

    return [
        user for user in cs.holders(case, Decision.FLOW_STAGE[Decision.CQAC])
        if user is not None and user.is_active and user.user_type == "staff"
        and getattr(getattr(user, "role", None), "code", "") == "cqac-member"
    ]


def cqac_panel_members(case):
    return _cqac_panel(case)


def _save_cqac_vote(case, row, *, user, values):
    from . import case_services as cs

    stage_code = Decision.FLOW_STAGE[Decision.CQAC]
    if user not in _cqac_panel(case):
        raise PermissionDenied("Only an assigned, active CQAC member may submit an individual vote.")
    if not user.has_perm(STAGE_PERMISSION[Decision.CQAC]):
        raise PermissionDenied(f"You do not hold {STAGE_PERMISSION[Decision.CQAC]}.")
    if stage_code not in cs.current_stages(case):
        raise ValidationError("The case is not at the CQAC recommendation stage.")
    if row.qa_request_id != case.qa_request_id or row.status != QARequestProgramme.ACTIVE:
        raise ValidationError("That programme is not active on this case.")
    approval_type, streams, commencement, weaknesses = _clean(row, **values)
    return CQACVote.unscoped.update_or_create(
        case=case, qa_request_programme=row, user=user,
        defaults={
            "programme": row.programme,
            "outcome": values["outcome"],
            "approval_type": approval_type,
            "approved_streams": streams,
            "commencement_date": commencement,
            "weaknesses": weaknesses,
            "note": (values.get("note") or "").strip(),
        },
    )[0]


def _cqac_votes_complete(case, panel=None, programme_rows=None):
    panel = panel if panel is not None else _cqac_panel(case)
    programme_rows = programme_rows if programme_rows is not None else rows(case)
    if not panel or not programme_rows:
        return False
    expected = {(row.pk, user.pk) for row in programme_rows for user in panel}
    actual = set(CQACVote.unscoped.filter(
        case=case, qa_request_programme_id__in=[row.pk for row in programme_rows],
        user_id__in=[user.pk for user in panel],
    ).values_list("qa_request_programme_id", "user_id"))
    return expected.issubset(actual)


def _aggregate_cqac_votes(case, *, actor):
    programme_rows = rows(case)
    panel = _cqac_panel(case)
    if not _cqac_votes_complete(case, panel, programme_rows):
        return []
    decisions = []
    for row in programme_rows:
        votes = list(CQACVote.unscoped.filter(case=case, qa_request_programme=row, user__in=panel)
                     .select_related("approval_type").order_by("user_id", "pk"))
        counts = Counter(vote.outcome for vote in votes)
        largest = max(counts.values())
        winners = [outcome for outcome, count in counts.items() if count == largest]
        if len(winners) > 1 or largest * 2 <= len(votes):
            outcome, approval_type, streams, commencement = Decision.RETURN, None, 0, None
            weaknesses = []
            if len(winners) > 1:
                note = f"CQAC panel vote tied ({largest} votes each); return for review."
            else:
                note = f"No CQAC outcome received a majority ({largest} of {len(votes)} votes); return for review."
        else:
            outcome = winners[0]
            winning_votes = [vote for vote in votes if vote.outcome == outcome]
            representative = winning_votes[0]
            details_tied = False
            weaknesses = list(dict.fromkeys(
                weakness for vote in winning_votes for weakness in vote.weaknesses
            ))
            note = f"CQAC panel majority: {outcome} ({largest} of {len(votes)} votes)."
            if outcome == Decision.APPROVE:
                details = (
                    ("approval type", "approval_type_id"),
                    ("approved streams", "approved_streams"),
                    ("commencement date", "commencement_date"),
                )
                chosen = {}
                unresolved = []
                for label, field in details:
                    detail_counts = Counter(getattr(vote, field) for vote in winning_votes)
                    value, count = detail_counts.most_common(1)[0]
                    if count * 2 <= len(winning_votes):
                        unresolved.append(label)
                    else:
                        chosen[field] = value
                if unresolved:
                    details_tied = True
                    outcome, approval_type, streams, commencement = Decision.RETURN, None, 0, None
                    note += (f" Approval details did not reach a majority for {', '.join(unresolved)}; "
                             "return for review.")
                else:
                    approval_type = next(vote.approval_type for vote in winning_votes
                                         if vote.approval_type_id == chosen["approval_type_id"])
                    streams = chosen["approved_streams"]
                    commencement = chosen["commencement_date"]
            else:
                approval_type, streams, commencement = None, 0, None
            if representative.note and not details_tied:
                note += f" {representative.note}"
        decisions.append(record(
            case, row, Decision.CQAC, user=actor, check=False, outcome=outcome,
            approval_type=approval_type, approved_streams=streams, commencement_date=commencement,
            weaknesses=weaknesses, note=note,
        ))
    return decisions


def problems(case, stage_code):
    """What blocks *approve* at a decision stage (plain language)."""
    stage = STAGE_OF_FLOW.get(stage_code)
    if stage is None:
        return []
    if stage == Decision.CQAC and recommendation_mode() == "individual":
        panel = _cqac_panel(case)
        programme_rows = rows(case)
        if not panel:
            return ["No active CQAC panel members are assigned to this stage."]
        votes = CQACVote.unscoped.filter(
            case=case, qa_request_programme_id__in=[row.pk for row in programme_rows],
            user_id__in=[user.pk for user in panel],
        )
        vote_pairs = set(votes.values_list("qa_request_programme_id", "user_id"))
        incomplete = [
            f"{row.programme} ({sum((row.pk, member.pk) in vote_pairs for member in panel)} "
            f"of {len(panel)} votes)"
            for row in programme_rows
            if any((row.pk, member.pk) not in vote_pairs for member in panel)
        ]
        if incomplete:
            return ["Every assigned CQAC member must vote on every active programme: "
                    + ", ".join(incomplete) + "."]
        have = decisions_at(case, stage)
        if any(row.pk not in have for row in programme_rows):
            return ["All panel votes are in; save a vote to finalize the aggregated recommendation."]
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
    decision (if any), the starting values and the earlier stages' decisions
    (hidden from a blind decider until he has saved his own)."""
    stage = STAGE_OF_FLOW.get(stage_code)
    if stage is None:
        return []
    saved = decisions_at(case, stage)
    show_earlier = reveal_earlier(case, stage, user)
    chair_tally = stage == Decision.CHAIR
    from . import capacity

    history = {}
    for d in Decision.unscoped.filter(case=case).select_related("approval_type", "decided_by").order_by("pk"):
        history.setdefault(d.qa_request_programme_id, []).append(d)
    cqac_mode = recommendation_mode() if stage == Decision.CQAC else ""
    panel = cqac_panel_members(case) if stage == Decision.CQAC else []
    programme_rows = rows(case)
    capacity_reviews = capacity.review_states(case, programme_rows)
    out = []
    for row in programme_rows:
        decision = saved.get(row.pk)
        vote = None
        panel_votes = []
        if cqac_mode == "individual":
            panel_votes = list(CQACVote.unscoped.filter(
                case=case, qa_request_programme=row, user__in=panel).select_related("user").order_by("user_id"))
            vote = next((item for item in panel_votes if item.user_id == user.pk), None)
        start = suggestion(case, row, stage) if decision is None else {
            "outcome": decision.outcome, "approval_type": decision.approval_type,
            "approved_streams": decision.approved_streams or row.requested_streams,
            "commencement_date": decision.commencement_date or timezone.localdate(),
            "weaknesses": list(decision.weaknesses or []), "note": decision.note, "from": None}
        if vote is not None:
            start = {"outcome": vote.outcome, "approval_type": vote.approval_type,
                     "approved_streams": vote.approved_streams or row.requested_streams,
                     "commencement_date": vote.commencement_date or timezone.localdate(),
                     "weaknesses": list(vote.weaknesses or []), "note": vote.note, "from": None}
        report = _report_for(case, row)
        voted_user_ids = {item.user_id for item in panel_votes}
        tally = _vote_tally(case, row) if chair_tally else None
        out.append({"row": row, "programme": row.programme, "decision": decision, "start": start,
                    "weaknesses_text": "\n".join(start["weaknesses"]),
                    "history": [d for d in history.get(row.pk, []) if d.stage != stage] if show_earlier else [],
                    "earlier_hidden": not show_earlier,
                    "vote_tally": tally,
                    "report": report, "capacity": capacity.overall(row), "vote": vote,
                    "capacity_review": capacity_reviews[row.pk]["review"],
                    "capacity_review_current": capacity_reviews[row.pk]["is_current"],
                    "recommendation_mode": cqac_mode,
                    "votes_received": len(panel_votes), "votes_required": len(panel),
                    "panel_members": panel,
                    "panel_votes": panel_votes,
                    "panel_pending": [member for member in panel if member.pk not in voted_user_ids]})
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
        values = parse_form(post, row)
        if stage == Decision.CQAC and recommendation_mode() == "individual":
            saved.append(_save_cqac_vote(case, row, user=user, values=values))
        else:
            saved.append(record(case, row, stage, user=user, check=False, **values))
    if not saved:
        raise ValidationError("Nothing to save.")
    if stage == Decision.CQAC and recommendation_mode() == "individual":
        _aggregate_cqac_votes(case, actor=user)
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
        # D-19: when the body's letter is required, the programme waits in a
        # distinct status until that letter is uploaded.
        waiting = bool(programme.awaiting_body_letter
                       and config.get("qa.pb.wait_for_body_letter", True))
        programme.status = ("approved_pending_body_letter" if waiting
                            else decision.approval_type.programme_status)
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
    if decision.outcome == Decision.APPROVE and programme.professional_body_id:  # v2.1 R.5 X43
        from django.db import transaction

        from institutions import pb_portal

        # D-19 (v2.2): while NBTE waits for the body's letter the body is asked
        # for it; "approval to commence" goes out only once the letter is in.
        event = ("body_letter_requested" if programme.status == "approved_pending_body_letter"
                 else "approval_to_commence")
        transaction.on_commit(lambda: pb_portal.notify_body(programme, event))
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
        if decision.outcome == Decision.APPROVE:  # v2.1 X24/X25: provisional approval to commence (placeholder)
            from documents.issue import approval_recorded

            # D-19 (v2.2): a programme waiting for its body's letter gets the
            # provisional letter now; approval to commence is blocked until the
            # body uploads its letter (institutions.pb_portal.upload_letter).
            event = ("qa_case.decision_pending_body" if programme.status == "approved_pending_body_letter"
                     else "qa_case.decision_approved")
            approval_recorded(event, decision, actor=actor, institution=case.institution,
                              context={"programme": str(programme), "streams": decision.approved_streams,
                                       "commencement": decision.commencement_date.strftime("%d %B %Y")
                                       if decision.commencement_date else ""})
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


def _vote_tally(case, row):
    """The CQAC panel's individual votes for one programme, for the Chairman:
    counts per outcome and every member's reasons (names stay off the tally)."""
    votes = list(CQACVote.unscoped.filter(case=case, qa_request_programme=row)
                 .select_related("approval_type").order_by("pk"))
    if not votes:
        return None
    labels = dict(Decision.OUTCOME_CHOICES)
    counts = Counter(v.outcome for v in votes)
    total = len(votes)
    lines = [{"outcome": o, "label": labels.get(o, o), "count": counts[o],
              "pct": round(counts[o] * 100 / total)} for o in ("approve", "deny", "return") if counts.get(o)]
    reasons = []
    for v in votes:
        text = "; ".join([*(v.weaknesses or []), v.note]).strip("; ").strip()
        if v.outcome == Decision.APPROVE and v.approval_type is not None:
            text = (f"{v.approval_type.name}, {v.approved_streams} stream(s)" + (f" — {text}" if text else ""))
        if text:
            reasons.append({"outcome": labels.get(v.outcome, v.outcome), "text": text})
    return {"total": total, "lines": lines, "reasons": reasons}


# ===========================================================================
# v2.9: the decision brief — everything the ES (stage 16) sees before he
# decides, in one structure. The CQAC Chairman (stage 15) gets the same brief
# read-only, without the stages after his own.
# ===========================================================================
def brief_allowed(case, user, stage):
    """The ES (stage 16) and the Chairman (stage 15) get the brief when they
    may decide at their stage; a superuser always."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if stage not in (Decision.CHAIR, Decision.ES):
        return False
    if user.is_superuser:
        return True
    return can_decide(case, user, stage)[0]


def _who(user):
    return (user.get_full_name() or user.email) if user is not None else ""


def _decision_dict(d):
    if d is None:
        return None
    labels = dict(Decision.OUTCOME_CHOICES)
    return {"decision": d, "stage": d.stage, "stage_label": d.get_stage_display(), "outcome": d.outcome,
            "outcome_label": labels.get(d.outcome, d.outcome),
            "approval_type": d.approval_type, "approved_streams": d.approved_streams,
            "commencement_date": d.commencement_date, "expiry_date": d.expiry_date,
            "weaknesses": list(d.weaknesses or []), "remark": d.note, "by": d.decided_by,
            "by_name": _who(d.decided_by), "at": d.decided_at}


def _report_brief(report):
    """The compiled post-visit report, its endorsements and every attendee's report."""
    from inspection.models import PostVisitMemberReport, PostVisitMemberReview, PostVisitReport, ReportEndorsement

    if report is None:
        return None, [], None
    rec_labels = dict(PostVisitReport.RECOMMENDATION_CHOICES)
    endorsements = [{"layer": e.layer, "layer_label": e.get_layer_display(), "action": e.action,
                     "action_label": e.get_action_display(), "content": e.content, "by": e.actor,
                     "by_name": _who(e.actor), "at": e.at}
                    for e in ReportEndorsement.unscoped.filter(report=report).select_related("actor")
                    .order_by("at", "pk")]
    compiled = {"report": report, "pk": report.pk, "status": report.status,
                "summary": report.summary, "findings": report.findings or {}, "sections": report.sections or {},
                "recommendation": report.recommendation,
                "recommendation_label": rec_labels.get(report.recommendation, ""),
                "submitted_by": report.submitted_by, "submitted_by_name": _who(report.submitted_by),
                "submitted_at": report.submitted_at, "fines_total": report.fines_total,
                "gps_flag": report.gps_flag, "gps_distance_km": report.gps_distance_km,
                "endorsements": endorsements}
    reviews = {}
    for rv in (PostVisitMemberReview.unscoped.filter(member_report__report=report).select_related("actor")
               .order_by("at", "pk")):
        reviews.setdefault(rv.member_report_id, []).append(
            {"action": rv.action, "action_label": rv.get_action_display(), "content": rv.content,
             "by": rv.actor, "by_name": _who(rv.actor), "at": rv.at})
    members = []
    for mr in (PostVisitMemberReport.unscoped.filter(report=report).exclude(status="draft")
               .select_related("author", "reviewed_by", "attendee").order_by("attendee_id", "pk")):
        attendee = mr.attendee
        members.append({"member_report": mr, "pk": mr.pk, "author": mr.author, "author_name": _who(mr.author),
                        "person_type": attendee.person_type, "role": attendee.role_on_team or attendee.get_duty_display(),
                        "status": mr.status, "status_label": mr.get_status_display(),
                        "summary": mr.summary, "findings": mr.findings or {},
                        "recommendation": mr.recommendation,
                        "recommendation_label": rec_labels.get(mr.recommendation, ""),
                        "submitted_at": mr.submitted_at, "review_note": mr.review_note,
                        "reviewed_by": mr.reviewed_by, "reviewed_by_name": _who(mr.reviewed_by),
                        "reviews": reviews.get(mr.pk, [])})
    directorate = None
    if report.recommended_by_id:
        directorate = {"recommendation": report.recommendation,
                       "label": rec_labels.get(report.recommendation, ""),
                       "note": report.recommendation_note, "by": report.recommended_by,
                       "by_name": _who(report.recommended_by), "at": report.updated_at}
    return compiled, members, directorate


def _rp_report_dict(item):
    return {key: item[key] for key in (
        "pk", "person", "person_kind", "scope", "scope_label", "target", "duty_label", "from_date", "to_date",
        "status_label", "approval_label", "rp_response", "rp_response_label", "rp_response_at",
        "rp_response_note", "report", "report_recommendation", "report_recommendation_label",
        "report_submitted_at")}


def timeline_remarks(case, user):
    """Every remark written on the case's flow and every comment on its stage
    tasks (that ``user`` may read), oldest first."""
    from . import case_services as cs
    from tasks.services import visible_comments

    out = []
    instance = cs.instance_of(case)
    if instance is not None:
        for e in instance.events.select_related("actor").order_by("at", "id"):
            if (e.remark or "").strip():
                out.append({"at": e.at, "kind": "flow", "stage": e.from_stage,
                            "stage_label": cs.stage_label(e.from_stage) if e.from_stage else "",
                            "action": e.action_code.replace("_", " "), "by": e.actor, "by_name": _who(e.actor),
                            "text": e.remark.strip()})
    for link in cs.case_links(case):
        for c in visible_comments(link.task, user).select_related("author").order_by("created_at"):
            out.append({"at": c.created_at, "kind": "comment", "stage": link.stage_code,
                        "stage_label": cs.stage_label(link.stage_code), "action": "comment",
                        "by": c.author, "by_name": _who(c.author), "text": c.body,
                        "internal": c.is_internal})
    out.sort(key=lambda r: r["at"])
    return out


def decision_brief(case, user, stage):
    """``es_brief``: per programme the compiled report, every attendee's report,
    the Directorate's stage-13 recommendation, every CQAC member's vote and
    comment (by name), the tally, the panel's recommendation and the
    Chairman's decision and remark, plus the resource persons' reports; and
    every remark on the case. ``None`` when ``user`` may not have it.

    For the Chairman (stage 15) the brief stops before his own stage, and the
    panel's aggregated recommendation stays hidden until he has decided
    (his blind rule is unchanged)."""
    if not brief_allowed(case, user, stage):
        return None
    from inspection import services as insp

    reveal = reveal_earlier(case, stage, user)
    history = {}
    for d in Decision.unscoped.filter(case=case).select_related("approval_type", "decided_by").order_by("pk"):
        history.setdefault(d.qa_request_programme_id, {})[d.stage] = d
    labels = dict(Decision.OUTCOME_CHOICES)
    rp_items = insp.case_rp_mappings(case)
    programmes = []
    for row in rows(case):
        compiled, members, directorate = _report_brief(_report_for(case, row))
        votes = []
        for v in (CQACVote.unscoped.filter(case=case, qa_request_programme=row)
                  .select_related("user", "approval_type").order_by("user__first_name", "pk")):
            votes.append({"vote": v, "member": v.user, "member_name": _who(v.user), "outcome": v.outcome,
                          "outcome_label": labels.get(v.outcome, v.outcome), "approval_type": v.approval_type,
                          "approved_streams": v.approved_streams, "commencement_date": v.commencement_date,
                          "weaknesses": list(v.weaknesses or []), "comment": v.note, "at": v.updated_at})
        by_stage = history.get(row.pk, {})
        item = {"row": row, "programme": row.programme, "report": compiled, "member_reports": members,
                "directorate_recommendation": directorate, "cqac_votes": votes,
                "tally": _vote_tally(case, row),
                "cqac_decision": _decision_dict(by_stage.get(Decision.CQAC)) if reveal else None,
                "chair": None, "es": None,
                "rp_reports": [_rp_report_dict(i) for i in rp_items if i["programme"] is not None
                               and i["mapping"].qa_request_programme_id == row.pk]}
        if stage == Decision.ES:
            item["chair"] = _decision_dict(by_stage.get(Decision.CHAIR))
            item["es"] = _decision_dict(by_stage.get(Decision.ES))
        programmes.append(item)
    return {"stage": stage, "for_es": stage == Decision.ES, "earlier_hidden": not reveal,
            "programmes": programmes,
            "rp_reports": [_rp_report_dict(i) for i in rp_items if i["programme"] is None],
            "rp_mappings": [_rp_report_dict(i) for i in rp_items],
            "timeline_remarks": timeline_remarks(case, user)}
