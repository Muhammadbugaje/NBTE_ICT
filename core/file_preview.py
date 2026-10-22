"""Inline "quick preview" support for every file-serving view.

A download view stays exactly as it was (same permission checks, same
storage path) and asks this module two things:

* ``wants_inline(request, content_type)`` — did the caller ask for
  ``?inline=1`` and is the type one a browser can show safely (PDF, raster
  images, plain text)? SVG, HTML and anything scriptable are never inline.
* ``finish(response, filename, inline)`` — set ``Content-Disposition``
  (``inline`` or ``attachment``), the no-store / nosniff headers, and, for an
  inline response, ``X-Frame-Options: SAMEORIGIN`` so the preview viewer can
  frame it (the site default is DENY). A ``sandbox`` CSP keeps a previewed
  file from running anything even if a browser sniffs it.

Audit notes use ``audit_note(inline)``: "file previewed" vs "file downloaded".
"""
from urllib.parse import quote

INLINE_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    "text/plain", "text/csv",
}


def base_type(content_type):
    return (content_type or "").split(";", 1)[0].strip().lower()


def is_previewable(content_type):
    return base_type(content_type) in INLINE_TYPES


def wants_inline(request, content_type):
    return request.GET.get("inline") == "1" and is_previewable(content_type)


def audit_note(inline, base="file"):
    return f"{base} {'previewed' if inline else 'downloaded'}"


def text_charset(content_type):
    """Plain text is served as UTF-8 so the viewer renders it correctly."""
    if base_type(content_type) in ("text/plain", "text/csv") and "charset" not in content_type:
        return content_type + "; charset=utf-8"
    return content_type


def finish(response, filename, inline):
    kind = "inline" if inline else "attachment"
    response["Content-Disposition"] = f"{kind}; filename*=UTF-8''{quote(filename or 'file')}"
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    if inline:
        response["X-Frame-Options"] = "SAMEORIGIN"
        if base_type(response.get("Content-Type")) != "application/pdf":
            # PDF viewers are blocked inside a sandboxed document in Chromium;
            # images and text need no script, so lock them down.
            response["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return response
