"""v2.3: dev settings use Redis only when it is actually running.

A laptop whose .env has REDIS_URL (copied from the server) but no Redis
running must still serve pages: in-memory cache, tasks run inline.
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

PROBE = (
    "import django, os; os.environ['DJANGO_SETTINGS_MODULE']='nbte_ict.settings.dev'; "
    "from django.conf import settings; "
    "print(settings.CACHES['default']['BACKEND'], settings.CELERY_TASK_ALWAYS_EAGER)"
)


def _run(**env):
    full = {**os.environ, **env}
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BASE, env=full, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1], out.stderr


def test_unreachable_redis_falls_back_to_memory_and_inline_tasks():
    line, err = _run(REDIS_URL="redis://127.0.0.1:1/0", CELERY_EAGER="False")
    assert line == "django.core.cache.backends.locmem.LocMemCache True"
    assert "is not running" in err


def test_no_redis_url_uses_memory_cache():
    line, _ = _run(REDIS_URL="", CELERY_EAGER="True")
    assert line.startswith("django.core.cache.backends.locmem.LocMemCache")
