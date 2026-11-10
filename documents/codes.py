"""Tokens, short codes and digests for the verification registry (S-19, S-20).

* ``new_token()``      144-bit URL-safe token (``secrets.token_urlsafe(18)``); the QR carries only
                       ``<verify base>/v/<token>/`` - no personal data.
* ``new_short_code()`` ``NBTE-XXXX-XXXX`` from a 32-symbol alphabet without 0/O/1/I (typed on Scan).
* ``digest(payload)``  SHA-256 of the canonical JSON of the certified facts (EE Hub ``make_digest``).
"""
import hashlib
import json
import re
import secrets

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 symbols, no 0/O/1/I


def new_token():
    return secrets.token_urlsafe(18)


def new_short_code():
    body = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"NBTE-{body[:4]}-{body[4:]}"


def normalise_short_code(text):
    """Accept what people type (lower case, spaces, missing dashes).
    Returns ``NBTE-XXXX-XXXX`` or '' when it cannot be a short code."""
    raw = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    if raw.startswith("NBTE"):
        raw = raw[4:]
    if len(raw) != 8 or any(c not in ALPHABET for c in raw):
        return ""
    return f"NBTE-{raw[:4]}-{raw[4:]}"


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def digest(payload):
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def hashed(text):
    """What the verification log keeps when a code did not match anything."""
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:40]
