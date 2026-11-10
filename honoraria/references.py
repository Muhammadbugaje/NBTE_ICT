"""v2.3: reference numbers for honoraria that never block a payment.

Root cause of "No active reference format 'honoraria'": the ``honoraria``
row was added to ``qa_core.seed_data.REFERENCE_FORMATS`` after most
databases had been seeded, and nothing re-ran ``seed_startup`` / ``seed_nbte``
on them, so ``qa_core.references.issue("honoraria")`` found no row.

The fix, in three layers:
* migration ``honoraria.0010`` creates the formats on every existing database;
* seed set ``honoraria.references`` creates them on fresh ones (idempotent);
* ``issue`` below re-creates a missing format on the spot, and if Settings has
  switched the format off it still issues a unique fallback reference (and
  logs a warning) rather than stopping a payment.
"""
from __future__ import annotations

import logging
import secrets

from django.utils import timezone

logger = logging.getLogger(__name__)

PAYSTACK_SAFE = "A-Z0-9.=-"
# code, name, pattern, reset, check, allowed, scope
FORMATS = [
    ("honoraria", "Honoraria schedule", "NBTE-HON-{YEAR}-{SEQ:6}", "yearly", "none", PAYSTACK_SAFE, "global"),
    ("honoraria_file", "Honorarium file (per institution)", "NBTE-HF-{YEAR}-{SEQ:5}", "yearly", "none",
     PAYSTACK_SAFE, "global"),
]
FALLBACK_PREFIX = {"honoraria": "NBTE-HON", "honoraria_file": "NBTE-HF"}


def ensure_formats(model=None):
    """Create any missing honoraria reference format. Never changes an
    existing row (Settings -> References owns it). Returns the number created."""
    if model is None:
        from qa_core.models import ReferenceFormat as model
    created = 0
    for code, name, pattern, reset, check, allowed, scope in FORMATS:
        _obj, was_created = model.objects.get_or_create(
            code=code, defaults={"name": name, "pattern": pattern, "reset_period": reset, "check_digit": check,
                                 "allowed_chars": allowed, "sequence_scope": scope, "is_active": True})
        created += was_created
    return created


def _fallback(code, unique_check=None):
    prefix = FALLBACK_PREFIX.get(code, "NBTE-HON")
    for _ in range(10):
        ref = f"{prefix}-{timezone.now():%Y%m%d%H%M%S}-{secrets.token_hex(2).upper()}"
        if unique_check is None or not unique_check(ref):
            return ref
    return f"{prefix}-{secrets.token_hex(6).upper()}"


def issue(code, *, unique_check=None):
    """The next reference for ``code`` (``honoraria`` or ``honoraria_file``)."""
    from qa_core import references

    try:
        return references.issue(code, inst="NBTE")
    except references.ReferenceError_ as exc:
        if "No active reference format" not in str(exc):
            logger.warning("honoraria reference %s could not be issued (%s); using a fallback", code, exc)
            return _fallback(code, unique_check)
    try:
        if ensure_formats():
            logger.warning("honoraria reference format %r was missing and has been created", code)
        return references.issue(code, inst="NBTE")
    except Exception:  # an inactive format, or anything else: never block a payment
        logger.warning("honoraria reference format %r is not active; using a fallback reference", code,
                       exc_info=True)
        return _fallback(code, unique_check)
