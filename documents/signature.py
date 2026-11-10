"""Staff signatures (X23, S-13, S-07) - ported from EE Hub results/signature.py
(person -> user). Image processing lives in documents/imaging.py.

Public API used by later phases (documents issued from approvals, memos):

* ``png_for(user, at=None)``  -> transparent PNG bytes of the signature the user
  had in force at ``at`` (the one accepted most recently before then), else
  their current one, else None.
* ``data_uri_for(user, at=None)`` -> "data:image/png;base64,..." for HTML/PDF.
* ``signature_required(user)`` -> True for staff at or above
  ``qa.signatures.required_min_rank`` (default 100 = Director).
* ``require_signature_for_approval(user)`` -> raises ``SignatureMissing`` (a
  ValidationError with a friendly message pointing at My profile -> Signature)
  when a required signature is missing and
  ``qa.signatures.block_approvals_without_signature`` is on. Call it at the
  start of every approval that generates a document or memo.

No view ever serves a signature image by URL.
"""
import base64
import hashlib

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from config import registry as config
from core import audit

from .imaging import INKS, SignatureError, demo_photo, demo_signature_png, extract, from_drawing  # noqa: F401

DRAWN = "drawn on screen"
DEMO = "demo (generated)"
MAX_PHOTO_BYTES = 12 * 1024 * 1024


class SignatureMissing(ValidationError):
    """A Director (or above) tried to approve something that generates a
    document before adding a signature."""


# ---------------------------------------------------------------------------
# Who must sign
# ---------------------------------------------------------------------------
def rank_of(user):
    """Seniority used for the signature rule: the highest rank among the
    user's roles (Role.rank; Director = 100, ES = 999)."""
    if user is None or not getattr(user, "pk", None):
        return 0
    ranks = []
    role = getattr(user, "role", None)
    if role is not None:
        ranks.append(role.rank or 0)
    extra = getattr(user, "roles", None)  # pb-hr may add more roles per person
    if extra is not None and hasattr(extra, "values_list"):
        try:
            ranks += [r or 0 for r in extra.values_list("rank", flat=True)]
        except Exception:
            pass
    return max(ranks or [0])


def min_rank():
    try:
        return int(config.get("qa.signatures.required_min_rank", 100))
    except (TypeError, ValueError):
        return 100


def signature_required(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "user_type", "staff") != "staff":
        return False
    return rank_of(user) >= min_rank()


def blocking_enabled():
    return bool(config.get("qa.signatures.block_approvals_without_signature", True))


def require_signature_for_approval(user):
    if signature_required(user) and active_for(user) is None and blocking_enabled():
        raise SignatureMissing(
            "Add your signature first: My profile → Signature. Documents you approve carry your signature, "
            "so approvals that produce a document or memo wait until it is on file.")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _model():
    from .models import StaffSignature
    return StaffSignature


def active_for(user):
    if user is None or not getattr(user, "pk", None):
        return None
    return _model().objects.filter(user=user, is_active=True).first()


def pending_for(user):
    if user is None or not getattr(user, "pk", None):
        return None
    return _model().objects.filter(user=user, accepted_at__isnull=True, is_active=False).first()


def _read(field):
    if not field:
        return None
    try:
        with field.open("rb") as fh:
            return fh.read()
    except Exception:
        return None


def signature_at(user, at=None):
    if user is None or not getattr(user, "pk", None):
        return None
    sig = None
    if at is not None:
        sig = (_model().objects.filter(user=user, accepted_at__isnull=False, accepted_at__lte=at)
               .order_by("-accepted_at").first())
    return sig or active_for(user)


def png_for(user, at=None):
    """Transparent PNG bytes of the signature ``user`` had at ``at`` (or now)."""
    sig = signature_at(user, at)
    if sig is None:
        return None
    return _read(sig.image)


def data_uri_for(user, at=None):
    png = png_for(user, at)
    return "data:image/png;base64," + base64.b64encode(png).decode() if png else ""


def preview_uri(sig):
    """Inline data URI for the OWNER's preview (pending or active row)."""
    png = _read(sig.image) if sig else None
    return "data:image/png;base64," + base64.b64encode(png).decode() if png else ""


# ---------------------------------------------------------------------------
# Writing (callers check ownership and recent authentication first)
# ---------------------------------------------------------------------------
def _store(user, png, info, actor, source_name="", sensitivity=50, ink="navy", source_bytes=None,
           source_ext=".jpg", accept=False):
    Model = _model()
    for old in Model.objects.filter(user=user, accepted_at__isnull=True, is_active=False):
        discard_pending(old)
    sig = Model(user=user, sensitivity=sensitivity, ink=ink, width=info["width"], height=info["height"],
                ink_ratio=info.get("ink_ratio", 0), created_by=actor, source_name=(source_name or "")[:255],
                sha256=hashlib.sha256(png).hexdigest())
    if source_bytes is not None:
        sig.source.save("photo" + source_ext, ContentFile(source_bytes), save=False)
    sig.image.save("signature.png", ContentFile(png), save=False)
    sig.save()
    if accept:
        accept_pending(sig, actor)
    return sig


