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

GROUP_ORDER = [G_FEES, G_INSTITUTIONS, G_WORKFLOW, G_COMPLAINTS, G_ELIGIBILITY, G_ACCESS, G_INTERACTION, G_SETTINGS]


def _formula(expr, vars_, returns="decimal"):
    return json.dumps({"expr": expr, "vars": vars_, "returns": returns})


SETTINGS = [
    # --- Fees and payments -------------------------------------------------
    ("qa.fee.institutional_processing",
     _formula("4000000 if ownership == 'private' else 2000000", ["ownership"]),
     "formula", G_FEES,
     "SOP-01 institutional processing fee (₦). Annex A: first figure Private, second Public (Q-04).",
     EDIT, True),
    ("qa.fee.programme_processing",
     _formula("programmes * (2000000 if ownership == 'private' else 1000000)", ["programmes", "ownership"]),
     "formula", G_FEES,
     "Programme processing fee (₦) for a number of programmes, by ownership.",
     EDIT, True),
    ("qa.fee.qa_request_processing",
     _formula("cfg('qa.fee.qa_request.base') + programmes * cfg('qa.fee.qa_request.per_programme')",
              ["programmes"]),
     "formula", G_FEES,
     "SOP-02 processing fee (₦): base + programmes × per-programme (D15).",
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
    ("qa.payment.final_approver_role", "qa-finance-director", "str", G_FEES,
     "Role code that gives the final confirm/reject on every payment (Director of Finance).",
     EDIT, True),
    ("qa.payment.receipt_required_for_provider_payments", "true", "bool", G_FEES,
     "A provider-reported payment needs an uploaded receipt before the Director can confirm it.",
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
    """Remita and Paystack are switched on from day one (16 Oct decision) in
    development and demo installs, where the simulator stands in for missing
    keys. On a production install they are seeded OFF until Finance clears them
    (Spec Q-05, Risk 2); a provider is only ever offered when its .env keys are
    present anyway (§10.1)."""
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
    ("qa.payment.dual_verify_threshold", "5000000", "decimal", G_FEES,
     "Payments above this amount (₦) need a second first-pass check before the Director decides.",
     EDIT, True),
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
    ("qa.ssq.submit_roles", json.dumps(["inst-chief-executive", "inst-admin"]), "json", G_QA_REQUESTS,
     "Institution roles that may submit an SSQ to NBTE (the academic department completes sections only).",
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
    ("qa.calendar.business_hours", '["08:00", "16:00"]', "json", G_WORKFLOW,
     "Opening hours used by business-hour SLAs (with attendance.work_week and the portal holiday calendar).",
     EDIT, False),
    ("qa.suspension.enabled_for", "[]", "json", G_WORKFLOW,
     'Record types a complaint may place on hold, e.g. ["payments.paymentobligation"] (Requirements 15–17). '
     "Empty = holds are off.", EDIT, True),
]

SETTINGS = (SETTINGS + SETTINGS_PHASE2 + SETTINGS_PHASE3 + SETTINGS_PHASE4 + SETTINGS_PHASE5 + SETTINGS_PHASE6
            + SETTINGS_PHASE7)

SETTING_KEYS = [row[0] for row in SETTINGS]
