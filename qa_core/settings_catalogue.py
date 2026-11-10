"""The Master Plan §14 settings catalogue, Phases 1 and 2.

Every row is created by ``seed_startup`` through the existing ``config``
mechanism (get-or-create by key; an admin's edits are never overwritten).

Phase 1 seeds (PHASES.md, Phase 1 "Settings added"):
* ``qa.settings.*``, ``qa.credentials.*``, ``qa.impersonation.*``, ``qa.portal.*``
* ``qa.stage.default_sla_hours`` and ``qa.stage.reminder_at_percent``
* the v1.1 Eligibility and qualifications block (7 keys)
* the v1.1 §20 interaction keys (2)
* the v1.1 payment-state vocabulary keys, including the locked
  ``qa.payment.provider_confirmation_is_evidence_only``
* the ``qa.fee.*`` fee formulas — pulled forward from Phase 2 because the
  Phase 1 definition of done edits ``qa.fee.programme_processing`` under
  dual control. Nothing reads them until payments ship.

Phase 2 adds (PHASES.md, Phase 2 "Settings added"): every remaining
``qa.payment.*`` key, ``qa.ownership_change.hold_open_cases``,
``qa.institution.admin_min_grade_level`` and ``qa.documents.max_upload_mb``.
``qa.portal.self_registration_enabled`` already shipped in Phase 1.

Columns: key, value, type, group, description, editable_by, dual_control.
"""
import json

from config.models import LOCKED_PERMISSION

EDIT = "qa_core.edit_qa_settings"

G_FEES = "QA · Fees and payments"
G_WORKFLOW = "QA · Workflow and SLA"
G_ELIGIBILITY = "QA · Eligibility and qualifications"
G_ACCESS = "QA · Credentials, portal and impersonation"
G_INTERACTION = "QA · Interaction (§20)"
G_SETTINGS = "QA · Settings"
G_INSTITUTIONS = "QA · Institutions and documents"
G_COMPLAINTS = "QA · Complaints"  # Phase 7
G_BACKUPS = "Backups"  # Phase 8 (Master Plan §22): system-wide, edited on Settings → Backups
G_DECISIONS = "QA · Decisions and letters"  # Phase 8 (Master Plan Phase 6)
G_CAPACITY = "QA · Capacity engine (Spec §7)"  # Phase 8 (Master Plan Phase 6)
G_ROTATION = "QA · Rotation and review chains"  # v2.1 Phase P (X27–X30)
G_DOCUMENTS = "QA · Generated documents and verification"  # v2.1 Phase F (X22–X26)
G_HONORARIA = "QA · Honoraria and visit costs"  # v2.1 Phase H (X32–X39)
G_REFUNDS = "QA · Fees & refunds"  # v2.5: cancellation and refund policy

GROUP_ORDER = [G_FEES, G_REFUNDS, G_HONORARIA, G_INSTITUTIONS, G_WORKFLOW, G_DECISIONS, G_DOCUMENTS, G_CAPACITY, G_ROTATION, G_COMPLAINTS, G_ELIGIBILITY, G_ACCESS,
               G_INTERACTION, G_SETTINGS, G_BACKUPS, "QA · Privacy and access"]

# Phase 8: every fee formula is evaluated with these variables (documented on
# Settings → Fees). ``payments.services.fee_variables`` always supplies all of them.
FEE_VARIABLES = [
    ("ownership", "Ownership code of the institution: 'federal', 'state' or 'private'."),
    ("institution_type", "Institution type code, e.g. 'polytechnic', 'monotechnic'."),
    ("programmes", "Number of programmes the bill is for."),
    ("new_programmes", "How many of those programmes are new (proposed)."),
    ("streams", "Total streams asked for across those programmes (each programme counts at least 1)."),
    ("max_streams", "The most streams asked for in any one of those programmes."),
    ("falsified", "X40: how many catalogue items the visit found falsified (declared but not found)."),
]
FEE_VARIABLE_NAMES = [name for name, _help in FEE_VARIABLES]


def _formula(expr, vars_, returns="decimal"):
    return json.dumps({"expr": expr, "vars": vars_, "returns": returns})


SETTINGS = [
    # --- Fees and payments -------------------------------------------------
    ("qa.fee.institutional_processing",
     _formula("4000000 if ownership == 'private' else 2000000", FEE_VARIABLE_NAMES),
     "formula", G_FEES,
     "Fallback SOP-01 institutional processing fee (₦) for a category with no qa.fee.institution.<category>."
     "<public|private> setting. Annex A: first figure Private, second Public (Q-04).",
     EDIT, True),
    ("qa.fee.institutional_processing_v3",
     _formula("0", []),
     "formula", G_FEES,
     "Fallback for the v3 SOP-01 bill. FeeComponent rows provide each line; keep this at 0. Missing components block "
     "billing rather than falling back to this formula.", EDIT, True),
    ("qa.fee.programme_processing",
     _formula("programmes * (2000000 if ownership == 'private' else 1000000)"
              " + max(streams - programmes, 0) * cfg('qa.fee.programme.per_additional_stream')", FEE_VARIABLE_NAMES),
     "formula", G_FEES,
     "Programme processing fee (₦) for a number of programmes, by ownership, plus any extra streams beyond one "
     "per programme (qa.fee.programme.per_additional_stream, ₦0 until NBTE sets it).",
     EDIT, True),
    ("qa.fee.qa_request_processing",
     _formula("cfg('qa.fee.qa_request.base') + programmes * cfg('qa.fee.qa_request.per_programme')"
              " + max(streams - programmes, 0) * cfg('qa.fee.qa_request.per_additional_stream')",
              FEE_VARIABLE_NAMES),
     "formula", G_FEES,
     "SOP-02 processing fee (₦): base + programmes × per-programme + extra streams × per-additional-stream (D15).",
     EDIT, True),
    ("qa.fee.qa_request_processing.confirmed", "false", "bool", G_FEES,
     "While false, SOP-02 bills cannot be issued (D15 unconfirmed; Q-04).",
     EDIT, True),
    ("qa.fee.qa_request.base", "0", "decimal", G_FEES,
     "Input to the SOP-02 formula: the base amount (₦). Unconfirmed.",
     EDIT, True),
    ("qa.fee.qa_request.per_programme", "0", "decimal", G_FEES,
     "Input to the SOP-02 formula: the amount per programme (₦). Unconfirmed.",
     EDIT, True),
    ("qa.payment.finance_department", "finance", "str", G_FEES,
     "Code of the department whose director gives the final confirm/reject on every payment "
     "(the Director of Finance). Phase B: replaces qa.payment.final_approver_role.",
     EDIT, True),
    ("qa.payment.receipt_required_for_provider_payments", "false", "bool", G_FEES,
     "A provider-reported payment that the gateway check did NOT verify needs an uploaded receipt before the "
     "Director can confirm it (X21: gateway-verified payments never need one). New installs: off (D-7).",
     EDIT, True),
    ("qa.payment.provider_confirmation_is_evidence_only", "true", "bool", G_FEES,
     "Fixed by policy: a gateway report is evidence, never confirmation. Changing it needs a code release.",
     LOCKED_PERMISSION, False),

    # --- Workflow and SLA ---------------------------------------------------
    ("qa.stage.default_sla_hours", "72", "int", G_WORKFLOW,
     "Fallback SLA (hours) for workflow stages that have none.",
     EDIT, False),
    ("qa.stage.reminder_at_percent", "[50, 90]", "json", G_WORKFLOW,
     "Send stage reminders at these percentages of the SLA.",
     EDIT, False),

    # --- Eligibility and qualifications (v1.1, §9) ---------------------------
    ("qa.institution.admin_required_department", "any", "str", G_ELIGIBILITY,
     "Department an Institutional Admin nominee must belong to, or 'any'.",
     EDIT, False),
    ("qa.institution.chief_executive_required_position", "true", "bool", G_ELIGIBILITY,
     "The Chief Executive nominee must match the institution's head-of-institution contact.",
     EDIT, False),
    ("qa.resource_person.min_rank", "director", "str", G_ELIGIBILITY,
     "Minimum accounts.Role (by rank) for NBTE staff serving as resource persons.",
     EDIT, False),
    ("qa.resource_person.required_designations",
     '["director", "deputy-director", "chief-lecturer", "principal-lecturer"]', "json", G_ELIGIBILITY,
     "Allowed designation codes (staff) and rank codes (external resource persons).",
     EDIT, False),
    ("qa.resource_person.max_active_engagements", "2", "int", G_ELIGIBILITY,
     "Cap on simultaneous live mappings per person, all for one programme (R-11).",
     EDIT, False),
    ("qa.resource_person.max_open_ssq_per_officer", "3", "int", G_ELIGIBILITY,
     "Open SSQ reviews per NBTE reviewing officer (R-01).",
     EDIT, False),
    ("qa.resource_person.max_visits_per_year", "6", "int", G_ELIGIBILITY,
     "Annual visit cap per person in the availability picker.",
     EDIT, False),

    # --- Credentials, portal, impersonation ---------------------------------
    ("qa.credentials.resend_cooldown_minutes", "30", "int", G_ACCESS,
     "Minimum minutes between two set-password emails to the same account.",
     EDIT, False),
    ("qa.credentials.link_valid_hours", "72", "int", G_ACCESS,
     "How long a portal set-password link stays valid.",
     EDIT, False),
    ("qa.impersonation.max_minutes", "30", "int", G_ACCESS,
     "Longest impersonation session, in minutes.",
     EDIT, True),
    ("qa.impersonation.allow_write", "false", "bool", G_ACCESS,
     "Allow write-mode impersonation (superusers only). Off by default (Q-13).",
     EDIT, True),
    ("qa.portal.self_registration_enabled", "true", "bool", G_ACCESS,
     "Proposed institutions may self-register on the portal (opens with Phase 2).",
     EDIT, False),
    ("qa.portal.maintenance_message", "", "str", G_ACCESS,
     "Banner text shown on every portal page while not empty.",
     EDIT, False),
    # Phase 0 (X8 slice, S-18): the public payment verify page /verify/<token>/.
    ("qa.verify.rate_limit_per_hour", "60", "int", G_ACCESS,
     "Public verification (Scan, /v/ and the old /verify/ page): checks allowed per IP address per hour (then 429).",
     EDIT, False),

    # --- Interaction (§20) ---------------------------------------------------
    ("qa.ui.max_show_all_rows", "500", "int", G_INTERACTION,
     "Above this many rows, 'Show all' is disabled (the server enforces it too).",
     EDIT, False),
    ("qa.ui.request_timeout_seconds", "15", "int", G_INTERACTION,
     "Client-side network timeout before the retry/error message.",
     EDIT, False),

    # --- Settings settings ----------------------------------------------------
    ("qa.settings.pending_action_expiry_hours", "72", "int", G_SETTINGS,
     "Pending confirmations expire after this many hours.",
     EDIT, True),
]

