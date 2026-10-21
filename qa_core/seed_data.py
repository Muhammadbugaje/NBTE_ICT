"""Seed definitions for `seed_startup` (Master Plan §12, §15; PHASES Phase 1).

Everything is keyed by a natural key (code / name) and created only when
missing. Permission labels for apps that have no models yet (institutions,
payments, accreditation, inspection, complaints) are listed now so the roles
are complete on paper; the seed adds each one as soon as the phase that
declares it has migrated, and reports the rest as "deferred".
"""

# ---------------------------------------------------------------------------
# Roles (§12). code, name, rank, max_open_tasks, user_type, description
# ---------------------------------------------------------------------------
NEW_ROLES = [
    ("qa-finance-officer", "QA Finance Officer", 20, 6, "staff",
     "First-pass verification of QA payments, statement import, waiver/refund initiation."),
    ("qa-finance-director", "Director of Finance (QA)", 100, 50, "staff",
     "Final confirm/reject on every QA payment; confirms waivers, refunds and bank accounts."),
    ("qa-audit-officer", "QA Audit Officer", 20, 6, "staff",
     "Audits QA cost sheets."),
    ("cqac-member", "CQAC Member", 0, 10, "staff",
     "Reviews QA cases on the CQAC board."),
    ("cqac-chairman", "CQAC Chairman", 0, 20, "staff",
     "Chairman decisions, including batch decisions."),
    ("pay-officer", "Pay Officer", 20, 6, "staff",
     "Executes QA fund releases."),
    ("curriculum-officer", "Curriculum Officer", 20, 6, "staff",
     "Edits the curriculum catalogue and SSQ templates."),
    ("ict-qa-monitor", "ICT QA Monitor", 0, 5, "staff",
     "Read-only oversight of every QA case (R-13): no act, confirm or edit rights."),
    ("qa-settings-admin", "QA Settings Admin", 0, 5, "staff",
     "Edits QA rules, formulas, workflows drafts and templates. Cannot publish flows or confirm dual-control changes."),
    ("inst-chief-executive", "Institution Chief Executive", 0, 0, "institution",
     "Portal: submits SSQs for the institution."),
    ("inst-admin", "Institutional Administrator", 0, 0, "institution",
     "Portal: manages the institution's users and profile."),
    ("inst-academic-dept", "Institution Academic Department", 0, 0, "institution",
     "Portal: completes SSQ sections sent back to the department."),
    ("resource-person", "Resource Person", 0, 0, "resource_person",
     "Portal: views own engagements, submits findings, raises complaints."),
]

# QA permissions per role (additive; existing role baselines are untouched).
# qa_core.* exist in Phase 1; the rest are added as their phases ship.
ROLE_PERMISSIONS = {
    "executive-secretary": [
        "qa_core.view_all_institutions", "qa_core.confirm_any_department", "qa_core.reassign_qa_task",
        "qa_core.view_oversight_board", "qa_core.view_pending_actions",
        "accreditation.es_final_decide", "accreditation.batch_decide", "accreditation.approve_fund_release",
        "institutions.confirm_delist", "institutions.confirm_relist", "institutions.confirm_approve_institution",
    ],
    "director": [
        "qa_core.reassign_qa_task", "qa_core.place_suspension", "qa_core.lift_suspension",
        "qa_core.view_pending_actions", "qa_core.view_timeline_internal",
        # §12: publishing flows and confirming dual-control settings "need a Director".
        "qa_core.publish_flow_version", "qa_core.edit_qa_dual_control_settings",
        "accreditation.act_on_qa_stage", "institutions.initiate_delist", "institutions.initiate_revert_qa_history",
    ],
    "deputy-director": [
        "qa_core.reassign_qa_task", "qa_core.view_pending_actions",
        "institutions.confirm_wipe_requests", "institutions.confirm_revert_qa_history",
        "inspection.confirm_override_post_visit_personnel", "inspection.endorse_post_visit_report",
        "accreditation.act_on_qa_stage",
    ],
    "division-head": [
        "qa_core.reassign_qa_task", "qa_core.view_pending_actions",
        "institutions.confirm_update_contact", "institutions.confirm_update_address",
        "institutions.confirm_update_state_lga", "institutions.confirm_drop_programmes",
        "inspection.confirm_edit_qa_mapping", "inspection.confirm_edit_qa_personnel",
        "inspection.endorse_post_visit_report",
    ],
    "officer": [
        "institutions.send_login_credentials", "accreditation.raise_query",
        "inspection.submit_post_visit_report", "complaints.handle_complaint",
    ],
    "qa-finance-officer": [
        "payments.view_obligation", "payments.view_proof", "payments.verify_payment",
        "payments.import_bank_statement", "payments.manual_match_payment",
        "payments.initiate_waiver", "payments.initiate_refund",
    ],
    "qa-finance-director": [
        "qa_core.edit_qa_dual_control_settings", "qa_core.view_pending_actions",
        "payments.second_verify_payment", "payments.final_confirm_payment", "payments.confirm_waiver",
        "payments.confirm_refund", "payments.manage_fee_schedule", "payments.manage_bank_accounts",
    ],
    "qa-audit-officer": ["accreditation.audit_cost_sheet", "accreditation.view_qa_case"],
    "cqac-member": ["accreditation.cqac_review"],
    "cqac-chairman": ["accreditation.cqac_chair_decide", "accreditation.batch_decide"],
    "pay-officer": ["accreditation.release_funds"],
    "curriculum-officer": ["accreditation.manage_catalogue", "accreditation.manage_ssq_templates"],
    "ict-qa-monitor": [
        "qa_core.view_all_institutions", "qa_core.view_timeline_internal", "qa_core.view_pending_actions",
        "qa_core.view_oversight_board", "qa_core.contact_on_oversight_board", "accreditation.view_qa_case",
    ],
    "qa-settings-admin": [
        "qa_core.edit_qa_settings", "qa_core.manage_flows", "qa_core.manage_reference_formats",
        "qa_core.manage_notification_templates",
        "complaints.manage_complaint_layers", "complaints.manage_routing_rules",
        "complaints.manage_escalation_rules", "institutions.manage_document_requirements",
        "institutions.manage_institution_lookups", "accreditation.manage_approval_types",
    ],
}

