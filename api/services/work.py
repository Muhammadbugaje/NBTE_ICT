"""Shared helpers for the staff "Work" endpoints (tasks, tickets, requests,
leave, attendance, assets, staff directory, workspace).

* ``run``            calls a website service and turns its Django
                     PermissionDenied / ValidationError into the contract's
                     error envelope (403 permission_denied with the service's
                     own message / 400 validation_error).
* ``form_errors``    raises a website form's errors as 400 ``fields``.
* ``download``       serves a protected attachment through the website's own
                     ``core.views.protected_download`` (same ``can_download``
                     rule, audit entry, X-Accel-Redirect and ?inline=1).
* ``user_ref`` / ``attachment_ref`` / ``activity_ref`` shape small objects.
"""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from ..exceptions import ApiError


class Forbidden(ApiError):
    status_code, code = 403, "permission_denied"


def _messages(exc):
    msgs = getattr(exc, "messages", None)
    if msgs:
        return [str(m) for m in msgs]
    text = str(exc)
    return [text] if text else []


def run(fn, *args, **kwargs):
    """Call a website service; map its exceptions to the API envelope."""
    try:
        return fn(*args, **kwargs)
    except DjangoPermissionDenied as exc:
        msgs = _messages(exc)
        raise Forbidden("; ".join(msgs) if msgs else None)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"non_field_errors": _messages(exc) or ["Invalid request."]})


def form_errors(form, rename=None):
    """Raise a bound, invalid Django form's errors as a 400 with ``fields``."""
    rename = rename or {}
    errors = {}
    for name, errs in form.errors.items():
        key = "non_field_errors" if name == "__all__" else rename.get(name, name)
        errors.setdefault(key, []).extend(str(e) for e in errs)
    raise serializers.ValidationError(errors)


def iso(value):
    """Date-time in local time with offset (contract), or None."""
    if value is None:
        return None
    return timezone.localtime(value).isoformat()


def initials_of(user):
    letters = "".join(part[:1] for part in (user.first_name, user.last_name) if part).upper()
    return letters or (user.email[:1].upper() if user.email else "?")


def user_ref(user):
    if user is None:
        return None
    return {"id": user.pk, "name": user.get_full_name() or user.email, "initials": initials_of(user)}


def download_path(kind, owner_id, attachment_id):
    """API path of an attachment download (relative to api/v1/)."""
    return f"{kind}/{owner_id}/attachments/{attachment_id}/"


def attachment_ref(att, kind, owner_id, request=None):
    size = None
    try:
        size = att.file.size if att.file else None
    except Exception:  # file missing on disk
        size = None
    path = reverse("api:health").rsplit("health/", 1)[0] + download_path(kind, owner_id, att.pk)
    return {
        "id": att.pk,
        "name": att.original_name or (att.file.name.rsplit("/", 1)[-1] if att.file else ""),
        "description": getattr(att, "description", "") or "",
        "size": size,
        "uploaded_by": user_ref(getattr(att, "uploaded_by", None)),
        "created_at": iso(att.created_at),
        "download_url": request.build_absolute_uri(path) if request is not None else path,
    }


def activity_ref(row):
    return {"id": row.pk, "kind": row.kind, "actor": user_ref(row.actor), "message": row.message or "",
            "created_at": iso(row.created_at)}


def download(request, obj):
    """Serve ``obj`` (an attachment model with PROTECTED_DOWNLOAD) exactly as
    the website does. The caller has already checked the parent is visible
    (404 otherwise); can_download is checked again inside."""
    from core.views import protected_download

    return protected_download(request._request, obj._meta.label_lower, obj.pk)


def paginate(view, request, items, serializer_class, shape):
    """Paginate a queryset/list with StandardPagination and shape each item."""
    from ..pagination import StandardPagination

    paginator = StandardPagination()
    page = paginator.paginate_queryset(items, request, view=view)
    return paginator.get_paginated_response(serializer_class([shape(x) for x in page], many=True).data)
