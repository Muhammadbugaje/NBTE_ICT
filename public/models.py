from django.db import models

from core.models import TimeStampedModel
from core.storage import uuid_upload_to


class HistoryMilestone(TimeStampedModel):
    """One step in NBTE's history (v2.1 A1/A2): the landing timeline and /history/.

    Edited in Settings → History (HTMX). Text NBTE has not confirmed is kept
    with `is_placeholder` so the page marks it "to confirm"."""

    AUDITED = True
    MEDIA_SUBDIR = "public/history"

    year = models.CharField(max_length=12, help_text="Shown large on the timeline: 1977, 20XX, Today …")
    title = models.CharField(max_length=120)
    body = models.TextField(blank=True, help_text="Two or three sentences.")
    led_by = models.CharField(max_length=160, blank=True,
                              help_text="Who led this reform (for example the Executive Secretary at the time).")
    image = models.ImageField(upload_to=uuid_upload_to, blank=True, null=True)
    order = models.PositiveIntegerField(default=100, db_index=True, help_text="Lower comes first.")
    is_published = models.BooleanField(default=True)
    is_current = models.BooleanField(default=False, help_text="Highlight as 'today' at the end of the timeline.")
    is_placeholder = models.BooleanField(
        default=False, help_text="Facts still to be confirmed by NBTE: shown with a 'to confirm' tag.")

    class Meta:
        ordering = ["order", "pk"]
        permissions = [("manage_history", "Can add, edit, reorder and remove history milestones")]

    def __str__(self):
        return f"{self.year} · {self.title}"
