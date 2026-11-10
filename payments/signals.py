"""Payment signals other apps listen to (v2.1 F.18, owner decision D-5).

``obligation_confirmed`` is sent once, after the transaction commits, when the
Director of Finance's final decision settles a bill (balance zero). Keyword
arguments: ``obligation``, ``verification``, ``actor``. Onboarding listens to
auto-submit the application (built in a later task); nothing here depends on
a listener being connected.

The older ``payments.services.payment_settled`` signal (also sent for
waivers) keeps working for the existing accreditation listeners.
"""
from django.dispatch import Signal

obligation_confirmed = Signal()
