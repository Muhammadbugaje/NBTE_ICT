from django.core.management.base import BaseCommand

from config.defaults import DEFAULTS
from config.models import ConfigEntry


class Command(BaseCommand):
    help = "Create any missing registered config entries (never overwrites existing values)."

    def handle(self, *args, **opts):
        for key, value, vtype, group, desc in DEFAULTS:
            _, created = ConfigEntry.objects.get_or_create(
                key=key, scope="global",
                defaults={"value": value, "value_type": vtype, "group": group, "description": desc})
            self.stdout.write(f"{'created' if created else 'exists '} {key}")