# ---------------------------------------------------------------------------
# Phase 2 (Master Plan §10, §14)
# ---------------------------------------------------------------------------
def _online_default():
    """Offer configured online providers in development and demo installs.
    Production installs seed providers OFF until Finance clears them (Q-05);
    missing credentials prevent a provider from being offered in any setting."""
    from django.conf import settings

    return "true" if (settings.DEBUG or getattr(settings, "QA_PAYMENTS_SIMULATE", False)) else "false"


PAYMENTS_PERM = "payments.import_bank_statement"

SETTINGS_PHASE2 = [
    ("qa.payment.remita.enabled", _online_default, "bool", G_FEES,
     "Offer Remita (RRR on the slip, online payment). Needs REMITA_* in .env.", EDIT, True),
    ("qa.payment.paystack.enabled", _online_default, "bool", G_FEES,
     "Offer Paystack checkout. Production use subject to Finance clearance (Q-05). Needs PAYSTACK_* in .env.",
     EDIT, True),
    ("qa.payment.bank_transfer.enabled", "true", "bool", G_FEES,
     "Offer bank transfer + 'I have paid'. Forced back on automatically if every other method is off.",
     EDIT, True),
    ("qa.payment.offline.enabled", "true", "bool", G_FEES,
     "Allow fee waivers (Finance officer proposes, Director of Finance confirms).", EDIT, True),
    ("qa.payment.method_order", '["remita", "paystack", "bank_transfer"]', "json", G_FEES,
     "Order in which payment methods are shown to institutions.", EDIT, False),
    ("qa.payment.paystack.fee_bearer", "institution", "str", G_FEES,
     "Who bears the Paystack gateway fee: 'institution' (added at checkout) or 'nbte'.", EDIT, True),
    ("qa.payment.slip_validity_days", "30", "int", G_FEES,
     "Days a payment slip stays valid; after that the institution regenerates it (same reference).",
     EDIT, False),
    ("qa.payment.due_days", "30", "int", G_FEES,
     "Days from issue to the due date printed on slips (unless the fee schedule says otherwise).",
     EDIT, False),
    ("qa.payment.finance_confirmation_window_hours", "24", "int", G_FEES,
     "Hours the Director of Finance has to decide before 'Payment Pending Confirmation' (R-07).",
     EDIT, False),
    ("qa.payment.allow_partial", "false", "bool", G_FEES,
     "Let institutions pay less than the balance online. Workflows still wait for a zero balance.",
     EDIT, True),
    ("qa.payment.apply_credit_automatically", "false", "bool", G_FEES,
     "Apply held credit to the institution's next bill without Finance review.", EDIT, True),
    ("qa.payment.provider_poll_minutes", "15", "int", G_FEES,
     "How often online attempts pending for 10+ minutes are re-checked with the provider.", EDIT, False),
    ("qa.payment.proof_extensions", '["pdf", "jpg", "jpeg", "png"]', "json", G_FEES,
     "File types accepted as proof of payment.", EDIT, False),
    ("qa.payment.proof_max_mb", "5", "int", G_FEES,
     "Largest proof-of-payment upload, in MB.", EDIT, False),
    ("qa.payment.statement_column_map",
     json.dumps({"default": {"date": "Value Date", "narration": "Narration", "amount": "Credit",
                             "date_format": "%d/%m/%Y"}}),
     "json", G_FEES,
     "CSV column names per bank (key = bank name or 'default'): date, narration, amount, date_format.",
     PAYMENTS_PERM, False),
    ("qa.ownership_change.hold_open_cases", "true", "bool", G_INSTITUTIONS,
     "When ownership changes, hold the institution's open application until the new documents are in.",
     EDIT, False),
    ("qa.institution.admin_min_grade_level", "12", "int", G_INSTITUTIONS,
     "Lowest grade level an institution may nominate as its Institutional Administrator (R-03).",
     EDIT, False),
    ("qa.institution.proposed_department", "ntird", "str", G_INSTITUTIONS,
     "Department (QA code) that handles proposed institutions until they are approved.", EDIT, False),
    ("qa.documents.max_upload_mb", "10", "int", G_INSTITUTIONS,
     "Largest statutory-document upload, in MB (a document kind may set a lower limit).", EDIT, False),
]

G_QA_REQUESTS = "QA · Requests and SSQ"

