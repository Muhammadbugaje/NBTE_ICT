"""v2.2 — double-entry ledger for QA money.

Every bill, confirmed payment, waiver and cancellation is posted as a
balanced journal (debits = credits). Postings are derived from the bill's
own figures by :func:`sync_obligation`, which posts only the *difference*
between what the ledger holds and what the bill says, under a unique key — so
calling it twice, from a signal and from the nightly reconciliation, can never
double-post.

Accounts (seeded on first use):

    1000 Bank (NBTE TSA)                  asset
    1100 Institution receivables          asset
    2200 Institution credits / overpaid   liability
    4000 QA fee income                    income
    5000 Fees waived                      expense
    5100 Honoraria                        expense
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum

logger = logging.getLogger(__name__)
ZERO = Decimal("0.00")

ACCOUNTS = {
    "1000": ("Bank (NBTE TSA)", "asset"),
    "1100": ("Institution receivables", "asset"),
    "2200": ("Institution credits / overpaid", "liability"),
    "4000": ("QA fee income", "income"),
    "5000": ("Fees waived", "expense"),
    "5100": ("Honoraria", "expense"),
}
BANK, RECEIVABLE, CREDITS, INCOME, WAIVED, HONORARIA = "1000", "1100", "2200", "4000", "5000", "5100"


class LedgerError(Exception):
    pass


def account(code):
    from .models import LedgerAccount

    name, kind = ACCOUNTS[code]
    acct, _ = LedgerAccount.objects.get_or_create(code=code, defaults={"name": name, "kind": kind})
    return acct


def post(key, description, lines, *, obligation=None, institution=None, source="", actor=None):
    """Post one balanced journal. ``lines`` = [(account_code, debit, credit)].
    Returns the journal, or the existing one when ``key`` was already posted."""
    from .models import LedgerJournal, LedgerLine

    lines = [(code, Decimal(d or 0), Decimal(c or 0)) for code, d, c in lines if (d or c)]
    if not lines:
        return None
    debit = sum((d for _c, d, _x in lines), ZERO)
    credit = sum((c for _c, _d, c in lines), ZERO)
    if debit != credit:
        raise LedgerError(f"Journal {key} does not balance: debit {debit} ≠ credit {credit}.")
    existing = LedgerJournal.objects.filter(key=key).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            journal = LedgerJournal.objects.create(
                key=key, description=description[:255], obligation=obligation,
                institution=institution or getattr(obligation, "institution", None), source=source,
                posted_by=actor if getattr(actor, "pk", None) else None)
            LedgerLine.objects.bulk_create([LedgerLine(journal=journal, account=account(code), debit=d, credit=c)
                                            for code, d, c in lines])
    except IntegrityError:   # posted concurrently under the same key
        return LedgerJournal.objects.get(key=key)
    return journal


def _sum(obligation, code, side, sources=None):
    from .models import LedgerLine

    qs = LedgerLine.objects.filter(journal__obligation=obligation, account__code=code)
    if sources:
        qs = qs.filter(journal__source__in=sources)
    return qs.aggregate(s=Sum(side))["s"] or ZERO


def ledger_view(obligation):
    """What the ledger currently holds for one bill."""
    billed = _sum(obligation, INCOME, "credit") - _sum(obligation, INCOME, "debit")
    paid = _sum(obligation, BANK, "debit") - _sum(obligation, BANK, "credit", sources=["refund"])
    waived = _sum(obligation, WAIVED, "debit") - _sum(obligation, WAIVED, "credit")
    receivable = _sum(obligation, RECEIVABLE, "debit") - _sum(obligation, RECEIVABLE, "credit")
    credits = _sum(obligation, CREDITS, "credit") - _sum(obligation, CREDITS, "debit")
    return {"billed": billed, "paid": paid, "waived": waived, "receivable": receivable, "credits": credits}


def target_view(obligation):
    """What the ledger *should* hold, from the bill's own fields."""
    cancelled = obligation.status in ("cancelled", "expired")
    billed = ZERO if cancelled else Decimal(obligation.amount or 0)
    paid = Decimal(obligation.amount_paid or 0)
    waived = billed if obligation.status == "waived" else ZERO
    if obligation.status == "waived":
        waived = max(billed - paid, ZERO)
    overpaid = Decimal(obligation.overpaid_amount or 0)
    return {"billed": billed, "paid": paid, "waived": waived, "overpaid": overpaid}


