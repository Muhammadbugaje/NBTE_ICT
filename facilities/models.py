"""v2.2 — the NBTE register of minimum physical facilities and equipment per
ND/HND programme (from the curricula), managed in Excel with a preview of
every change, analysed, and linked to the SSQ curriculum catalogue."""
from django.conf import settings
from django.db import models
from django.utils.text import slugify

from core.models import TimeStampedModel


class FacilityProgramme(TimeStampedModel):
    AUDITED = True
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    levels = models.CharField(max_length=20, blank=True, help_text="ND, HND or 'ND, HND'.")
    programme_master = models.ForeignKey("institutions.ProgrammeMaster", null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="facility_registers")
    source = models.CharField(max_length=500, blank=True, help_text="Curricula the list was taken from.")
    notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="facility_programmes",
                                    help_text="Officer who maintains this programme's list.")
    is_active = models.BooleanField(default=True)
    last_synced_to_catalogue = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        permissions = [("manage_facilities", "Facilities & equipment: can import, edit and assign")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:200] or "programme"
            slug, n = base, 2
            while FacilityProgramme.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)


class FacilitySection(models.Model):
    """A laboratory, workshop, studio or room (A., B., … in the curriculum)."""

    programme = models.ForeignKey(FacilityProgramme, on_delete=models.CASCADE, related_name="sections")
    code = models.CharField(max_length=4, blank=True)
    name = models.CharField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["programme", "sort_order", "code"]
        constraints = [models.UniqueConstraint(fields=["programme", "name"], name="facilities_section_unique")]

    def __str__(self):
        return f"{self.code}. {self.name}" if self.code else self.name


class FacilityItem(TimeStampedModel):
    AUDITED = True
    programme = models.ForeignKey(FacilityProgramme, on_delete=models.CASCADE, related_name="items")
    section = models.ForeignKey(FacilitySection, on_delete=models.CASCADE, related_name="items")
    group = models.CharField(max_length=255, blank=True, help_text="Sub-heading inside the facility, if any.")
    name = models.CharField(max_length=500)
    nd_qty = models.CharField("ND quantity", max_length=60, blank=True)
    hnd_qty = models.CharField("HND quantity", max_length=60, blank=True)
    nd_qty_num = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    hnd_qty_num = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    remarks = models.CharField(max_length=500, blank=True)
    is_critical = models.BooleanField(default=False, help_text="Counts double in the SSQ shortfall score.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["programme", "section__sort_order", "sort_order"]
        constraints = [models.UniqueConstraint(fields=["section", "group", "name"], name="facilities_item_unique")]
        indexes = [models.Index(fields=["programme", "is_active"], name="facilities_item_prog_active"),
                   models.Index(fields=["name"], name="facilities_item_name")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from .docx_parser import qty_number

        self.nd_qty_num = qty_number(self.nd_qty)
        self.hnd_qty_num = qty_number(self.hnd_qty)
        super().save(*args, **kwargs)