SETTINGS_PHASE3 = [
    ("qa.programme.reassessment_window_months", "6", "int", G_QA_REQUESTS,
     "How many months before expiry a programme may apply for reaccreditation (Requirement 4).", EDIT, False),
    ("qa.programme.expiry_warning_days", json.dumps([180, 90, 30]), "json", G_QA_REQUESTS,
     "Days before expiry at which institutions are reminded.", EDIT, False),
    ("qa.portal.staff_sign_in_enabled", "true", "bool", G_ACCESS,
     "NBTE staff may sign in to the QA portal and see the Staff Portal Dashboard (Phase 3). When false, staff "
     "are sent to the internal system as in Phase 1.", EDIT, False),
    ("qa.ssq.submit_roles", json.dumps(["inst-chief-executive"]), "json", G_QA_REQUESTS,
     "Institution roles that may submit an SSQ to NBTE (v2.1 X12: the Chief Executive only; the academic "
     "department completes sections only).",
     EDIT, False),
    ("qa.ssq.autosave_delay_ms", "900", "int", G_QA_REQUESTS,
     "Pause after typing before an SSQ field is saved automatically (milliseconds).", EDIT, False),
    ("qa.query.default_due_days", "14", "int", G_QA_REQUESTS,
     "Default number of days an institution has to answer a query.", EDIT, False),
    ("qa.qa_request.max_open_per_type", "1", "int", G_QA_REQUESTS,
     "How many open requests of the same QA type an institution may have at once.", EDIT, False),
]

# Phase 4 — SOP-03 assignment rules, workload checks and the visit (§14)
SETTINGS_PHASE4 = [
    ("qa.assignment.second_ict_officer_threshold", "5", "int", G_WORKFLOW,
     "A case with this many programmes or more needs two tracking ICT officers (R-04).", EDIT, False),
    ("qa.assignment.max_programmes_per_verifier", "5", "int", G_WORKFLOW,
     "Most programmes one verification officer may hold (R-12).", EDIT, False),
    ("qa.assignment.verifier_same_discipline", "true", "bool", G_WORKFLOW,
     "Verification officers must share the programme's discipline (R-12).", EDIT, False),
    ("qa.workload.reviewer_cannot_verify", "true", "bool", G_WORKFLOW,
     "A reviewer of a case cannot also be on its visit team (R-02).", EDIT, True),
    ("qa.staff.ssq_grade_min", "7", "int", G_ELIGIBILITY,
     "Lowest grade level that may hold officer-level QA work (R-20).", EDIT, False),
    ("qa.staff.ssq_grade_max", "12", "int", G_ELIGIBILITY,
     "Highest grade level that may hold officer-level QA work (R-20).", EDIT, False),
    ("qa.visit.team_lead_roles", '["director", "deputy-director", "zonal-director"]', "json", G_WORKFLOW,
     "Roles eligible to lead a QA visit (B13).", EDIT, False),
    ("qa.cost.rates_confirmed", "false", "bool", G_FEES,
     "Finance has confirmed the rate table (cost sheets show 'rates not confirmed' until then).", EDIT, True),
]

# ---------------------------------------------------------------------------
# Settings (§14)
# ---------------------------------------------------------------------------
SETTINGS_PHASE5 = [
    ("qa.matching.travel_buffer_days", "2", "int", G_WORKFLOW,
     "Days around a visit in which the same person cannot be booked again (double-booking buffer).", EDIT, False),
    ("qa.matching.same_institution_cooldown_months", "24", "int", G_WORKFLOW,
     "Months before the same assessor may visit the same institution again.", EDIT, False),
    ("qa.matching.allow_secondary_discipline", "false", "bool", G_WORKFLOW,
     "Let people be matched on a secondary discipline, not only their primary one.", EDIT, False),
    ("qa.matching.rank_score",
     _formula("visits_this_year * 10 - days_since_last / 30 - rating", ["visits_this_year", "days_since_last",
                                                                         "rating"]),
     "formula", G_WORKFLOW,
     "Ranking of available people (lower first): fewest visits this year, longest since last served, rating. "
     "Advisory only.", EDIT, False),
]


SETTINGS_PHASE6 = [
    ("qa.portal.registration_contact_email", "", "str", G_ACCESS,
     "E-mail shown when self-registration is off (blank = the site's public contact e-mail).", EDIT, False),
    ("qa.portal.registration_contact_phone", "", "str", G_ACCESS,
     "Phone shown when self-registration is off (blank = the site's public contact phone).", EDIT, False),
]

# Phase 7 — complaints (Master Plan §14; PHASES.md Phase 7 "Settings added")
ROUTING_PERM = "complaints.manage_routing_rules"
SETTINGS_PHASE7 = [
    ("qa.complaints.fallback_department", "ict", "str", G_COMPLAINTS,
     "Department code of the general triage queue: complaints no routing rule matches go here.", ROUTING_PERM, False),
    ("qa.complaints.sla_poll_minutes", "5", "int", G_COMPLAINTS,
     "How often the SLA sweep looks for reminders, breaches and escalations (minutes).", EDIT, False),
    ("qa.complaints.auto_close_days", "5", "int", G_COMPLAINTS,
     "Days after a complaint is resolved before it closes by itself (TR-09).", EDIT, False),
    ("qa.complaints.reopen_window_days", "14", "int", G_COMPLAINTS,
     "Days after resolution in which the raiser may reopen a complaint (TR-10).", EDIT, False),
    ("qa.complaints.show_officer_name_to_raiser", "false", "bool", G_COMPLAINTS,
     "Show the handling officer's name to the raiser (otherwise only the department and stage).", EDIT, False),
    ("qa.complaints.sms_for_critical", "false", "bool", G_COMPLAINTS,
     "Also send an SMS when a critical complaint escalates (TR-14).", EDIT, False),
    # v2.1 X9: read-only watchers of a complaint (they see it, cannot act).
    ("qa.complaints.watch_overseeing_director", "true", "bool", G_COMPLAINTS,
     "The director of the department overseeing the institution can read its complaints (read-only).",
     EDIT, False),
    ("qa.complaints.watch_ict_supervisor", "true", "bool", G_COMPLAINTS,
     "The institution's ICT supervisor can read its complaints (read-only).", EDIT, False),
    ("qa.calendar.business_hours", '["08:00", "16:00"]', "json", G_WORKFLOW,
     "Opening hours used by business-hour SLAs (with attendance.work_week and the portal holiday calendar).",
     EDIT, False),
    ("qa.suspension.enabled_for", "[]", "json", G_WORKFLOW,
     'Record types a complaint may place on hold, e.g. ["payments.paymentobligation"] (Requirements 15–17). '
     "Empty = holds are off.", EDIT, True),
]

# Phase 8 — backups (Master Plan §22). The schedule is monthly by default; a
# daily Celery Beat entry checks whether the interval has passed.
BACKUP_PERM = "backups.manage_destinations"
SETTINGS_BACKUPS = [
    ("qa.backup.interval_days", "30", "int", G_BACKUPS,
     "Days between scheduled backups (30 = monthly). A daily check starts one when this has passed.",
     BACKUP_PERM, False),
    ("qa.backup.keep_count", "8", "int", G_BACKUPS,
     "Backups kept at each destination. Older ones are removed only after a newer backup succeeds there.",
     BACKUP_PERM, False),
    ("qa.backup.drill_every_n_runs", "4", "int", G_BACKUPS,
     "Run an automatic restore drill after this many successful backups.", BACKUP_PERM, False),
    ("qa.backup.encrypt_at_rest", "true", "bool", G_BACKUPS,
     "Encrypt backups before they leave the server (needs BACKUP_ENCRYPTION_KEY in .env).", BACKUP_PERM, False),
]

