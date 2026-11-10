"""Staff bank details (X33) - the only code that reads or writes them.

Rules (owner decisions, 28 Oct 2026):
* only the OWNER (request.user, never while "acting as") may view or change;
* a change needs the owner's password; every change is logged and e-mailed;
* the owner is asked to confirm the details are correct (banner until
  confirmed; again every ``profile.bank.reconfirm_days`` days, default 365);
* masked everywhere; the full number is shown only to the owner on request
  (after the password) and released only to the payment schedule generator
  (honoraria.disbursement, Phase H).
"""
import re
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from config import registry as config
from core import fieldcrypto, ratelimit

from .models import Bank, BankDetailsLog, PayeeBankAccount

NUBAN_RE = re.compile(r"^\d{10}$")


class NotOwner(PermissionDenied):
    pass


# ---------------------------------------------------------------------------
# Who may touch bank details
# ---------------------------------------------------------------------------
def assert_owner_request(request):
    """Only the signed-in person, for themselves, never while impersonating."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise NotOwner("Sign in to see your bank details.")
    if getattr(request, "is_impersonating", False):
        log(owner=user, actor=getattr(request, "impersonator", None), action=BankDetailsLog.BLOCKED,
            note="Blocked: opened while acting as this person", ip=ratelimit.client_ip(request))
        raise NotOwner("Bank details can only be seen and changed by their owner. They are hidden while "
                       "acting as someone else.")
    if _payee_fields(user) is None:
        raise NotOwner("Bank details are available only to staff and resource persons.")
    return user


def _payee_fields(user):
    user_type = getattr(user, "user_type", "")
    if user_type == "staff":
        return {"user": user}
    if user_type == "resource_person":
        from inspection.models import ResourcePerson

        person = ResourcePerson.objects.filter(user=user).first()
        return {"resource_person": person} if person else None
    return None


def log(*, owner, action, account=None, actor=None, before="", after="", note="", ip=None):
    return BankDetailsLog.objects.create(owner=owner, account=account, actor=actor or owner, action=action,
                                         before=before, after=after, note=note[:255], ip=ip or None)


# ---------------------------------------------------------------------------
# Reading (masked)
# ---------------------------------------------------------------------------
def active_account(user):
    if user is None or not getattr(user, "pk", None):
        return None
    fields = _payee_fields(user)
    if fields is None:
        return None
    return (PayeeBankAccount.objects.select_related("bank")
            .filter(**fields, status=PayeeBankAccount.ACTIVE).first())


def describe(account):
    """Masked summary: 'Demo Bank ••4821'."""
    if account is None:
        return ""
    return f"{account.bank.name} ••{account.account_last4}"


def summary(account):
    """Dict for templates and the API - masked only."""
    if account is None:
        return None
    return {
        "bank": account.bank.name, "bank_code": account.bank.code, "masked": account.masked,
        "last4": account.account_last4, "account_name": account.account_name,
        "name_match": account.name_match, "name_match_label": account.get_name_match_display(),
        "confirmed_at": account.confirmed_at, "updated_at": account.created_at,
        "needs_confirmation": needs_confirmation(account),
    }


def reconfirm_days():
    try:
        return max(0, int(config.get("profile.bank.reconfirm_days", 365)))
    except (TypeError, ValueError):
        return 365


def needs_confirmation(account):
    if account is None:
        return False
    if account.confirmed_at is None:
        return True
    days = reconfirm_days()
    return bool(days) and account.confirmed_at < timezone.now() - timedelta(days=days)


def banner_for(user):
    """None, or {"kind": "add"|"confirm", ...} for the site-wide prompt."""
    if not bool(config.get("profile.bank.banner_enabled", True)):
        return None
    if user is None or not user.is_authenticated or _payee_fields(user) is None:
        return None
    account = active_account(user)
    if account is None:
        return {"kind": "add"}
    if needs_confirmation(account):
        return {"kind": "confirm", "summary": summary(account)}
    return None


def reveal_for_owner(request, account, password):
    """Full number for the owner, after their password. Logged."""
    from core import reauth

    user = assert_owner_request(request)
    fields = _payee_fields(user)
    if fields is None:
        raise NotOwner("Not your account.")
    if ("user" in fields and account.user_id != user.pk) or (
        "resource_person" in fields and account.resource_person_id != fields["resource_person"].pk
    ):
        raise NotOwner("Not your account.")
    reauth.require(request, password)
    log(owner=user, account=account, action=BankDetailsLog.REVEALED, ip=ratelimit.client_ip(request))
    return fieldcrypto.decrypt(account.account_number_enc)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _name_tokens(text):
    return {t for t in re.split(r"[^a-z]+", (text or "").lower()) if len(t) > 1}


def name_match(user, account_name):
    if getattr(user, "user_type", "") == "resource_person":
        from inspection.models import ResourcePerson

        person = ResourcePerson.objects.filter(user=user).first()
        owner_name = person.full_name if person else ""
    else:
        owner_name = f"{user.first_name} {user.last_name}"
    mine = _name_tokens(owner_name)
    theirs = _name_tokens(account_name)
    if not mine or not theirs:
        return "unchecked"
    if mine <= theirs:
        return "exact"
    if mine & theirs:
        return "partial"
    return "mismatch"


def clean_input(bank, account_number, account_name):
    errors = {}
    number = re.sub(r"\s+", "", str(account_number or ""))
    if not NUBAN_RE.match(number):
        errors["account_number"] = "A NUBAN account number is exactly 10 digits."
    if bank is None or not getattr(bank, "is_active", False):
        errors["bank"] = "Choose your bank from the list."
    name = " ".join(str(account_name or "").split())
    if len(name) < 3:
        errors["account_name"] = "Enter the name on the account, as the bank has it."
    if errors:
        raise ValidationError(errors)
    return bank, number, name


def _email_owner(user, subject, body, details=()):
    if not user.email:
        return
    try:
        from core.email_utils import send_templated_email

        send_templated_email(subject, [user.email], "emails/account_security_notice.html",
                             {"subject": subject, "body": body, "details": list(details),
                              "name": user.get_full_name() or user.email}, category="security")
    except Exception:
        pass


@transaction.atomic
def save_for_owner(request, *, bank, account_number, account_name, password, via="web"):
    """Create or replace the owner's bank details. Needs the password every time."""
    from core import reauth

    user = assert_owner_request(request) if via == "web" else request.user
    fields = _payee_fields(user)
    if fields is None:
        raise NotOwner("Bank details are available only to staff and resource persons.")
    if via == "web":
        if not reauth.confirm(request, password or ""):
            raise reauth.ReauthRequired("That password is not right. Your bank details were not changed.")
    else:
        reauth.require_for_user(user, password)
    bank, number, name = clean_input(bank, account_number, account_name)
    return _replace(user, fields, bank, number, name, actor=user, ip=ratelimit.client_ip(request), via=via)


