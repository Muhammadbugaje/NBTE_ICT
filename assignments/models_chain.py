"""Review chains (v2.1 X30, spec_v21 G6.c): two- or three-step verification.

``ReviewChainPolicy``  the steps for one kind of work, e.g.
                       ``[{"key": "work", "actor": "assignee"},
                          {"key": "director", "actor": "director_of_department"},
                          {"key": "second", "actor": "approver_set", "when": "amount > 5000000",
                           "set": {"mode": "roles", "roles": ["executive-secretary"]}}]``
``ReviewChain``        one subject going through those steps.
``ReviewStep``         append-only record of each action (submit, approve,
                       return, reject, reopen) with the actor and remark.

Rules live in ``assignments.chain``.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

USER = settings.AUTH_USER_MODEL


class ReviewChainPolicy(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    steps = models.JSONField(default=list, help_text="Ordered steps: key, actor, optional when / set / label.")
    return_targets = models.JSONField(default=dict, blank=True,
                                      help_text='Which earlier steps a step may send back to, e.g. {"director": ["work"]}.')
    is_active = models.BooleanField(default=True)
    description = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "review chain policies"

    def __str__(self):
        return self.name or self.code

    def step(self, key):
        return next((s for s in self.steps or [] if s.get("key") == key), None)

    @property
    def step_keys(self):
        return [s.get("key") for s in self.steps or []]


class ReviewChain(models.Model):
    OPEN, APPROVED, REJECTED, WITHDRAWN = "open", "approved", "rejected", "withdrawn"
    STATE_CHOICES = [(OPEN, "Open"), (APPROVED, "Approved"), (REJECTED, "Rejected"), (WITHDRAWN, "Withdrawn")]

    policy = models.ForeignKey(ReviewChainPolicy, on_delete=models.PROTECT, related_name="chains")
    subject_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    subject_id = models.CharField(max_length=64, blank=True)
    subject = GenericForeignKey("subject_ct", "subject_id")
    subject_label = models.CharField(max_length=200, blank=True)
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    assignee = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                 help_text="Who does the first (work) step.")
    context = models.JSONField(default=dict, blank=True,
                               help_text="Values the `when` conditions read, e.g. {\"amount\": 6500000}.")
    state = models.CharField(max_length=12, choices=STATE_CHOICES, default=OPEN, db_index=True)
    current_step = models.CharField(max_length=24, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["subject_ct", "subject_id"], name="asg_chain_subject"),
                   models.Index(fields=["department", "state"], name="asg_chain_dept")]

    def __str__(self):
        return f"{self.policy.code} chain #{self.pk} ({self.state})"

    @property
    def is_open(self):
        return self.state == self.OPEN


class ReviewStep(models.Model):
    SUBMIT, APPROVE, RETURN, REJECT, REOPEN = "submit", "approve", "return", "reject", "reopen"
    ACTION_CHOICES = [(SUBMIT, "Submitted"), (APPROVE, "Approved"), (RETURN, "Sent back"), (REJECT, "Rejected"),
                      (REOPEN, "Reopened")]

    chain = models.ForeignKey(ReviewChain, on_delete=models.CASCADE, related_name="steps")
    step_key = models.CharField(max_length=24)
    actor = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    to_step = models.CharField(max_length=24, blank=True)
    remark = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "pk"]

    def __str__(self):
        return f"{self.chain_id}:{self.step_key} {self.action} by {self.actor_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Review steps are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Review steps are append-only.")
