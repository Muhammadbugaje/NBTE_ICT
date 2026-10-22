"""App-wide UX template tags (Phase 8).

``{% preview_button url name size %}`` renders the small pill "Preview"
button that opens the quick-preview viewer (static/js/file-preview.js)
next to a download link. The viewer asks the same URL with ``?inline=1``;
every file view applies its normal permission checks to that request.

    {% load ux_tags %}
    <a href="{{ d.download_url }}">Download</a>
    {% preview_button d.download_url d.display_name d.size_bytes %}

``size`` may be a byte count or an already formatted label. Pass
``compact=True`` for an icon-only button (keeps an accessible label).
"""
import os

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

EYE = ('<svg class="fpv-btn__icon" width="{s}" height="{s}" viewBox="0 0 24 24" fill="none" '
       'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
       'aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
       '<circle cx="12" cy="12" r="3"/></svg>')


def human_size(size):
    if size in (None, ""):
        return ""
    if isinstance(size, str) and not size.isdigit():
        return size
    try:
        n = int(size)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n >= 1048576:
        return f"{n / 1048576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


@register.simple_tag
def preview_button(url, name="", size="", label="Preview", compact=False, download=None):
    if callable(url):
        url = url()
    if not url:
        return ""
    name = str(name or "")
    if not name:
        name = os.path.basename(str(url).split("?", 1)[0]) or "File"
    if download is None:
        download = url
    classes = "fpv-btn" + (" fpv-btn--compact" if compact else "")
    text = "" if compact else format_html('<span class="fpv-btn__label">{}</span>', label)
    return format_html(
        '<button type="button" class="{}" data-preview="{}" data-preview-name="{}" '
        'data-preview-size="{}" data-preview-download="{}" aria-label="Preview {}" '
        'title="Preview {}">{}{}</button>',
        classes, url, name, human_size(size), download, name, name,
        mark_safe(EYE.format(s=14)), text,
    )


# ---------------------------------------------------------------------------
# Bulk select (static/js/bulk-select.js)
# ---------------------------------------------------------------------------
@register.simple_tag
def bulk_check(form_id, pk, label=""):
    """Per-row checkbox, tied to the bulk form by the HTML ``form`` attribute
    (so it works even inside rows that contain their own forms)."""
    return format_html(
        '<label class="bulk-check" data-uxb-check><input type="checkbox" name="ids" value="{}" form="{}" '
        'data-uxb-item="{}" aria-label="Select {}"><span class="bulk-check__box" aria-hidden="true"></span></label>',
        pk, form_id, form_id, label or "this item",
    )


@register.simple_tag
def bulk_all(form_id, label="Select all on this page"):
    # A "Select" button switches the list into select mode; until then the
    # checkboxes stay hidden (bulk-select.js). Without JS they always show.
    return format_html(
        '<span class="bulk-mode" data-uxb-mode="{}">'
        '<button type="button" class="bulk-select-btn" data-uxb-toggle="{}" aria-pressed="false">'
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="5"/>'
        '<path d="m8 12 3 3 5-6"/></svg><span data-uxb-toggle-label>Select</span></button>'
        '<label class="bulk-all" data-uxb-check><input type="checkbox" data-uxb-all="{}">'
        '<span class="bulk-check__box" aria-hidden="true"></span><span class="bulk-all__label">{}</span></label>'
        '</span>',
        form_id, form_id, form_id, label,
    )


@register.simple_tag(takes_context=True)
def bulk_bar(context, form_id, url, actions="delete", noun="item", nouns="", note="",
             confirm_title="", confirm_body=""):
    """The sticky action bar + its form.

    ``actions`` is a comma list of ``verb`` or ``verb:Label`` or
    ``verb:Label:danger`` (danger → NBTEConfirm before running and the rows
    are removed optimistically). ``remove`` in the 4th slot marks a
    non-destructive verb that also takes rows off this list (e.g. archive).
    """
    from django.middleware.csrf import get_token

    request = context.get("request")
    token = get_token(request) if request is not None else ""
    nouns = nouns or noun + "s"
    buttons = []
    for spec in [a.strip() for a in str(actions).split(",") if a.strip()]:
        parts = spec.split(":")
        verb = parts[0]
        label = parts[1] if len(parts) > 1 and parts[1] else verb.replace("_", " ").capitalize()
        tone = parts[2] if len(parts) > 2 else ("danger" if verb == "delete" else "")
        removes = tone == "danger" or (len(parts) > 3 and parts[3] == "remove")
        cls = "bulk-bar__btn" + (" bulk-bar__btn--danger" if tone == "danger" else "")
        buttons.append(format_html(
            '<button type="submit" name="action" value="{}" class="{}" data-uxb-action="{}" '
            'data-uxb-removes="{}" data-uxb-tone="{}" data-uxb-label="{}">{}</button>',
            verb, cls, verb, "1" if removes else "", tone, label, label,
        ))
    path = request.get_full_path() if request is not None else ""
    return format_html(
        '<form id="{fid}" class="bulk-bar" method="post" action="{url}" data-uxb-bar="{fid}" '
        'data-uxb-noun="{noun}" data-uxb-nouns="{nouns}" data-uxb-confirm-title="{ct}" '
        'data-uxb-confirm-body="{cb}" aria-label="Actions for selected {nouns}">'
        '<input type="hidden" name="csrfmiddlewaretoken" value="{token}">'
        '<input type="hidden" name="next" value="{path}">'
        '<div class="bulk-bar__inner" role="toolbar" aria-label="Selected {nouns}">'
        '<span class="bulk-bar__count" data-uxb-count aria-live="polite">0 selected</span>'
        '{note}<span class="bulk-bar__actions">{buttons}</span>'
        '<button type="button" class="bulk-bar__btn bulk-bar__btn--ghost bulk-bar__cancel" data-uxb-cancel>Cancel</button>'
        '</div></form>',
        fid=form_id, url=url, noun=noun, nouns=nouns, ct=confirm_title, cb=confirm_body, token=token,
        path=path,
        note=format_html('<span class="bulk-bar__note">{}</span>', note) if note else "",
        buttons=mark_safe("".join(buttons)),
    )


# ---------------------------------------------------------------------------
# Profile completeness nudge (accounts/profile_nudges.py)
# ---------------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def profile_nudge(context):
    """Status for the banner + interrupt dialog, computed once per request.
    Also runs the once-a-day reminder check. Returns None when there is
    nothing to nudge about (complete, not staff, impersonation, …)."""
    request = context.get("request")
    if request is None:
        return None
    cached = getattr(request, "_profile_nudge", "unset")
    if cached != "unset":
        return cached
    from accounts import profile_nudges as nudges

    result = None
    user = getattr(request, "user", None)
    try:
        if nudges.applies_to(user) and not getattr(request, "is_impersonating", False):
            st = nudges.status(user)
            if not st["complete"]:
                from django.urls import reverse

                nudges.daily_check(request)
                result = dict(st, banner=nudges.banner_enabled(), interrupt=nudges.interrupt_enabled(),
                              gated=nudges.gated_paths(), profile_url=reverse("accounts:profile"),
                              continue_url=reverse("accounts:profile_nudge_continue"),
                              missing_top=st["missing"][:4], missing_more=max(0, len(st["missing"]) - 4))
    except Exception:  # a nudge must never break a page
        import logging

        logging.getLogger(__name__).exception("profile nudge failed")
        result = None
    request._profile_nudge = result
    return result
