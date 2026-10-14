"""Views for the /settings/files/ management page.

Routes:

    files_dashboard          GET    The page itself (stats + cached scan).
    files_scan               POST   Synchronous scan (no-JS fallback).
    files_scan_stream        GET    SSE streaming scan with progress.
    files_purge              POST   Auto-delete orphans older than
                                    the configured threshold.
    files_delete_selected    POST   Delete specific orphans by kind+rel.
"""
from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import media_scanner as scanner

logger = logging.getLogger(__name__)

_SCAN_CACHE_TTL = 15 * 60
_STATS_CACHE_TTL = 5 * 60


def _scan_key(user):
    return f"media_scan:{user.pk}"


def _stats_key():
    return "media_stats:global"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@permission_required("config.change_configentry", raise_exception=True)
def files_dashboard(request):
    scan_data = cache.get(_scan_key(request.user))
    stats_data = cache.get(_stats_key())

    if stats_data is None:
        stats = scanner.storage_stats()
        stats_data = stats.to_dict()
        cache.set(_stats_key(), stats_data, _STATS_CACHE_TTL)

    folders = [
        {
            "key": k,
            "label": k,
            "count": v["count"],
            "size": v["bytes"],
            "size_h": scanner.human_size(v["bytes"]),
        }
        for k, v in sorted(
            stats_data.get("folders", {}).items(),
            key=lambda kv: kv[1]["bytes"],
            reverse=True,
        )
    ]

    orphans = []
    if scan_data:
        for o in scan_data.get("orphans", []):
            orphans.append({
                **o,
                "size_h": scanner.human_size(o["size"]),
                "age_h": scanner.human_age(o["mtime"]),
            })

    return render(request, "core/files/dashboard.html", {
        "stats": {
            **stats_data,
            "total_bytes_h": scanner.human_size(stats_data.get("total_bytes", 0)),
            "public_bytes_h": scanner.human_size(stats_data.get("public_bytes", 0)),
            "protected_bytes_h": scanner.human_size(stats_data.get("protected_bytes", 0)),
        },
        "folders": folders,
        "scan": {
            **(scan_data or {}),
            "orphans": orphans,
            "orphan_bytes_h": scanner.human_size((scan_data or {}).get("orphan_bytes", 0)),
            "total_bytes_h": scanner.human_size((scan_data or {}).get("total_bytes", 0)),
        } if scan_data else None,
        "purge_min_age_days": scanner.purge_min_age_days(),
        "max_files": scanner.max_files(),
    })


# ---------------------------------------------------------------------------
# Scan — synchronous (no-JS fallback)
# ---------------------------------------------------------------------------
@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def files_scan(request):
    try:
        result = scanner.scan()
    except Exception:
        logger.exception("media_scanner: scan failed")
        messages.error(request, "The scan could not complete. Check the server log.")
        return redirect("config:files_dashboard")

    cache.set(_scan_key(request.user), result.to_dict(), _SCAN_CACHE_TTL)
    cache.delete(_stats_key())

    if result.truncated:
        messages.warning(
            request,
            f"Found {len(result.orphans)} orphan(s), but the scan hit "
            f"the {scanner.max_files()}-file limit. Increase "
            f"`files.orphan_scan_max_files` for a full scan.",
        )
    elif result.orphans:
        messages.warning(
            request,
            f"Found {len(result.orphans)} orphan(s), "
            f"{scanner.human_size(result.orphan_bytes)} total.",
        )
    else:
        messages.success(request, "No orphaned files. Everything is in use.")

    return redirect("config:files_dashboard")


