"""Phase 2 seed definitions for `seed_startup` (PHASES.md, Phase 2 "Seed additions").

* Lookups: ownership, institution types and categories (→ department
  mapping), disciplines, programme levels and categories.
* Document kinds and the registration document rules (Requirement 1).
* Fee schedules, a placeholder bank account, institution request types.
* Flows: institution lifecycle, onboarding (SOP-01, with the parallel
  clearance gate) and refund. The payment flow shipped in Phase 1.
* Action policies for every §6 action and the payment two-step actions.
* Notification templates and rules for onboarding and payments.
* Demo additions (``--with-demo``): a second demo institution, programmes,
  one paid and one pending obligation, the clearance officers and the
  Director of Finance.

As in Phase 1, every object is keyed by a natural key and created only when
missing; an administrator's edits are never overwritten.
"""

# ---------------------------------------------------------------------------
# Lookups. Department values are QA codes resolved by qa_core.departments.
# ---------------------------------------------------------------------------
OWNERSHIPS = [  # code, name, is_public
    ("federal", "Federal", True),
    ("state", "State", True),
    ("private", "Private", False),
]

INSTITUTION_TYPES = [  # code, name, default department
    ("polytechnic", "Polytechnic", "dpap"),
    ("monotechnic", "Monotechnic", "dpap"),
    ("college-health", "College of Health Sciences/Technology", "dnhtp"),
    ("college-nursing", "College of Nursing", "dnhtp"),
    ("technical-college", "Technical College", "vtsd"),
    ("iei", "Innovation Enterprise Institution (IEI)", "vtsd"),
    ("vei", "Vocational Enterprise Institution (VEI)", "vtsd"),
]

INSTITUTION_CATEGORIES = [  # code, name, type code, routing department
    ("polytechnic", "Polytechnic", "polytechnic", "dpap"),
    ("monotechnic", "Monotechnic", "monotechnic", "dpap"),
    ("specialised-monotechnic", "Specialised Monotechnic", "monotechnic", "dpap"),
    ("college-health", "College of Health Sciences/Technology", "college-health", "dnhtp"),
    ("college-nursing", "College of Nursing", "college-nursing", "dnhtp"),
    ("technical-college", "Technical College", "technical-college", "vtsd"),
    ("iei", "Innovation Enterprise Institution", "iei", "vtsd"),
    ("vei", "Vocational Enterprise Institution", "vei", "vtsd"),
]

DISCIPLINES = [
    ("engineering", "Engineering"),
    ("science-environmental", "Science & Environmental"),
    ("applied-arts", "Applied Arts & Communication"),
    ("health", "Health Sciences"),
    ("management", "Management Studies"),
    ("agriculture", "Agriculture"),
]

PROGRAMME_LEVELS = [
    ("nd", "National Diploma (ND)"),
    ("hnd", "Higher National Diploma (HND)"),
    ("nid", "National Innovation Diploma (NID)"),
    ("nvc", "National Vocational Certificate (NVC)"),
    ("admin", "Institutional administration"),
]

PROGRAMME_CATEGORIES = [  # name, discipline, students per stream, technology based
    ("Engineering Technology", "engineering", 30, True),
    ("Computing & Information Technology", "science-environmental", 30, True),
    ("Applied Sciences", "science-environmental", 30, True),
    ("Environmental Studies", "science-environmental", 30, True),
    ("Business & Management", "management", 40, False),
    ("Mass Communication & Arts", "applied-arts", 40, False),
    ("Health Technology", "health", 30, True),
    ("Agricultural Technology", "agriculture", 30, True),
]

