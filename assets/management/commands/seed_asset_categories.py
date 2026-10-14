"""Seed the standard asset categories with their spec schemas.

Idempotent — re-running updates existing rows by `code` without duplicating.

Usage:
    python manage.py seed_asset_categories
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from assets.models import AssetCategory


CATEGORIES = [
    # code, name, icon, sort, spec_fields
    (
        "computer", "Desktop Computer", "monitor", 10,
        [
            {"key": "cpu", "label": "CPU", "type": "text"},
            {"key": "ram_gb", "label": "RAM (GB)", "type": "number"},
            {"key": "storage_gb", "label": "Storage (GB)", "type": "number"},
            {"key": "os", "label": "Operating system", "type": "text"},
        ],
    ),
    (
        "laptop", "Laptop", "laptop", 20,
        [
            {"key": "cpu", "label": "CPU", "type": "text"},
            {"key": "ram_gb", "label": "RAM (GB)", "type": "number"},
            {"key": "storage_gb", "label": "Storage (GB)", "type": "number"},
            {"key": "os", "label": "Operating system", "type": "text"},
            {"key": "screen_in", "label": "Screen size (in)", "type": "number"},
        ],
    ),
    (
        "printer", "Printer", "printer", 30,
        [
            {"key": "type", "label": "Type", "type": "text",
             "help": "Laser / Inkjet / Dot matrix"},
            {"key": "networked", "label": "Networked", "type": "boolean"},
            {"key": "colour", "label": "Colour printing", "type": "boolean"},
        ],
    ),
    (
        "scanner", "Scanner", "scan", 40,
        [{"key": "type", "label": "Type", "type": "text"}],
    ),
    (
        "network", "Networking Equipment", "wifi", 50,
        [
            {"key": "type", "label": "Type", "type": "text",
             "help": "Router / Switch / Access Point / Firewall"},
            {"key": "ports", "label": "Ports", "type": "number"},
        ],
    ),
    (
        "vehicle", "Vehicle", "car", 60,
        [
            {"key": "plate", "label": "Plate number", "type": "text"},
            {"key": "make", "label": "Make", "type": "text"},
            {"key": "year", "label": "Year", "type": "number"},
            {"key": "fuel", "label": "Fuel type", "type": "text"},
            {"key": "capacity", "label": "Seating capacity", "type": "number"},
        ],
    ),
    (
        "furniture", "Furniture", "armchair", 70,
        [{"key": "type", "label": "Type", "type": "text"}],
    ),
    (
        "appliance", "Appliance", "wind", 80,
        [
            {"key": "type", "label": "Type", "type": "text",
             "help": "Air conditioner / Refrigerator / Microwave / Water dispenser"},
        ],
    ),
    (
        "other", "Other", "box", 100,
        [],
    ),
]


class Command(BaseCommand):
    help = "Seed standard asset categories."

    @transaction.atomic
    def handle(self, *args, **opts):
        for code, name, icon, sort, spec_fields in CATEGORIES:
            obj, created = AssetCategory.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "icon": icon,
                    "sort_order": sort,
                    "spec_fields": spec_fields,
                    "is_active": True,
                },
            )
            self.stdout.write(f"{'created' if created else 'updated'} category {code}")
        self.stdout.write(self.style.SUCCESS("Seed complete."))