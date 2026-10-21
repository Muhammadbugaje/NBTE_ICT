"""Bank transfer (always available): slip → pay at the bank → "I have paid"
with proof → Finance verifies against the statement → Director confirms."""
from .base import PaymentProvider


class BankTransferProvider(PaymentProvider):
    code = "bank_transfer"
    label = "Bank transfer or deposit"
    online = False

    def supports_partial(self):
        return True
