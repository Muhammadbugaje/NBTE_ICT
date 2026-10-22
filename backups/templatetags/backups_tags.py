from django import template

from backups.destinations import human_size

register = template.Library()


@register.filter
def filesize_h(value):
    return human_size(value)


@register.filter
def duration_h(value):
    if value is None:
        return ""
    seconds = int(value.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


@register.filter
def bk_field(key):
    """Form field name for a setting key (dots are awkward in names)."""
    return str(key).replace(".", "_")