# ---------------------------------------------------------------------------
# Groups (§12): name, description, permissions, linked role codes
# ---------------------------------------------------------------------------
GROUPS = [
    ("QA Desk", "Department QA desk officers: start QA actions (initiate_*) and view QA records.",
     ["qa_core.view_pending_actions"], []),
    ("QA Confirmers", "Second officers for routine QA changes (contact, address, programmes, mapping).",
     ["qa_core.view_pending_actions"], []),
    ("QA Senior Confirmers", "Second officers for delist, revert, wipe, category and ownership changes.",
     ["qa_core.view_pending_actions", "qa_core.confirm_any_department"], []),
    ("Finance – Payment Verification", "First-pass verification of QA payments and statement imports.",
     ["payments.view_obligation", "payments.view_proof", "payments.verify_payment",
      "payments.import_bank_statement", "payments.manual_match_payment"], ["qa-finance-officer"]),
    ("Finance – Approvals", "Director of Finance: final confirmation of payments, waivers, refunds and bank details.",
     ["qa_core.edit_qa_dual_control_settings", "qa_core.view_pending_actions",
      "payments.final_confirm_payment", "payments.confirm_waiver", "payments.confirm_refund"],
     ["qa-finance-director"]),
    ("Audit – QA Cost", "Audit of QA cost sheets.", ["accreditation.audit_cost_sheet"], ["qa-audit-officer"]),
    ("CQAC", "CQAC review board members and Chairman.",
     ["accreditation.cqac_review"], ["cqac-member", "cqac-chairman"]),
    ("ICT – QA Monitor", "Read-only QA oversight with reminder/escalation contact controls (R-13, §21).",
     ["qa_core.view_all_institutions", "qa_core.view_timeline_internal", "qa_core.view_pending_actions",
      "qa_core.view_oversight_board", "qa_core.contact_on_oversight_board"], ["ict-qa-monitor"]),
    ("QA Settings Admin", "Edit QA rules, formulas, reference formats (dual control) and notification templates.",
     ["qa_core.edit_qa_settings", "qa_core.manage_flows", "qa_core.manage_reference_formats",
      "qa_core.manage_notification_templates"], ["qa-settings-admin"]),
    ("Resource Person Vetting", "Nominate and vet resource persons.",
     ["inspection.manage_resource_person", "inspection.vet_resource_person"], []),
    ("Complaints Handlers", "Handle QA complaints in the department queue.",
     ["complaints.view_complaint_queue", "complaints.handle_complaint"], []),
    ("Complaints Admin", "Configure complaint layers, routing and escalation.",
     ["complaints.manage_complaint_layers", "complaints.manage_routing_rules",
      "complaints.manage_escalation_rules", "complaints.manage_knowledge_base"], []),
]

