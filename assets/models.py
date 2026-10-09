"""Asset & inventory models.

Hierarchy:
    AssetCategory   — Computer / Laptop / Printer / Vehicle / Furniture / …
    Asset           — one physical item, uniquely identified by `asset_tag`
    AssetAssignment — one row per hand-off (who has it now, when, condition)
    AssetIssue      — a logged problem (linked to a workflow Request when raised)
    AssetAttachment — photos, receipts, warranty docs

Design notes:
- `asset_tag` is the QR payload and the human handle. Immutable after creation.
- `current_custodian` is denormalized from the open `AssetAssignment` row so
  the workflow's asset-picker query is trivially fast.
- `state` is the item's lifecycle state. `condition` is a physical grade
  captured at issue/return, kept separate from state.
- Soft-delete via core.SoftDeleteModel. Never hard-delete.
- Files: photos live under media/assets/photos/, attachments under
  media/assets/attachments/ — see core.storage.uuid_upload_to.
"""
import io
from datetime import date

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core.models import SoftDeleteModel, TimeStampedModel
from core.storage import capture_original_filename, uuid_upload_to


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
STATE_CHOICES = [
    ("new", "New"),
    ("in_service", "In service"),
    ("under_repair", "Under repair"),
    ("damaged", "Damaged"),
    ("retired", "Retired"),
    ("lost", "Lost"),
    ("disposed", "Disposed"),
]

CONDITION_CHOICES = [
    ("new", "New"),
    ("good", "Good"),
    ("fair", "Fair"),
    ("poor", "Poor"),
    ("damaged", "Damaged"),
]


# ---------------------------------------------------------------------------
# AssetCategory
# ---------------------------------------------------------------------------
class AssetCategory(TimeStampedModel):
    """A kind of asset. Defines an optional `spec_fields` schema for the
    structured spec attached to every asset in that category.

    Example `spec_fields` for "Computer":
        [
          {"key": "cpu", "label": "CPU", "type": "text"},
          {"key": "ram_gb", "label": "RAM (GB)", "type": "number"},
          {"key": "storage_gb", "label": "Storage (GB)", "type": "number"},
          {"key": "os", "label": "Operating system", "type": "text"}
        ]
    """

    AUDITED = True

    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(
        max_length=50, blank=True,
        help_text="Optional icon hint (e.g. 'laptop'). Rendered by the UI.",
    )
    spec_fields = models.JSONField(
        default=list, blank=True,
        help_text="List of {key, label, type, required?} entries describing this category's spec.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "asset categories"

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------
class Asset(SoftDeleteModel):
    """One physical item in the inventory."""

    AUDITED = True
    MEDIA_SUBDIR = "assets/photos"

    # Identity
    asset_tag = models.CharField(
        max_length=40, unique=True, db_index=True,
        help_text="Unique inventory code (also the QR payload). Immutable once set.",
    )
    serial_number = models.CharField(
        max_length=100, blank=True, db_index=True,
        help_text="Manufacturer serial number, if any.",
    )
    category = models.ForeignKey(
        AssetCategory, on_delete=models.PROTECT, related_name="assets",
    )

    # Description
    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=150, blank=True)
    color = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    spec = models.JSONField(
        default=dict, blank=True,
        help_text="Structured spec — keys match the category's `spec_fields`.",
    )

    # Ownership & location
    owner_department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.PROTECT, related_name="owned_assets",
        help_text="Which department owns this asset.",
    )
    current_location = models.CharField(
        max_length=200, blank=True,
        help_text="Free-text location, e.g. 'Block B, Room 12'.",
    )
    current_custodian = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="custody_assets",
        help_text="Denormalized from the open AssetAssignment. Updated by the service layer.",
    )

    # Lifecycle
    state = models.CharField(
        max_length=20, choices=STATE_CHOICES, default="new", db_index=True,
    )
    acquired_at = models.DateField(null=True, blank=True)
    cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )
    warranty_until = models.DateField(null=True, blank=True)

    # Optional hints for the UI
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["asset_tag"]
        indexes = [
            models.Index(fields=["category", "state"]),
            models.Index(fields=["current_custodian", "state"]),
            models.Index(fields=["owner_department", "state"]),
        ]

    def __str__(self):
        label = f"{self.brand} {self.model}".strip() or self.category.name
        return f"{self.asset_tag} · {label}"

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    @property
    def display_name(self):
        """Human-friendly one-liner, best-effort."""
        parts = [self.brand, self.model]
        label = " ".join(p for p in parts if p).strip()
        return label or self.category.name

    @property
    def is_available(self):
        """True when the item is in service and not currently held by anyone."""
        return self.state in ("new", "in_service") and self.current_custodian_id is None

    def open_assignment(self):
        return self.assignments.filter(returned_at__isnull=True).first()

    # ------------------------------------------------------------------
    # QR code
    # ------------------------------------------------------------------
    def qr_payload(self):
        """The string encoded into the QR code. A URL pointing at the asset's
        detail page — scanning opens it directly."""
        from django.urls import reverse
        path = reverse("assets:detail", args=[self.asset_tag])
        return f"{settings.SITE_URL.rstrip('/')}{path}"

    def qr_svg(self, box_size=6, border=2):
        """Return the QR code as an inline SVG string, ready for embedding
        in HTML. Returns an empty string if `qrcode` isn't installed."""
        try:
            import qrcode
            from qrcode.image.svg import SvgPathImage
        except ImportError:
            return ""
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box_size,
            border=border,
            image_factory=SvgPathImage,
        )
        qr.add_data(self.qr_payload())
        qr.make(fit=True)
        img = qr.make_image()
        buf = io.BytesIO()
        img.save(buf)
        svg = buf.getvalue().decode("utf-8")
        # Strip the XML declaration so it can be inlined directly into HTML.
        if svg.startswith("<?xml"):
            svg = svg.split("?>", 1)[1].strip()
        return svg


