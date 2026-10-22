"""Error envelope for every API error (contract: "Errors").

    HTTP status + {"error": {"code": "...", "message": "...", "fields": {...}}}

Extra keys sit inside ``error``: ``correct_door`` (wrong_door) and
``retry_after`` (throttled). Unknown exceptions become ``server_error`` with
no internals; the traceback goes to the log only.
"""
import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.serializers import as_serializer_error

logger = logging.getLogger("api")

MESSAGES = {
    "not_authenticated": "Please sign in to continue.",
    "token_expired": "Your session has expired. Please sign in again.",
    "token_revoked": "This session was signed out. Please sign in again.",
    "permission_denied": "You do not have permission to do that.",
    "not_found": "We could not find what you were looking for.",
    "validation_error": "Please check the highlighted fields.",
    "throttled": "Too many attempts. Please wait a moment and try again.",
    "wrong_door": "This account signs in through the other door of the app.",
    "password_change_required": "Please change your password to continue.",
    "account_inactive": "This account is not active. Please contact NBTE.",
    "invalid_credentials": "Email or password is wrong.",
    "method_not_allowed": "That action is not allowed here.",
    "unsupported_media_type": "The request format is not supported.",
    "not_acceptable": "The requested response format is not available.",
    "not_ready": "This feature is not available yet.",
    "server_error": "Something went wrong on our side. Please try again.",
}


class ApiError(exceptions.APIException):
    """An error with a contract code. Not a subclass of AuthenticationFailed
    on purpose: DRF turns those into 403 on views without authenticators."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"

    def __init__(self, message=None, **extra):
        self.message = message or MESSAGES.get(self.code, "The request could not be completed.")
        self.extra = extra
        super().__init__(detail=self.message, code=self.code)


class InvalidCredentials(ApiError):
    status_code, code = 401, "invalid_credentials"


class NotAuthenticatedError(ApiError):
    status_code, code = 401, "not_authenticated"


class TokenExpired(ApiError):
    status_code, code = 401, "token_expired"


class TokenRevoked(ApiError):
    status_code, code = 401, "token_revoked"


class WrongDoor(ApiError):
    status_code, code = 403, "wrong_door"

    def __init__(self, correct_door, message=None):
        if message is None:
            message = ("This account signs in at the QA portal. Choose 'Institution or resource person' "
                       "on the sign-in screen." if correct_door == "portal" else
                       "This account is an NBTE staff account. Choose 'NBTE staff' on the sign-in screen.")
        super().__init__(message, correct_door=correct_door)


class AccountInactive(ApiError):
    status_code, code = 403, "account_inactive"


class PasswordChangeRequired(ApiError):
    status_code, code = 403, "password_change_required"


class NotReady(ApiError):
    status_code, code = 501, "not_ready"


def _flatten(detail, prefix=""):
    """DRF error detail -> {"field": ["message", ...]} (nested keys dotted)."""
    out = {}
    if isinstance(detail, dict):
        for key, value in detail.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(_flatten(value, name))
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                for i, item in enumerate(value):
                    out.update(_flatten(item, f"{name}.{i}"))
            else:
                items = value if isinstance(value, list) else [value]
                out[name] = [str(v) for v in items]
    elif isinstance(detail, list):
        out[prefix or "non_field_errors"] = [str(v) for v in detail]
    else:
        out[prefix or "non_field_errors"] = [str(detail)]
    return out


def envelope(code, message=None, status_code=400, fields=None, **extra):
    body = {"code": code, "message": message or MESSAGES.get(code, "")}
    if fields:
        body["fields"] = fields
    body.update(extra)
    return Response({"error": body}, status=status_code)


def handler(exc, context):
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    elif isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(as_serializer_error(exc))

    headers = {}
    if isinstance(exc, ApiError):
        response = envelope(exc.code, exc.message, exc.status_code, **exc.extra)
    elif isinstance(exc, exceptions.ValidationError):
        fields = _flatten(exc.detail)
        message = MESSAGES["validation_error"]
        if list(fields) == ["non_field_errors"]:
            message = fields["non_field_errors"][0]
        response = envelope("validation_error", message, 400, fields=fields)
    elif isinstance(exc, exceptions.ParseError):
        response = envelope("validation_error", "The request body could not be read.", 400)
    elif isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        response = envelope("not_authenticated", status_code=exc.status_code)
    elif isinstance(exc, exceptions.PermissionDenied):
        response = envelope("permission_denied", status_code=403)
    elif isinstance(exc, exceptions.NotFound):
        response = envelope("not_found", status_code=404)
    elif isinstance(exc, exceptions.Throttled):
        wait = int(exc.wait + 0.999) if exc.wait is not None else None
        response = envelope("throttled", status_code=429, retry_after=wait)
        if wait is not None:
            headers["Retry-After"] = str(wait)
    elif isinstance(exc, exceptions.MethodNotAllowed):
        response = envelope("method_not_allowed", status_code=405)
    elif isinstance(exc, exceptions.UnsupportedMediaType):
        response = envelope("unsupported_media_type", status_code=415)
    elif isinstance(exc, exceptions.NotAcceptable):
        response = envelope("not_acceptable", status_code=406)
    elif isinstance(exc, exceptions.APIException):
        code = exc.default_code if isinstance(exc.default_code, str) else "error"
        response = envelope(code, str(exc.detail) if isinstance(exc.detail, str) else None, exc.status_code)
    else:
        view = context.get("view")
        logger.exception("Unhandled API error in %s", type(view).__name__ if view else "?", exc_info=exc)
        return envelope("server_error", status_code=500)

    if response.status_code == 401:
        headers.setdefault("WWW-Authenticate", 'Bearer realm="api"')
    auth_header = getattr(exc, "auth_header", None)
    if auth_header:
        headers["WWW-Authenticate"] = auth_header
    for key, value in headers.items():
        response[key] = value
    return response