def sync_obligation(obligation, *, actor=None, source_hint=""):
    """Post whatever is missing for ``obligation`` (idempotent). Returns the
    list of journals posted (empty when the ledger already matches)."""
    from .models import LedgerJournal

    have = ledger_view(obligation)
    want = target_view(obligation)
    posted = []
    seq = LedgerJournal.objects.filter(obligation=obligation).count()

    def key(kind):
        nonlocal seq
        seq += 1
        return f"ob{obligation.pk}:{kind}:{seq}"

    diff = want["billed"] - have["billed"]
    if diff:
        kind = "bill" if diff > 0 else "cancel"
        posted.append(post(key(kind), f"{'Bill' if diff > 0 else 'Bill reduced/cancelled'} {obligation.reference}",
                           [(RECEIVABLE, diff, 0), (INCOME, 0, diff)] if diff > 0
                           else [(INCOME, -diff, 0), (RECEIVABLE, 0, -diff)],
                           obligation=obligation, source=kind, actor=actor))
    diff = want["paid"] - have["paid"]
    if diff:
        # Money in: settles the receivable; anything beyond the bill is a credit held for the institution.
        over = max(min(diff, want["overpaid"] - have["credits"]), ZERO) if diff > 0 else ZERO
        to_receivable = diff - over
        lines = [(BANK, diff, 0)] if diff > 0 else [(BANK, 0, -diff)]
        if to_receivable > 0:
            lines.append((RECEIVABLE, 0, to_receivable))
        elif to_receivable < 0:
            lines.append((RECEIVABLE, -to_receivable, 0))
        if over:
            lines.append((CREDITS, 0, over))
        posted.append(post(key("payment"), f"Payment confirmed on {obligation.reference}", lines,
                           obligation=obligation, source="payment", actor=actor))
    diff = want["waived"] - have["waived"]
    if diff:
        posted.append(post(key("waiver"), f"Waiver on {obligation.reference}",
                           [(WAIVED, diff, 0), (RECEIVABLE, 0, diff)] if diff > 0
                           else [(RECEIVABLE, -diff, 0), (WAIVED, 0, -diff)],
                           obligation=obligation, source="waiver", actor=actor))
    return [j for j in posted if j is not None]


def post_honorarium(schedule_line_key, amount, description, *, actor=None):
    """Honoraria paid out of the bank (from honoraria.mark_paid)."""
    amount = Decimal(amount or 0)
    if amount <= 0:
        return None
    return post(f"hon:{schedule_line_key}", description, [(HONORARIA, amount, 0), (BANK, 0, amount)],
                source="honorarium", actor=actor)


def unbalanced_journals():
    from .models import LedgerJournal

    out = []
    for j in LedgerJournal.objects.annotate(d=Sum("lines__debit"), c=Sum("lines__credit")).exclude(d=None):
        if (j.d or ZERO) != (j.c or ZERO):
            out.append(j)
    return out


def trial_balance():
    """[(account, debit_total, credit_total, balance)] for the ledger page."""
    from .models import LedgerAccount

    for code in ACCOUNTS:
        account(code)
    rows = []
    for acct in LedgerAccount.objects.annotate(d=Sum("lines__debit"), c=Sum("lines__credit")).order_by("code"):
        d, c = acct.d or ZERO, acct.c or ZERO
        rows.append((acct, d, c, d - c if acct.kind in ("asset", "expense") else c - d))
    return rows


def on_obligation_saved(sender, instance, created, **kwargs):
    """Signal receiver: keep the ledger in step after every committed change."""
    if kwargs.get("raw"):
        return
    pk = instance.pk

    def _sync():
        from .models import PaymentObligation

        ob = PaymentObligation.unscoped.filter(pk=pk).first()
        if ob is None:
            return
        try:
            sync_obligation(ob)
        except Exception:  # the ledger must never block a payment; reconciliation retries
            logger.exception("ledger sync failed for obligation %s", pk)

    transaction.on_commit(_sync)
