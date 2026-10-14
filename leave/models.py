from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class LeaveEntitlement(TimeStampedModel):
    AUDITED = True

    staff = models.ForeignKey(
        "staff.Staff",
        on_delete=models.PROTECT,
        related_name="annual_leave_entitlements",
    )
    year = models.PositiveSmallIntegerField()
    days = models.DecimalField(max_digits=5, decimal_places=2)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["staff__last_name", "staff__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=("staff", "year"),
                name="leave_entitlement_staff_year_uniq",
            ),
            models.CheckConstraint(
                condition=Q(days__gte=0),
                name="leave_entitlement_days_nonnegative",
            ),
        ]
        permissions = [
            ("manage_leave_entitlements", "Can manage annual leave entitlements"),
        ]

    def __str__(self):
        return f"{self.staff.full_name} — {self.year}: {self.days} days"


class LeaveApplication(TimeStampedModel):
    AUDITED = True

    request = models.OneToOneField(
        "workflow.Request",
        on_delete=models.CASCADE,
        related_name="annual_leave_application",
    )
    staff = models.ForeignKey(
        "staff.Staff",
        on_delete=models.PROTECT,
        related_name="annual_leave_applications",
    )
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["start_date", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=models.F("start_date")),
                name="leave_application_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.staff.full_name}: {self.start_date}–{self.end_date}"
