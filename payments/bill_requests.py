"""v2.4 — Issue a bill: Finance builds it, the Executive Secretary approves it.

1. Finance picks an institution. ``catalogue(inst)`` lists every programme in
   the NBTE master list (``institutions.ProgrammeMaster``) and marks which
   ones the institution already has on its record and which it does not have
   (it can now be billed for a new programme approval). Programmes typed by
   hand (not in the master list) are listed with the ones it has.
2. Finance ticks items. ``price(inst, items)`` prices them with the existing
   fee rules — ``billing.compute`` over the fee schedules
   ``programme_processing`` (new programmes) and ``qa_request_processing``
   (re-accreditation of programmes it runs), which read
   *Settings → Fees by institution category* first and the formula settings
   otherwise — plus any other active fee schedule ticked on its own.
3. ``submit`` freezes the lines and sends the request to the Executive
   Secretary (``awaiting_es``). The institution sees nothing yet.
4. ``approve`` (ES only, never the person who prepared it) issues a normal
   ``PaymentObligation`` with the frozen lines; ``services.publish`` then
   makes the slip and notifies the institution. ``reject`` needs a reason and
   tells the officer.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core import audit
from qa_core import flow, references, timeline
from qa_core.formula import FormulaError

from .models import ZERO, BillRequest, FeeSchedule, PaymentObligation
from .money import amount_in_words, naira, to_money

NEW, REACC, FEE = "new", "reacc", "fee"
NEW_SCHEDULE, REACC_SCHEDULE = "programme_processing", "qa_request_processing"
FAMILY = {NEW: (NEW_SCHEDULE, "New programme approval"), REACC: (REACC_SCHEDULE, "Re-accreditation")}
# Programme statuses that mean "the institution runs / has run this programme".
HAS_STATUSES = ("accredited", "interim", "approval_to_commence", "approved_pending_body_letter",
                "resource_inspected", "expired")
GONE_STATUSES = ("dropped", "denied")
# Schedules priced from the programme picks (not offered as separate "other fees").
PROGRAMME_SCHEDULES = (NEW_SCHEDULE, REACC_SCHEDULE)
REGISTRATION_PREFIX = "institutional_processing"


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------
def can_prepare(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "user_type", "staff") == "staff"
                and (user.is_superuser or user.has_perm("payments.view_obligation")))


def is_approver(user):
    """Only the Executive Secretary approves a bill made by hand (superusers
    too, for support)."""
    import org

    return bool(getattr(user, "is_authenticated", False) and user.is_active
                and getattr(user, "user_type", "staff") == "staff" and (org.is_es(user) or user.is_superuser))


def can_view(user):
    from qa_core.permissions import can_view_finance

    return bool(can_view_finance(user) or is_approver(user))


def es_users():
    import org

    return list(org.users_with_role(org.ES_ROLE, staff_only=True))


# ---------------------------------------------------------------------------
# The programme catalogue for one institution
# ---------------------------------------------------------------------------
def _key(name, level_id):
    return (" ".join(str(name or "").lower().split()), level_id)


def catalogue(inst):
    """{"has": [...], "can_offer": [...], "counts": {...}} — one row per
    programme: {key, master_id, programme_id, name, level, level_code,
    status, status_label, has, kind, discipline}. ``key`` is the value the
    checkbox posts ("m<master_pk>" or "p<programme_pk>")."""
    from institutions.models import Programme, ProgrammeMaster

    own = list(Programme.unscoped.filter(institution=inst).select_related("level", "master"))
    by_master = {p.master_id: p for p in own if p.master_id}
    by_name = {_key(p.name, p.level_id): p for p in own}
    has, can_offer, matched = [], [], set()
    masters = ProgrammeMaster.objects.filter(is_active=True).select_related("level", "discipline") \
        .order_by("level__sort_order", "name")
    for m in masters:
        p = by_master.get(m.pk) or by_name.get(_key(m.name, m.level_id))
        if p is not None:
            matched.add(p.pk)
        row = {"master_id": m.pk, "programme_id": p.pk if p else None, "name": m.name,
               "level": m.level.name if m.level_id else "", "level_code": (m.level.code.upper() if m.level_id else ""),
               "discipline": m.discipline.name if m.discipline_id else "", "status": p.status if p else "",
               "status_label": p.get_status_display() if p else "", "streams": max(1, p.approved_streams or
                                                                                    p.requested_streams or 1) if p else 1}
        _classify(row, p)
        row["key"] = f"m{m.pk}"
        (has if row["has"] else can_offer).append(row)
    for p in own:  # typed by hand, not (yet) in the master list
        if p.pk in matched:
            continue
        row = {"master_id": None, "programme_id": p.pk, "name": p.name,
               "level": p.level.name if p.level_id else "", "level_code": p.level.code.upper() if p.level_id else "",
               "discipline": "", "status": p.status, "status_label": p.get_status_display(),
               "streams": max(1, p.approved_streams or p.requested_streams or 1), "not_in_master": True}
        _classify(row, p)
        row["key"] = f"p{p.pk}"
        (has if row["has"] else can_offer).append(row)
    return {"has": has, "can_offer": can_offer,
            "counts": {"has": len(has), "can_offer": len(can_offer), "master": len(masters)}}


def _classify(row, programme):
    """has: the institution runs it → re-accreditation. Otherwise (not on its
    record, proposed, dropped or denied) → new programme approval."""
    status = getattr(programme, "status", "") if programme is not None else ""
    row["has"] = status in HAS_STATUSES
    row["kind"] = REACC if row["has"] else NEW
    row["proposed"] = status == "proposed"
    row["gone"] = status in GONE_STATUSES


def other_fees():
    """Active fee schedules Finance may add on their own (not the programme
    or registration ones, which have their own items)."""
    return [s for s in FeeSchedule.objects.filter(is_active=True).order_by("name")
            if s.code not in PROGRAMME_SCHEDULES and not s.code.startswith(REGISTRATION_PREFIX)]


# ---------------------------------------------------------------------------
# Picks → items
# ---------------------------------------------------------------------------
def items_from_post(inst, data):
    """Read the ticked programmes (``prog`` = keys), their streams
    (``streams_<key>``) and the ticked other fees (``fee`` = codes)."""
    cat = catalogue(inst)
    rows = {r["key"]: r for r in cat["has"] + cat["can_offer"]}
    items = []
    keys = data.getlist("prog") if hasattr(data, "getlist") else list(data.get("prog") or [])
    for key in dict.fromkeys(keys):
        row = rows.get(key)
        if row is None:
            continue
        try:
            streams = int(data.get(f"streams_{key}") or row["streams"] or 1)
        except (TypeError, ValueError):
            streams = 1
        streams = min(max(streams, 1), 20)
        items.append({"kind": row["kind"], "key": key, "master_id": row["master_id"],
                      "programme_id": row["programme_id"],
                      "label": f"{row['level_code']} {row['name']}".strip(), "streams": streams})
    allowed = {s.code for s in other_fees()}
    fee_codes = data.getlist("fee") if hasattr(data, "getlist") else list(data.get("fee") or [])
    for code in dict.fromkeys(fee_codes):
        if code in allowed:
            items.append({"kind": FEE, "fee_code": code, "label": FeeSchedule.objects.get(code=code).name})
    return items


# ---------------------------------------------------------------------------
# Pricing (the existing fee rules; nobody types an amount)
# ---------------------------------------------------------------------------
def _variables(inst, picks, kind):
    from . import services

    streams = [max(1, int(i.get("streams") or 1)) for i in picks]
    return services.fee_variables(inst, programmes=len(picks), streams=sum(streams),
                                  max_streams=max(streams, default=0),
                                  new_programmes=len(picks) if kind == NEW else 0)


def _schedule_lines(schedule, inst, variables):
    """billing.compute with the same guards issue_obligation applies."""
    from . import billing, services
    from .models import InstitutionTypeFee

    if schedule.confirmed_setting and not services._bool(schedule.confirmed_setting, False) \
            and InstitutionTypeFee.for_institution(inst) is None:
        raise ValidationError(f"{schedule.name}: NBTE has not confirmed this amount yet "
                              f"({schedule.confirmed_setting}).")
    try:
        _snap, lines = billing.compute(schedule, inst, variables)
    except FormulaError as exc:
        raise ValidationError(f"{schedule.name}: the fee could not be worked out ({exc}).") from None
    return lines


def price(inst, items):
    """{"groups": [{kind, title, schedule, items, lines, subtotal, error}],
    "lines": [flat priced lines], "total", "errors", "schedule" (primary)}."""
    groups, flat, errors = [], [], []
    for kind in (NEW, REACC):
        picks = [i for i in items if i["kind"] == kind]
        if not picks:
            continue
        code, title = FAMILY[kind]
        schedule = FeeSchedule.objects.filter(code=code, is_active=True).first()
        group = {"kind": kind, "title": title, "schedule": schedule, "items": picks, "lines": [],
                 "subtotal": ZERO, "error": ""}
        if schedule is None:
            group["error"] = f"The fee schedule “{code}” is not set up or is switched off."
        else:
            try:
                lines = _schedule_lines(schedule, inst, _variables(inst, picks, kind))
            except ValidationError as exc:
                group["error"] = " ".join(exc.messages)
            else:
                names = ", ".join(i["label"] for i in picks)
                named = False
                for line in lines:
                    priced = {"code": f"{kind}-{line['code']}", "label": f"{title}: {line['label']}",
                              "description": line.get("description") or "", "kind": line.get("kind", "other"),
                              "amount": to_money(line["amount"]), "group": kind, "schedule": schedule.code,
                              "to_confirm": bool(line.get("to_confirm"))}
                    if line.get("kind") == "base" and not named:
                        named = True
                        priced["description"] = names if not priced["description"] else \
                            f"{priced['description']} — {names}"
                    group["lines"].append(priced)
                group["subtotal"] = sum((ln["amount"] for ln in group["lines"]), ZERO)
        if group["error"]:
            errors.append(group["error"])
        flat.extend(group["lines"])
        groups.append(group)
    for item in [i for i in items if i["kind"] == FEE]:
        schedule = FeeSchedule.objects.filter(code=item["fee_code"], is_active=True).first()
        group = {"kind": FEE, "title": item["label"], "schedule": schedule, "items": [item], "lines": [],
                 "subtotal": ZERO, "error": ""}
        if schedule is None:
            group["error"] = f"{item['label']} is switched off."
        else:
            from . import services

            try:
                lines = _schedule_lines(schedule, inst, services.fee_variables(inst))
            except ValidationError as exc:
                group["error"] = " ".join(exc.messages)
            else:
                for line in lines:
                    label = line["label"] if line["label"] == schedule.name else f"{schedule.name}: {line['label']}"
                    group["lines"].append({"code": f"fee-{schedule.code}-{line['code']}", "label": label,
                                           "description": line.get("description") or "",
                                           "kind": line.get("kind", "other"), "amount": to_money(line["amount"]),
                                           "group": FEE, "schedule": schedule.code,
                                           "to_confirm": bool(line.get("to_confirm"))})
                group["subtotal"] = sum((ln["amount"] for ln in group["lines"]), ZERO)
        if group["error"]:
            errors.append(group["error"])
        flat.extend(group["lines"])
        groups.append(group)
    primary = next((g["schedule"] for g in groups if g["schedule"] is not None and not g["error"]), None)
    return {"groups": groups, "lines": flat, "total": sum((ln["amount"] for ln in flat), ZERO),
            "errors": errors, "schedule": primary, "count": len(items)}


def describe(items):
    new = sum(1 for i in items if i["kind"] == NEW)
    reacc = sum(1 for i in items if i["kind"] == REACC)
    fees = [i["label"] for i in items if i["kind"] == FEE]
    parts = []
    if new:
        parts.append(f"new programme approval ({new} programme{'s' if new != 1 else ''})")
    if reacc:
        parts.append(f"re-accreditation ({reacc} programme{'s' if reacc != 1 else ''})")
    parts.extend(f.lower() for f in fees)
    text = ", ".join(parts) or "fees"
    return (text[0].upper() + text[1:])[:255]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def _new_reference():
    year = timezone.localdate().year
    n = BillRequest.unscoped.filter(reference__startswith=f"BR-{year}-").count() + 1
    while BillRequest.unscoped.filter(reference=f"BR-{year}-{n:05d}").exists():
        n += 1
    return f"BR-{year}-{n:05d}"


def _check_institution(inst):
    if inst is None or getattr(inst, "status", "") == "delisted":
        raise ValidationError("Choose an institution that is not delisted.")


@transaction.atomic
def save(inst, items, *, actor, reason="", description="", request=None, submit=False):
    """Create or update a draft; with ``submit`` also send it to the ES.
    Returns the BillRequest."""
    if not can_prepare(actor):
        raise PermissionDenied("Preparing a bill needs Finance access (payments.view_obligation).")
    _check_institution(inst)
    if request is not None:
        request = BillRequest.unscoped.select_for_update().get(pk=request.pk)
        if request.status != BillRequest.DRAFT:
            raise ValidationError(f"{request.reference} is {request.get_status_display().lower()}; "
                                  "it can no longer be changed.")
        if request.institution_id != inst.pk:
            raise ValidationError("This draft belongs to another institution.")
    if not items:
        raise ValidationError("Tick at least one programme or fee.")
    priced = price(inst, items)
    if submit:
        if priced["errors"]:
            raise ValidationError(priced["errors"])
        if priced["total"] <= 0:
            raise ValidationError(f"The fee rules give {naira(priced['total'])}; a bill must be more than zero.")
        if len((reason or "").strip()) < 5:
            raise ValidationError("Say why this bill is issued (at least 5 characters) — the ES reads it.")
    br = request or BillRequest(reference=_new_reference(), institution=inst, created_by=actor)
    br.items = items
    br.lines = [{**ln, "amount": str(ln["amount"])} for ln in priced["lines"]]
    br.total = priced["total"]
    br.fee_schedule = priced["schedule"]
    br.description = (description or "").strip()[:255] or describe(items)
    br.reason = (reason or "").strip()
    if submit:
        br.status, br.submitted_at = BillRequest.AWAITING, timezone.now()
    br.save()
    audit.log("other", br, actor=actor,
              note=(f"Bill {br.reference} sent to the Executive Secretary: {naira(br.total)}" if submit
                    else f"Bill {br.reference} saved as a draft: {naira(br.total)}")[:255])
    if submit:
        transaction.on_commit(lambda: _notify_es(br, actor))
    return br


def submit(br, *, actor):
    """Send a saved draft to the ES with its current items (re-priced now)."""
    return save(br.institution, br.items, actor=actor, reason=br.reason, description=br.description,
                request=br, submit=True)


def can_decide(br, user, *, impersonating=False):
    if br.status != BillRequest.AWAITING:
        return False, f"{br.reference} is {br.get_status_display().lower()}."
    if not is_approver(user):
        return False, "Only the Executive Secretary approves a bill before it is sent."
    if impersonating:
        return False, "You cannot approve while impersonating another user."
    if br.created_by_id and br.created_by_id == user.pk:
        return False, "You prepared this bill, so someone else must approve it."
    return True, ""


@transaction.atomic
def approve(br, *, actor, remark="", impersonating=False):
    """ES approval: issue the bill to the institution with the frozen lines."""
    br = BillRequest.unscoped.select_for_update().select_related("institution").get(pk=br.pk)
    ok, why = can_decide(br, actor, impersonating=impersonating)
    if not ok:
        raise PermissionDenied(why)
    _check_institution(br.institution)
    obligation = issue_from_request(br, actor=actor)
    br.status, br.decided_by, br.decided_at = BillRequest.APPROVED, actor, timezone.now()
    br.decision_remark, br.obligation = (remark or "").strip(), obligation
    br.save(update_fields=["status", "decided_by", "decided_at", "decision_remark", "obligation", "updated_at"])
    audit.log("other", br, actor=actor, note=f"Bill {br.reference} approved by the ES; issued as "
                                             f"{obligation.reference}"[:255])
    transaction.on_commit(lambda: _notify_officer(br, actor, approved=True))
    return br


@transaction.atomic
def reject(br, *, actor, remark, impersonating=False):
    br = BillRequest.unscoped.select_for_update().get(pk=br.pk)
    ok, why = can_decide(br, actor, impersonating=impersonating)
    if not ok:
        raise PermissionDenied(why)
    remark = (remark or "").strip()
    if len(remark) < 5:
        raise ValidationError("Give the reason for rejecting (at least 5 characters); the officer sees it.")
    br.status, br.decided_by, br.decided_at, br.decision_remark = BillRequest.REJECTED, actor, timezone.now(), remark
    br.save(update_fields=["status", "decided_by", "decided_at", "decision_remark", "updated_at"])
    audit.log("other", br, actor=actor, note=f"Bill {br.reference} rejected by the ES: {remark}"[:255])
    transaction.on_commit(lambda: _notify_officer(br, actor, approved=False))
    return br


@transaction.atomic
def withdraw(br, *, actor):
    br = BillRequest.unscoped.select_for_update().get(pk=br.pk)
    if br.status not in BillRequest.OPEN:
        raise ValidationError(f"{br.reference} is {br.get_status_display().lower()}.")
    if br.created_by_id != actor.pk and not actor.is_superuser:
        raise PermissionDenied("Only the officer who prepared this bill can withdraw it.")
    br.status = BillRequest.WITHDRAWN
    br.save(update_fields=["status", "updated_at"])
    audit.log("other", br, actor=actor, note=f"Bill {br.reference} withdrawn")
    return br


def issue_from_request(br, *, actor):
    """Create the PaymentObligation for an approved request (like
    services.issue_obligation, with the approved lines instead of one
    schedule's formula) and publish it — the institution is notified then."""
    from . import services

    inst = br.institution
    lines = br.priced_lines
    total = to_money(sum((ln["amount"] for ln in lines), ZERO))
    if total <= 0:
        raise ValidationError("This bill has no amount.")
    schedule = br.fee_schedule or FeeSchedule.objects.filter(
        code__in=[ln.get("schedule") for ln in lines], is_active=True).first()
    if schedule is None:
        raise ValidationError("No active fee schedule for this bill.")
    due_days = schedule.due_days or services._int("qa.payment.due_days", 30)
    snapshot = {"key": "payments.BillRequest", "expr": "bill made by Finance, approved by the Executive Secretary",
                "source": "bill_request", "bill_request": br.reference, "result": str(total), "returns": "decimal",
                "inputs": {"items": len(br.items or [])}, "items": br.items,
                "approved_by": (actor.get_full_name() or actor.email) if actor else "",
                "evaluated_at": timezone.now().isoformat(),
                "components": [{k: (str(v) if k == "amount" else v) for k, v in ln.items()} for ln in lines]}
    obligation = PaymentObligation.unscoped.create(
        reference=references.issue("payment", inst=inst.code), institution=inst,
        source_type=ContentType.objects.get_for_model(BillRequest), source_id=str(br.pk),
        source_reference=br.reference, fee_schedule=schedule, description=br.description or schedule.name,
        currency=schedule.currency, amount=total, amount_in_words=amount_in_words(total), formula_snapshot=snapshot,
        due_at=timezone.now() + datetime.timedelta(days=due_days), status="issued", balance=total,
        issued_by=br.created_by if br.created_by_id else actor,
    )
    flow.start(services.FLOW, obligation, actor=actor)
    if services._bool("qa.payment.apply_credit_automatically"):
        services.apply_credits(obligation, actor=actor)
    timeline.record(inst, "payment.issued", subject=obligation, actor=actor, visibility="public",
                    public_text=f"Bill {obligation.reference} issued: {obligation.description}, {naira(total)}.")
    services.publish(obligation, actor=actor)
    return obligation


# ---------------------------------------------------------------------------
# Notifications (in-app; the institution is told by services.publish)
# ---------------------------------------------------------------------------
def _url(br):
    return f"/qa/finance/bill-requests/{br.pk}/"


def _notify_es(br, actor):
    from notifications.services import notify_many

    notify_many(es_users(), title=f"Bill awaiting your approval: {br.reference}",
                body=f"{br.institution.name}: {br.description}, {naira(br.total)}. Prepared by "
                     f"{(actor.get_full_name() or actor.email) if actor else 'Finance'}.",
                url=_url(br), actor=actor)


def _notify_officer(br, actor, *, approved):
    from notifications.services import notify

    if not br.created_by_id:
        return
    if approved:
        title = f"Bill approved and sent: {br.reference}"
        body = (f"The Executive Secretary approved the bill for {br.institution.name} ({naira(br.total)}). "
                f"It was issued as {br.obligation.reference if br.obligation_id else ''} and the institution "
                "has been notified.")
    else:
        title = f"Bill rejected: {br.reference}"
        body = f"The Executive Secretary rejected the bill for {br.institution.name}: {br.decision_remark}"
    notify(br.created_by, title=title, body=body, url=_url(br), actor=actor)


def counts(user=None):
    qs = BillRequest.unscoped.all()
    return {"awaiting": qs.filter(status=BillRequest.AWAITING).count(),
            "drafts": qs.filter(status=BillRequest.DRAFT, created_by=user).count() if user is not None else 0}


def total_of(rows):
    return sum((Decimal(str(r.get("amount") or 0)) for r in rows), ZERO)
