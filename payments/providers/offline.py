"""Offline / waived: no money moves. A waiver is the two-step PendingAction
`payments.waive` (Finance officer → Director of Finance) with a reason."""
from .base import PaymentProvider


class OfflineProvider(PaymentProvider):
    code = "offline"
    label = "Waiver (no payment)"
    online = False