# ---------------------------------------------------------------------------
# Departments (§15). code, name, codes that already mean the same department
# (from seed_nbte's NBTE reference list) — reused instead of duplicated.
# ---------------------------------------------------------------------------
DEPARTMENTS = [
    ("dpap", "Polytechnic and Allied Programmes Department", []),
    ("dnhtp", "Nursing and Health Technology Programmes Department", ["nhspd"]),
    ("ntird", "New TVET Institutions Registration Department", []),
    ("vtsd", "Vocational and Technical Skills Department", []),
    ("finance", "Finance and Account Department", []),
    ("audit", "Internal Audit Department", ["int-aud-dept"]),
    ("legal", "Legal Unit", []),
    ("academic-planning", "Academic and Strategic Planning Department", ["aspd"]),
    ("physical-planning", "Physical Planning & Development Department", ["ppd"]),
    ("curriculum-office", "Curriculum Department", ["curr-dept"]),
    ("ict", "ICT", []),
    ("es-office", "Executive Secretary's Office", []),
]

# ---------------------------------------------------------------------------
# Reference formats (§10.5): code, name, pattern, reset, check, allowed, scope
# ---------------------------------------------------------------------------
PAYSTACK_SAFE = "A-Z0-9.=-"
REFERENCE_FORMATS = [
    ("application", "Application (SOP-01)", "NBTE/QA/{YEAR}/{INST}/{SEQ:5}", "yearly", "none", "", "institution"),
    ("qa_request", "QA request (SOP-02)", "NBTE/QA/{YEAR}/{INST}/{SEQ:5}", "yearly", "none", "", "institution"),
    ("payment", "Payment", "NBTE-QAP-{YEAR}-{INST}-{SEQ:6}-{CHK97}", "yearly", "mod97", PAYSTACK_SAFE, "global"),
    ("case", "Internal QA case", "NBTE/QAC/{YEAR}/{DEPT}/{SEQ:4}", "yearly", "none", "", "department"),
    ("inspection", "Inspection", "NBTE/INS/{YEAR}/{DEPT}/{SEQ:4}", "yearly", "none", "", "department"),
    ("complaint", "Complaint", "NBTE-CMP-{YEAR}-{SEQ:6}", "yearly", "none", PAYSTACK_SAFE, "global"),
    ("letter", "Outcome letter", "NBTE/QA/LTR/{YEAR}/{SEQ:5}", "yearly", "none", "", "global"),
    ("advice", "Payment advice", "NBTE-ADV-{YEAR}-{SEQ:6}", "yearly", "none", PAYSTACK_SAFE, "global"),
    ("pending_action", "Pending action", "PA-{YEAR}-{SEQ:6}", "yearly", "none", PAYSTACK_SAFE, "global"),
]

# ---------------------------------------------------------------------------
# Notification templates and rules (Phase 1 subset)
# ---------------------------------------------------------------------------
TEMPLATES = [
    ("pending_action.created", "in_app",
     "Confirmation needed: {{ action }}",
     "{{ initiator }} started {{ reference }} ({{ target }}). It needs a second officer before "
     "{{ expires_at }}.{% if reason %} Reason: {{ reason }}{% endif %}",
     "Tells eligible confirmers a change is waiting."),
    ("pending_action.created", "email",
     "Confirmation needed: {{ action }} ({{ reference }})",
     "<p><strong>{{ initiator }}</strong> started <strong>{{ reference }}</strong> — {{ action }} on "
     "<em>{{ target }}</em>.</p><p>A second officer must confirm or reject it before {{ expires_at }}."
     "</p>{% if reason %}<p>Reason given: {{ reason }}</p>{% endif %}"
     "<p>Open <em>Quality Assurance → Pending confirmations</em> in the NBTE system to decide.</p>",
     "Email digest to eligible confirmers."),
    ("pending_action.decided", "in_app",
     "{{ reference }}: {{ status }}",
     "{{ action }} on {{ target }} was {{ status|lower }}{% if decider %} by {{ decider }}{% endif %}."
     "{% if remark %} Remark: {{ remark }}{% endif %}{% if failure %} {{ failure }}{% endif %}",
     "Tells the initiator the outcome."),
    ("pending_action.expired", "in_app",
     "{{ reference }} expired",
     "No second officer decided {{ action }} on {{ target }} in time. Start it again if it is still needed.",
     "Tells the initiator an item expired."),
]

RULES = [
    ("pending_action.created", "eligible_confirmers", "pending_action.created", "in_app"),
    ("pending_action.created", "eligible_confirmers", "pending_action.created", "email"),
    ("pending_action.decided", "initiator", "pending_action.decided", "in_app"),
    ("pending_action.expired", "initiator", "pending_action.expired", "in_app"),
]

