"""Payment provider abstraction (Master Plan §10.1).

Application code only ever calls the interface in :mod:`.base`:

    provider = providers.get("paystack")
    if provider.is_enabled(): attempt = provider.initiate(obligation, payer, amount, request=request)

Four adapters: Remita, Paystack, bank transfer and offline (waiver). A
provider's report is *evidence*: nothing here changes an obligation's state —
``payments.services`` does that, and it can never confirm a payment.
"""
from .bank_transfer import BankTransferProvider
from .base import PaymentProvider, ProviderError, VerificationResult
from .offline import OfflineProvider
from .paystack import PaystackProvider
from .remita import RemitaProvider

PROVIDERS = {p.code: p for p in (RemitaProvider(), PaystackProvider(), BankTransferProvider(), OfflineProvider())}
ONLINE = ("remita", "paystack")


def get(code):
    try:
        return PROVIDERS[code]
    except KeyError:
        raise ProviderError(f"Unknown payment method '{code}'.") from None


def enabled_methods(*, include_offline=False):
    """Methods to offer institutions, in qa.payment.method_order. Bank transfer
    is forced on when nothing else is enabled (the §10.1 fallback)."""
    import config

    order = config.get("qa.payment.method_order", ["remita", "paystack", "bank_transfer"]) or []
    if isinstance(order, str):
        order = [o.strip() for o in order.split(",")]
    codes = [c for c in order if c in PROVIDERS] + [c for c in PROVIDERS if c not in order]
    out = [PROVIDERS[c] for c in codes if c != "offline" and PROVIDERS[c].is_enabled()]
    if not out:
        out = [PROVIDERS["bank_transfer"]]
    if include_offline and PROVIDERS["offline"].is_enabled():
        out.append(PROVIDERS["offline"])
    return out


def status_report():
    """[(code, label, enabled_setting, configured, effective, missing_env)] for
    the Payments settings page and the seed summary."""
    rows = []
    for p in PROVIDERS.values():
        rows.append({"code": p.code, "label": p.label, "setting": p.setting_enabled(),
                     "configured": p.is_configured(), "enabled": p.is_enabled(), "missing": p.missing_env(),
                     "simulated": p.is_simulated()})
    return rows