# ---------------------------------------------------------------------------
# Documents (Requirement 1). code, name, description, extensions, max MB
# ---------------------------------------------------------------------------
PDF_IMG = ["pdf", "jpg", "jpeg", "png"]
DOCUMENT_KINDS = [
    ("cac", "CAC Certificate of Incorporation", "Corporate Affairs Commission certificate (private proprietors).",
     PDF_IMG, 5),
    ("bank-guarantee", "Bank Guarantee", "Bank guarantee or statement of financial capacity (private).", ["pdf"], 10),
    ("enabling-law", "Enabling Law / Edict", "The Act, Law or Edict establishing the institution (public).",
     ["pdf"], 10),
    ("funding-commitment", "Government Funding Commitment", "Letter from the proprietor government committing "
     "capital and recurrent funding (public).", ["pdf"], 5),
    ("c-of-o", "Certificate of Occupancy / Land Title", "Evidence of title to the land the institution occupies.",
     PDF_IMG, 10),
    ("physical-master-plan", "Physical Master Plan", "Site layout and development plan.", ["pdf"], 20),
    ("academic-master-plan", "Academic Master Plan", "Programmes, staffing and growth plan.", ["pdf"], 20),
    ("need-survey", "Need Assessment Survey", "Evidence of demand for the institution and its programmes.",
     ["pdf"], 10),
    ("organogram", "Organogram", "Management structure and principal officers.", PDF_IMG, 5),
    ("medical-mou", "MoU with a Teaching Hospital", "Clinical placement agreement (health and nursing colleges).",
     ["pdf"], 10),
]

# process, ownership (None = any), institution type (None = any), kind, mandatory, reviewing department
_REG = "registration"
DOCUMENT_REQUIREMENTS = [
    (_REG, "private", None, "cac", True, "legal"),
    (_REG, "private", None, "bank-guarantee", True, "finance"),
    (_REG, "federal", None, "enabling-law", True, "legal"),
    (_REG, "state", None, "enabling-law", True, "legal"),
    (_REG, "federal", None, "funding-commitment", True, "finance"),
    (_REG, "state", None, "funding-commitment", True, "finance"),
    (_REG, None, None, "c-of-o", True, "legal"),
    (_REG, None, None, "academic-master-plan", True, "academic-planning"),
    (_REG, None, None, "need-survey", True, "academic-planning"),
    (_REG, None, None, "organogram", False, "academic-planning"),
    (_REG, None, None, "physical-master-plan", True, "physical-planning"),
    (_REG, None, "college-health", "medical-mou", True, "academic-planning"),
    (_REG, None, "college-nursing", "medical-mou", True, "academic-planning"),
]

# ---------------------------------------------------------------------------
# Fees, bank account, request types
# ---------------------------------------------------------------------------
FEE_SCHEDULES = [  # code, name, slip description, formula key, confirmed setting
    ("institutional_processing", "Institutional processing fee", "Institutional processing fee (SOP-01)",
     "qa.fee.institutional_processing", ""),
    ("programme_processing", "Programme processing fee", "Programme processing fee",
     "qa.fee.programme_processing", ""),
    ("qa_request_processing", "QA request processing fee", "QA request processing fee (SOP-02)",
     "qa.fee.qa_request_processing", "qa.fee.qa_request_processing.confirmed"),
]

PLACEHOLDER_BANK = {"bank_name": "(to be set by Finance)", "account_name": "National Board for Technical Education",
                    "account_number": "0000000000", "is_active": False, "is_placeholder": True}

REQUEST_TYPES = [  # code, name, enabled
    ("qa", "Quality Assurance", True),
    ("siwes", "SIWES", False),
    ("dtacademy", "DTAcademy", False),
]

# ---------------------------------------------------------------------------
# Flows (§5.7). stage: code, label, public label, kind, actor kind, extras
# transition: from, to, action, label, extras
# ---------------------------------------------------------------------------
CONFIRM = {"requires_confirmation": True}

INSTITUTION_FLOW = {
    "code": "institution_lifecycle",
    "name": "Institution lifecycle",
    "subject_model": "institutions.Institution",
    "description": "proposed → under_review → approved ⇄ suspended → delisted. Approve, delist and relist "
                   "need a second officer (§5.7, §6).",
    "stages": [
        ("proposed", "Proposed", "Proposed institution — onboarding", "start", "institution", {}),
        ("under_review", "Under review", "Application under review", "action", "role_in_department",
         {"actor_scope": "ntird"}),
        ("approved", "Approved", "Approved", "action", "role_in_department", {}),
        ("suspended", "Suspended", "Suspended", "action", "owning_department_director", {}),
        ("delisted", "Delisted", "Delisted", "action", "es", {}),
    ],
    "transitions": [
        ("proposed", "under_review", "submit_for_review", "Application submitted", {}),
        ("under_review", "proposed", "return_to_proposed", "Application returned", {}),
        ("proposed", "approved", "approve", "Approve institution", CONFIRM),
        ("under_review", "approved", "approve", "Approve institution", CONFIRM),
        ("approved", "suspended", "suspend", "Suspend", CONFIRM),
        ("suspended", "approved", "reinstate", "Reinstate", CONFIRM),
        ("proposed", "delisted", "delist", "Delist", CONFIRM),
        ("under_review", "delisted", "delist", "Delist", CONFIRM),
        ("approved", "delisted", "delist", "Delist", CONFIRM),
        ("suspended", "delisted", "delist", "Delist", CONFIRM),
        ("delisted", "approved", "relist", "Relist", CONFIRM),
    ],
}

