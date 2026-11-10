"""Full bank details for the authorized honoraria payment release only.

Only ``honoraria.schedules`` may call this module. Every release is logged
against the account so that access to full payee details is auditable.
"""
from core import fieldcrypto

from .models import BankDetailsLog, PayeeBankAccount
from .services import describe, log

ALLOWED_CALLERS = ("honoraria.schedules", "honoraria.tests")


def details_for_schedule(users, *, schedule_reference, generated_by, resource_persons=()):
    """Return full details for confirmed active payee accounts and log releases.

    User accounts are keyed by user ID; resource-person accounts use
    ``("resource_person", id)`` keys. Payees without confirmed accounts are
    omitted so the caller can block an incomplete payment run.
    """
    if not schedule_reference:
        raise ValueError("A payment schedule reference is required.")
    out = {}
    accounts = (PayeeBankAccount.objects.select_related("bank", "user")
                .filter(user__in=users, status=PayeeBankAccount.ACTIVE, confirmed_at__isnull=False))
    for acc in accounts:
        out[acc.user_id] = {"bank": acc.bank.name, "cbn_code": acc.bank.cbn_code,
                            "account_number": fieldcrypto.decrypt(acc.account_number_enc),
                            "account_name": acc.account_name}
        log(owner=acc.user or acc.resource_person.user, account=acc, actor=generated_by,
            action=BankDetailsLog.RELEASED,
            before=describe(acc), note=f"Payment schedule {schedule_reference}"[:255])
    resource_accounts = (PayeeBankAccount.objects.select_related("bank", "resource_person")
                         .filter(resource_person__in=resource_persons, status=PayeeBankAccount.ACTIVE,
                                 confirmed_at__isnull=False))
    for acc in resource_accounts:
        out[("resource_person", acc.resource_person_id)] = {
            "bank": acc.bank.name, "cbn_code": acc.bank.cbn_code,
            "account_number": fieldcrypto.decrypt(acc.account_number_enc),
            "account_name": acc.account_name,
        }
        log(owner=acc.user, account=acc, actor=generated_by, action=BankDetailsLog.RELEASED,
            before=describe(acc), note=f"Payment schedule {schedule_reference}"[:255])
    return out
