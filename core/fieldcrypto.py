"""Field-level encryption for personal financial data (X33, S-28).

Fernet (AES-128-CBC + HMAC-SHA256, from the `cryptography` package, pinned in
requirements.txt) with the key from the environment variable
``FIELD_ENCRYPTION_KEY`` (settings.FIELD_ENCRYPTION_KEY). Several keys may be
given, comma-separated: the FIRST encrypts, all of them decrypt (rotation).

A key may be a Fernet key (``python -c "from cryptography.fernet import
Fernet; print(Fernet.generate_key().decode())"``) or any long random string
(it is stretched with SHA-256). Production refuses to start without it
(nbte_ict/settings/prod.py). Development falls back to a key derived from
SECRET_KEY so the dev server works without a .env - never use that in
production.
"""
import base64
import hashlib

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from cryptography.fernet import Fernet, InvalidToken, MultiFernet
except ImportError as exc:  # pragma: no cover - cryptography is a hard requirement
    raise ImproperlyConfigured("The 'cryptography' package is required for field encryption.") from exc


class DecryptionError(Exception):
    pass


def _as_fernet_key(raw):
    raw = raw.strip()
    try:
        if len(base64.urlsafe_b64decode(raw.encode() + b"=" * (-len(raw) % 4))) == 32 and len(raw) >= 43:
            return raw.encode()
    except Exception:
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


def _keys():
    configured = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    if configured:
        return [k for k in (part.strip() for part in configured.split(",")) if k]
    if getattr(settings, "DEBUG", False) or getattr(settings, "FIELD_ENCRYPTION_DEV_FALLBACK", False):
        return ["dev-fallback:" + settings.SECRET_KEY]
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not set (see .env.example).")


def _fernet():
    return MultiFernet([Fernet(_as_fernet_key(k)) for k in _keys()])


def encrypt(value):
    if value in (None, ""):
        return ""
    return _fernet().encrypt(str(value).encode()).decode()


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("Cannot decrypt: wrong FIELD_ENCRYPTION_KEY or damaged value.") from exc


def blind_index(value):
    """Keyed hash for equality checks (duplicate detection) without decrypting."""
    if not value:
        return ""
    key = _as_fernet_key(_keys()[0])
    return hashlib.sha256(key + b"|" + str(value).encode()).hexdigest()


def mask(last4, length=10):
    """'•••••• 4821' style mask from the last four digits."""
    last4 = (last4 or "")[-4:]
    return ("•" * max(0, length - 4 - 2)) + " " + last4 if last4 else ""
