"""Phase 4 seed data (PHASES.md Phase 4; Master Plan §5.4, §5.7, §8, §14).

Everything here is *data*: the SOP-03 flow (17 stages, actor resolvers per
stage, SLA hours, task category), the allowance rate table, the QA task
categories, permissions and settings. ``seed_startup`` creates what is
missing and never overwrites an existing row.

The rates are PLACEHOLDERS (not NBTE's approved figures). Cost sheets show
"rates not confirmed" until Finance sets the real amounts in Settings → Rate
table and switches ``qa.cost.rates_confirmed`` on.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The SOP-03 flow. Stage tuple: code, label, public label, kind, actor kind, extras.
# extras may hold: sop_stage_no, actor_scope, actor_role (role code), form_key
# (for specific_user: the QACase field holding the user), sla_hours,
# creates_task, task_category (code).
# Every action stage has `approve` and `return` (remark required); a return
# goes back to the previous node and the bridge routes its new task to the
# exact person who submitted from there (B11, R-24).
# ---------------------------------------------------------------------------
TASK = {"creates_task": True, "task_category": "qa-sop03"}
PUBLIC = "Under review by NBTE"
REVIEW_STAGES = ("confirm_personnel", "audit_personnel_cost", "confirm_audit_cost", "confirm_visit_details",
                 "post_visit_recommendation")
OFFICER_STAGES = ("generate_cost", "add_personnel", "add_visit_details", "post_visit_report",
                  "audit_personnel_cost")

CASE_STAGES = [
    ("case_opened", "Case opened — choose the desk officer", PUBLIC, "start", "owning_department_director",
     {**TASK, "actor_scope": "owning", "sla_hours": 48}),
    ("assign_supervising_staff", "Assign supervising staff", PUBLIC, "parallel_gate", "system",
     {"sop_stage_no": 1}),
    ("assign_audit_officer", "Assign the supervising Audit officer", PUBLIC, "action", "audit_director",
     {**TASK, "sop_stage_no": 1, "actor_scope": "audit", "sla_hours": 48}),
    ("assign_finance_officer", "Assign the supervising Finance officer", PUBLIC, "action", "finance_director",
     {**TASK, "sop_stage_no": 1, "actor_scope": "finance", "sla_hours": 48}),
    ("assign_ict_officer", "Assign the tracking ICT officer(s)", PUBLIC, "action", "ict_director",
     {**TASK, "sop_stage_no": 1, "actor_scope": "ict", "sla_hours": 48}),
    ("supervisors_assigned", "Supervising staff assigned", PUBLIC, "join", "system", {"sop_stage_no": 1}),
    ("generate_cost", "Generate the cost sheet", PUBLIC, "action", "specific_user",
     {**TASK, "sop_stage_no": 2, "form_key": "desk_officer", "actor_scope": "owning", "sla_hours": 72}),
    ("add_personnel", "Add the personnel list", PUBLIC, "action", "specific_user",
     {**TASK, "sop_stage_no": 3, "form_key": "desk_officer", "actor_scope": "owning", "sla_hours": 72}),
    ("confirm_personnel", "Confirm the personnel list", PUBLIC, "action", "owning_department_director",
     {**TASK, "sop_stage_no": 4, "actor_scope": "owning", "sla_hours": 48}),
    ("audit_personnel_cost", "Audit the personnel cost", PUBLIC, "action", "specific_user",
     {**TASK, "sop_stage_no": 5, "form_key": "supervising_audit_officer", "actor_scope": "audit",
      "sla_hours": 72}),
    ("confirm_audit_cost", "Confirm the audited cost (Director of Audit)", PUBLIC, "action", "audit_director",
     {**TASK, "sop_stage_no": 6, "actor_scope": "audit", "sla_hours": 48}),
    ("es_approve_cost", "ES approval of the cost", PUBLIC, "action", "es",
     {**TASK, "sop_stage_no": 7, "actor_scope": "es-office", "sla_hours": 72}),
    ("add_visit_details", "Add the visit details", PUBLIC, "action", "specific_user",
     {**TASK, "sop_stage_no": 8, "form_key": "desk_officer", "actor_scope": "owning", "sla_hours": 72}),
    ("confirm_visit_details", "Confirm the visit details", PUBLIC, "action", "owning_department_director",
     {**TASK, "sop_stage_no": 9, "actor_scope": "owning", "sla_hours": 48}),
    ("es_approve_visit", "ES approval of the visit", PUBLIC, "action", "es",
     {**TASK, "sop_stage_no": 10, "actor_scope": "es-office", "sla_hours": 72}),
    ("request_fund_release", "Release the funds (Pay Officer)", "Visit being arranged", "action",
     "role_in_department",
     {**TASK, "sop_stage_no": 11, "actor_role": "pay-officer", "actor_scope": "finance", "sla_hours": 72}),
    # Phase 5: stage 12's work is the post-visit report sub-flow (one report per
    # programme, each with its own tasks), so the stage has no task of its own.
    ("post_visit_report", "Post-visit report", "Visit carried out — report in preparation", "action",
     "specific_user",
     {"sop_stage_no": 12, "form_key": "desk_officer", "actor_scope": "owning", "sla_hours": 120}),
    ("post_visit_recommendation", "Post-visit recommendation", "Report under review by NBTE", "action",
     "owning_department_director", {**TASK, "sop_stage_no": 13, "actor_scope": "owning", "sla_hours": 72}),
    ("cqac_recommendation", "CQAC recommendation", "With the Committee on Quality Assurance", "action", "cqac",
     {**TASK, "sop_stage_no": 14, "actor_role": "cqac-member", "sla_hours": 168}),
    ("cqac_chair_approval", "CQAC Chairman's approval", "With the Committee on Quality Assurance", "action",
     "cqac_chair", {**TASK, "sop_stage_no": 15, "actor_role": "cqac-chairman", "sla_hours": 120}),
    ("es_final_approval", "ES final approval", "Awaiting the Executive Secretary's decision", "action", "es",
     {**TASK, "sop_stage_no": 16, "actor_scope": "es-office", "sla_hours": 120}),
    ("programme_auto_update", "Programme record update", "Decision being recorded", "system", "system",
     {"sop_stage_no": 17}),
    ("closed", "SOP-03 complete", "Decision issued", "end", "system", {"is_terminal_success": True}),
]

# Linear order of the action stages after the gate: approve → next, return → previous.
LINEAR = ["generate_cost", "add_personnel", "confirm_personnel", "audit_personnel_cost", "confirm_audit_cost",
          "es_approve_cost", "add_visit_details", "confirm_visit_details", "es_approve_visit",
          "request_fund_release", "post_visit_report", "post_visit_recommendation", "cqac_recommendation",
          "cqac_chair_approval", "es_final_approval"]

APPROVE_LABELS = {
    "generate_cost": "Submit the cost sheet", "add_personnel": "Submit the personnel list",
    "confirm_personnel": "Confirm the personnel list", "audit_personnel_cost": "Send the audited cost on",
    "confirm_audit_cost": "Confirm the audited cost", "es_approve_cost": "Approve the cost",
    "add_visit_details": "Submit the visit details", "confirm_visit_details": "Confirm the visit details",
    "es_approve_visit": "Approve the visit", "request_fund_release": "Mark funds released",
    "post_visit_report": "Submit the post-visit report", "post_visit_recommendation": "Send the recommendation",
    "cqac_recommendation": "Send to the CQAC Chairman", "cqac_chair_approval": "Approve",
    "es_final_approval": "Give final approval",
}
EFFECTS = {
    "generate_cost": ["case_cost_submitted"], "add_personnel": ["case_personnel_submitted"],
    "confirm_personnel": ["case_personnel_confirmed"], "audit_personnel_cost": ["case_cost_audited"],
    "confirm_audit_cost": ["case_cost_audit_confirmed"], "es_approve_cost": ["case_cost_es_approved"],
    "add_visit_details": ["case_visit_submitted"], "confirm_visit_details": ["case_visit_confirmed"],
    "es_approve_visit": ["case_visit_es_approved"], "request_fund_release": ["case_funds_released"],
}
RET = {"requires_remark": True, "effects": ["case_returned"]}
ACT = {"required_permission": "accreditation.act_on_qa_stage"}


def _case_transitions():
    out = [
        ("case_opened", "assign_supervising_staff", "approve", "Send for supervising-staff assignment",
         {**ACT, "condition": "supervisors_ready == 0", "effects": ["case_opened"]}),
        ("case_opened", "generate_cost", "resubmit", "Send back to the desk officer (supervisors already chosen)",
         {**ACT, "condition": "supervisors_ready == 1", "effects": ["case_opened"]}),
        ("assign_supervising_staff", "assign_audit_officer", "branch_audit", "Audit", {}),
        ("assign_supervising_staff", "assign_finance_officer", "branch_finance", "Finance", {}),
        ("assign_supervising_staff", "assign_ict_officer", "branch_ict", "ICT", {}),
        ("assign_audit_officer", "supervisors_assigned", "approve", "Assign the Audit officer",
         {**ACT, "condition": "audit_officers >= 1"}),
        ("assign_finance_officer", "supervisors_assigned", "approve", "Assign the Finance officer",
         {**ACT, "condition": "finance_officers >= 1"}),
        # R-04: two ICT officers when the case has many programmes.
        ("assign_ict_officer", "supervisors_assigned", "approve", "Assign the ICT officer(s)",
         {**ACT, "condition": "ict_officers >= 2 or (ict_officers >= 1 and "
                              "programme_count < cfg('qa.assignment.second_ict_officer_threshold'))"}),
        ("assign_audit_officer", "case_opened", "return", "Return to the department", {**ACT, **RET}),
        ("assign_finance_officer", "case_opened", "return", "Return to the department", {**ACT, **RET}),
        ("assign_ict_officer", "case_opened", "return", "Return to the department", {**ACT, **RET}),
        ("supervisors_assigned", "generate_cost", "auto_generate_cost", "Supervising staff assigned", {}),
    ]
    previous = "case_opened"
    for i, code in enumerate(LINEAR):
        nxt = LINEAR[i + 1] if i + 1 < len(LINEAR) else "programme_auto_update"
        out.append((code, nxt, "approve", APPROVE_LABELS[code], {**ACT, "effects": EFFECTS.get(code, [])}))
        out.append((code, previous, "return", "Return with remark", {**ACT, **RET}))
        previous = code
    # Phase 5: stage 12 moves on by itself when the Director has endorsed every report.
    out.append(("post_visit_report", "post_visit_recommendation", "reports_endorsed",
                "All post-visit reports endorsed by the Director",
                {"condition": "reports_total > 0 and reports_open == 0"}))
    out.append(("programme_auto_update", "closed", "auto_close", "Close the SOP-03 case",
                {"effects": ["case_completed"]}))
    return out


CASE_FLOW = {
    "code": "qa_case",
    "name": "SOP-03 QA case (17 stages)",
    "subject_model": "accreditation.QACase",
    "description": "Stage 1 assigns supervising Audit, Finance and ICT officers in parallel (two ICT officers "
                   "when programme_count ≥ qa.assignment.second_ict_officer_threshold), then cost (2–7), visit "
                   "and funds (8–11), post-visit and decision (12–16) and the system update (17). Every action "
                   "stage has approve and return (remark required, back to the exact submitting node).",
    "stages": CASE_STAGES,
    "transitions": _case_transitions(),
}
FLOWS = [CASE_FLOW]

# ---------------------------------------------------------------------------
# Task categories (§8) and the system user that creates QA tasks
# ---------------------------------------------------------------------------
TASK_CATEGORIES = [  # code, name, description
    ("qa-sop03", "QA — SOP-03 case stage",
     "Created by the QA system for a case stage. Completed from QA (Open in QA), never from Tasks."),
]
SYSTEM_USER = ("qa-system@nbte.local", "QA", "System")

# ---------------------------------------------------------------------------
# Rate table (B12) — PLACEHOLDER amounts in naira
# code, role, allowance type, amount, unit
# ---------------------------------------------------------------------------
RATES = [
    ("dta-director", "Team lead (Director / Deputy Director)", "dta", 75000, "per_day"),
    ("dta-officer", "Team member (officer)", "dta", 50000, "per_day"),
    ("dta-support", "Support staff / driver", "dta", 30000, "per_day"),
    ("acad-allow-lead", "Academic allowance — team lead", "academic_allowance", 60000, "per_trip"),
    ("acad-allow-member", "Academic allowance — member", "academic_allowance", 40000, "per_trip"),
    ("honorarium-rp", "Resource person honorarium", "fee", 100000, "per_trip"),
    ("transport-road", "Road transport (return)", "transport", 45000, "per_trip"),
    ("transport-air", "Air ticket (return)", "transport", 250000, "per_trip"),
    ("audit-supervision", "Audit supervision", "other", 25000, "per_trip"),
]

# ---------------------------------------------------------------------------
# Permissions (additive)
# ---------------------------------------------------------------------------
_CASE = ["accreditation.view_qa_case", "accreditation.act_on_qa_stage"]
ROLE_PERMISSIONS = {
    "executive-secretary": _CASE + ["accreditation.approve_fund_release"],
    "director": _CASE + ["accreditation.edit_cost_sheet", "accreditation.audit_cost_sheet"],
    "deputy-director": _CASE + ["accreditation.edit_cost_sheet", "accreditation.audit_cost_sheet"],
    "division-head": _CASE + ["accreditation.edit_cost_sheet", "accreditation.audit_cost_sheet"],
    "officer": _CASE + ["accreditation.edit_cost_sheet", "accreditation.audit_cost_sheet"],
    "pay-officer": _CASE + ["accreditation.release_funds"],
    "qa-finance-officer": _CASE,
    "qa-finance-director": _CASE + ["accreditation.manage_rate_table"],
    "cqac-member": _CASE,
    "cqac-chairman": _CASE,
    "ict-qa-monitor": ["accreditation.view_qa_case", "qa_core.view_all_institutions"],
}
GROUP_PERMISSIONS = {
    "QA Settings Admin": ["accreditation.manage_rate_table"],
}

SEND_BACK_POLICY = {
    "action_code": "accreditation.send_back_to_audit",
    "description": "Moves a QA case at stage 7 or later back to stage 5 (Audit the personnel cost) with a "
                   "remark; the audit task is re-opened.",
}

# ---------------------------------------------------------------------------
# Notification templates (in-app) for SLA reminders and reassignment
# ---------------------------------------------------------------------------
TEMPLATES = [
    ("qa_stage.reminder", "in_app", "Reminder: {{ title }}",
     "{{ percent }}% of the time for this stage has passed (due {{ due }}).",
     "SLA reminder to the holder of a QA stage task."),
    ("qa_stage.breached", "in_app", "Overdue: {{ title }}",
     "The time for this stage ran out on {{ due }}.", "SLA breach to the holder and the department head."),
    ("qa_stage.needs_reassignment", "in_app", "Reassign: {{ title }}",
     "{{ who }} returned the task: {{ reason }}", "Tells department heads a QA task needs a new assignee."),
]
RULES = [
    ("qa_stage.reminder", "role", "qa_stage.reminder", "in_app"),
    ("qa_stage.breached", "role", "qa_stage.breached", "in_app"),
    ("qa_stage.needs_reassignment", "role", "qa_stage.needs_reassignment", "in_app"),
]

# ---------------------------------------------------------------------------
# Demo (seed_phase4_demo): accounts, departments and grade levels
# key, email, first, last, role code, department QA code, grade level
# ---------------------------------------------------------------------------
DEMO_ACCOUNTS = [
    ("desk_officer", "qa.officer@demo.nbte.local", "Amina", "Officer (Demo)", "officer", "dpap", "10"),
    ("director", "qa.director@demo.nbte.local", "Bello", "Director (Demo)", "director", "dpap", "17"),
    ("audit_director", "qa.audit_director@demo.nbte.local", "Chinedu", "Director of Audit (Demo)", "director",
     "audit", "17"),
    ("audit_officer", "qa.audit_officer@demo.nbte.local", "Danladi", "Audit Officer (Demo)", "officer", "audit",
     "10"),
    ("finance_director", "qa.finance_director@demo.nbte.local", "Fatima", "Director of Finance (Demo)",
     "director", "finance", "17"),
    ("finance_officer", "qa.finance_officer@demo.nbte.local", "Gambo", "Finance Officer (Demo)", "officer",
     "finance", "9"),
    ("ict_director", "qa.ict_director@demo.nbte.local", "Hauwa", "Director of ICT (Demo)", "director", "ict",
     "17"),
    ("ict_officer", "qa.ict_officer@demo.nbte.local", "Ibrahim", "ICT Officer (Demo)", "officer", "ict", "9"),
    ("ict_officer2", "qa.ict_officer2@demo.nbte.local", "Jamila", "ICT Officer 2 (Demo)", "officer", "ict", "8"),
    ("es", "qa.es@demo.nbte.local", "Kabiru", "Executive Secretary (Demo)", "executive-secretary", "es-office",
     "17"),
    ("pay_officer", "qa.pay_officer@demo.nbte.local", "Lami", "Pay Officer (Demo)", "pay-officer", "finance",
     "10"),
    ("cqac_member", "qa.cqac@demo.nbte.local", "Musa", "CQAC Member (Demo)", "cqac-member", "dpap", "16"),
    ("cqac_chair", "qa.cqac_chair@demo.nbte.local", "Ngozi", "CQAC Chairman (Demo)", "cqac-chairman", "dpap",
     "17"),
    ("ict_monitor", "qa.ict_monitor@demo.nbte.local", "Ojo", "ICT QA Monitor (Demo)", "ict-qa-monitor", "ict",
     "12"),
    ("busy_officer", "qa.busy_officer@demo.nbte.local", "Patience", "Busy Officer (Demo)", "officer", "dpap",
     "10"),
    ("institution_admin", "admin@p4demo.local", "Rakiya", "Admin (Phase 4 Demo Poly)", "inst-admin", None, ""),
]
DEMO_INSTITUTION = {"code": "KDP", "name": "Kaduna Demo Polytechnic (Phase 4)", "department": "dpap",
                    "state": "KADUNA", "lga": "Kaduna North"}
DEMO_PROGRAMMES = [  # name, level, category
    ("Computer Science", "nd", "Computing & Information Technology"),
    ("Electrical/Electronic Engineering Technology", "nd", "Engineering Technology"),
]
