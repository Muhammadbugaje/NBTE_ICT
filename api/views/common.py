from drf_spectacular.utils import OpenApiResponse

from ..serializers import ErrorSerializer


def errors(*codes):
    """OpenAPI responses for the error envelope, by HTTP status."""
    described = {
        400: "validation_error",
        401: "not_authenticated / token_expired",
        403: "permission_denied / wrong_door / password_change_required / account_inactive",
        404: "not_found",
        429: "throttled",
        501: "not_ready",
    }
    return {code: OpenApiResponse(ErrorSerializer, description=described.get(code, "error")) for code in codes}


AUTHED_ERRORS = errors(401, 403, 429)
