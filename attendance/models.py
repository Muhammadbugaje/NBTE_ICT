"""Attendance models.

Two ways attendance gets recorded:

  1. GPS self check-in. A user opens the app, the browser sends their
     current lat/lng, the server checks it against every active
     `AttendanceLocation` linked to the user's department. If the point
     falls inside the location's effective radius (radius + offset),
     a record is created with `source='gps'`.

  2. Excel upload. A director or office head uploads a monthly matrix.
     Rows are staff (by email), columns are day numbers of the month.
     Cell value `1` marks present, `0` marks absent. Records are created
     with `source='excel'`.

Every record is one (user, date) pair. The unique constraint guarantees
only one status per person per day.
"""
from datetime import time

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


# ===========================================================================
# Location
# ===========================================================================
class AttendanceLocation(TimeStampedModel):
    """A physical place where staff can check in.

    Multiple departments can share one location (same building), and one
    department can have multiple locations (main building + annex). The
    link is M2M.

    If a location has no departments linked, it matches every department
    (useful for a shared HQ where everyone checks in).
    """
    AUDITED = True

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    # ---- Geographic -----------------------------------------------------
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6,
        help_text="Center of the geofence. Copy from Google Maps.",
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6,
        help_text="Center of the geofence. Copy from Google Maps.",
    )
    radius_meters = models.PositiveIntegerField(
        default=200,
        help_text="Base radius of the geofence, in meters.",
    )
    gps_offset_meters = models.PositiveIntegerField(
        default=50,
        help_text=(
            "Extra grace margin outside the base radius where check-in is "
            "still allowed. Effective radius = radius + offset."
        ),
    )

    # ---- Working hours --------------------------------------------------
    work_start_time = models.TimeField(
        default=time(9, 0),
        help_text="Standard start time. Check-ins after this are 'late'.",
    )
    work_end_time = models.TimeField(
        default=time(17, 0),
        help_text="Standard end time (reserved for future hours-worked tracking).",
    )
    grace_minutes = models.PositiveIntegerField(
        default=15,
        help_text=(
            "Minutes after work_start_time during which a check-in is still "
            "'present' rather than 'late'."
        ),
    )

    # ---- Scope ----------------------------------------------------------
    departments = models.ManyToManyField(
        "staff.Department",
        blank=True,
        related_name="attendance_locations",
        help_text=(
            "Departments whose staff may check in here. Blank = applies to "
            "every department (e.g. a shared HQ reception)."
        ),
    )

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Attendance location"
        verbose_name_plural = "Attendance locations"

    def __str__(self):
        return self.name

    @property
    def effective_radius_meters(self):
        return self.radius_meters + self.gps_offset_meters

    def distance_from(self, lat, lng):
        """Return distance in meters from a point to this location's center."""
        from .utils import haversine_meters
        return haversine_meters(self.latitude, self.longitude, lat, lng)

    def contains(self, lat, lng):
        """True if the point falls inside the effective geofence."""
        dist = self.distance_from(lat, lng)
        if dist is None:
            return False, None
        return dist <= self.effective_radius_meters, dist

    def applies_to_department(self, department_id):
        """True if this location serves the given department (or everyone)."""
        if not self.departments.exists():
            return True
        return self.departments.filter(pk=department_id).exists()


# ===========================================================================
# Record
# ===========================================================================
class AttendanceRecord(TimeStampedModel):
    """One row per (user, date). Presence is derived from status:
    PRESENT and LATE both count as 'was in attendance'; ABSENT does not.
    """
    AUDITED = True

    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    EXCUSED = "excused"
    HOLIDAY = "holiday"
    ON_LEAVE = "on_leave"

    STATUS_CHOICES = [
        (PRESENT, "Present"),
        (LATE, "Late"),
        (ABSENT, "Absent"),
        (EXCUSED, "Excused"),
        (HOLIDAY, "Holiday"),
        (ON_LEAVE, "On approved leave"),
    ]

    STATUSES_PRESENT = {PRESENT, LATE}
    STATUSES_ABSENT = {ABSENT}

    SOURCE_GPS = "gps"
    SOURCE_EXCEL = "excel"
    SOURCE_MANUAL = "manual"
    SOURCE_AUTO = "auto"
    SOURCE_LEAVE = "leave"

    SOURCE_CHOICES = [
        (SOURCE_GPS, "GPS check-in"),
        (SOURCE_EXCEL, "Excel upload"),
        (SOURCE_MANUAL, "Manual entry"),
        (SOURCE_AUTO, "Automatic"),
        (SOURCE_LEAVE, "Approved leave"),
    ]

    # ---- Identity -------------------------------------------------------
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    date = models.DateField(db_index=True)

    # ---- Result ---------------------------------------------------------
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=ABSENT,
    )

    # ---- Check-in detail (only set for gps / manual) --------------------
    check_in_at = models.DateTimeField(null=True, blank=True)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
    )
    distance_meters = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Distance between the check-in point and the location center.",
    )
    location = models.ForeignKey(
        AttendanceLocation,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="records",
    )

    # ---- Provenance -----------------------------------------------------
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default=SOURCE_MANUAL,
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who created this record, if it wasn't the user themselves.",
    )
    leave_application = models.ForeignKey(
        "leave.LeaveApplication",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_records",
    )
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date", "user__first_name", "user__last_name"]
        indexes = [
            models.Index(fields=["user", "-date"]),
            models.Index(fields=["date", "status"]),
        ]
        verbose_name = "Attendance record"
        verbose_name_plural = "Attendance records"
        permissions = [
            ("view_all_attendance",
             "Can view attendance reports and dashboards for ALL departments"),
            ("view_department_attendance",
             "Can view attendance reports and dashboards for their own department"),
        ]

    def __str__(self):
        return f"{self.user} — {self.date} — {self.get_status_display()}"

    @property
    def is_present(self):
        return self.status in self.STATUSES_PRESENT

    @property
    def is_absent(self):
        return self.status in self.STATUSES_ABSENT


# ===========================================================================
# Rotating check-in codes
# ===========================================================================
class LocationCheckInCode(TimeStampedModel):
    """A short rotating code that proves a staff member is physically at
    the office when using the manual check-in flow.

    One row per (location, window_start). Windows are computed from the
    configured rotation cadence. Codes only exist during the location's
    working hours (plus a configurable grace window).
    """

    AUDITED = True
    # Review M8: live check-in codes must not be readable from the audit log.
    AUDIT_EXCLUDE_FIELDS = ("code",)

    location = models.ForeignKey(
        AttendanceLocation,
        on_delete=models.CASCADE,
        related_name="check_in_codes",
    )
    code = models.CharField(max_length=12, db_index=True)
    window_start = models.DateTimeField(db_index=True)
    window_end = models.DateTimeField()
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        unique_together = ("location", "window_start")
        ordering = ["-window_start"]
        indexes = [
            models.Index(fields=["location", "window_end"]),
            models.Index(fields=["code", "window_end"]),
        ]
        verbose_name = "Location check-in code"
        verbose_name_plural = "Location check-in codes"
        permissions = [
            ("view_location_codes",
             "Can view the live check-in codes for all office locations"),
        ]

    def __str__(self):
        return f"{self.location.name} — {self.code} ({self.window_start:%H:%M})"

    @property
    def is_live(self):
        from django.utils import timezone as _tz
        return self.window_end > _tz.now()

    @property
    def seconds_remaining(self):
        from django.utils import timezone as _tz
        delta = (self.window_end - _tz.now()).total_seconds()
        return max(0, int(delta))

