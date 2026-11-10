"""P.4 (S-04, S-05): the Django admin never opens QA data to anyone but
superusers, and (optionally) only from the networks in ``ADMIN_ALLOWED_IPS``.

* Every ModelAdmin of a QA app answers "no" to non-superusers for module,
  view, add, change and delete — whatever permissions they hold. QA data is
  read and changed through the QA screens, which go through the
  ``assignments.access`` chokepoint; the admin does not.
* ``AdminIPGuardMiddleware``: when ``settings.ADMIN_ALLOWED_IPS`` (env
  ``ADMIN_ALLOWED_IPS``, comma-separated IPs or CIDR networks) is not empty,
  ``/admin/`` answers 404 to every other address.
"""
from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import Http404

QA_ADMIN_APPS = {"institutions", "accreditation", "inspection", "complaints", "payments", "qa_core",
                 "assignments"}
GUARDED = ("has_module_permission", "has_view_permission", "has_add_permission", "has_change_permission",
           "has_delete_permission")


def _superuser_only(original):
    def method(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if user is None or not user.is_active or not user.is_superuser:
            return False
        return original(request, *args, **kwargs)
    method._qa_superuser_only = True
    return method


def guard_admin(site=None):
    from django.contrib import admin
    site = site or admin.site
    n = 0
    for model, model_admin in list(site._registry.items()):
        if model._meta.app_label not in QA_ADMIN_APPS:
            continue
        for name in GUARDED:
            current = getattr(model_admin, name)
            if getattr(current, "_qa_superuser_only", False):
                continue
            setattr(model_admin, name, _superuser_only(current))
        n += 1
    return n


def allowed_networks():
    raw = getattr(settings, "ADMIN_ALLOWED_IPS", "") or ""
    if isinstance(raw, (list, tuple)):
        raw = ",".join(raw)
    nets = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return nets


def ip_allowed(ip):
    nets = allowed_networks()
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return False
    return any(addr in n for n in nets)


class AdminIPGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/") and allowed_networks():
            ip = request.META.get("REMOTE_ADDR")
            try:
                from core.ratelimit import client_ip  # type: ignore
                ip = client_ip(request) or ip
            except Exception:
                pass
            if not ip_allowed(ip):
                raise Http404
        return self.get_response(request)
