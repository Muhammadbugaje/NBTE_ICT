"""Rotation (v2.1 X27–X29, spec_v21 G6.c).

``RotationPool``         who takes turns at one kind of work in one department
                         (``document_analysis:dpap``, ``ssq_review:dpap``,
                         ``oversight_ict`` …), with per-pool rule overrides.
``RotationMember``       one person in a pool (weight, own cap, pause,
                         disciplines, last time chosen). ``PoolMember`` is an alias.
``ConflictDeclaration``  a declared conflict of interest with an institution.
``RotationDecision``     the log of one suggestion: every candidate with score
                         and reasons, who was suggested, who was chosen, the
                         override reason and who decided. Append-only once
                         decided.

Services live in ``assignments.rotation``; nothing here holds rules.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

USER = settings.AUTH_USER_MODEL

__all__ = ["RotationPool", "RotationMember", "PoolMember", "ConflictDeclaration", "RotationDecision",
           "ReviewChainPolicy", "ReviewChain", "ReviewStep", "ROTATION_KIND_CHOICES", "ROTATION_KINDS"]

# Kinds that rotate (subset of WorkAssignment kinds).
OVERSIGHT_ICT = "oversight_ict"
DOCUMENT_ANALYSIS = "document_analysis"
SSQ_REVIEW = "ssq_review"
VISIT_PANEL = "visit_panel"
PHYSICAL_VERIFICATION = "physical_verification"
ROTATION_KIND_CHOICES = [
    (DOCUMENT_ANALYSIS, "Document analysis"),
    (SSQ_REVIEW, "SSQ review"),
    (VISIT_PANEL, "Visit panel"),
    (PHYSICAL_VERIFICATION, "Physical verification"),
    (OVERSIGHT_ICT, "ICT supervision (oversight)"),
]
ROTATION_KINDS = [k for k, _ in ROTATION_KIND_CHOICES]
VISIT_KINDS = (VISIT_PANEL, PHYSICAL_VERIFICATION)

pool_code_validator = RegexValidator(r"^[a-z0-9_\-]+(:[a-z0-9_\-]+)?$",
                                     "Use lower-case letters, digits, - and _, optionally ':<department code>'.")


class RotationPoolQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class RotationPool(models.Model):
    code = models.CharField(max_length=80, unique=True, validators=[pool_code_validator],
                            help_text="e.g. document_analysis:dpap, ssq_review:dpap, oversight_ict.")
    kind = models.CharField(max_length=32, choices=ROTATION_KIND_CHOICES, db_index=True)
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="rotation_pools")
    division = models.ForeignKey("org.Division", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                 help_text="Optional: only this division takes turns.")
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    eligible_roles = models.JSONField(default=list, blank=True,
                                      help_text="Role codes that may be members (blank = any staff role).")
    min_rank = models.PositiveSmallIntegerField(null=True, blank=True,
                                                help_text="Lowest role rank that may be a member (blank = any).")
    rules = models.JSONField(default=dict, blank=True,
                             help_text="Per-pool overrides of the qa.rotation.* settings (cap, weights, toggles).")
    auto_assign = models.BooleanField(default=False, help_text="Assign the suggestion at once (director may change).")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = RotationPoolQuerySet.as_manager()

    class Meta:
        ordering = ["department__name", "kind", "code"]
        permissions = [
            ("override_rotation_constraints", "QA: can assign someone a rotation rule blocks (reason, audited)"),
            ("manage_rotation_pools", "QA: can edit rotation pools of any department"),
            ("view_rotation_board", "QA: can see the rotation board of any department"),
        ]

    def __str__(self):
        return self.name or self.code

    def active_members(self):
        return (self.members.filter(is_active=True, user__is_active=True)
                .select_related("user", "user__role", "user__department").order_by("user__first_name", "user_id"))


class RotationMember(models.Model):
    pool = models.ForeignKey(RotationPool, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="rotation_memberships")
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1,
                                 help_text="Share of the work: 1 = normal, 0.5 = half (part-time), 2 = double.")
    max_concurrent = models.PositiveSmallIntegerField(null=True, blank=True,
                                                      help_text="Own cap (blank = the pool's cap).")
    disciplines = models.ManyToManyField("institutions.Discipline", blank=True, related_name="+")
    is_active = models.BooleanField(default=True)
    paused_until = models.DateField(null=True, blank=True)
    pause_reason = models.CharField(max_length=120, blank=True)
    joined_at = models.DateTimeField(default=timezone.now)
    last_assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pool", "user__first_name", "user_id"]
        constraints = [models.UniqueConstraint(fields=["pool", "user"], name="rotation_member_once_per_pool")]

    def __str__(self):
        return f"{self.user} in {self.pool.code}"

    @property
    def is_paused(self):
        return bool(self.paused_until and self.paused_until >= timezone.localdate())


PoolMember = RotationMember


class ConflictDeclarationQuerySet(models.QuerySet):
    def live(self, now=None):
        now = now or timezone.now()
        return self.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


class ConflictDeclaration(models.Model):
    SOURCE_CHOICES = [("self", "Declared by the person"), ("visit_form_section_b", "Visit form, section B"),
                      ("hr", "HR record")]
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="conflict_declarations")
    institution = models.ForeignKey("institutions.Institution", on_delete=models.CASCADE, related_name="+")
    reason = models.CharField(max_length=255)
    declared_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=24, choices=SOURCE_CHOICES, default="self")

    objects = ConflictDeclarationQuerySet.as_manager()

    class Meta:
        ordering = ["-declared_at"]
        indexes = [models.Index(fields=["user", "institution"], name="asg_coi_user_inst")]

    def __str__(self):
        return f"COI {self.user_id} · inst {self.institution_id}"


class RotationDecision(models.Model):
    """One suggestion and its outcome. Created by ``rotation.suggest``;
    ``rotation.record_decision`` fills the outcome once, after which the row
    is frozen."""

    pool = models.ForeignKey(RotationPool, on_delete=models.PROTECT, related_name="decisions")
    kind = models.CharField(max_length=32, choices=ROTATION_KIND_CHOICES, db_index=True)
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    subject_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    subject_id = models.CharField(max_length=64, blank=True)
    subject = GenericForeignKey("subject_ct", "subject_id")
    subject_label = models.CharField(max_length=200, blank=True)
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    window_start = models.DateField(null=True, blank=True)
    window_end = models.DateField(null=True, blank=True)
    candidates = models.JSONField(default=list, blank=True)
    suggested_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    chosen_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    overridden = models.BooleanField(default=False)
    constraint_overridden = models.BooleanField(default=False,
                                                help_text="The chosen person was blocked by a rule (ES/permission).")
    override_reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    parameters = models.JSONField(default=dict, blank=True)
    work_assignment = models.ForeignKey("assignments.WorkAssignment", null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="rotation_decisions")

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["pool", "decided_at"], name="asg_rotdec_pool"),
                   models.Index(fields=["chosen_user", "kind", "decided_at"], name="asg_rotdec_chosen")]

    def __str__(self):
        return f"Rotation {self.pool.code} #{self.pk}"

    @property
    def is_decided(self):
        return self.decided_at is not None

    def save(self, *args, **kwargs):
        if self.pk is not None:
            prev = type(self).objects.filter(pk=self.pk).values("decided_at").first()
            if prev and prev["decided_at"] is not None:
                # frozen once decided; only the WorkAssignment link may be added
                fields = kwargs.get("update_fields")
                if not fields or set(fields) - {"work_assignment"}:
                    raise ValueError("A decided rotation decision cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Rotation decisions are never deleted.")


from .models_chain import ReviewChain, ReviewChainPolicy, ReviewStep  # noqa: E402,F401  (X30)
