"""Geography (v2.1 X49): LGAs and NBTE zonal offices.

Kept in its own module (imported at the end of staff/models.py) so the
organisation and HR work on staff/models.py does not collide with it.
States stay `staff.StateOfOrigin` (36 + FCT, with zones).

Seeded by `seed_nbte --only geo` from core/seed_data/geo/*.json; edited in
Settings -> States & LGAs (permission staff.manage_geography).
"""
from django.db import models
from django.utils.text import slugify

from core.models import TimeStampedModel


class LGA(TimeStampedModel):
    AUDITED = True

    state = models.ForeignKey("staff.StateOfOrigin", on_delete=models.PROTECT, related_name="lgas")
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=60, unique=True, blank=True,
                            help_text="State code + slug of the name, e.g. kd-zaria (filled automatically).")
    headquarters = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["state__name", "sort_order", "name"]
        verbose_name = "LGA"
        verbose_name_plural = "LGAs"
        constraints = [models.UniqueConstraint(fields=["state", "name"], name="staff_lga_state_name_uniq")]
        indexes = [models.Index(fields=["state", "is_active"])]
        permissions = [("manage_geography", "Can add and edit states, LGAs and zonal offices")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            prefix = (self.state.code or slugify(self.state.name)[:6]) if self.state_id else "x"
            base = f"{prefix}-{slugify(self.name)}"[:56] or prefix
            code, n = base, 2
            while LGA.objects.exclude(pk=self.pk).filter(code=code).exists():
                code, n = f"{base}-{n}", n + 1
            self.code = code
        super().save(*args, **kwargs)


class ZonalOffice(TimeStampedModel):
    AUDITED = True

    code = models.SlugField(max_length=10, unique=True)
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    email_confirmed = models.BooleanField(default=False,
                                          help_text="False until NBTE confirms the address (some are truncated "
                                                    "in the source document).")
    states = models.ManyToManyField("staff.StateOfOrigin", blank=True, related_name="zonal_offices")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


from . import geo  # noqa: E402,F401  (cache-bump receivers for LGA edits)
