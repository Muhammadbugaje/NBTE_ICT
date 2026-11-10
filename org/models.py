"""Organisation model (Master Plan v2.1 §4, Phase B: B6-B8, C7, X31).

* ``GradeLevel``   — CONTEDISS levels (placeholders "to confirm" until HR fills them).
* ``Division``     — a division of one department, with a head and a task capacity.
* ``GrantablePermission`` — the allow-list of permissions a director may grant
  to their own staff (X31), with a duration limit.

The rules live in ``org/services.py`` (re-exported as ``import org``).
"""
from django.conf import settings
from django.contrib.auth.models import Permission
from django.db import models
from django.utils.text import slugify

from core.models import TimeStampedModel


class GradeLevel(TimeStampedModel):
    """One salary grade level (CONTEDISS 01-17). Seniority for promotions (D4)
    and the role-eligibility check (C9) comes from ``number``."""

    AUDITED = True
    SCALE_CHOICES = [("CONTEDISS", "CONTEDISS"), ("CONHESS", "CONHESS"), ("OTHER", "Other")]

    number = models.PositiveSmallIntegerField(unique=True, help_text="Level number, e.g. 8 for CONTEDISS 08.")
    code = models.SlugField(max_length=30, unique=True, blank=True, help_text="Stable code, e.g. 'contediss-08'.")
    scale = models.CharField(max_length=20, choices=SCALE_CHOICES, default="CONTEDISS")
    label = models.CharField(max_length=60, blank=True, help_text="Display name, e.g. 'CONTEDISS 08'.")
    min_years_in_grade = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Minimum years before promotion to the next level (to confirm by HR).")
    max_role = models.ForeignKey(
        "accounts.Role", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Highest role a person on this level may hold (optional).")
    notes = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["number"]
        permissions = [("manage_grade_levels", "Can manage grade levels and designations")]

    def __str__(self):
        return self.display

    @property
    def display(self):
        return self.label or f"{self.scale} {self.number:02d}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"{self.scale.lower()}-{self.number:02d}"
        if not self.label:
            self.label = f"{self.scale} {self.number:02d}"
        super().save(*args, **kwargs)


class Division(TimeStampedModel):
    """A division (or ES-office unit) inside one department (B6). The head
    gets the "Division Head" group while appointed (B7)."""

    AUDITED = True

    department = models.ForeignKey("staff.Department", on_delete=models.CASCADE, related_name="divisions")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=60, blank=True)
    head = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="headed_divisions")
    max_open_tasks = models.PositiveIntegerField(
        default=10, help_text="How many open group tasks the division can hold at once (B8).")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["department", "code"], name="org_division_unique_code"),
            models.UniqueConstraint(fields=["department", "name"], name="org_division_unique_name"),
        ]
        permissions = [("manage_all_divisions", "Can manage divisions of any department")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            base = slugify(self.name)[:55] or "division"
            code, n = base, 2
            while Division.objects.filter(department_id=self.department_id, code=code).exclude(pk=self.pk).exists():
                code, n = f"{base}-{n}", n + 1
            self.code = code
        super().save(*args, **kwargs)


class GrantablePermission(TimeStampedModel):
    """X31 allow-list: a permission a director may grant to their own staff.

    ``min_grantor_rank`` (the spec calls it ``max_grantor_rank``): the grantor's
    role rank must be at least this (0 = any director). ``max_duration_days``:
    the longest grant allowed (empty = ``org.grant.max_days_default``)."""

    AUDITED = True

    permission = models.OneToOneField(Permission, on_delete=models.CASCADE, related_name="grantable")
    min_grantor_rank = models.PositiveIntegerField(default=0)
    max_duration_days = models.PositiveIntegerField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["permission__content_type__app_label", "permission__codename"]
        permissions = [
            ("assign_director", "Can assign and vacate department directors"),
            ("manage_offices", "Can manage offices and their permission groups"),
        ]

    def __str__(self):
        return f"{self.permission.content_type.app_label}.{self.permission.codename}"

    @property
    def label(self):
        return str(self)
