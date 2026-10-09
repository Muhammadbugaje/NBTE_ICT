import shutil

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

import config


def health(request):
    """Public: {'status': ok|degraded}. Staff with config access also get the
    detail (disk usage of MEDIA_ROOT - doc 33's capacity check)."""
    checks = {}
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "down"
    try:
        cache.set("health", "1", 5)
        checks["cache"] = "ok" if cache.get("health") == "1" else "down"
    except Exception:
        checks["cache"] = "down"
    usage = shutil.disk_usage(settings.MEDIA_ROOT if settings.MEDIA_ROOT.exists() else settings.BASE_DIR)
    used_pct = round(usage.used * 100 / usage.total, 1)
    warn = int(config.get("media.disk_warn_percent", 80))
    checks["disk"] = "ok" if used_pct < warn else "warn"
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    body = {"status": status}
    user = request.user
    if user.is_authenticated and user.has_perm("config.view_configentry"):
        body["checks"] = checks
        body["disk"] = {"used_percent": used_pct, "free_gb": round(usage.free / 1e9, 1), "warn_at_percent": warn}
    return JsonResponse(body, status=200 if checks["database"] == "ok" else 503)
