"""{% load showcase_tags %}
{% showcase_images "signin_background" as bg_images %}
{% for img in bg_images %}<img src="{{ img.get_image_url }}" alt="{{ img.alt_text }}">{% endfor %}
"""
from django import template

from showcase.services import active_images

register = template.Library()


@register.simple_tag
def showcase_images(placement, limit=12):
    """Active images for a placement, as a list (evaluated once)."""
    try:
        return list(active_images(placement)[: int(limit)])
    except Exception:  # decorative: never break a page over a banner
        return []