def _replace(user, fields, bank, number, name, *, actor, ip=None, via="web"):
    now = timezone.now()
    old = (PayeeBankAccount.objects.select_for_update().select_related("bank")
           .filter(**fields, status=PayeeBankAccount.ACTIVE).first())
    before = describe(old)
    if old is not None:
        if (old.bank_id == bank.pk and old.account_hash == fieldcrypto.blind_index(number)
                and old.account_name == name):
            old.confirmed_at = now
            old.save(update_fields=["confirmed_at", "updated_at"])
            log(owner=user, account=old, actor=actor, action=BankDetailsLog.CONFIRMED, before=before,
                after=before, note=f"Same details saved again ({via})", ip=ip)
            return old
        old.status, old.superseded_at = PayeeBankAccount.SUPERSEDED, now
        old.save(update_fields=["status", "superseded_at", "updated_at"])
    account = PayeeBankAccount.objects.create(
        **fields, bank=bank, account_number_enc=fieldcrypto.encrypt(number),
        account_hash=fieldcrypto.blind_index(number), account_last4=number[-4:], account_name=name,
        name_match=name_match(user, name), status=PayeeBankAccount.ACTIVE, confirmed_at=now)
    after = describe(account)
    action = BankDetailsLog.CHANGED if old is not None else BankDetailsLog.CREATED
    log(owner=user, account=account, actor=actor, action=action, before=before, after=after,
        note=f"Saved by the owner ({via})", ip=ip)
    when = timezone.localtime(now).strftime("%d %b %Y, %H:%M")
    if old is not None:
        _email_owner(user, "Your bank details were changed",
                     "The bank account used for your NBTE payments (SSQ reviews, visits and other payments) "
                     "was changed.", [f"Before: {before}", f"Now: {after}", f"When: {when}"])
    else:
        _email_owner(user, "Your bank details were added",
                     "A bank account was added for your NBTE payments.", [f"Account: {after}", f"When: {when}"])
    return account


def confirm_correct(request):
    user = assert_owner_request(request)
    account = active_account(user)
    if account is None:
        raise ValidationError("Add your bank details first.")
    account.confirmed_at = timezone.now()
    account.save(update_fields=["confirmed_at", "updated_at"])
    log(owner=user, account=account, action=BankDetailsLog.CONFIRMED, before=describe(account),
        after=describe(account), ip=ratelimit.client_ip(request))
    return account


def history_for_owner(user, limit=10):
    return list(BankDetailsLog.objects.filter(owner=user).exclude(action=BankDetailsLog.BLOCKED)[:limit])


def active_banks():
    return Bank.objects.filter(is_active=True).order_by("sort_order", "name")
