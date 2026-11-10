"""Visit invitations (SOP-03, after the ES approves the visit).

``VisitInvitation`` — one row per (case, professional body) and per (case,
external resource person): the invitation letter (an ``IssuedDocument``), how
it was sent, whether the invitee confirmed (portal, the tokenised e-mail link,
or recorded by NBTE after a call), and the reminders already sent.

``VisitInvitationContact`` — NBTE's contact log for one invitation (calls,
e-mails, visits by the desk officer) plus the system's own entries (sent,
resent, reminder, amended letter) so the history reads in one place.

Imported at the bottom of ``accreditation.models`` so Django registers them.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from qa_core.scoping import InstitutionScopedManager, InstitutionScopedModelMixin, UnscopedInstitutionManager

USER = settings.AUTH_USER_MODEL


class VisitInvitation(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "case__institution"

    BODY, RESOURCE_PERSON = "body", "resource_person"
    KIND_CHOICES = [(BODY, "Professional body"), (RESOURCE_PERSON, "Resource person")]

    QUEUED, SENT, FAILED, NO_CONTACT, ACKNOWLEDGED, DECLINED, WITHDRAWN = (
        "queued", "sent", "failed", "no_contact", "acknowledged", "declined", "withdrawn")
    STATUS_CHOICES = [(QUEUED, "Queued"), (SENT, "Sent, awaiting reply"), (FAILED, "E-mail failed"),
                      (NO_CONTACT, "No e-mail on file"), (ACKNOWLEDGED, "Confirmed"), (DECLINED, "Declined"),
                      (WITHDRAWN, "Withdrawn")]
    OPEN = (QUEUED, SENT, FAILED, NO_CONTACT)  # still waiting for an answer

    VIA_CHOICES = [("portal", "Portal"), ("link", "E-mail link"), ("phone", "Phone call"), ("email", "E-mail reply"),
                   ("in_person", "In person"), ("letter", "Letter"), ("other", "Other")]

    case = models.ForeignKey("accreditation.QACase", on_delete=models.CASCADE, related_name="visit_invitations")
    visit = models.ForeignKey("accreditation.VisitDetail", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="invitations")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    body = models.ForeignKey("institutions.ProfessionalBody", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="visit_invitations")
    resource_person = models.ForeignKey("inspection.ResourcePerson", null=True, blank=True,
                                        on_delete=models.PROTECT, related_name="visit_invitations")
    name = models.CharField(max_length=200, help_text="Invitee name when the invitation was made.")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=60, blank=True)
    programmes = models.JSONField(default=list, blank=True)

    letter = models.ForeignKey("documents.IssuedDocument", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    letter_version = models.PositiveIntegerField(default=0, help_text="1 = first letter; 2+ = amended / reissued.")
    visit_start = models.DateField(null=True, blank=True, help_text="Dates printed on the current letter.")
    visit_end = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    token = models.CharField(max_length=64, unique=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_count = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    portal_notified_at = models.DateTimeField(null=True, blank=True)
    portal_recipient_count = models.PositiveIntegerField(default=0)

    responded_at = models.DateTimeField(null=True, blank=True)
    responded_via = models.CharField(max_length=12, choices=VIA_CHOICES, blank=True)
    responded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     help_text="Portal user who answered, or the NBTE officer who recorded it.")
    responder_name = models.CharField(max_length=150, blank=True, help_text="Who answered on the invitee's side.")
    response_note = models.TextField(blank=True)

    reminders_sent = models.JSONField(default=list, blank=True,
                                      help_text="Reminder thresholds (days before the visit) already sent.")
    reminder_requested_at = models.DateTimeField(
        null=True, blank=True,
        help_text="The invitee asked NBTE to send a reminder before the visit (portal/link page).")
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["case", "kind", "name"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["case", "body"], condition=models.Q(body__isnull=False),
                                    name="visit_invitation_body_once"),
            models.UniqueConstraint(fields=["case", "resource_person"],
                                    condition=models.Q(resource_person__isnull=False),
                                    name="visit_invitation_rp_once"),
            models.CheckConstraint(condition=(models.Q(body__isnull=False, resource_person__isnull=True)
                                              | models.Q(body__isnull=True, resource_person__isnull=False)),
                                   name="visit_invitation_one_invitee"),
        ]

    def __str__(self):
        return f"{self.case.reference} → {self.name}"

    @property
    def invitee(self):
        return self.body or self.resource_person

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def is_amended(self):
        return self.letter_version > 1

    @property
    def token_expired(self):
        return bool(self.token_expires_at and timezone.now() > self.token_expires_at)


class VisitInvitationContact(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "invitation__case__institution"

    CHANNEL_CHOICES = [("phone", "Phone call"), ("email", "E-mail"), ("sms", "SMS / WhatsApp"),
                       ("in_person", "In person"), ("letter", "Letter / dispatch"), ("system", "System")]
    MANUAL_CHANNELS = ("phone", "email", "sms", "in_person", "letter")
    OUTCOME_CHOICES = [("reached", "Reached, will reply"), ("confirmed", "Confirmed attendance"),
                       ("declined", "Declined"), ("no_answer", "No answer"), ("left_message", "Left a message"),
                       ("wrong_contact", "Wrong number / address"), ("other", "Other"),
                       # system entries
                       ("sent", "Letter sent"), ("resent", "Letter resent"), ("amended", "Amended letter sent"),
                       ("reminder", "Reminder sent"), ("failed", "Sending failed"),
                       ("regenerated", "Letter regenerated"), ("withdrawn", "Invitation withdrawn")]
    MANUAL_OUTCOMES = ("reached", "confirmed", "declined", "no_answer", "left_message", "wrong_contact", "other")

    invitation = models.ForeignKey(VisitInvitation, on_delete=models.CASCADE, related_name="contacts")
    channel = models.CharField(max_length=12, choices=CHANNEL_CHOICES)
    outcome = models.CharField(max_length=16, choices=OUTCOME_CHOICES)
    note = models.TextField(blank=True)
    contact_name = models.CharField(max_length=150, blank=True, help_text="Who was spoken to.")
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(default=timezone.now)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.get_channel_display()} · {self.get_outcome_display()}"

    @property
    def is_manual(self):
        return self.channel != "system"
