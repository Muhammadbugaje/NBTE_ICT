"""Phase 8 seed data — the Master Plan's Phase 6 ("Decisions, letters and
capacity assessment"), which the project's own Phase 6 never built.

Everything here is *data*, created by ``seed_startup`` when missing and never
overwritten: the three approval types (B18), the decision/letter/capacity
permissions, the two-officer policies for new fee schedules and manual bills,
and the notification templates for decisions, renewal reminders and expiry.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Approval types (B18 / R-05). code, name, months, programme status, description
# ---------------------------------------------------------------------------
APPROVAL_TYPES = [
    ("full", "Full accreditation", 60, "accredited", "Five years from the commencement date."),
    ("interim", "Interim accreditation", 12, "interim", "One year to correct the weaknesses found."),
    ("approval_to_commence", "Approval to commence", 24, "approval_to_commence",
     "A new programme may admit students; full accreditation follows."),
]

# Post-visit report recommendation (stage 13) → the default stage-14 decision.
RECOMMENDATION_DEFAULTS = {
    "accredit": ("approve", "full"),
    "interim": ("approve", "interim"),
    "deny": ("deny", ""),
    "revisit": ("return", ""),
}
# A new programme (resource inspection) is approved to commence by default.
NEW_PROGRAMME_APPROVAL = "approval_to_commence"

# ---------------------------------------------------------------------------
# Permissions (additive; never removed)
# ---------------------------------------------------------------------------
_VIEW_CAP = "accreditation.view_capacity_assessment"
_RUN_CAP = "accreditation.run_capacity_assessment"
ROLE_PERMISSIONS = {
    "cqac-member": ["accreditation.cqac_review", _VIEW_CAP],
    "cqac-chairman": ["accreditation.cqac_review", "accreditation.cqac_chair_decide", "accreditation.batch_decide",
                      _VIEW_CAP],
    "executive-secretary": ["accreditation.es_final_decide", "accreditation.regenerate_letter", _VIEW_CAP],
    "director": [_VIEW_CAP, _RUN_CAP, "accreditation.regenerate_letter"],
    "deputy-director": [_VIEW_CAP, _RUN_CAP],
    "division-head": [_VIEW_CAP, _RUN_CAP],
    "officer": [_VIEW_CAP, _RUN_CAP],
    "ict-qa-monitor": [_VIEW_CAP],
}
GROUP_PERMISSIONS = {
    "QA Settings Admin": ["accreditation.manage_approval_types"],
}

# ---------------------------------------------------------------------------
# Two-officer actions (§7)
# ---------------------------------------------------------------------------
ACTION_POLICIES = [
    {"action_code": "payments.create_fee_schedule", "label": "Add Fee Schedule",
     "description": "Adds a new fee with its own formula setting. Applies only when a second officer (the "
                    "Director of Finance) confirms.",
     "menu_group": "Settings", "requires_second_officer": True, "initiate_permission": "payments.view_obligation",
     "confirm_permission": "payments.manage_fee_schedule", "confirmer_min_rank": 100,
     "same_department_required": True, "is_enabled": True, "expires_after_hours": None},
    {"action_code": "payments.issue_bill", "label": "Issue a Bill",
     "description": "Issues a bill to an institution from a fee schedule (the formula computes the amount). "
                    "A Finance officer proposes; the Director of Finance confirms.",
     "menu_group": "Finance (QA)", "requires_second_officer": True, "initiate_permission": "payments.view_obligation",
     "confirm_permission": "payments.final_confirm_payment", "confirmer_min_rank": 100,
     "same_department_required": True, "is_enabled": True, "expires_after_hours": None},
]

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
TEMPLATES = [
    ("programme.decided", "in_app", "Decision on {{ programme }}",
     "{{ outcome }}. {{ detail }} Your outcome letter is on the Outcome letters page.",
     "Tells the institution the final decision on a programme (stage 17)."),
    ("programme.decided", "email", "NBTE: decision on {{ programme }}",
     "<p>The Executive Secretary has decided on <strong>{{ programme }}</strong>: {{ outcome }}.</p>"
     "<p>{{ detail }}</p><p>Sign in to the NBTE Quality Assurance Portal and open <em>Outcome letters</em> to "
     "download the letter.</p>",
     "E-mail with the final decision on a programme."),
    ("programme.renewal_due", "in_app", "{{ programme }} expires in {{ days }} days",
     "The approval of {{ programme }} ends on {{ expiry }}. Request re-accreditation from the Programmes page.",
     "Renewal reminder to the institution."),
    ("programme.renewal_due", "email", "NBTE: {{ programme }} expires on {{ expiry }}",
     "<p>The approval of <strong>{{ programme }}</strong> ends on <strong>{{ expiry }}</strong> "
     "({{ days }} days from now).</p><p>Sign in to the NBTE Quality Assurance Portal and request "
     "re-accreditation from the <em>Programmes</em> page.</p>",
     "Renewal reminder e-mail to the institution."),
    ("programme.renewal_due_staff", "in_app", "{{ institution }}: {{ programme }} expires in {{ days }} days",
     "Approval ends on {{ expiry }}.", "Renewal reminder to the handling department."),
    ("programme.expired", "in_app", "{{ programme }} has expired",
     "The approval ended on {{ expiry }}. Request re-accreditation from the Programmes page.",
     "Tells the institution a programme's approval has expired."),
    ("programme.expired_staff", "in_app", "{{ institution }}: {{ programme }} has expired",
     "The approval ended on {{ expiry }}.", "Tells the handling department a programme has expired."),
]
RULES = [
    ("programme.decided", "institution_users", "programme.decided", "in_app"),
    ("programme.decided", "institution_users", "programme.decided", "email"),
    ("programme.renewal_due", "institution_users", "programme.renewal_due", "in_app"),
    ("programme.renewal_due", "institution_users", "programme.renewal_due", "email"),
    ("programme.renewal_due_staff", "role", "programme.renewal_due_staff", "in_app"),
    ("programme.expired", "institution_users", "programme.expired", "in_app"),
    ("programme.expired_staff", "role", "programme.expired_staff", "in_app"),
]

# Fee formulas: the Phase 1 defaults, upgraded in place by seed_startup only
# while an administrator has not changed them (the amounts stay the same: the
# per-additional-stream settings start at ₦0).
OLD_FEE_EXPRESSIONS = {
    "qa.fee.institutional_processing": "4000000 if ownership == 'private' else 2000000",
    "qa.fee.programme_processing": "programmes * (2000000 if ownership == 'private' else 1000000)",
    "qa.fee.qa_request_processing":
        "cfg('qa.fee.qa_request.base') + programmes * cfg('qa.fee.qa_request.per_programme')",
}