# ---------------------------------------------------------------------------
# Phase 8 — Master Plan Phase 6: decisions, letters, capacity, streams, expiry
# ---------------------------------------------------------------------------
CAPACITY_FORMULA_VARS = ["standard_hours", "total_size_factor", "contact_hours", "approved_streams", "other_load"]
LETTER_TEMPLATE = {
    "heading": "OUTCOME OF {qa_type_upper}",
    "salutation": "Dear Sir/Madam,",
    "subject": "{qa_type}: {programme} ({level})",
    "approve": [
        "Following the {qa_type_lower} exercise of {programme} at {institution} and the consideration of the "
        "report by the Committee on Quality Assurance, I am directed to inform you that the Executive Secretary has "
        "approved the programme for {approval_type} with effect from {commencement}.",
        "The approval is for {streams} stream(s) and remains valid until {expiry}. You may apply for "
        "re-accreditation from {reassessment_from}.",
    ],
    "deny": [
        "Following the {qa_type_lower} exercise of {programme} at {institution} and the consideration of the "
        "report by the Committee on Quality Assurance, I regret to inform you that the programme has not been "
        "approved.",
        "You may apply again when the weaknesses listed below have been corrected.",
    ],
    "weaknesses_intro": "The following weaknesses were noted and must be corrected:",
    "closing": "Please accept the assurances of the Executive Secretary.",
}

SETTINGS_PHASE8 = [
    # Streams and fees
    ("qa.streams.max_per_programme", "10", "int", G_INSTITUTIONS,
     "Most streams an institution may ask for in one programme (programme selection and QA requests).", EDIT,
     False),
    ("qa.fee.programme.per_additional_stream", "0", "decimal", G_FEES,
     "Programme processing fee: amount (₦) for each stream beyond one per programme. 0 = streams do not change "
     "the fee.", EDIT, True),
    ("qa.fee.qa_request.per_additional_stream", "0", "decimal", G_FEES,
     "SOP-02 fee: amount (₦) for each stream beyond one per programme. 0 = streams do not change the fee.",
     EDIT, True),
    # Decisions, approval and expiry
    ("qa.cqac.batch_approval_enabled", "true", "bool", G_DECISIONS,
     "The CQAC Chairman may approve recommendations in a batch, one programme category at a time (B23).",
     EDIT, False),
    ("qa.cqac.recommendation_mode", "shared", "str", G_DECISIONS,
     "Stage 14: shared lets one member submit for the CQAC panel; individual requires every assigned member to vote "
     "on every programme. Outcome is by full-panel majority; approval details by majority of approving votes. Ties "
     "return for review.",
     EDIT, False),
    ("qa.programme.renewal_reminder_days", json.dumps([180, 90, 30, 7]), "json", G_DECISIONS,
     "Days before expiry at which the institution and its handling department are reminded to renew "
     "(each reminder is sent once per expiry date).", EDIT, False),
    ("qa.letters.watermark", json.dumps({"text": "NBTE DUPLICATE", "opacity": 0.12, "angle": 45}), "json",
     G_DECISIONS, "Watermark on the duplicate outcome letter: text, opacity (0–1) and angle (degrees).", EDIT, False),
    ("qa.letters.signatory", json.dumps({"name": "", "title": "Executive Secretary"}), "json", G_DECISIONS,
     "Name and title printed under the signature line of outcome letters.", EDIT, True),
    ("qa.letters.template", json.dumps(LETTER_TEMPLATE), "json", G_DECISIONS,
     "Outcome letter wording. Placeholders: {institution} {programme} {level} {qa_type} {approval_type} "
     "{streams} {commencement} {expiry} {reassessment_from} {reference} {date}.", EDIT, False),
    # Capacity engine (Spec §7)
    ("qa.capacity.classroom.use_factor", "0.8", "decimal", G_CAPACITY,
     "Share of the standard hours a classroom can realistically be used.", EDIT, False),
    ("qa.capacity.hours_per_day", "8", "int", G_CAPACITY, "Teaching hours in a day (standard hours).", EDIT, False),
    ("qa.capacity.days_per_week", "5", "int", G_CAPACITY, "Teaching days in a week (standard hours).", EDIT, False),
    ("qa.capacity.weeks_per_semester", "15", "int", G_CAPACITY, "Teaching weeks in a semester (standard hours).",
     EDIT, False),
    ("qa.capacity.classroom.size_factors", json.dumps([[15, 20, 0.5], [30, 40, 1.0], [45, 60, 1.5], [60, None, 2.0]]),
     "json", G_CAPACITY,
     "Classroom size factors: [from seats, to seats, factor]. A room takes the factor of the highest band it "
     "reaches; below the first band it counts 0.", EDIT, False),
    ("qa.capacity.lab.use_factor", "0.75", "decimal", G_CAPACITY,
     "Share of the standard hours a laboratory or workshop can realistically be used.", EDIT, False),
    ("qa.capacity.lab.size_factors", json.dumps([[15, 0.5], [30, 1.0], [45, 1.5]]), "json", G_CAPACITY,
     "Laboratory size factors: [from places, factor]; the highest band reached applies.", EDIT, False),
    ("qa.capacity.additional_streams",
     _formula("((standard_hours * total_size_factor) - (contact_hours * approved_streams + other_load)) "
              "/ contact_hours", CAPACITY_FORMULA_VARS),
     "formula", G_CAPACITY,
     "Additional streams the rooms can carry (classrooms and laboratories). Every capacity decision uses it.",
     EDIT, True),
    ("qa.capacity.library.seat_ratio", "0.1", "decimal", G_CAPACITY,
     "Library seats needed per student (0.1 = one seat for every ten students).", EDIT, False),
    ("qa.capacity.library.material_ratio", "0.3333", "decimal", G_CAPACITY,
     "Programme titles needed per student (0.3333 = one title for every three students).", EDIT, False),
    ("qa.capacity.lecturer_max_hours_week", "18", "int", G_CAPACITY,
     "Most teaching hours a week for a full-time lecturer.", EDIT, False),
    ("qa.capacity.admin_post_hours_week", "8", "int", G_CAPACITY,
     "Teaching hours a week for a lecturer holding an administrative post.", EDIT, False),
    ("qa.capacity.service_staff_ratio", "20", "int", G_CAPACITY,
     "Students per technologist/technician (support staff needed = students ÷ this).", EDIT, False),
    ("qa.capacity.rule_of_thumb", _formula("4 + 2 * (streams - 1)", ["streams"]), "formula", G_CAPACITY,
     "Indicative minimum number of lecturers for a number of streams (a cross-check only).", EDIT, False),
    ("qa.capacity.default_contact_hours_week", "20", "int", G_CAPACITY,
     "Weekly contact hours of one stream, used to prefill an assessment (officers can change it).", EDIT, False),
    ("qa.capacity.default_room_seats", "40", "int", G_CAPACITY,
     "Seats assumed per classroom when the SSQ gives only the number of rooms (prefill only).", EDIT, False),
    ("qa.ratio.staff_student", _formula("students / counted_staff", ["students", "counted_staff"]), "formula",
     G_CAPACITY, "Staff–student ratio (B15): students per counted lecturer.", EDIT, False),
    ("qa.ratio.hnd_min_rank", "lecturer-i", "str", G_CAPACITY,
     "Lowest rank counted as HND teaching staff (B15).", EDIT, False),
]

SETTINGS = (SETTINGS + SETTINGS_PHASE2 + SETTINGS_PHASE3 + SETTINGS_PHASE4 + SETTINGS_PHASE5 + SETTINGS_PHASE6
            + SETTINGS_PHASE7 + SETTINGS_PHASE8 + SETTINGS_BACKUPS)

