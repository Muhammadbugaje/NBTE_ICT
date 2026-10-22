"""Template helpers for the Phase 9 chat UI."""
import json

from django import template
from django.utils.safestring import mark_safe

from messaging.chat_services import highlight_html

register = template.Library()


@register.filter
def chat_text(text, q=""):
    """Escape, highlight search matches and keep line breaks."""
    html = highlight_html(text or "", q or "")
    return mark_safe(html.replace("\r\n", "\n").replace("\n", "<br>"))


@register.filter
def as_json(value):
    return mark_safe(json.dumps(value).replace("<", "\\u003c"))


@register.filter
def bar_height(value):
    """Waveform bar height in % (with a visible minimum)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 0
    return max(12, min(100, v))


@register.simple_tag
def ms_label(ms):
    seconds = int(round((ms or 0) / 1000))
    return f"{seconds // 60}:{seconds % 60:02d}"


@register.simple_tag
def reaction_emojis():
    from messaging.models import REACTION_EMOJIS

    return REACTION_EMOJIS


@register.filter
def chat_time(value):
    """"14:05" today, "Mon 14:05" this week, else "12 Oct 14:05"."""
    from django.utils import timezone

    if not value:
        return ""
    local = timezone.localtime(value)
    today = timezone.localdate()
    delta = (today - local.date()).days
    if delta == 0:
        return local.strftime("%H:%M")
    if 0 < delta < 7:
        return local.strftime("%a %H:%M")
    if local.year == today.year:
        return local.strftime("%-d %b %H:%M")
    return local.strftime("%-d %b %Y")
