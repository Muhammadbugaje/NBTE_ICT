"""Host helpers for the two front doors (Master Plan §3).

* The **portal host** (`settings.QA_PORTAL_HOST`, e.g. qa.nbte.gov.ng) serves
  institutions and resource persons from `qa_core.portal_urls` only.
* Every other allowed host is the **internal host** and serves the existing
  NBTE system from `ROOT_URLCONF`.

Hosts are compared case-insensitively and without the port, so
`qa.localhost:8000` matches `QA_PORTAL_HOST=qa.localhost`.
"""
from django.conf import settings


def _hostname(host):
    host = (host or "").strip().lower()
    if host.startswith("["):  # IPv6 literal
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def _port(host):
    host = (host or "").strip()
    if host.startswith("["):
        rest = host.split("]", 1)[1] if "]" in host else ""
        return rest[1:] if rest.startswith(":") else ""
    return host.rsplit(":", 1)[1] if ":" in host else ""


def portal_hostname():
    return _hostname(getattr(settings, "QA_PORTAL_HOST", ""))


def request_hostname(request):
    try:
        return _hostname(request.get_host())
    except Exception:  # DisallowedHost: let CommonMiddleware report it
        return _hostname(request.META.get("HTTP_HOST", ""))


def is_portal_request(request):
    portal = portal_hostname()
    return bool(portal) and request_hostname(request) == portal


def _with_port(target_host, request):
    """Keep the request's non-standard port (e.g. :8000 in development) when
    the configured host does not name one."""
    if ":" in target_host and not target_host.startswith("["):
        return target_host
    try:
        port = _port(request.get_host())
    except Exception:
        port = ""
    return f"{target_host}:{port}" if port else target_host


def internal_host_for(request):
    configured = (getattr(settings, "INTERNAL_HOST", "") or "").strip().lower()
    if not configured:
        portal = portal_hostname()
        configured = portal[3:] if portal.startswith("qa.") else "localhost"
    return _with_port(configured, request)


def portal_host_for(request):
    return _with_port(getattr(settings, "QA_PORTAL_HOST", "") or "", request)


def internal_url(request, path="/"):
    return f"{request.scheme}://{internal_host_for(request)}{path}"


def portal_url(request, path="/"):
    return f"{request.scheme}://{portal_host_for(request)}{path}"


# ---------------------------------------------------------------------------
# Absolute portal links from anywhere (Phase 2): e-mails, QR codes, the
# public-site button and staff screens that send someone to the portal.
# ---------------------------------------------------------------------------
def portal_base_url():
    """`settings.QA_PORTAL_URL`, or https://QA_PORTAL_HOST when it is blank."""
    configured = (getattr(settings, "QA_PORTAL_URL", "") or "").strip().rstrip("/")
    if configured:
        return configured
    host = getattr(settings, "QA_PORTAL_HOST", "") or "qa.nbte.gov.ng"
    return f"https://{host}"


def portal_reverse(name, *args, **kwargs):
    """reverse() against the portal URLconf (it is not the current one on the
    internal host)."""
    from django.urls import reverse

    return reverse(name, urlconf=getattr(settings, "QA_PORTAL_URLCONF", "qa_core.portal_urls"),
                   args=args or None, kwargs=kwargs or None)


def portal_absolute(path="/", request=None):
    """Absolute URL of `path` on the portal host. With a request, keep its
    scheme and development port; without one, use portal_base_url()."""
    if request is not None:
        try:
            return f"{request.scheme}://{portal_host_for(request)}{path}"
        except Exception:
            pass
    return f"{portal_base_url()}{path}"