# ---------------------------------------------------------------------------
# AssetAssignment
# ---------------------------------------------------------------------------
class AssetAssignment(TimeStampedModel):
    """One hand-off. Multiple rows per asset over time; the currently-open
    row (returned_at is null) identifies the current custodian."""

    AUDITED = True

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="assignments")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="asset_assignments",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
    )
    assigned_at = models.DateTimeField(default=timezone.now, db_index=True)

    returned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    returned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
        help_text="Who received the item back.",
    )

    condition_on_assign = models.CharField(
        max_length=20, choices=CONDITION_CHOICES, default="good",
    )
    condition_on_return = models.CharField(
        max_length=20, choices=CONDITION_CHOICES, blank=True,
    )

    location_on_assign = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-assigned_at", "-id"]

    def __str__(self):
        return f"{self.asset.asset_tag} → {self.assigned_to_id} ({'open' if self.is_open else 'closed'})"

    @property
    def is_open(self):
        return self.returned_at is None

    @property
    def duration_days(self):
        end = self.returned_at or timezone.now()
        return (end - self.assigned_at).days


# ---------------------------------------------------------------------------
# AssetIssue
# ---------------------------------------------------------------------------
class AssetIssue(TimeStampedModel):
    """A logged problem on an asset. Optionally linked to a workflow Request
    (populated when the request type is an asset request)."""

    AUDITED = True

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="issues")
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reported_asset_issues",
    )
    reported_at = models.DateTimeField(default=timezone.now, db_index=True)

    summary = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    resolution_notes = models.TextField(blank=True)

    # Link to the workflow request, if any (populated by workflow integration)
    request = models.ForeignKey(
        "workflow.Request", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="asset_issues",
    )

    class Meta:
        ordering = ["-reported_at", "-id"]

    def __str__(self):
        return f"{self.asset.asset_tag}: {self.summary[:60]}"

    @property
    def is_resolved(self):
        return self.resolved_at is not None


# ---------------------------------------------------------------------------
# AssetAttachment
# ---------------------------------------------------------------------------
class AssetAttachment(TimeStampedModel):
    """Photos, receipts, warranty documents."""

    AUDITED = True
    MEDIA_SUBDIR = "assets/attachments"

    KIND_CHOICES = [
        ("photo", "Photo"),
        ("receipt", "Receipt"),
        ("warranty", "Warranty"),
        ("manual", "Manual"),
        ("other", "Other"),
    ]

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
    )
    file = models.FileField(upload_to=uuid_upload_to)
    original_name = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="other")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name or f"attachment {self.pk}"

    def save(self, *args, **kwargs):
        if self.file and not getattr(self.file, "_committed", True):
            capture_original_filename(self, "file", "original_name")
        super().save(*args, **kwargs)