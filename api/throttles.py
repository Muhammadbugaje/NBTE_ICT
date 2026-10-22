"""Login throttle: 10 attempts per minute per client IP + e-mail (scope
``login`` in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]). The client IP comes
from core.ratelimit.client_ip, like the website's sign-in limiter."""
from rest_framework.throttling import SimpleRateThrottle

from core import ratelimit


class LoginRateThrottle(SimpleRateThrottle):
    scope = "login"

    def get_cache_key(self, request, view):
        try:
            email = str(request.data.get("email", "") or "").strip().lower()
        except Exception:
            email = ""
        return self.cache_format % {"scope": self.scope,
                                    "ident": f"{ratelimit.client_ip(request)}|{email}"}