# ---------------------------------------------------------------------------
# Scan — streaming (SSE)
# ---------------------------------------------------------------------------
@login_required
@permission_required("config.change_configentry", raise_exception=True)
def files_scan_stream(request):
    """Server-Sent Events stream for a scan in progress."""
    user = request.user

    def event_gen():
        collected_orphans = []
        final_summary = None

        yield ": connected\n\n"

        try:
            for event in scanner.scan_stream():
                etype = event.get("type", "message")

                if etype == "found":
                    collected_orphans.append({
                        "kind": event["orphan"]["kind"],
                        "rel": event["orphan"]["rel"],
                        "size": event["orphan"]["size"],
                        "mtime": event["orphan"]["mtime"],
                        "age_days": event["orphan"]["age_days"],
                        "is_variant": event["orphan"]["is_variant"],
                    })
                elif etype == "done":
                    final_summary = event["summary"]

                yield f"event: {etype}\ndata: {json.dumps(event)}\n\n"

        except GeneratorExit:
            pass
        except Exception:
            logger.exception("files_scan_stream failed")
            yield (
                "event: error\n"
                f"data: {json.dumps({'type': 'error', 'message': 'Scan failed'})}\n\n"
            )
        finally:
            if final_summary is not None:
                cache.set(
                    _scan_key(user),
                    {
                        "orphans": collected_orphans,
                        "total_files": final_summary["total_files"],
                        "total_bytes": final_summary["total_bytes"],
                        "orphan_bytes": final_summary["orphan_bytes"],
                        "truncated": final_summary["truncated"],
                        "scanned_at": timezone.now().isoformat(),
                    },
                    _SCAN_CACHE_TTL,
                )
                cache.delete(_stats_key())

    resp = StreamingHttpResponse(event_gen(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


# ---------------------------------------------------------------------------
# Purge (auto-delete aged orphans)
# ---------------------------------------------------------------------------
@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def files_purge(request):
    scan_data = cache.get(_scan_key(request.user))
    if not scan_data:
        messages.error(request, "Run a scan first.")
        return redirect("config:files_dashboard")

    paths = [
        {"kind": o["kind"], "rel": o["rel"]}
        for o in scan_data.get("orphans", [])
    ]
    if not paths:
        messages.info(request, "Nothing to purge.")
        return redirect("config:files_dashboard")

    min_age = scanner.purge_min_age_days()
    result = scanner.delete_paths(paths, min_age_days=min_age, actor=request.user)

    parts = []
    if result.deleted:
        parts.append(f"{result.deleted} deleted ({scanner.human_size(result.freed)})")
    if result.skipped_young:
        parts.append(f"{result.skipped_young} kept (younger than {min_age}d)")
    if result.skipped_missing:
        parts.append(f"{result.skipped_missing} already gone")
    if result.errors:
        parts.append(f"{len(result.errors)} error(s) — see log")

    summary = ", ".join(parts) if parts else "Nothing changed."
    if result.errors:
        messages.warning(request, summary)
    else:
        messages.success(request, summary)

    cache.delete(_scan_key(request.user))
    cache.delete(_stats_key())
    return redirect("config:files_dashboard")


# ---------------------------------------------------------------------------
# Delete selected — manual selection bypasses the age guard
# ---------------------------------------------------------------------------
@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def files_delete_selected(request):
    scan_data = cache.get(_scan_key(request.user))
    if not scan_data:
        messages.error(request, "Run a scan first.")
        return redirect("config:files_dashboard")

    allowed = {(o["kind"], o["rel"]) for o in scan_data.get("orphans", [])}

    kinds = request.POST.getlist("kind")
    rels = request.POST.getlist("rel")

    paths = []
    for kind, rel in zip(kinds, rels):
        if (kind, rel) in allowed:
            paths.append({"kind": kind, "rel": rel})

    if not paths:
        messages.info(request, "No valid selections.")
        return redirect("config:files_dashboard")

    # Manual selection bypasses the age guard.
    #
    # `purge_min_age_days()` exists to protect the AUTO-purge from
    # deleting recently-uploaded files. When the operator explicitly
    # ticks a row and clicks Delete, that's an intentional choice and
    # should go through regardless of the file's age.
    result = scanner.delete_paths(
        paths,
        min_age_days=0,
        actor=request.user,
    )

    parts = []
    if result.deleted:
        parts.append(f"{result.deleted} deleted ({scanner.human_size(result.freed)})")
    if result.skipped_missing:
        parts.append(f"{result.skipped_missing} already gone")
    if result.errors:
        parts.append(f"{len(result.errors)} error(s)")

    summary = ", ".join(parts) or "Nothing changed."
    if result.errors:
        messages.warning(request, summary)
    else:
        messages.success(request, summary)

    cache.delete(_scan_key(request.user))
    cache.delete(_stats_key())
    return redirect("config:files_dashboard")