CLEAR = {"required_permission": "accreditation.decide_departmental_clearance"}
RETURN = {"required_permission": "accreditation.decide_departmental_clearance", "requires_remark": True,
          "effects": ["clearance_returned"]}

ONBOARDING_FLOW = {
    "code": "onboarding",
    "name": "Onboarding (SOP-01)",
    "subject_model": "accreditation.Application",
    "description": "draft → submitted → [parallel: Legal, Finance, Academic Planning, Physical Planning] → join "
                   "→ institutional fee → programme selection … Any clearance branch can return the "
                   "application to draft (§5.7).",
    "stages": [
        ("draft", "Draft", "Draft — complete your profile and documents, then submit", "start", "institution", {}),
        ("submitted", "Submitted", "Submitted", "system", "system", {}),
        ("clearance", "Departmental clearance", "Under review by NBTE departments", "parallel_gate", "system", {}),
        ("legal_review", "Legal review", "Under review by NBTE departments", "action", "role_in_department",
         {"actor_scope": "legal", "sla_hours": 72}),
        ("finance_review", "Finance review", "Under review by NBTE departments", "action", "role_in_department",
         {"actor_scope": "finance", "sla_hours": 72}),
        ("academic_planning_review", "Academic Planning review", "Under review by NBTE departments", "action",
         "role_in_department", {"actor_scope": "academic-planning", "sla_hours": 72}),
        ("physical_planning_review", "Physical Planning review", "Under review by NBTE departments", "action",
         "role_in_department", {"actor_scope": "physical-planning", "sla_hours": 72}),
        ("clearance_join", "All departments cleared", "Cleared by NBTE departments", "join", "system", {}),
        ("institutional_fee_due", "Institutional fee due", "Institutional processing fee due", "action",
         "institution", {}),
        ("payment_pending_confirmation", "Payment awaiting Finance confirmation",
         "Institution claims paid — awaiting Finance confirmation", "action", "finance_director", {}),
        ("fee_confirmed", "Institutional fee confirmed", "Institutional fee confirmed", "system", "system", {}),
        ("programme_selection", "Programme selection", "Select the programmes you will offer", "action",
         "institution", {}),
        ("programme_clearance", "Programme clearance (NTIRD)", "Programmes under review by NBTE", "action",
         "role_in_department", {"actor_scope": "ntird"}),
        ("programme_fee_due", "Programme fee due", "Programme processing fee due", "action", "institution", {}),
        ("programme_payment_pending", "Programme payment awaiting Finance confirmation",
         "Institution claims paid — awaiting Finance confirmation", "action", "finance_director", {}),
        ("programme_fee_confirmed", "Programme fee confirmed", "Programme fee confirmed", "system", "system", {}),
        ("ssq_in_progress", "SSQ in progress", "Complete your questionnaire (SSQ)", "action", "institution", {}),
        ("ssq_submitted", "SSQ submitted", "Questionnaire submitted", "end", "system",
         {"is_terminal_success": True}),
        ("withdrawn", "Withdrawn", "Application withdrawn", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        ("draft", "submitted", "submit", "Submit application", {"effects": ["onboarding_submitted"]}),
        ("submitted", "clearance", "auto_open_clearance", "Open departmental clearance",
         {"effects": ["create_clearances"]}),
        ("clearance", "legal_review", "branch_legal", "Legal", {}),
        ("clearance", "finance_review", "branch_finance", "Finance", {}),
        ("clearance", "academic_planning_review", "branch_academic_planning", "Academic Planning", {}),
        ("clearance", "physical_planning_review", "branch_physical_planning", "Physical Planning", {}),
        ("legal_review", "clearance_join", "clear", "Clear (Legal)", CLEAR),
        ("finance_review", "clearance_join", "clear", "Clear (Finance)", CLEAR),
        ("academic_planning_review", "clearance_join", "clear", "Clear (Academic Planning)", CLEAR),
        ("physical_planning_review", "clearance_join", "clear", "Clear (Physical Planning)", CLEAR),
        ("legal_review", "draft", "return", "Return to institution (Legal)", RETURN),
        ("finance_review", "draft", "return", "Return to institution (Finance)", RETURN),
        ("academic_planning_review", "draft", "return", "Return to institution (Academic Planning)", RETURN),
        ("physical_planning_review", "draft", "return", "Return to institution (Physical Planning)", RETURN),
        ("clearance_join", "institutional_fee_due", "auto_issue_fee", "Issue the institutional fee",
         {"effects": ["create_payment_obligation:institutional_processing"]}),
        ("institutional_fee_due", "payment_pending_confirmation", "payment_claimed", "Payment claimed", {}),
        ("payment_pending_confirmation", "institutional_fee_due", "payment_rejected", "Payment rejected", {}),
        ("payment_pending_confirmation", "fee_confirmed", "payment_confirmed", "Payment confirmed", {}),
        ("institutional_fee_due", "fee_confirmed", "payment_confirmed", "Payment confirmed (waived/matched)", {}),
        ("fee_confirmed", "programme_selection", "auto_programme_selection", "Open programme selection", {}),
        ("programme_selection", "programme_clearance", "submit_programmes", "Submit programmes", {}),
        ("programme_clearance", "programme_fee_due", "clear_programmes", "Clear programmes",
         {"effects": ["create_payment_obligation:programme_processing"]}),
        ("programme_clearance", "programme_selection", "return", "Return programmes",
         {"requires_remark": True}),
        ("programme_fee_due", "programme_payment_pending", "payment_claimed", "Payment claimed", {}),
        ("programme_payment_pending", "programme_fee_due", "payment_rejected", "Payment rejected", {}),
        ("programme_payment_pending", "programme_fee_confirmed", "payment_confirmed", "Payment confirmed", {}),
        ("programme_fee_due", "programme_fee_confirmed", "payment_confirmed",
         "Payment confirmed (waived/matched)", {}),
        ("programme_fee_confirmed", "ssq_in_progress", "auto_open_ssq", "Open the SSQ", {}),
        ("ssq_in_progress", "ssq_submitted", "submit_ssq", "Submit SSQ", {}),
        ("draft", "withdrawn", "withdraw", "Withdraw application", {"requires_remark": True}),
    ],
}

REFUND_FLOW = {
    "code": "refund",
    "name": "Refund",
    "subject_model": "payments.Refund",
    "description": "request (Finance officer) → approve (Director of Finance, two-step) → paid (reference "
                   "recorded). The source application is never reversed automatically (§10.6).",
    "stages": [
        ("requested", "Requested", "Refund requested", "start", "finance_director", {}),
        ("approved", "Approved", "Refund approved", "action", "role_in_department", {"actor_scope": "finance"}),
        ("paid", "Paid", "Refunded", "end", "system", {"is_terminal_success": True}),
        ("rejected", "Rejected", "Refund declined", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        ("requested", "approved", "approve", "Approve refund (Director of Finance)",
         {**CONFIRM, "required_permission": "payments.confirm_refund"}),
        ("requested", "rejected", "reject", "Decline refund", {"requires_remark": True}),
        ("approved", "paid", "mark_paid", "Record refund payment",
         {"required_permission": "payments.initiate_refund", "requires_remark": True}),
    ],
}

FLOWS = [INSTITUTION_FLOW, ONBOARDING_FLOW, REFUND_FLOW]

# ---------------------------------------------------------------------------
# Action policies (§6). Labels and menu groups are editable data.
# ---------------------------------------------------------------------------
NI, TS, FIN, SET = "New Institution", "TVET Support", "Finance (QA)", "Settings"


def _pair(code, label, group, rank, *, desc="", same_dept=True, initiate=None, confirm=None, second=True,
          enabled=True, expires=None):
    app, name = code.split(".", 1)
    return {
        "action_code": code, "label": label, "description": desc, "menu_group": group,
        "requires_second_officer": second,
        "initiate_permission": initiate if initiate is not None else f"{app}.initiate_{name}",
        "confirm_permission": confirm if confirm is not None else (f"{app}.confirm_{name}" if second else ""),
        "confirmer_min_rank": rank, "same_department_required": same_dept, "is_enabled": enabled,
        "expires_after_hours": expires,
    }


LATER = "Needs Phase 3–5 records (QA requests, personnel, mapping, audit). Shown disabled until then."
ACTION_POLICIES = [
    _pair("institutions.add_tvet", "Add TVET", NI, 50,
          desc="Creates the institution (status proposed), allocates its code and the primary portal account."),
    _pair("institutions.approve_institution", "Initiate Approve Inst.", NI, 100, same_dept=False,
          desc="proposed/under review → approved; the handling department moves from NTIRD to the category's "
               "department. Confirmed by the ES or a delegate (rank 100+)."),
    _pair("institutions.drop_programmes", "Drop Programmes", TS, 50,
          desc="Marks programmes dropped and cancels their open tasks."),
    _pair("institutions.update_state_lga", "Update State & LGA", TS, 50,
          desc="Changes the state and LGA; the zone is recomputed."),
    _pair("institutions.wipe_requests", "Wipe Institution Requests", TS, 80,
          desc="Cancels every open, pre-submission request of the chosen types. Never deletes; submitted "
               "requests are excluded and listed."),
    _pair("institutions.revert_qa_history", "Revert QA History", TS, 80,
          desc="Moves an application back to an earlier stage with a compensating event. Never deletes events."),
    _pair("institutions.cancel_institution_request", "Cancel Request", TS, 50,
          desc="Cancels one request of an enabled request type."),
    _pair("institutions.update_contact", "Update Contact", TS, 50,
          desc="Replaces a contact; the old one is kept in history."),
    _pair("institutions.update_address", "Update Address", TS, 50,
          desc="Closes the current address and opens a new one."),
    _pair("institutions.update_category", "Update Category", TS, 100,
          desc="May move the institution to another handling department — the confirm screen shows the "
               "transfer."),
    _pair("institutions.update_name", "Update TVET Name", TS, 100,
          desc="The old name is kept in former names; letters already issued are untouched."),
    _pair("institutions.delist", "Delist Institution", TS, 100, same_dept=False,
          desc="Status delisted; portal sign-in disabled; open requests frozen. Confirmed by the ES or delegate."),
    _pair("institutions.relist", "Relist Institution", TS, 100, same_dept=False,
          desc="delisted → approved; portal sign-in re-enabled. Confirmed by the ES or delegate."),
    _pair("institutions.update_ownership", "Update Ownership", TS, 100,
          desc="Re-checks which statutory documents are required; newly required ones become an outstanding "
               "request and the open application is held (qa.ownership_change.hold_open_cases)."),
    _pair("institutions.suspend_portal_access", "Suspend Portal Access", TS, 80,
          desc="portal_enabled = false without delisting."),
    _pair("institutions.restore_portal_access", "Restore Portal Access", TS, 80,
          initiate="institutions.initiate_suspend_portal_access",
          confirm="institutions.confirm_suspend_portal_access",
          desc="portal_enabled = true again."),
    _pair("institutions.send_login_credentials", "Send Login Credentials", TS, 0, second=False,
          initiate="institutions.send_login_credentials",
          desc="Activates the primary account and e-mails a one-time set-password link (never a password)."),
    _pair("institutions.impersonate", "Impersonate Institution", TS, 0, second=False,
          initiate="institutions.impersonate_institution",
          desc="Read-only, time-boxed view of the portal as the institution, with a banner and an audit trail."),
    _pair("institutions.bulk_import", "Bulk Import (Excel)", NI, 50,
          desc="Creates proposed institutions or updates existing ones / programmes from a validated sheet."),
    # Shown disabled until the phase that provides their records.
    _pair("institutions.add_programme_to_qa", "Add Programme to QA", TS, 50, enabled=False, desc=LATER),
    _pair("inspection.edit_qa_personnel", "Edit QA Personnel", TS, 100, enabled=False, desc=LATER),
    _pair("inspection.edit_qa_mapping", "Edit QA Mapping", TS, 100, enabled=False, desc=LATER),
    _pair("inspection.override_post_visit_personnel", "Override Post-Visit Personnel", TS, 80, enabled=False,
          desc=LATER),
    _pair("accreditation.send_back_to_audit", "Send Back To Audit", TS, 100, enabled=False, desc=LATER),
    # Payments (§7 list: waivers, refunds, bank accounts, cancel and reissue)
    _pair("payments.waive", "Waive Payment", FIN, 100, initiate="payments.initiate_waiver",
          confirm="payments.confirm_waiver",
          desc="No money moves. Finance officer proposes with a reason; the Director of Finance confirms."),
    _pair("payments.cancel_obligation", "Cancel Bill", FIN, 100,
          desc="Cancels an unpaid bill (cancel and reissue). Money that arrives later goes to refund."),
    _pair("payments.refund", "Approve Refund", FIN, 100, initiate="payments.initiate_refund",
          confirm="payments.confirm_refund",
          desc="Finance officer requests; the Director of Finance confirms; the payment reference is recorded."),
    _pair("payments.update_bank_account", "Change Bank Account", SET, 100, initiate="payments.view_obligation",
          confirm="payments.manage_bank_accounts",
          desc="Adds or changes the NBTE account printed on slips (dual control)."),
    _pair("payments.update_fee_schedule", "Change Fee Schedule", SET, 100, initiate="payments.view_obligation",
          confirm="payments.manage_fee_schedule",
          desc="Changes a fee schedule's formula key, due days, Remita service type or account (dual control)."),
]

# ---------------------------------------------------------------------------
# Role permissions added in Phase 2 (additive; merged with Phase 1's list).
# ---------------------------------------------------------------------------
_ROUTINE_CONFIRMS = [
    "institutions.confirm_add_tvet", "institutions.confirm_update_contact", "institutions.confirm_update_address",
    "institutions.confirm_update_state_lga", "institutions.confirm_drop_programmes",
    "institutions.confirm_cancel_institution_request", "institutions.confirm_bulk_import",
]
_ALL_INITIATES = [
    "institutions.initiate_add_tvet", "institutions.initiate_update_contact", "institutions.initiate_update_address",
    "institutions.initiate_update_state_lga", "institutions.initiate_update_category",
    "institutions.initiate_update_name", "institutions.initiate_update_ownership",
    "institutions.initiate_drop_programmes", "institutions.initiate_wipe_requests",
    "institutions.initiate_cancel_institution_request", "institutions.initiate_bulk_import",
    "institutions.export_institutions", "institutions.send_login_credentials",
]
_CLEARANCE = ["accreditation.view_onboarding_application", "accreditation.decide_departmental_clearance"]

ROLE_PERMISSIONS = {
    "executive-secretary": ["accreditation.view_onboarding_application", "payments.view_obligation",
                            "payments.view_payment_reports"],
    "director": _ROUTINE_CONFIRMS + _ALL_INITIATES + _CLEARANCE + [
        "institutions.confirm_wipe_requests", "institutions.confirm_revert_qa_history",
        "institutions.confirm_update_category", "institutions.confirm_update_name",
        "institutions.confirm_update_ownership", "institutions.confirm_suspend_portal_access",
        "institutions.initiate_relist", "institutions.initiate_approve_institution",
        "institutions.initiate_suspend_portal_access", "institutions.impersonate_institution",
    ],
    "deputy-director": _ROUTINE_CONFIRMS + _ALL_INITIATES + _CLEARANCE + [
        "institutions.confirm_suspend_portal_access", "institutions.impersonate_institution",
    ],
    "division-head": _ROUTINE_CONFIRMS + _ALL_INITIATES + _CLEARANCE + ["institutions.impersonate_institution"],
    "officer": _ALL_INITIATES + _CLEARANCE,
    # Finance handles every institution's payments, so its queue and bills
    # are not limited to one handling department.
    "qa-finance-officer": ["payments.initiate_cancel_obligation", "qa_core.view_all_institutions"],
    "qa-finance-director": ["payments.confirm_cancel_obligation", "payments.view_obligation", "qa_core.view_all_institutions",
                            "payments.view_proof", "payments.view_payment_reports"],
    "ict-qa-monitor": ["accreditation.view_onboarding_application", "payments.view_obligation"],
}

# ---------------------------------------------------------------------------
# Notification templates (code, channel, subject, body, description) + rules
# ---------------------------------------------------------------------------
TEMPLATES = [
    ("onboarding.submitted", "in_app", "New onboarding application: {{ institution }}",
     "{{ reference }} was submitted for departmental clearance. Your department's review is waiting.",
     "Tells clearance departments an application is waiting."),
    ("onboarding.returned", "in_app", "Your application was returned",
     "{{ department }} returned {{ reference }}: {{ remark }}. Update your documents and submit again.",
     "Tells the institution an application was returned."),
    ("onboarding.returned", "email", "NBTE returned your application {{ reference }}",
     "<p>{{ department }} returned application <strong>{{ reference }}</strong>.</p><p>Remark: {{ remark }}</p>"
     "<p>Sign in to the NBTE Quality Assurance Portal to update your documents and submit again.</p>",
     "E-mail to the institution when an application is returned."),
    ("onboarding.cleared", "in_app", "All departments cleared {{ reference }}",
     "Your institutional processing fee is now due. Open Payments to download the slip or pay online.",
     "Tells the institution clearance is complete."),
    ("payment.issued", "in_app", "New bill: {{ description }}",
     "{{ reference }} for {{ amount }} is due by {{ due }}. Download the slip or pay online from Payments.",
     "Tells institution users a bill was issued."),
    ("payment.issued", "email", "NBTE payment due: {{ reference }}",
     "<p>A bill has been issued: <strong>{{ description }}</strong>.</p><p>Reference: <strong>{{ reference }}"
     "</strong><br>Amount: {{ amount }}<br>Due: {{ due }}</p><p>Download the slip from the Payments page of the "
     "NBTE Quality Assurance Portal.</p>",
     "E-mail to institution users when a bill is issued."),
    ("payment.claimed", "in_app", "Payment to verify: {{ reference }}",
     "{{ institution }} — {{ source }}. First-pass check waiting in the Finance verification queue.",
     "Tells Finance officers a payment entered the queue."),
    ("payment.awaiting_final", "in_app", "Payment awaiting your confirmation: {{ reference }}",
     "First pass recorded by {{ verifier }} ({{ decision }}). Only the Director of Finance can confirm it.",
     "Tells the Director of Finance an item is ready."),
    ("payment.confirmed", "in_app", "Payment confirmed: {{ reference }}",
     "The Director of Finance confirmed your payment. Your Payment Advice {{ advice }} is ready to download.",
     "Tells institution users a payment was confirmed."),
    ("payment.confirmed", "email", "NBTE payment confirmed: {{ reference }}",
     "<p>Your payment for <strong>{{ reference }}</strong> has been confirmed by the Director of Finance.</p>"
     "<p>Payment Advice: <strong>{{ advice }}</strong> — download it from the Payments page.</p>",
     "E-mail to institution users on confirmation."),
    ("payment.rejected", "in_app", "Payment not confirmed: {{ reference }}",
     "Reason: {{ reason }}. You can pay again or upload a new proof.",
     "Tells institution users a payment was rejected."),
    ("payment.needs_info", "in_app", "Finance needs more information: {{ reference }}",
     "{{ reason }}", "Tells institution users Finance needs more information."),
    ("payment.overdue", "in_app", "Finance confirmation overdue: {{ reference }}",
     "{{ reference }} has waited longer than the confirmation window.",
     "Tells the Director of Finance a confirmation is overdue."),
    ("payment.reminder", "in_app", "Reminder: {{ reference }} awaiting confirmation",
     "{{ percent }}% of the Finance confirmation window has passed.",
     "SLA reminder to Finance at 50% and 90%."),
    ("payment.alert", "in_app", "Payment alert: {{ reference }}",
     "{{ message }}", "Alerts Finance and ICT (provider/verify mismatch, duplicate, payment after cancellation)."),
    ("institution.credentials", "email", "Your NBTE Quality Assurance Portal account",
     "<p>An account has been created for {{ institution }}.</p>", "Kept for reference; the link e-mail is a file."),
    ("institution.document_returned", "in_app", "Document returned: {{ document }}",
     "{{ department }}: {{ remark }}", "Tells the institution a document needs a new version."),
    ("institution.approved", "in_app", "{{ institution }} is approved",
     "NBTE has approved your institution. It is now handled by {{ department }}.",
     "Tells the institution it was approved."),
]

RULES = [  # event, recipient kind, template code, channel
    ("onboarding.submitted", "role", "onboarding.submitted", "in_app"),
    ("onboarding.returned", "institution_users", "onboarding.returned", "in_app"),
    ("onboarding.returned", "institution_users", "onboarding.returned", "email"),
    ("onboarding.cleared", "institution_users", "onboarding.cleared", "in_app"),
    ("payment.issued", "institution_users", "payment.issued", "in_app"),
    ("payment.issued", "institution_users", "payment.issued", "email"),
    ("payment.claimed", "role", "payment.claimed", "in_app"),
    ("payment.awaiting_final", "role", "payment.awaiting_final", "in_app"),
    ("payment.confirmed", "institution_users", "payment.confirmed", "in_app"),
    ("payment.confirmed", "institution_users", "payment.confirmed", "email"),
    ("payment.rejected", "institution_users", "payment.rejected", "in_app"),
    ("payment.needs_info", "institution_users", "payment.needs_info", "in_app"),
    ("payment.overdue", "role", "payment.overdue", "in_app"),
    ("payment.reminder", "role", "payment.reminder", "in_app"),
    ("payment.alert", "role", "payment.alert", "in_app"),
    ("institution.document_returned", "institution_users", "institution.document_returned", "in_app"),
    ("institution.approved", "institution_users", "institution.approved", "in_app"),
]

# ---------------------------------------------------------------------------
# Demo (--with-demo)
# ---------------------------------------------------------------------------
DEMO_INSTITUTION_PROFILE = {  # applied to the Phase 1 demo institution DMP
    "ownership": "private", "type": "polytechnic", "category": "polytechnic", "state": "Kaduna",
    "lga": "Kaduna North", "year_established": 2015, "cac_rc_number": "RC 1234567",
    "website": "https://demo-poly.example.ng",
}
DEMO_SECOND_INSTITUTION = {"code": "DCH", "name": "Demo College of Health Technology", "ownership": "state",
                           "type": "college-health", "category": "college-health", "department": "dnhtp",
                           "state": "Niger", "lga": "Minna", "status": "approved"}
DEMO_PROGRAMMES = [  # institution code, name, level, category, status, streams
    ("DMP", "Computer Science", "nd", "Computing & Information Technology", "accredited", 2),
    ("DMP", "Electrical/Electronic Engineering Technology", "hnd", "Engineering Technology", "accredited", 1),
    ("DCH", "Community Health", "nd", "Health Technology", "interim", 1),
]

DEMO_USERS = [
    # email, first, last, user_type, department QA code, role code, extra groups
    ("finance.director@demo.nbte.local", "Fatima", "Director of Finance (Demo)", "staff", "finance",
     "qa-finance-director", []),
    ("legal.officer@demo.nbte.local", "Garba", "Legal (Demo)", "staff", "legal", "officer", []),
    ("finance.clearance@demo.nbte.local", "Hauwa", "Finance Clearance (Demo)", "staff", "finance", "officer", []),
    ("planning.academic@demo.nbte.local", "Ifeanyi", "Academic Planning (Demo)", "staff", "academic-planning",
     "officer", []),
    ("planning.physical@demo.nbte.local", "Joy", "Physical Planning (Demo)", "staff", "physical-planning",
     "officer", []),
]

# A demo-only convenience group so the Phase 1 demo officer and director (DPAP)
# can follow a proposed institution (handled by NTIRD) through the walkthrough.
DEMO_WALKTHROUGH_GROUP = ("QA Demo Walkthrough",
                          "DEMO ONLY: see every institution and confirm across departments, so the demo "
                          "officer/director can follow the whole walkthrough. Removed by --reset-demo.",
                          ["qa_core.view_all_institutions", "qa_core.confirm_any_department"],
                          ["qa.officer@demo.nbte.local", "qa.director@demo.nbte.local"])

DEMO_BANK = {"bank_name": "Central Bank of Nigeria — TSA (DEMO)",
             "account_name": "National Board for Technical Education", "account_number": "1234567890",
             "sort_code": "", "is_active": True, "is_default": True, "is_placeholder": False}
