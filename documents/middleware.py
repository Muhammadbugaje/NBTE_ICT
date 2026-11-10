"""Permissions-Policy (spec S-25): the camera only where a page needs it.

* ``/scan/`` (both hosts) -> ``camera=(self)``.
* Pages that already use the camera or location (attendance QR check-in, asset scan, and
  any extra prefixes in ``settings.PERMISSIONS_POLICY_DEVICE_PATHS``, e.g. the
  visit-report pages) -> ``camera=(self), geolocation=(self)``.
* Everything else -> ``camera=(), geolocation=()``.

The microphone stays allowed for the site itself (voice notes in Messages).
A view that sets its own header keeps it.
"""
from django.conf import settings

SCAN_PREFIXES = ("/scan/",)
DEVICE_PREFIXES = ("/attendance/", "/assets/scan/")  # QR check-in, asset QR scan
REST = "microphone=(self), payment=(), usb=(), interest-cohort=()"


def policy_for(path):
    if any(path.startswith(p) for p in SCAN_PREFIXES):
        return f"camera=(self), geolocation=(), {REST}"
    extra = tuple(getattr(settings, "PERMISSIONS_POLICY_DEVICE_PATHS", ()) or ())
    if any(path.startswith(p) for p in DEVICE_PREFIXES + extra):
        return f"camera=(self), geolocation=(self), {REST}"
    return f"camera=(), geolocation=(), {REST}"


class PermissionsPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "Permissions-Policy" not in response:
            response["Permissions-Policy"] = policy_for(request.path_info or "")
        return response
