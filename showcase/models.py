"""Showcase images: decorative, admin-managed pictures for the sign-in page
background, the landing-page hero and the dashboard banner.

Placement is stored as a comma-separated list of codes in one CharField
(e.g. "signin_background,hero"). There are only four fixed placements and
they never need their own table, metadata or permissions, so a CharField
keeps queries join-free and the admin simple (a checkbox list). See
SHOWCASE_APP.md.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse

from core.models import TimeStampedModel
from core.storage import restricted_storage, uuid_upload_to

IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]


class ShowcaseImage(TimeStampedModel):
    AUDITED = True
    MEDIA_SUBDIR = "showcase"

    SIGNIN_BACKGROUND = "signin_background"
    HERO = "hero"
    DASHBOARD_BANNER = "dashboard_banner"
    EMAIL_HEADER = "email_header"
    PLACEMENT_CHOICES = [
        (SIGNIN_BACKGROUND, "Sign-in background"),
        (HERO, "Landing page hero"),
        (DASHBOARD_BANNER, "Dashboard banner"),
        (EMAIL_HEADER, "Email header"),
    ]
    PLACEMENT_CODES = [c for c, _ in PLACEMENT_CHOICES]

    image = models.ImageField(
        upload_to=uuid_upload_to,
        storage=restricted_storage,
        validators=[FileExtensionValidator(IMAGE_EXTENSIONS)],
        help_text="JPG, PNG or WebP. Landscape, at least 1920×1080 for backgrounds.",
    )
    title = models.CharField(max_length=120, blank=True)
    caption = models.TextField(blank=True)
    alt_text = models.CharField(
        max_length=200,
        help_text="Describe the image for people using screen readers. Required.",
    )
    placements = models.CharField(
        max_length=120,
        help_text="Where this image appears (one or more).",
    )
    order = models.PositiveIntegerField(
        default=0, help_text="Lower numbers show first. Change the number to reorder."
    )
    is_active = models.BooleanField(default=True)
    start_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional: only show from this date/time."
    )
    end_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional: stop showing after this date/time."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="showcase_images",
    )

    class Meta:
        ordering = ["order", "-created_at"]
        verbose_name = "showcase image"
        verbose_name_plural = "showcase images"
        indexes = [models.Index(fields=["is_active", "order"])]
        permissions = [
            ("manage_showcase", "Can add, edit and delete showcase images"),
        ]

    def __str__(self):
        return self.title or self.alt_text or f"Image {self.pk}"

    # ---- placement helpers ------------------------------------------------
    @property
    def placement_list(self):
        return [p for p in (self.placements or "").split(",") if p]

    def set_placements(self, codes):
        self.placements = ",".join(c for c in self.PLACEMENT_CODES if c in set(codes))

    def get_placements_display(self):
        labels = dict(self.PLACEMENT_CHOICES)
        return ", ".join(labels.get(p, p) for p in self.placement_list)

    def clean(self):
        unknown = [p for p in self.placement_list if p not in self.PLACEMENT_CODES]
        if unknown:
            raise ValidationError({"placements": f"Unknown placement: {', '.join(unknown)}"})
        if not self.placement_list:
            raise ValidationError({"placements": "Choose at least one placement."})
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": "End must be after the start."})

    # ---- serving ----------------------------------------------------------
    def get_image_url(self):
        """Public URL (the file itself lives in protected storage and is
        streamed by showcase.views.image, which needs no login)."""
        stamp = int(self.updated_at.timestamp()) if self.updated_at else 0
        return reverse("showcase:image", args=[self.pk]) + f"?v={stamp}"