# ---------------------------------------------------------------------------
# Action policies (Phase 1: the two dual-control write paths)
# ---------------------------------------------------------------------------
ACTION_POLICIES = [
    {
        "action_code": "config.update_setting",
        "label": "Change a dual-control QA setting",
        "description": "Fee formulas, payment rules, impersonation limits and other keys flagged dual control.",
        "menu_group": "Settings",
        "requires_second_officer": True,
        "initiate_permission": "",   # the row's own editable_by decides (handler)
        "confirm_permission": "qa_core.edit_qa_dual_control_settings",
        "confirmer_min_rank": 50,
        "same_department_required": True,
    },
    {
        "action_code": "qa_core.update_reference_format",
        "label": "Change a reference format",
        "description": "Patterns apply to new references only; issued references never change.",
        "menu_group": "Settings",
        "requires_second_officer": True,
        "initiate_permission": "qa_core.manage_reference_formats",
        "confirm_permission": "qa_core.edit_qa_dual_control_settings",
        "confirmer_min_rank": 50,
        "same_department_required": True,
    },
]

# ---------------------------------------------------------------------------
# Flows (§5.7). Phase 1 seeds the payment flow (v1.1 vocabulary fixed now)
# and the pending-action flow. Each stage: code, label, public_label, kind,
# actor_kind, extras. Each transition: from, to, action, label, extras.
# ---------------------------------------------------------------------------
FINAL = {"required_permission": "payments.final_confirm_payment",
         "required_role_setting": "qa.payment.final_approver_role"}

