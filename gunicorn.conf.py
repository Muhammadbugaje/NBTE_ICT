"""Gunicorn settings with conservative defaults; measure before scaling.

    WEB_CONCURRENCY   worker processes   (1)
    GUNICORN_THREADS  threads per worker (8)
    GUNICORN_TIMEOUT  seconds before a stuck request is killed (60)

Each thread can hold one database connection, so the database sees at most
WEB_CONCURRENCY x GUNICORN_THREADS connections from the web service.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
worker_class = "gthread"            # threads: cheap concurrency for I/O-bound Django views

# With 2+ workers, load the app once in the master and fork: workers share the
# code pages (less memory). With one worker that would just keep a second copy
# in the master, so it is off.
preload_app = workers > 1

# Recycle each worker after ~1000 requests (jittered so they don't all restart
# together): keeps memory flat over days on a small box.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = 100

timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5

# Heartbeat files in RAM, not on a slow disk (avoids false "worker timeout").
worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None

# Behind Render / nginx: trust the proxy's X-Forwarded-Proto for https.
forwarded_allow_ips = "*"

# Hard limits on what a client may send in the request line and headers.
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
# Method + path only (query strings can carry tokens), status, size and the
# request time in microseconds so slow pages show up in Render's logs.
access_log_format = '"%(m)s %(U)s" %(s)s %(b)s %(D)sus'


def post_fork(server, worker):
    # Each forked worker opens its own database connections (never share the
    # master's socket across processes).
    try:
        from django.db import connections

        for conn in connections.all(initialized_only=True):
            conn.close()
    except Exception:
        pass
