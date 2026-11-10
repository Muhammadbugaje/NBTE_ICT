"""Test helper: give a staff user an accepted (generated, fictional) signature
quickly. The demo image is drawn once per process and reused, so fixtures that
create Directors stay fast. Not used by application code."""
from django.utils import timezone

_PNG = {}


def give_signature(user, ink="navy"):
    from . import signature

    if ink not in _PNG:
        _PNG[ink] = signature.demo_signature_png("test-fixture", ink)
    png, info = _PNG[ink]
    sig = signature._store(user, png, info, None, signature.DEMO, 50, ink)
    signature._model().objects.filter(user=user, is_active=True).update(is_active=False, retired_at=timezone.now())
    sig.is_active, sig.accepted_at = True, timezone.now()
    sig.save()
    return sig


def give_if_required(user):
    from . import signature

    if signature.signature_required(user) and signature.active_for(user) is None:
        return give_signature(user)
    return None