def start_from_photo(user, upload, actor, sensitivity=50, ink="navy"):
    """Photo upload -> a pending (not yet accepted) signature to check."""
    if not upload:
        raise SignatureError("Choose a photo of your signature.")
    if upload.size > MAX_PHOTO_BYTES:
        raise SignatureError("The photo is larger than 12 MB.")
    ink = ink if ink in INKS else "navy"
    data = upload.read()
    png, info = extract(data, sensitivity, ink, True)
    ext = ".png" if (upload.name or "").lower().endswith(".png") else ".jpg"
    return _store(user, png, info, actor, upload.name, sensitivity, ink, source_bytes=data, source_ext=ext)


def adjust_pending(pending, sensitivity=50, ink="navy"):
    ink = ink if ink in INKS else "navy"
    data = _read(pending.source)
    if not data:
        raise SignatureError("The original photo is no longer available. Upload it again.")
    png, info = extract(data, sensitivity, ink, True)
    old = pending.image.name
    pending.image.save("signature.png", ContentFile(png), save=False)
    pending.sensitivity, pending.ink = int(sensitivity), ink
    pending.width, pending.height, pending.ink_ratio = info["width"], info["height"], info["ink_ratio"]
    pending.sha256 = hashlib.sha256(png).hexdigest()
    pending.save()
    if old and old != pending.image.name:
        try:
            pending.image.storage.delete(old)
        except Exception:
            pass
    return pending


@transaction.atomic
def accept_pending(pending, actor=None):
    Model = _model()
    now = timezone.now()
    for old in Model.objects.select_for_update().filter(user=pending.user, is_active=True).exclude(pk=pending.pk):
        old.is_active, old.retired_at = False, now
        old.save(update_fields=["is_active", "retired_at", "updated_at"])
    if pending.source:
        pending.source.delete(save=False)
    pending.is_active, pending.accepted_at = True, now
    pending.save()
    audit.log("state_change", pending, actor=actor, note="signature accepted")
    _notify_owner(pending.user, "Your signature was changed",
                  "A new signature was saved on your NBTE account. It will appear on documents and memos "
                  "your approvals generate from now on. Documents already issued keep the signature they "
                  "were signed with.")
    return pending


def discard_pending(pending):
    if pending.source:
        pending.source.delete(save=False)
    if pending.image:
        pending.image.delete(save=False)
    pending.delete()


def retire(user, actor=None):
    """Stop using the user's signature (kept for documents already signed)."""
    n = 0
    for old in _model().objects.filter(user=user, is_active=True):
        old.is_active, old.retired_at = False, timezone.now()
        old.save(update_fields=["is_active", "retired_at", "updated_at"])
        audit.log("state_change", old, actor=actor, note="signature removed")
        n += 1
    if n:
        _notify_owner(user, "Your signature was removed",
                      "Your signature was removed from your NBTE account. Documents already issued keep it."
                      + (" As a Director (or above) you must add a new one before approving anything that "
                         "produces a document." if signature_required(user) else ""))
    return n


def save_drawn(user, data, actor, ink="navy"):
    """Signature pad -> the user's active signature (no separate check step:
    the person saw exactly what they drew)."""
    ink = ink if ink in INKS else "navy"
    png, info = from_drawing(data, ink)
    return _store(user, png, info, actor, DRAWN, 50, ink, accept=True)


def save_demo(user, seed=None, ink="navy"):
    """A generated, fictional signature for demo accounts (never traced)."""
    png, info = demo_signature_png(seed or f"demo-{user.pk}", ink)
    sig = _store(user, png, info, None, DEMO, 50, ink)
    sig.is_active, sig.accepted_at = True, timezone.now()
    Model = _model()
    Model.objects.filter(user=user, is_active=True).exclude(pk=sig.pk).update(is_active=False,
                                                                              retired_at=timezone.now())
    sig.save()
    return sig


def _notify_owner(user, subject, body):
    """E-mail (console in dev) to the owner about a change to their signature."""
    if not getattr(user, "email", ""):
        return
    try:
        from core.email_utils import send_templated_email

        send_templated_email(subject, [user.email], "emails/account_security_notice.html",
                             {"subject": subject, "body": body, "name": user.get_full_name() or user.email},
                             category="security")
    except Exception:  # mail must never break the change
        pass


# ---------------------------------------------------------------------------
# Reports and reminders
# ---------------------------------------------------------------------------
def directors_without_signature():
    """Active staff who must sign but have no active signature (names only)."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    have = set(_model().objects.filter(is_active=True).values_list("user_id", flat=True))
    qs = (User.objects.filter(is_active=True, user_type="staff", role__rank__gte=min_rank())
          .exclude(pk__in=have).select_related("role", "department").order_by("last_name", "first_name"))
    return list(qs)
