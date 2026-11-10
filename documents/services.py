"""Register actions on issued documents (X25, G5.e): official copy, revoke,
supersede, and the provisional pop-up memory. Web views and the API call these."""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core import audit

from .issue import notify_institution_users, popup_text  # noqa: F401  (re-exported for views)

MAX_OFFICIAL_MB = 15


def _staff(user):
    return bool(user and user.is_authenticated and getattr(user, "user_type", "staff") == "staff")


def can_view_register(user):
    return _staff(user) and (user.is_superuser or user.has_perm("documents.view_issued_documents"))


def can_upload_official(user, doc=None):
    """The permission, or - for ``doc`` - the handling department's Director,
    the ES or a super admin (official letters pipeline)."""
    if _staff(user) and (user.is_superuser or user.has_perm("documents.upload_official_copy")):
        return True
    if doc is not None:
        from . import official_letters

        return official_letters.can_manage(user, doc)
    return False


def can_revoke(user):
    return _staff(user) and (user.is_superuser or user.has_perm("documents.revoke_document"))


def can_view_log(user):
    return _staff(user) and (user.is_superuser or user.has_perm("documents.view_verification_log"))


@transaction.atomic
def revoke(doc, *, user, reason, superseded_by=None):
    if not can_revoke(user):
        raise PermissionDenied("Revoking a document needs documents.revoke_document.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Say why the document is revoked (at least 5 characters).")
    if doc.status != doc.VALID:
        raise ValidationError("Only a valid document can be revoked or superseded.")
    doc.status = doc.SUPERSEDED if superseded_by is not None else doc.REVOKED
    doc.superseded_by = superseded_by
    doc.revoked_at, doc.revoked_by, doc.revoke_reason = timezone.now(), user, reason[:500]
    doc.save(update_fields=["status", "superseded_by", "revoked_at", "revoked_by", "revoke_reason", "updated_at"])
    audit.log("other", doc, actor=user, note=f"document {doc.status}: {reason[:200]}")
    from . import official_letters

    official_letters.on_revoked(doc, user)
    if doc.institution_id and doc.audience != "internal":
        inst, word = doc.institution, ("replaced" if superseded_by is not None else "revoked")
        transaction.on_commit(lambda: notify_institution_users(
            inst, f"Document {word}: {doc.title}", f"NBTE {word} {doc.reference}. Reason: {reason}", url="/letters/"))
    return doc


@transaction.atomic
def upload_official(doc, *, user, upload, note=""):
    """X25: the signed official copy; the verify page then shows 'Official copy issued on ...'."""
    from .models import OfficialCopy

    if not can_upload_official(user, doc):
        raise PermissionDenied("Uploading the official copy needs documents.upload_official_copy.")
    if doc.status == doc.REVOKED:
        raise ValidationError("This document was revoked; issue a new one instead.")
    if upload is None:
        raise ValidationError("Choose the signed PDF.")
    name = getattr(upload, "name", "") or ""
    if not name.lower().endswith(".pdf"):
        raise ValidationError("Upload the official copy as a PDF.")
    if getattr(upload, "size", 0) > MAX_OFFICIAL_MB * 1024 * 1024:
        raise ValidationError(f"The file is larger than {MAX_OFFICIAL_MB} MB.")
    head = upload.read(5)
    upload.seek(0)
    if head != b"%PDF-":
        raise ValidationError("That file is not a PDF.")
    copy = OfficialCopy(document=doc, uploaded_by=user, note=(note or "")[:300], original_name=name[:255])
    copy.file.save(name, upload, save=False)
    copy.save()
    audit.log("other", doc, actor=user, note="official copy uploaded")
    from . import official_letters

    official_letters.on_official_uploaded(doc, user)
    if doc.institution_id and doc.audience != "internal":
        inst = doc.institution
        label = official_letters.letter_label(doc)
        transaction.on_commit(lambda: notify_institution_users(
            inst, f"Your official {label[:1].lower() + label[1:]} is ready",
            f"The signed official copy of {doc.reference} ({doc.title}) is ready to download on the Letters page.",
            url=f"/letters/documents/{doc.pk}/"))
    return copy


def opened(doc, user):
    """Record that ``user`` opened ``doc``; True when the provisional pop-up
    should show (provisional, no official copy yet, not acknowledged)."""
    from .models import DocumentOpen

    row, _created = DocumentOpen.objects.get_or_create(document=doc, user=user)
    return bool(doc.provisional and row.acknowledged_at is None and doc.official_copy is None)


def acknowledge(doc, user):
    from .models import DocumentOpen

    row, _ = DocumentOpen.objects.get_or_create(document=doc, user=user)
    if row.acknowledged_at is None:
        row.acknowledged_at = timezone.now()
        row.save(update_fields=["acknowledged_at"])
    return row


def for_institution(user):
    """Documents an institution user may see (their institution, not internal)."""
    from .models import IssuedDocument

    return (IssuedDocument.objects.for_user(user).exclude(audience="internal").exclude(kind="payment_slip")
            .select_related("template").order_by("-issued_at"))
