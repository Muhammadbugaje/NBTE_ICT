from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import ConfigEntry
from .registry import invalidate


@receiver([post_save, post_delete], sender=ConfigEntry)
def _invalidate(sender, instance, **kwargs):
    invalidate(instance.key, instance.scope)
