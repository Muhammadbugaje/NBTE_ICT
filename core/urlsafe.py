"""v2.2 (security): one check for every "next" / return-to address.

Only a path on this site is allowed. ``url_has_allowed_host_and_scheme``
also refuses the tricks a plain ``startswith("/")`` test lets through, such as
``//evil.example`` and ``/\\evil.example`` (browsers read a backslash as a
slash), so a crafted link cannot bounce a signed-in user to another site.
"""
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, value, fallback):
    value = (value or "").strip()
    if value and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return value
    return fallback