PAYMENT_FLOW = {
    "code": "payment",
    "name": "Payment",
    "subject_model": "payments.PaymentObligation",
    "description": ("v1.1 (Amendment 1): a provider report is evidence only. Every payment, online or manual, "
                    "is confirmed or rejected by the Director of Finance through final_confirm / final_reject."),
    "stages": [
        ("issued", "Issued", "Bill issued", "start", "system", {}),
        ("awaiting_payment", "Awaiting payment", "Awaiting payment", "action", "institution",
         {"allows_cancel": True}),
        ("provider_processing", "Provider processing", "Payment in progress", "system", "system", {}),
        ("provider_reported_paid", "Provider reported paid (evidence only)",
         "Institution claims paid — awaiting Finance confirmation", "system", "system", {}),
        ("proof_submitted", "Proof submitted", "Institution claims paid — awaiting Finance confirmation",
         "system", "system", {}),
        ("awaiting_finance_confirmation", "Awaiting Finance confirmation",
         "Institution claims paid — awaiting Finance confirmation", "action", "finance_director",
         {"sla_hours": 24}),
        ("finance_confirmation_overdue", "Finance confirmation overdue", "Payment Pending Confirmation",
         "action", "finance_director", {}),
        ("confirmed", "Confirmed", "Payment confirmed", "end", "system", {"is_terminal_success": True}),
        ("rejected", "Rejected", "Payment rejected — see reason", "action", "institution", {}),
        ("partially_paid", "Partially paid", "Part payment received — balance due", "action", "institution", {}),
        ("overpaid", "Overpaid", "Payment confirmed — overpayment recorded", "action", "finance_director", {}),
        ("refund_requested", "Refund requested", "Refund in progress", "action", "finance_director", {}),
        ("refunded", "Refunded", "Refunded", "end", "system", {"is_terminal_success": True}),
        ("waived", "Waived", "Fee waived", "end", "system", {"is_terminal_success": True}),
        ("expired", "Expired", "Bill expired", "end", "system", {"is_terminal_failure": True}),
        ("cancelled", "Cancelled", "Bill cancelled", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        ("issued", "awaiting_payment", "publish_slip", "Publish slip", {}),
        ("awaiting_payment", "provider_processing", "pay_online", "Pay online", {}),
        ("provider_processing", "provider_reported_paid", "provider_report",
         "Provider reports paid (evidence)", {}),
        ("provider_processing", "awaiting_payment", "provider_failed", "Provider attempt failed", {}),
        ("awaiting_payment", "proof_submitted", "submit_proof", "I have paid (upload proof)", {}),
        ("rejected", "proof_submitted", "submit_proof", "Upload new proof", {}),
        ("provider_reported_paid", "awaiting_finance_confirmation", "enter_finance_queue",
         "Enter Finance queue", {}),
        ("proof_submitted", "awaiting_finance_confirmation", "enter_finance_queue", "Enter Finance queue", {}),
        ("awaiting_payment", "awaiting_finance_confirmation", "statement_match",
         "Bank statement pre-matched", {}),
        ("awaiting_finance_confirmation", "finance_confirmation_overdue", "confirmation_timeout",
         "Confirmation window passed", {}),
        ("awaiting_finance_confirmation", "confirmed", "final_confirm", "Confirm payment (Director of Finance)",
         FINAL),
        ("finance_confirmation_overdue", "confirmed", "final_confirm", "Confirm payment (Director of Finance)",
         FINAL),
        ("awaiting_finance_confirmation", "rejected", "final_reject", "Reject payment (Director of Finance)",
         {**FINAL, "requires_remark": True}),
        ("finance_confirmation_overdue", "rejected", "final_reject", "Reject payment (Director of Finance)",
         {**FINAL, "requires_remark": True}),
        ("awaiting_finance_confirmation", "partially_paid", "final_confirm_partial",
         "Confirm part payment (Director of Finance)", FINAL),
        ("partially_paid", "awaiting_payment", "pay_balance", "Pay the balance", {}),
        ("rejected", "awaiting_payment", "repay", "Pay again", {}),
        ("confirmed", "overpaid", "record_overpayment", "Record overpayment", {}),
        ("confirmed", "refund_requested", "request_refund", "Request refund",
         {"requires_confirmation": True, "requires_remark": True}),
        ("overpaid", "refund_requested", "request_refund", "Request refund",
         {"requires_confirmation": True, "requires_remark": True}),
        ("refund_requested", "refunded", "confirm_refund", "Confirm refund paid",
         {"required_permission": "payments.confirm_refund", "requires_confirmation": True}),
        ("awaiting_payment", "waived", "waive", "Waive fee",
         {"requires_confirmation": True, "requires_remark": True}),
        ("awaiting_payment", "expired", "expire", "Expire", {}),
        ("issued", "cancelled", "cancel", "Cancel bill", {"requires_confirmation": True, "requires_remark": True}),
        ("awaiting_payment", "cancelled", "cancel", "Cancel bill",
         {"requires_confirmation": True, "requires_remark": True}),
    ],
}

PENDING_ACTION_FLOW = {
    "code": "pending_action",
    "name": "Pending action (two-step confirmation)",
    "subject_model": "qa_core.PendingAction",
    "description": "pending → confirmed → applied | failed, or pending → rejected | withdrawn | expired (§5.7).",
    "stages": [
        ("pending", "Pending", "Waiting for a second officer", "start", "role_in_department", {}),
        ("confirmed", "Confirmed", "Confirmed", "system", "system", {}),
        ("applied", "Applied", "Applied", "end", "system", {"is_terminal_success": True}),
        ("failed", "Failed", "Could not be applied", "end", "system", {"is_terminal_failure": True}),
        ("rejected", "Rejected", "Rejected", "end", "system", {"is_terminal_failure": True}),
        ("withdrawn", "Withdrawn", "Withdrawn", "end", "system", {"is_terminal_failure": True}),
        ("expired", "Expired", "Expired", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        ("pending", "confirmed", "confirm", "Confirm", {}),
        ("pending", "rejected", "reject", "Reject", {"requires_remark": True}),
        ("pending", "withdrawn", "withdraw", "Withdraw", {}),
        ("pending", "expired", "expire", "Expire", {}),
        ("confirmed", "applied", "apply", "Apply", {}),
        ("confirmed", "failed", "fail", "Fail (stale or invalid)", {}),
    ],
}

FLOWS = [PAYMENT_FLOW, PENDING_ACTION_FLOW]

# ---------------------------------------------------------------------------
# Demo (--with-demo). Passwords are generated per run and printed once.
# ---------------------------------------------------------------------------
DEMO_INSTITUTION = {"code": "DMP", "name": "Demo Polytechnic, Kaduna", "department": "dpap",
                    "status": "approved"}

DEMO_USERS = [
    # email, first, last, user_type, department code, role code, extra groups
    ("qa.officer@demo.nbte.local", "Amina", "Officer (Demo)", "staff", "dpap", "officer", ["QA Settings Admin"]),
    ("qa.director@demo.nbte.local", "Bello", "Director (Demo)", "staff", "dpap", "director", []),
    ("qa.finance@demo.nbte.local", "Chioma", "Finance (Demo)", "staff", "finance", "qa-finance-officer", []),
    ("admin@demo-poly.local", "Danjuma", "Admin (Demo Poly)", "institution", None, "inst-admin", []),
    ("resource@demo.nbte.local", "Esther", "Resource (Demo)", "resource_person", None, "resource-person", []),
]
