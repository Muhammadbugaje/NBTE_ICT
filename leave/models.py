from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class LeaveType(TimeStampedModel):
    """A kind of leave HR offers (v2.1 Phase D, F1-F3). HR creates as many as
    it needs in Settings -> Leave types; the seed set ``hr.leave_types`` adds
    the Public Service defaults (days marked [TO CONFIRM], decision 26)."""

    AUDITED = True

    ANNUAL = "annual"

    MODE_DATES = "dates"
    MODE_DAYS = "days"
    MODE_CHOICES = [
        (MODE_DATES, "First and last day (date range)"),
        (MODE_DAYS, "First day and number of days"),
    ]
    COUNT_WORKING = "working"
    COUNT_CALENDAR = "calendar"
    COUNTING_CHOICES = [
        (COUNT_WORKING, "Working days (skip weekends and public holidays)"),
        (COUNT_CALENDAR, "Calendar days (weekends and holidays count)"),
    ]
    GENDER_ANY = ""
    GENDER_FEMALE = "F"
    GENDER_MALE = "M"
    GENDER_CHOICES = [
        (GENDER_ANY, "Everyone"),
        (GENDER_FEMALE, "Women only"),
        (GENDER_MALE, "Men only"),
    ]

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    days = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Days awarded per year. Leave empty for no yearly limit (HR decides per application).",
    )
    input_mode = models.CharField(max_length=8, choices=MODE_CHOICES, default=MODE_DATES)
    counting = models.CharField(max_length=10, choices=COUNTING_CHOICES, default=COUNT_WORKING)
    paid = models.BooleanField(default=True)
    gender_restriction = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True, default="")
    needs_document = models.BooleanField(
        default=False, help_text="The application must carry a supporting document (e.g. a medical note).")
    notice_days = models.PositiveSmallIntegerField(
        default=0, help_text="Apply at least this many calendar days before the first day (0 = no notice).")
    max_per_year = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Most applications of this type per person per year (empty = no limit).")
    max_days_per_application = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Longest single application, in counted days (empty = no limit).")
    carry_over_days = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    colour = models.CharField(max_length=16, blank=True, default="",
                              help_text="Optional accent for calendars (a CSS colour token such as 'teal').")
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]
        permissions = [
            ("manage_leave_types", "Can manage leave types and public holidays (HR settings)"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_days_mode(self):
        return self.input_mode == self.MODE_DAYS

    @property
    def attendance_label(self):
        """"On maternity leave" / "On leave without pay" (F6)."""
        name = self.name.strip()
        low = name[:1].lower() + name[1:]
        return f"On {low}" if "leave" in low else f"On leave ({name})"

    @classmethod
    def annual(cls):
        obj = cls.objects.filter(code=cls.ANNUAL).first()
        if obj is None:
            obj, _ = cls.objects.get_or_create(code=cls.ANNUAL, defaults={"name": "Annual leave",
                                                                         "sort_order": 1})
        return obj


class _DefaultsToAnnual:
    """Rows saved without a type are annual leave (the v2 behaviour)."""

    def save(self, *args, **kwargs):
        if not self.leave_type_id:
            self.leave_type = LeaveType.annual()
        super().save(*args, **kwargs)


class LeaveEntitlement(_DefaultsToAnnual, TimeStampedModel):
    AUDITED = True

    staff = models.ForeignKey(
        "staff.Staff",
        on_delete=models.PROTECT,
        related_name="annual_leave_entitlements",
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="entitlements")
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
                fields=("staff", "leave_type", "year"),
                name="leave_entitlement_staff_type_year_uniq",
            ),
            models.CheckConstraint(
                condition=Q(days__gte=0),
                name="leave_entitlement_days_nonnegative",
            ),
        ]
        permissions = [
            ("manage_leave_entitlements", "Can manage leave entitlements"),
        ]

    def __str__(self):
        return f"{self.staff.full_name} — {self.leave_type} {self.year}: {self.days} days"


class LeaveApplication(_DefaultsToAnnual, TimeStampedModel):
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
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="applications")
    start_date = models.DateField()
    end_date = models.DateField()
    days_requested = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Days-mode types: the number of days asked for.")

    class Meta:
        ordering = ["start_date", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=models.F("start_date")),
                name="leave_application_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.staff.full_name}: {self.leave_type} {self.start_date}–{self.end_date}"