# Phase 8 — fair workload limits per rank (Master Plan §9; Settings → QA → Workload limits)
SETTINGS_PHASE8_WORKLOAD = [
    ("qa.workload.limits", '{"rows": [{"key": "default"}]}', "json", G_ELIGIBILITY,
     "Workload limits per rank (designation or role code): SSQ reviews at once and per year, visits per year, "
     "times at the same institution within N months, open tasks. Blank cells use the general settings. "
     "Edit in Settings → QA → Workload limits.", EDIT, False),
    ("qa.workload.staff_first", "true", "bool", G_ELIGIBILITY,
     "List eligible NBTE staff before resource persons in the availability finder.", EDIT, False),
    ("qa.workload.resource_persons_only_when_no_staff", "false", "bool", G_ELIGIBILITY,
     "Block resource persons while an eligible NBTE staff member is free for the same need.", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_PHASE8_WORKLOAD

# v2.1 Phase 0 (Master Plan §6.8, §6.12)
SETTINGS_V21_PHASE0 = [
    ("qa.onboarding.programme_clearance_department", "ntird", "str", G_INSTITUTIONS,
     "Department that clears the programmes of an onboarding application (Q2): a department code (default "
     "ntird) or 'category' for the institution category's routing department.", EDIT, False),
    ("qa.public.lga_rate_per_minute", "60", "int", G_ACCESS,
     "How many LGA look-ups per minute one network may make on the public registration page (X49).", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_V21_PHASE0

# v2.1 Phase P (spec_v21 G1.h): need-to-know privacy, access grants, portal milestones
G_PRIVACY = "QA · Privacy and access"
from qa_core.portal_journey import DEFAULT_MILESTONES as _PORTAL_MILESTONES  # noqa: E402
_KIND_SCOPES = json.dumps({
    "oversight_ict": ["full_journey"],
    "physical_verification": ["summary", "documents", "ssq", "visits"],
    "document_analysis": ["summary", "documents:subject"],
    "ssq_review": ["summary", "ssq:subject", "documents"],
    "visit_panel": ["summary", "ssq", "documents", "visits", "case"],
    "visit_lead": ["summary", "ssq", "documents", "visits", "case"],
    "complaint_task": ["summary", "complaints:subject"],
    "clearance": ["summary", "documents"],
    "programme_clearance": ["summary", "documents"],
    "case_desk": ["summary", "case", "ssq", "documents", "visits"],
    "case_supervision_audit": ["summary", "case", "visits"],
    "case_supervision_finance": ["summary", "case", "payments"],
    "payment_review": ["summary", "payments"],
    "honoraria_schedule": ["summary", "case"],
    "delegate_act_for": ["summary", "documents", "ssq"],
    "stage_task": ["summary", "case", "ssq", "documents", "visits"],
})
SETTINGS_V21_PHASE_P = [
    ("qa.privacy.mode", "assignment", "str", G_PRIVACY,
     "Institution visibility: 'assignment' (default; assignees and involved departments) or 'department' "
     "(handling department). Switch with manage.py privacy_mode after reviewing the access report. Changes require "
     "two separate authorized officers.",
     "qa_core.edit_qa_dual_control_settings", True),
    ("qa.privacy.full_view_roles", '["executive-secretary"]', "json", G_PRIVACY,
     "Role codes that see every institution's full journey (plus super admin and the Director of ICT).", EDIT, True),
    ("qa.privacy.ict_department", "ict", "str", G_PRIVACY,
     "Department code of Digital Platforms & Analytics: its director sees every institution's full journey.",
     EDIT, True),
    ("qa.privacy.after_completion_days", "0", "int", G_PRIVACY,
     "Days an assignee keeps summary sight of an institution after completing the work (0 = none).", EDIT, False),
    ("qa.privacy.supervisor_digest", "daily", "str", G_PRIVACY,
     "How the ICT supervisor hears about internal stage changes: off, instant or daily.", EDIT, False),
    ("qa.access.kind_scopes", _KIND_SCOPES, "json", G_PRIVACY,
     "What each kind of work lets the assignee see (summary, documents, ssq, payments, case, visits, complaints, "
     "full_journey). ':subject' limits it to the assigned record.", EDIT, True),
    ("qa.access.lead_scopes", '["summary", "documents", "ssq", "case", "visits", "complaints"]', "json", G_PRIVACY,
     "What a department lead sees of the institutions where the department has work (rows of other departments "
     "are never shown). Finance leads also see payments.", EDIT, True),
    ("qa.portal.milestones", json.dumps(_PORTAL_MILESTONES), "json", G_PRIVACY,
     "The plain steps an institution sees (X6) and which internal stages map to each. Internal stage names, "
     "committees and officers are never shown on the portal.", EDIT, False),
    ("qa.portal.show_department_names", "false", "bool", G_PRIVACY,
     "Show 'Legal returned your document' instead of 'NBTE returned your document' on the portal.", EDIT, False),
    ("qa.portal.forbidden_terms",
     '["CQAC", "EXCO", "SOP-2", "SOP-02", "SOP-03", "SOP 2", "desk officer", "first pass", "Committee on Quality '
     'Assurance"]', "json", G_PRIVACY,
     "Words that must never appear on a portal page (checked by a test and on render).", EDIT, False),
]


SETTINGS = SETTINGS + SETTINGS_V21_PHASE_P
# v2.1 Phase P — rotation and review chains (spec_v21 G6.h; X27–X30)
SETTINGS_V21_PHASE_P_ROTATION = [
    ("qa.rotation.weights", '{"load": 0.45, "fairness": 0.35, "recency": 0.20}', "json", G_ROTATION,
     "How the next person is chosen: weight of current load, of fair share over the window (divided by the "
     "member's weight) and of days since last assigned.", EDIT, False),
    ("qa.rotation.fairness_window_days", "90", "int", G_ROTATION,
     "Days of past assignments counted for the fair share.", EDIT, False),
    ("qa.rotation.never_assigned_days", "365", "int", G_ROTATION,
     "Idle days assumed for someone never assigned in a rotation.", EDIT, False),
    ("qa.rotation.no_consecutive", "true", "bool", G_ROTATION,
     "Anti-streak: never propose the person chosen last time while someone else is eligible.", EDIT, False),
    ("qa.rotation.idle_alert_days", "30", "int", G_ROTATION,
     "Show an idle badge (and tell the director) after this many days without an assignment.", EDIT, False),
    ("qa.rotation.document_analysis.cap", "5", "int", G_ROTATION,
     "Open document analyses one person may hold.", EDIT, False),
    ("qa.rotation.ssq_review.cap", "3", "int", G_ROTATION,
     "Open SSQ reviews one officer may hold (SOP; same as qa.ssq.max_open_per_officer).", EDIT, False),
    ("qa.rotation.oversight_ict.cap", "15", "int", G_ROTATION,
     "Institutions one ICT officer supervises (D-9, to confirm).", EDIT, False),
    ("qa.rotation.visit_panel.cap", "2", "int", G_ROTATION, "Open visits one person may hold.", EDIT, False),
    ("qa.rotation.physical_verification.cap", "2", "int", G_ROTATION,
     "Open physical verifications one person may hold.", EDIT, False),
    ("qa.rotation.reviewer_not_on_panel", "true", "bool", G_ROTATION,
     "Whoever reviewed an SSQ cannot be on the visit for it (R-02; mirrors qa.workload.reviewer_cannot_verify).",
     EDIT, False),
    ("qa.rotation.panel_not_reviewer", "true", "bool", G_ROTATION,
     "A visit panel member cannot review the SSQ of the same institution.", EDIT, False),
    ("qa.rotation.same_institution_cooldown_months", "24", "int", G_ROTATION,
     "Months before the same person serves the same institution again (visit pools).", EDIT, False),
    ("qa.rotation.travel_buffer_days", "2", "int", G_ROTATION,
     "Days around a visit in which the person counts as away.", EDIT, False),
    ("qa.rotation.coi.exclude_state_of_origin", "false", "bool", G_ROTATION,
     "Also treat staff from the institution's state as conflicted.", EDIT, False),
    ("qa.rotation.rules", "{}", "json", G_ROTATION,
     "Switch rotation rules off or on, e.g. {\"availability\": true, \"cooldown\": false}. Codes: paused, "
     "department, cap, availability, reviewer_not_on_panel, panel_not_reviewer, conflict_of_interest, cooldown, "
     "discipline, inspection_rules.", EDIT, False),
    ("qa.rotation.auto_assign_pools", "[]", "json", G_ROTATION,
     "Pool codes that assign the suggestion at once (the director may reassign).", EDIT, False),
    ("qa.chain.policies_editable", "false", "bool", G_ROTATION,
     "Let the super admin edit review chain policies.", EDIT, False),
    ("qa.chain.document_analysis.third_step", "none", "str", G_ROTATION,
     "Third confirmer for document analysis: none, es, or roles:<role codes>.", EDIT, False),
    ("qa.chain.ssq_review.third_step", "none", "str", G_ROTATION,
     "Third confirmer for SSQ reviews: none, es, or roles:<role codes>.", EDIT, False),
    ("qa.chain.visit_report.third_step", "none", "str", G_ROTATION,
     "Third confirmer (e.g. ES) for visit reports: none, es, or roles:<role codes>.", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_V21_PHASE_P_ROTATION

# v2.1 Phase F — payments (F.4 P1–P10, F.18 X21; spec_v21 G4.h)
SETTINGS_V21_PHASE_F_PAYMENTS = [
    ("qa.fee.processing.enabled", "true", "bool", G_FEES,
     "Add the processing-fee line (FeeComponent kind 'processing') to every bill whose amount is set.", EDIT, True),
    ("qa.fee.institutional_processing_v3.base.unconfigured", "0", "decimal", G_FEES,
     "Safety fallback for an unknown institution type. Keep at 0: a bill must not use the ownership-based formula "
     "when the type has no confirmed schedule.", EDIT, True),
    ("qa.fee.institutional_processing.processing.polytechnic", "1000000", "decimal", G_FEES,
     "Legacy v2 processing fee for a polytechnic. New v3 applications use the type-specific v3 setting below.",
     EDIT, True),
    ("qa.fee.institutional_processing.processing", "0", "decimal", G_FEES,
     "Legacy/default processing fee on the v2 SOP-01 bill for other institution types.", EDIT, True),
    ("qa.fee.institutional_processing_v3.processing", "0", "decimal", G_FEES,
     "Default v3 processing fee for a new institution type. Keep at 0 until NBTE confirms that category's rate.",
     EDIT, True),
    ("qa.fee.programme_processing.processing", "0", "decimal", G_FEES,
     "Processing fee (₦) on the programme processing bill. [FEE AMOUNT] — 0 = no line until NBTE confirms.",
     EDIT, True),
    ("qa.fee.qa_request_processing.processing", "0", "decimal", G_FEES,
     "Processing fee (₦) on the SOP-02 QA request bill. [FEE AMOUNT] — 0 = no line until NBTE confirms.",
     EDIT, True),
    ("qa.payment.gateway_amount_tolerance", "0.00", "decimal", G_FEES,
     "Largest difference (₦) between the provider's amount and the balance still treated as a match.",
     EDIT, True),
    ("qa.payment.dof_reminder_hours", "24", "int", G_FEES,
     "ICT may send the Director of Finance one reminder per queue item in this many hours.", EDIT, False),
]
INSTITUTION_FEE_TYPES = [
    ("polytechnic", "Polytechnic", "2000000", "1000000", False),
    ("college-nursing", "College of Nursing", "1000000", "1000000", False),
    ("college-health", "College of Health", "0", "0", True),
    ("iei", "Innovation Enterprise Institution", "0", "0", True),
    ("monotechnic", "Monotechnic", "0", "0", True),
    ("technical-college", "Technical College", "0", "0", True),
    ("vei", "Vocational Enterprise Institution", "0", "0", True),
]
for type_code, type_label, base_amount, processing_amount, unconfirmed in INSTITUTION_FEE_TYPES:
    SETTINGS_V21_PHASE_F_PAYMENTS.extend([
        (f"qa.fee.institutional_processing_v3.base.{type_code}", base_amount, "decimal", G_FEES,
         f"SOP-01 institutional fee (₦) for {type_label}. Set by NBTE Finance; 0 blocks billing until confirmed.",
         EDIT, unconfirmed),
        (f"qa.fee.institutional_processing_v3.processing.{type_code}", processing_amount, "decimal", G_FEES,
         f"SOP-01 processing fee (₦) for {type_label}. Set by NBTE Finance; 0 blocks billing until confirmed.",
         EDIT, unconfirmed),
    ])
SETTINGS = SETTINGS + SETTINGS_V21_PHASE_F_PAYMENTS

# SOP-01 institution fee and processing fee per institution category AND
# ownership (owner decision). They replace the hard-coded formula
# ``4000000 if ownership == 'private' else 2000000`` of the
# ``institutional_processing`` bill (payments.billing.institution_fee_settings)
# and start at the same amounts, so no bill changes until Finance edits them.
INSTITUTION_FEE_DEFAULTS = {"private": "4000000", "public": "2000000"}   # the former formula


def _current_processing(type_code):
    """Seed default for a processing setting: the processing fee that bill uses today."""
    def value():
        from config.models import ConfigEntry

        for key in (f"qa.fee.institutional_processing.processing.{type_code}",
                    "qa.fee.institutional_processing.processing"):
            raw = ConfigEntry.objects.filter(key=key, scope="global").values_list("value", flat=True).first()
            if raw not in (None, ""):
                return str(raw)
        return "1000000" if type_code == "polytechnic" else "0"
    return value


SETTINGS_INSTITUTION_FEE_BY_OWNERSHIP = []
for type_code, type_label, _base, _proc, _unconfirmed in INSTITUTION_FEE_TYPES:
    for group, group_label in (("public", "public (federal or state)"), ("private", "private")):
        SETTINGS_INSTITUTION_FEE_BY_OWNERSHIP.extend([
            (f"qa.fee.institution.{type_code}.{group}", INSTITUTION_FEE_DEFAULTS[group], "decimal", G_FEES,
             f"SOP-01 institution fee (₦) for a {group_label} {type_label}, on the institutional processing bill. "
             "Used unless the category has an active row in Fees by institution category. 0 = no fee line.",
             EDIT, True),
            (f"qa.fee.institution_processing.{type_code}.{group}", _current_processing(type_code), "decimal",
             G_FEES,
             f"SOP-01 processing fee (₦) for a {group_label} {type_label}, added to the institution fee. "
             "Used unless the category has an active row in Fees by institution category. 0 = no line.",
             EDIT, True),
        ])
SETTINGS = SETTINGS + SETTINGS_INSTITUTION_FEE_BY_OWNERSHIP
# v2.1 Phase F — generated documents, QR verification and Scan (spec_v21 G5.h; X22–X26)
SETTINGS_V21_PHASE_F_DOCUMENTS = [
    ("qa.documents.provisional_watermark",
     json.dumps({"text": "PROVISIONAL — OFFICIAL COPY TO FOLLOW", "opacity": 0.14, "angle": 45}), "json", G_DOCUMENTS,
     "Diagonal watermark on documents sent to institutions before the signed official copy (X25).", EDIT, False),
    ("qa.documents.provisional_popup_text",
     "This is a provisional copy generated automatically. Please await the official document, which NBTE will "
     "upload or send to you.", "str", G_DOCUMENTS,
     "Pop-up shown the first time an institution user opens a provisional document (X25).", EDIT, False),
    ("qa.documents.verify_base_url", "", "str", G_DOCUMENTS,
     "Address printed in every QR code (…/v/<token>/) and on the Scan page link. Blank = the QA portal address. "
     "PAYMENT_VERIFY_BASE_URL (env) still applies to slips printed before v2.1.", EDIT, True),
    ("qa.verify.public_fields_default",
     json.dumps(["kind", "reference", "institution", "code", "amount", "signatories", "issued_at", "status"]),
     "json", G_DOCUMENTS,
     "What a visitor (not signed in) sees after scanning a new document: kind, reference, institution, code, "
     "amount, signatories (names and designations only), issued_at, status.", EDIT, False),
    ("qa.verify.public_signature_thumbnails", "false", "bool", G_DOCUMENTS,
     "Show signature thumbnails to visitors on the verify result (off: only NBTE staff see them).", EDIT, True),
]
SETTINGS = SETTINGS + SETTINGS_V21_PHASE_F_DOCUMENTS
# Official letters pipeline (documents.official_letters): provisional -> signed official copy
SETTINGS_OFFICIAL_LETTERS = [
    ("qa.documents.official_copy_sla_days", "7", "int", G_DOCUMENTS,
     "Days NBTE has to upload the signed official copy of a provisional letter (approval, accreditation, ...). "
     "After that the handling department's Director (and the staff preparing it) are reminded and the letter is "
     "escalated to the ES (manage.py qa_stage_reminders / qa_official_letters).", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_OFFICIAL_LETTERS
G_PROGRAMMES = "QA · Programmes and professional bodies"  # v2.1 Phase R (X42-X44)
SETTINGS_V21_PHASE_R = [
    ("qa.programmes.master_list_department", "ntird", "str", G_PROGRAMMES,
     "Department (code) told about hand-typed programmes and that maps them to the master list.", EDIT, False),
    ("qa.programmes.require_body", "true", "bool", G_PROGRAMMES,
     "A programme typed by hand must name the professional body that regulates it.", EDIT, False),
    ("qa.programmes.allow_manual_entry", "true", "bool", G_PROGRAMMES,
     "Institutions may type a programme that is not on the master list.", EDIT, False),
    ("qa.pb.portal_enabled", "true", "bool", G_PROGRAMMES,
     "Professional bodies can sign in to the QA portal.", EDIT, False),
    ("qa.pb.wait_for_body_letter", "true", "bool", G_PROGRAMMES,
     "Approval to commence waits for the body's letter when the body requires one (D-19).", EDIT, False),
    ("qa.pb.notify_events",
     json.dumps(["programme_proposed", "ssq_submitted", "visit_scheduled", "approval_to_commence"]), "json",
     G_PROGRAMMES, "Events a professional body is notified about.", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_V21_PHASE_R

# v2.1 Phase F onboarding (F.13–F.16, X12–X18)
SETTINGS_V21_PHASE_F_ONBOARDING = [
    ("qa.onboarding.profile_gate_enabled", "true", "bool", G_INSTITUTIONS,
     "X13: institution pages that need a complete profile (application, documents, QA requests) send the user "
     "to the setup checklist (/setup/) until the profile, both accounts and the management team are complete.",
     EDIT, False),
    ("qa.onboarding.ce_invite_resend_minutes", "15", "int", G_ACCESS,
     "X12: the Head of ICT may resend the Chief Executive's invitation once in this many minutes.", EDIT, False),
    ("qa.onboarding.autosubmit_trigger", "finance_confirmed", "str", G_WORKFLOW,
     "X14 (D-5): the onboarding application submits itself when the Director of Finance confirms the "
     "institutional/processing fee (finance_confirmed).", EDIT, False),
    ("qa.onboarding.physical_verification_threshold", "5", "int", G_WORKFLOW,
     "X18 (D-6): more than this many proposed programmes → the Director of ICT assigns an ICT officer for "
     "physical verification.", EDIT, False),
    ("qa.onboarding.physical_verification_comparator", "gt", "str", G_WORKFLOW,
     "X18: gt = more than the threshold (owner decision D-6); gte = the threshold or more.", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_V21_PHASE_F_ONBOARDING

# v2.1 Phase H (X32–X40): honoraria, visit costing and visit-report fines
SETTINGS_V21_PHASE_H = [
    ("honoraria.enabled", "true", "bool", G_HONORARIA,
     "Enable automatic accrual of payable SSQ and document-analysis work.", EDIT, False),
    ("honoraria.rate.internal_item", "10000", "decimal", G_HONORARIA,
     "X32: honorarium (₦) per internal SSQ or document item reviewed.", EDIT, True),
    ("honoraria.rate.academic_resource_person", "30000", "decimal", G_HONORARIA,
     "X32: honorarium (₦) per academic resource person per visit served.", EDIT, True),
    ("honoraria.visibility.director_sees_department", "true", "bool", G_HONORARIA,
     "X34: a director also sees the payment lines of their own department's staff.", EDIT, False),
    ("qa.visit.fine_per_falsified_item", "500000", "decimal", G_HONORARIA,
     "X40: fine (₦) added to the institution's bill for each falsified catalogue item.", EDIT, True),
    ("qa.fee.visit_fine",
     _formula("falsified * cfg('qa.visit.fine_per_falsified_item')", ["falsified"]),
     "formula", G_FEES,
     "X40: bill for falsified items on a visit report (₦): items × the fine per item.", EDIT, True),
    ("qa.cost.visit_nights_default", "3", "int", G_HONORARIA,
     "X39: nights used when the cost-sheet builder is run without a number.", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_V21_PHASE_H

# v2.2 — Chief Executive set-up, payments hardening, honoraria roller
SETTINGS_V22 = [
    ("qa.onboarding.ce_auto_email", "true", "bool", G_ACCESS,
     "v2.2: e-mail the Chief Executive's set-up link to the CE automatically at registration. Off: NBTE (ES, "
     "super admin, Director of ICT or the assigned ICT officer) sends it from the institution page. The link is "
     "never shown to the Head of ICT.", EDIT, False),
    ("qa.payment.attempt_reuse_minutes", "60", "int", G_FEES,
     "v2.2: an open online checkout for the same bill, method and amount is reused for this many minutes "
     "(double-clicks and retries never start a second charge).", EDIT, False),
    ("honoraria.onboarding_ssq_payable", "true", "bool", G_HONORARIA,
     "Pay the officer who assesses a new institution's programme self-study (onboarding) the internal SSQ "
     "review honorarium (honoraria.rate.internal_item, ₦10,000 by default), the same as for QA-request SSQs. "
     "Off: onboarding self-study assessments earn nothing.", EDIT, False),
    ("honoraria.rate.external_ssq_review", "30000", "decimal", G_HONORARIA,
     "v2.2: honorarium (₦) per SSQ reviewed by an external (academic / professional body) resource person.",
     EDIT, True),
    ("qa.visit.gps_max_km", "5", "decimal", G_HONORARIA,
     "v2.2 X40: a visit report written more than this many km from the institution's recorded GPS is flagged "
     "for the Director.", EDIT, False),
    ("qa.visit.gps_auto_capture", "true", "bool", G_HONORARIA,
     "v2.7: the report page asks the device for its location automatically (the officer may still record it by "
     "hand). Off: only the manual 'Record my location' button is shown.", EDIT, False),
    ("qa.visit.gps_offset_m", "200", "int", G_HONORARIA,
     "v2.7: tolerance added to qa.visit.gps_max_km before a reading is flagged (campus sites are large). "
     "200 m by default.", EDIT, False),
    ("qa.visit.gps_accuracy_max_m", "150", "int", G_HONORARIA,
     "v2.7: a device reading less accurate than this many metres is noted as weak when the location is "
     "recorded (it is never silently dropped).", EDIT, False),
    ("qa.onboarding.fee_timing", "after_clearance", "str", G_WORKFLOW,
     "When a new institution pays the institutional fee: after_clearance (SOP-01: after all documents are "
     "approved) or before_review (before documents can be uploaded). New applications only.", EDIT, True),
    ("qa.review.require_preview", "true", "bool", G_WORKFLOW,
     "Accept / return / approve stay locked until the reviewer has opened the document; the server refuses a "
     "decision on an unopened file. Directors' group approval of officer-reviewed items is exempt.", EDIT, True),
    ("honoraria.roller.mode", "oldest_first", "str", G_HONORARIA,
     "v2.2: order of the 'who is next to be paid' list — oldest_first (earliest finished work first) or "
     "largest_first.", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_V22

# v2.5 — cancellation and refund policy (payments.refund_policy). The band a
# cancellation falls in comes from the stage of the request / application /
# QA case; each band's percentage is a setting.
SETTINGS_V25_REFUNDS = [
    ("qa.refund.percent.before_assignment", "90", "int", G_REFUNDS,
     "Refund (% of what was paid) when the institution cancels before an NBTE officer is assigned or work has "
     "started. The rest (10% by default) is kept as an administrative fee.", EDIT, True),
    ("qa.refund.percent.under_assessment", "50", "int", G_REFUNDS,
     "Refund (%) when the institution cancels after an officer is assigned / the assessment has started, but "
     "before the Executive Secretary approves the visit cost.", EDIT, True),
    ("qa.refund.percent.after_cost_approval", "0", "int", G_REFUNDS,
     "Refund (%) when the institution cancels after the Executive Secretary approved the visit cost (fund "
     "release, visit, report and decision stages).", EDIT, True),
    ("qa.refund.percent.nbte_withdrawal", "100", "int", G_REFUNDS,
     "Refund (%) when NBTE withdraws or rejects a request or application for reasons on NBTE's side.",
     EDIT, True),
    ("qa.refund.min_admin_fee", "0", "decimal", G_REFUNDS,
     "Smallest administrative fee (₦) kept when an institution cancels a paid request (never more than was paid; "
     "not applied when NBTE withdraws). 0 = only the percentage applies.", EDIT, True),
    ("qa.refund.request_window_days", "90", "int", G_REFUNDS,
     "Days after the cancellation within which the institution must give its bank details; the refund lapses "
     "after that.", EDIT, False),
    ("qa.refund.requires_es_approval", "true", "bool", G_REFUNDS,
     "Refunds are approved by the Executive Secretary after Finance verifies them. Off: the Director of Finance "
     "approves (never the officer who verified).", EDIT, True),
    ("qa.refund.void_unpaid_bills", "true", "bool", G_REFUNDS,
     "Unpaid bills of a cancelled request or application are voided (cancelled) automatically.", EDIT, False),
    ("qa.refund.stage_bands", "{}", "json", G_REFUNDS,
     'Override which band a stage falls in, e.g. {"qa_case:generate_cost": "before_assignment"}. Keys are '
     "flow:stage (qa_request, qa_case, onboarding); bands are before_assignment, under_assessment, "
     "after_cost_approval or none (cannot be cancelled). Empty = the built-in table.", EDIT, True),
]
SETTINGS = SETTINGS + SETTINGS_V25_REFUNDS

# Linear onboarding (accreditation/onboarding_linear.py): self-study review → approval → formal QA request
SETTINGS_ONBOARDING_LINEAR = [
    ("qa.onboarding.stage_sla_days", "5", "int", G_WORKFLOW,
     "Days a programme self-study may wait at one step (officer assignment, assessment, director's clearance) "
     "before the person holding it is reminded. After twice this the director is told (the ES when the director "
     "holds it); after three times the ES. Runs hourly in working hours (manage.py qa_onboarding_reminders).",
     EDIT, False),
    ("qa.onboarding.auto_request_type", "accreditation", "str", G_QA_REQUESTS,
     "QA type code (Settings → QA types) of the formal QA request NBTE starts by itself when the ES confirms an "
     "institution's approval, for the programmes whose onboarding self-studies were cleared. Their self-studies "
     "are used as the request's SSQs (the institution does not fill them again). Write 'none' to stop starting "
     "requests automatically.", EDIT, True),
    ("qa.onboarding.auto_request_fee_waived", "false", "bool", G_FEES,
     "On: the automatically started QA request skips the QA fee and goes straight to the SSQ / QA case. Off "
     "(default): the fee is billed as for any QA request and the case opens once it is paid.", EDIT, True),
]
SETTINGS = SETTINGS + SETTINGS_ONBOARDING_LINEAR

# SOP-03 people rules (accreditation/visit_people.py): who is Recommended / Busy / On leave / Not allowed
# in the cost-sheet, personnel and team-lead pickers.
SETTINGS_SOP03_PEOPLE = [
    ("qa.people.visit_gap_days", "7", "int", G_WORKFLOW,
     "A person is shown as Busy when another QA visit they are on falls within this many days of this visit "
     "(or of today, before the visit dates are set). 0 = only overlapping dates.", EDIT, False),
    ("qa.people.max_visits_per_period", "3", "int", G_WORKFLOW,
     "A person is shown as Busy once they are on this many other visits within the period below (0 = no cap).",
     EDIT, False),
    ("qa.people.visits_period_days", "90", "int", G_WORKFLOW,
     "The period (days either side of this visit) for the visits cap above.", EDIT, False),
    ("qa.people.busy_open_tasks", "8", "int", G_WORKFLOW,
     "Open tasks at which a person is shown as Busy in the person pickers (0 = never).", EDIT, False),
    ("qa.people.light_open_tasks", "3", "int", G_WORKFLOW,
     "Fewer open tasks than this counts as a light workload (needed for 'Recommended').", EDIT, False),
    ("qa.people.leave_lookahead_days", "14", "int", G_WORKFLOW,
     "Before the visit dates are set, approved leave starting within this many days shows a person as On leave.",
     EDIT, False),
    ("qa.cost.person_rules", json.dumps({
        "dta-director": {"lead": True}, "acad-allow-lead": {"lead": True},
        "dta-officer": {"department": "owning", "grade_min": 7, "grade_max": 16},
        "acad-allow-member": {"department": "owning", "grade_min": 7, "grade_max": 16},
        "dta-support": {"grade_max": 8}, "honorarium-rp": {"external_only": True},
        "audit-supervision": {"department": "audit", "department_strict": True, "visit": False}}), "json", G_HONORARIA,
     "Who each rate-table line is for (keyed by rate code): lead (team-lead roles only, one per sheet), department "
     "('owning' = the case's handling department, or audit / finance / ict / a department code; preferred, or the "
     "only one allowed with department_strict), grade_min / grade_max (a known grade outside locks the person), "
     "external_only (NBTE staff locked), visit: false (not a visiting line). Rates not listed prefer the handling "
     "department.", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_SOP03_PEOPLE

# SOP-03 visit invitations (accreditation/visit_invitations.py): letters to professional bodies and external
# resource persons once the ES approves the visit; reminders run with manage.py qa_stage_reminders.
SETTINGS_VISIT_INVITES = [
    ("qa.visit.invite_enabled", "true", "bool", G_WORKFLOW,
     "When the ES approves a visit, send an invitation letter (e-mail + portal) to each professional body on the "
     "case's programmes and each external resource person on the team.", EDIT, False),
    ("qa.visit.invite_reminder_days", "[7, 2]", "json", G_WORKFLOW,
     "Days before the visit when invitees who have not confirmed are reminded (once each) and the desk officer "
     "and director are told who has not confirmed. Empty list = no reminders.", EDIT, False),
    ("qa.visit.invite_bring_body", "your council's letter of nomination, a means of identification and your "
     "council's assessment checklist for the programme(s)", "str", G_WORKFLOW,
     "'What to bring' line of the invitation letter to a professional body.", EDIT, False),
    ("qa.visit.invite_bring_rp", "a means of identification, this letter, and any discipline checklist you use; "
     "the Team Lead will brief the team on the first morning", "str", G_WORKFLOW,
     "'What to bring' line of the invitation letter to a resource person.", EDIT, False),
]
SETTINGS = SETTINGS + SETTINGS_VISIT_INVITES

SETTING_KEYS = [row[0] for row in SETTINGS]
