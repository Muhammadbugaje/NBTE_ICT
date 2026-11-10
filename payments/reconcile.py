"""v2.2 — nightly reconciliation (``manage.py qa_payments_reconcile``, Celery
beat ``qa.payments.reconcile`` at 01:30).

For every bill and online attempt it checks NBTE's records against the
provider and the ledger:

* ledger ≠ bill            → a correcting journal is posted (auto-fixed)
* pending online attempts  → asked again with the SAME reference; a paid one is
                             recorded (provider_paid_not_recorded, auto-fixed)
* recorded paid but the provider now says not paid → flagged (critical)
* provider amount ≠ recorded amount → flagged
* two successful online payments for one bill → flagged as a possible double
  charge for Finance to refund
* provider-paid waiting for Finance beyond the SLA → flagged
* any unbalanced journal → flagged (critical)

Each run is stored (ReconciliationRun) with its issues; the Director of
Finance is notified when something needs a person.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from . import ledger, providers

logger = logging.getLogger(__name__)


def run(*, trigger="nightly", actor=None, verify_with_provider=True, days=45):
    from .models import (PaymentAttempt, PaymentObligation, ReconciliationIssue, ReconciliationRun)
    from .services import mark_attempt_unverified, record_provider_result

    rec = ReconciliationRun.objects.create(trigger=trigger, run_by=actor if getattr(actor, "pk", None) else None)
    issues = []

    def flag(kind, message, *, severity="warning", obligation=None, attempt=None, fixed=False):
        issues.append(ReconciliationIssue(run=rec, kind=kind, message=message[:500], severity=severity,
                                          obligation=obligation, attempt=attempt, auto_fixed=fixed,
                                          resolved_at=timezone.now() if fixed else None,
                                          resolution="Corrected automatically." if fixed else ""))

    # 1. Ledger vs bills
    obligations = PaymentObligation.unscoped.select_related("institution").order_by("pk")
    checked = 0
    for ob in obligations.iterator(chunk_size=500):
        checked += 1
        before = ledger.ledger_view(ob)
        posted = ledger.sync_obligation(ob, actor=actor)
        if posted and trigger != "first_run" and (before["billed"] or before["paid"] or before["waived"]):
            flag("ledger_drift", f"{ob.reference}: ledger corrected ({len(posted)} journal(s)).",
                 severity="info", obligation=ob, fixed=True)
    rec.checked_obligations = checked

    # 2. Online attempts vs the provider
    since = timezone.now() - datetime.timedelta(days=days)
    attempts = (PaymentAttempt.unscoped.select_related("obligation")
                .filter(provider__in=providers.ONLINE, created_at__gte=since))
    rec.checked_attempts = attempts.count()
    stale_hours = 24
    for att in attempts.filter(status__in=("initiated", "pending", "provider_processing")):
        prov = providers.get(att.provider)
        if verify_with_provider and prov.is_enabled():
            try:
                result = prov.verify(att)
            except providers.ProviderUnreachable as exc:
                mark_attempt_unverified(att, str(exc))
                result = None
            except providers.ProviderError:
                result = None
            if result is not None and result.paid:
                record_provider_result(att, result, via="reconcile")
                flag("provider_paid_not_recorded",
                     f"{att.provider_reference}: the provider reports {result.amount} paid; recorded now and "
                     "sent to the Director of Finance.", obligation=att.obligation, attempt=att, fixed=True)
                continue
        if att.created_at < timezone.now() - datetime.timedelta(hours=stale_hours) and att.checkout_url:
            flag("stale_pending", f"{att.provider_reference} has been pending for more than {stale_hours} hours.",
                 severity="info", obligation=att.obligation, attempt=att)

    for att in attempts.filter(status="provider_reported_paid"):
        recorded = (att.verify_result or {}).get("amount")
        if verify_with_provider and providers.get(att.provider).is_enabled():
            try:
                result = providers.get(att.provider).verify(att)
            except providers.ProviderError:
                result = None
            if result is not None and not result.paid:
                flag("recorded_not_paid", f"{att.provider_reference}: recorded as paid but the provider now says "
                     f"'{result.status}'. Check before confirming.", severity="critical",
                     obligation=att.obligation, attempt=att)
            elif result is not None and recorded and result.amount is not None \
                    and Decimal(str(recorded)) != result.amount:
                flag("amount_mismatch", f"{att.provider_reference}: recorded {recorded}, provider {result.amount}.",
                     severity="critical", obligation=att.obligation, attempt=att)

    dupes = (attempts.filter(status="provider_reported_paid").values("obligation")
             .annotate(n=Count("pk")).filter(n__gt=1))
    for row in dupes:
        ob = PaymentObligation.unscoped.get(pk=row["obligation"])
        flag("double_payment", f"{ob.reference}: {row['n']} successful online payments. Refund the extra "
             "payment(s) or keep them as credit.", severity="critical", obligation=ob)

    sla_days = 3
    late = PaymentObligation.unscoped.filter(status__in=("provider_reported_paid", "awaiting_finance_confirmation",
                                                         "finance_confirmation_overdue"),
                                             updated_at__lt=timezone.now() - datetime.timedelta(days=sla_days))
    for ob in late:
        flag("unconfirmed_too_long", f"{ob.reference} has waited more than {sla_days} days for Finance.",
             obligation=ob)

    # 3. Ledger integrity
    for j in ledger.unbalanced_journals():
        flag("unbalanced_journal", f"Journal {j.key} does not balance.", severity="critical",
             obligation=j.obligation)

    ReconciliationIssue.objects.bulk_create(issues)
    rec.issues_found = len(issues)
    rec.auto_fixed = sum(1 for i in issues if i.auto_fixed)
    rec.summary = {k: sum(1 for i in issues if i.kind == k) for k in {i.kind for i in issues}}
    rec.finished_at = timezone.now()
    rec.save()
    needs_person = [i for i in issues if not i.auto_fixed and i.severity != "info"]
    if needs_person:
        _notify_finance(rec, len(needs_person))
    return rec


def _notify_finance(rec, count):
    try:
        from notifications.services import notify

        from .services import final_approvers

        for user in final_approvers():
            notify(user, title=f"Payments reconciliation: {count} item(s) to check",
                   body="Last night's reconciliation found differences between NBTE's records and the provider.",
                   url=f"/qa/finance/reconciliation/{rec.pk}/")
    except Exception:
        logger.exception("could not notify Finance about reconciliation run %s", rec.pk)
