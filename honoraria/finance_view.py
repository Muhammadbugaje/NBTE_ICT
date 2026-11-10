"""v2.2 — Finance's view of payee bank details and the payment roller.

* The Director of Finance, the ES and super admins (not while acting as
  someone else) may *view* a payee's full bank details. Every view is logged
  in the append-only BankDetailsLog. Only the owner can change them.
* The roller lists everyone owed an honorarium — internal document
  assessment, internal SSQ review, external (resource person) SSQ review and
  visits — in the order they should be paid (oldest finished work first by
  default, setting ``honoraria.roller.mode``), with their bank-details status,
  so Finance always knows who is next.
"""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

import config
from django.core.exceptions import PermissionDenied
from django.db.models import Min, Q

from core import fieldcrypto

from .models import BankDetailsLog, HonorariumEntry, PayeeBankAccount


def can_view_bank_details(user, request=None):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if request is not None and getattr(request, "is_impersonating", False):
        return False
    import org
    from payments.services import is_final_approver

    return bool(user.is_superuser or org.is_es(user) or org.is_super_admin(user) or is_final_approver(user))


def account_for(user=None, resource_person=None):
    qs = PayeeBankAccount.objects.select_related("bank").filter(status=PayeeBankAccount.ACTIVE)
    if resource_person is not None:
        return qs.filter(resource_person=resource_person).first() or (
            qs.filter(user=resource_person.user).first() if resource_person.user_id else None)
    return qs.filter(user=user).first() if user is not None else None


def _accounts_for_rows(rows):
    """Same choice as ``account_for`` for many payees at once."""
    user_ids = {getattr(r["user"], "pk", None) for r in rows} | {
        getattr(r["resource_person"], "user_id", None) for r in rows if r["resource_person"] is not None}
    rp_ids = {r["resource_person"].pk for r in rows if r["resource_person"] is not None}
    by_user, by_rp = {}, {}
    from django.db.models import Q

    for acc in (PayeeBankAccount.objects.select_related("bank").filter(status=PayeeBankAccount.ACTIVE)
                .filter(Q(user_id__in=user_ids - {None}) | Q(resource_person_id__in=rp_ids))
                .order_by("-created_at", "-pk")):
        if acc.user_id:
            by_user.setdefault(acc.user_id, acc)          # first() under Meta.ordering: newest
        if acc.resource_person_id:
            by_rp.setdefault(acc.resource_person_id, acc)

    def pick(user, resource_person):
        if resource_person is not None:
            return by_rp.get(resource_person.pk) or (
                by_user.get(resource_person.user_id) if resource_person.user_id else None)
        return by_user.get(user.pk) if user is not None else None

    return pick


def reveal_for_finance(request, account, *, reason=""):
    """Full number for the DoF / ES / super admin. Logged with who and why."""
    if not can_view_bank_details(request.user, request):
        raise PermissionDenied("Only the Director of Finance, the ES and super admins can view bank details.")
    from core import ratelimit

    from .services import describe, log

    log(owner=account.user or getattr(account.resource_person, "user", None), account=account,
        actor=request.user, action=BankDetailsLog.REVEALED, before=describe(account),
        note=f"Viewed by Finance/ES: {reason or 'payment preparation'}"[:255], ip=ratelimit.client_ip(request))
    return {"bank": account.bank.name, "cbn_code": account.bank.cbn_code, "account_name": account.account_name,
            "account_number": fieldcrypto.decrypt(account.account_number_enc),
            "confirmed": bool(account.confirmed_at), "name_match": account.name_match}


def roller():
    """Everyone owed money, in payment order. Returns a list of dicts:
    {key, user, resource_person, name, kinds, items, amount, oldest, bank, bank_ok}."""
    from inspection.models import ServiceRecord

    from .models import HonorariumServiceRecordClaim
    from .schedules import academic_rate

    rows = OrderedDict()

    def row(key, **base):
        if key not in rows:
            rows[key] = {"key": key, "kinds": set(), "items": 0, "amount": Decimal("0"), "oldest": None, **base}
        return rows[key]

    for e in (HonorariumEntry.objects.filter(status=HonorariumEntry.ACCRUED)
              .select_related("payee_user", "institution").order_by("accrued_at")):
        r = row(("user", e.payee_user_id), user=e.payee_user, resource_person=None,
                name=e.payee_user.get_full_name() or e.payee_user.email)
        r["kinds"].add(e.get_kind_display())
        r["items"] += e.quantity
        r["amount"] += e.amount
        r["oldest"] = min(filter(None, [r["oldest"], e.accrued_at]))
    records = (ServiceRecord.unscoped.filter(resource_person__isnull=False, visit_count__gt=0, allowance_paid=False)
               .exclude(honorarium_claims__status__in=(HonorariumServiceRecordClaim.RESERVED,
                                                       HonorariumServiceRecordClaim.PAID))
               .select_related("resource_person", "resource_person__user").distinct())
    rate = academic_rate()
    for rec in records:
        rp = rec.resource_person
        r = row(("rp", rp.pk), user=rp.user, resource_person=rp, name=str(rp))
        r["kinds"].add("Visit (resource person)")
        r["items"] += rec.visit_count
        r["amount"] += rate * rec.visit_count
        when = getattr(rec, "served_to", None)
        if when is not None:
            from datetime import datetime, time
            from django.utils import timezone

            when = timezone.make_aware(datetime.combine(when, time.min)) if not hasattr(when, "hour") else when
            r["oldest"] = min(filter(None, [r["oldest"], when]))
    out = list(rows.values())
    accounts = _accounts_for_rows(out)            # v2.2: one query, not one or two per payee
    for r in out:
        acc = accounts(r["user"], r["resource_person"])
        r["bank"] = acc
        r["bank_ok"] = bool(acc and acc.confirmed_at)
        r["kinds"] = sorted(r["kinds"])
    mode = str(config.get("honoraria.roller.mode", "oldest_first") or "oldest_first")
    if mode == "largest_first":
        out.sort(key=lambda r: (-r["amount"], r["oldest"] or 0))
    else:
        from django.utils import timezone

        far = timezone.now()
        out.sort(key=lambda r: (not r["bank_ok"], r["oldest"] or far))
    for n, r in enumerate(out, 1):
        r["position"] = n
    return out
