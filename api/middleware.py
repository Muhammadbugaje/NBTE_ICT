"""Phase 9: response rules for everything under /api/.

* every response carries ``X-NBTE-Min-App-Version`` (settings.API_MIN_APP_VERSION);
* Django-level errors that never reached a DRF view (unknown URL -> 404,
  crash outside a view -> 500) are turned into the JSON error envelope, so the
  app never has to parse an HTML error page.
"""
import json

from django.conf import settings
from django.http import HttpResponse

API_PREFIX = "/api/"

_FALLBACK = {
    400: ("validation_error", "The request could not be read."),  # e.g. a disallowed Host header
    404: ("not_found", "We could not find what you were looking for."),
    500: ("server_error", "Something went wrong on our side. Please try again."),
}


class ApiResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not (request.path_info or "").startswith(API_PREFIX):
            return response
        if response.status_code in _FALLBACK and "json" not in response.get("Content-Type", ""):
            code, message = _FALLBACK[response.status_code]
            response = HttpResponse(json.dumps({"error": {"code": code, "message": message}}),
                                    status=response.status_code, content_type="application/json")
        response["X-NBTE-Min-App-Version"] = getattr(settings, "API_MIN_APP_VERSION", "1.0.0")
        return response
