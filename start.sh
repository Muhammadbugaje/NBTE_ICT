#!/bin/bash
# v2.2 process launcher for Render (and any single-container host).
#
#   PROCESS_MODE=all  (default on Render) gunicorn + one Celery worker with the
#                     beat scheduler, in the SAME container. Needed on Render
#                     because the persistent disk (uploads, generated letters,
#                     backups) attaches to one service only: a separate worker
#                     service could not read or write those files. It is also
#                     the cheapest setup (one paid service).
#   PROCESS_MODE=web  gunicorn only (when a separate worker shares storage).
#   PROCESS_MODE=worker  Celery worker + beat only.
#
# If either process dies, the container exits and the platform restarts it
# cleanly (no half-running site with a dead scheduler).
set -euo pipefail

MODE="${PROCESS_MODE:-all}"
CELERY_CMD=(celery -A nbte_ict worker -B --schedule "${CELERYBEAT_SCHEDULE_FILE:-.celerybeat-schedule}"
            -P threads -c "${CELERY_CONCURRENCY:-2}" -O fair
            --without-gossip --without-mingle --without-heartbeat -l "${CELERY_LOGLEVEL:-info}")
WEB_CMD=(gunicorn -c gunicorn.conf.py nbte_ict.wsgi:application)

case "$MODE" in
  web)    exec "${WEB_CMD[@]}" ;;
  worker) exec "${CELERY_CMD[@]}" ;;
  all)
    "${CELERY_CMD[@]}" &
    CELERY_PID=$!
    "${WEB_CMD[@]}" &
    WEB_PID=$!
    trap 'kill -TERM "$CELERY_PID" "$WEB_PID" 2>/dev/null; wait' TERM INT
    # Exit as soon as either process stops; the platform restarts the container.
    if wait -n "$CELERY_PID" "$WEB_PID"; then
      STATUS=0
    else
      STATUS=$?
    fi
    echo "start.sh: a process exited (status $STATUS); stopping the container." >&2
    kill -TERM "$CELERY_PID" "$WEB_PID" 2>/dev/null || true
    wait || true
    exit "$STATUS"
    ;;
  *) echo "start.sh: unknown PROCESS_MODE '$MODE' (use all, web or worker)" >&2; exit 2 ;;
esac
