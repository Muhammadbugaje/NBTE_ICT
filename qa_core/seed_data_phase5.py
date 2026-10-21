"""Phase 5 seed data (PHASES.md Phase 5; Master Plan §5.5, §5.7, §9, §14).

Data only: the ``inspection`` and ``post_visit_report`` flows, the qa_case
patch for stage 12 (its work is now the report sub-flow), the matching
settings, permissions, the three two-officer policies going live, the
notification templates and the demo people. ``seed_startup`` creates what is
missing and never overwrites an existing row (the stage-12 patch is an
explicit, idempotent data fix).
"""
from __future__ import annotations

TASK = {"creates_task": True, "task_category": "qa-sop03"}
SUBMIT = {"required_permission": "inspection.submit_post_visit_report"}
ENDORSE = {"required_permission": "inspection.endorse_post_visit_report"}
RET = {"required_permission": "inspection.endorse_post_visit_report", "requires_remark": True,
       "effects": ["report_returned"]}

# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------
INSPECTION_FLOW = {
    "code": "inspection",
    "name": "Inspection (visit)",
    "subject_model": "inspection.Inspection",
    "description": "scheduled → completed (the Team Lead records the visit and who attended) → closed (service "
                   "history written). A scheduled visit can be cancelled with a remark.",
    "stages": [
        ("scheduled", "Scheduled", "Visit scheduled", "start", "specific_user", {"form_key": "team_lead"}),
        ("completed", "Visit completed", "Visit carried out", "system", "system", {}),
        ("closed", "Closed", "Visit carried out", "end", "system", {"is_terminal_success": True}),
        ("cancelled", "Cancelled", "Visit cancelled", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        ("scheduled", "completed", "complete", "Mark the visit completed", {}),
        ("completed", "closed", "auto_close", "Close the inspection", {"effects": ["inspection_closed"]}),
        ("scheduled", "cancelled", "cancel", "Cancel the visit", {"requires_remark": True}),
    ],
}

REPORT_FLOW = {
    "code": "post_visit_report",
    "name": "Post-visit report (B14 endorsement)",
    "subject_model": "inspection.PostVisitReport",
    "description": "One report per programme. Written by someone who attended (B17), then endorsed by the "
                   "Division Head → Deputy Director → Director (B14). Any layer can add content or return it "
                   "with a remark; a return goes back to the person who submitted it.",
    "stages": [
        ("draft", "Write the post-visit report", "Report in preparation", "start", "specific_user",
         {**TASK, "form_key": "team_lead", "sla_hours": 120}),
        ("submitted", "Division Head endorsement", "Report under review by NBTE", "action", "role_in_department",
         {**TASK, "actor_role": "division-head", "actor_scope": "owning", "sla_hours": 48}),
        ("division_endorsed", "Deputy Director verification", "Report under review by NBTE", "action",
         "role_in_department", {**TASK, "actor_role": "deputy-director", "actor_scope": "owning", "sla_hours": 48}),
        ("deputy_verified", "Director endorsement", "Report under review by NBTE", "action",
         "owning_department_director", {**TASK, "actor_scope": "owning", "sla_hours": 48}),
        ("director_endorsed", "Endorsed by the Director", "Report endorsed", "end", "system",
         {"is_terminal_success": True}),
        ("returned", "Returned for correction", "Report in preparation", "action", "specific_user",
         {**TASK, "form_key": "submitted_by", "sla_hours": 72}),
    ],
    "transitions": [
        ("draft", "submitted", "submit", "Submit the report", {**SUBMIT, "effects": ["report_submitted"]}),
        ("submitted", "division_endorsed", "endorse", "Endorse (Division Head)", ENDORSE),
        ("submitted", "returned", "return", "Return with remark", RET),
        ("division_endorsed", "deputy_verified", "endorse", "Verify (Deputy Director)", ENDORSE),
        ("division_endorsed", "returned", "return", "Return with remark", RET),
        ("deputy_verified", "director_endorsed", "endorse", "Endorse (Director)",
         {**ENDORSE, "effects": ["report_director_endorsed"]}),
        ("deputy_verified", "returned", "return", "Return with remark", RET),
        ("returned", "submitted", "resubmit", "Re-submit the report", {**SUBMIT, "effects": ["report_submitted"]}),
    ],
}
FLOWS = [INSPECTION_FLOW, REPORT_FLOW]

# qa_case: stage 12 is now driven by the report sub-flow (no stage task of its
# own); it moves on by itself when every report is endorsed by the Director.
CASE_STAGE12_PATCH = {"stage": "post_visit_report", "creates_task": False}
CASE_STAGE12_TRANSITION = ("post_visit_report", "post_visit_recommendation", "reports_endorsed",
                           "All post-visit reports endorsed by the Director",
                           {"condition": "reports_total > 0 and reports_open == 0"})

# Settings: SETTINGS_PHASE5 in qa_core.settings_catalogue (§14).

# ---------------------------------------------------------------------------
# Permissions (additive)
# ---------------------------------------------------------------------------
_VIEW = ["inspection.view_resource_person", "inspection.view_service_history"]
ROLE_PERMISSIONS = {
    "executive-secretary": _VIEW + ["inspection.export_service_history"],
    "director": _VIEW + ["inspection.export_service_history", "inspection.manage_resource_person",
                         "inspection.vet_resource_person", "inspection.confirm_edit_qa_mapping",
                         "inspection.confirm_edit_qa_personnel", "inspection.initiate_override_post_visit_personnel",
                         "inspection.confirm_override_post_visit_personnel", "inspection.endorse_post_visit_report",
                         "inspection.submit_post_visit_report"],
    "deputy-director": _VIEW + ["inspection.export_service_history", "inspection.manage_resource_person",
                                "inspection.vet_resource_person", "inspection.endorse_post_visit_report",
                                "inspection.submit_post_visit_report",
                                "inspection.initiate_override_post_visit_personnel"],
    "division-head": _VIEW + ["inspection.manage_resource_person", "inspection.endorse_post_visit_report",
                              "inspection.submit_post_visit_report"],
    "officer": _VIEW + ["inspection.manage_resource_person", "inspection.initiate_edit_qa_mapping",
                        "inspection.initiate_edit_qa_personnel", "inspection.submit_post_visit_report"],
    "ict-qa-monitor": _VIEW,
}

# The three §6 two-officer actions that needed Phase 5 records.
POLICY_PATCHES = [
    {"action_code": "inspection.edit_qa_personnel",
     "description": "Registers or updates NBTE staff for QA duty (disciplines, verification officer, team lead "
                    "eligible). Eligibility (rank, designation) is checked; there is no override."},
    {"action_code": "inspection.edit_qa_mapping",
     "description": "Maps a resource person or QA officer to a programme of a case for a duty. Every §9 rule is "
                    "checked, and re-checked under a lock when confirmed (no double-booking)."},
    {"action_code": "inspection.override_post_visit_personnel",
     "description": "Records who actually attended a visit. Changes the service history (compensating rows), "
                    "never the plan."},
]

TEMPLATES = [
    ("rp.vetting_requested", "in_app", "Vet a resource person: {{ name }}",
     "{{ who }} nominated {{ name }}. Review the CV and approve or reject.", "To vetters."),
    ("rp.vetted", "in_app", "Resource person {{ outcome }}: {{ name }}",
     "{{ name }} was {{ outcome }} by {{ who }}.", "To the nominator."),
]
RULES = [
    ("rp.vetting_requested", "role", "rp.vetting_requested", "in_app"),
    ("rp.vetted", "role", "rp.vetted", "in_app"),
]

# ---------------------------------------------------------------------------
# Demo (seed_phase5_demo)
# key, email, first, last, role code, department, grade level, designation code
# ---------------------------------------------------------------------------
DEMO_ACCOUNTS = [
    ("team_lead", "qa.team_lead@demo.nbte.local", "Sadiq", "Team Lead (Demo)", "director", "dpap", "17",
     "director"),
    ("division_head", "qa.division_head@demo.nbte.local", "Tani", "Division Head (Demo)", "division-head", "dpap",
     "14", ""),
    ("deputy_director", "qa.deputy_director@demo.nbte.local", "Usman", "Deputy Director (Demo)", "deputy-director",
     "dpap", "16", "deputy-director"),
    ("qa_officer2", "qa.verifier@demo.nbte.local", "Victoria", "QA Verifier (Demo)", "director", "dpap", "17",
     "director"),
]
# Designations for the Phase 4 demo directors (so they qualify as QA personnel).
DEMO_DESIGNATIONS = {"director": "director", "audit_director": "director", "finance_director": "director",
                     "ict_director": "director"}

# key, full name, email, source, rank code, disciplines [(code, primary)], status, portal account?, notes
DEMO_RESOURCE_PERSONS = [
    ("rp_eng", "Engr. Prof. Yakubu Danjuma", "rp.yakubu@demo.nbte.local", "academia", "chief-lecturer",
     [("engineering", True), ("science-environmental", False)], "active", True, "Engineering assessor"),
    ("rp_sci", "Dr. Zainab Abdullahi", "rp.zainab@demo.nbte.local", "professional_body", "principal-lecturer",
     [("science-environmental", True)], "active", True, "Computing assessor"),
    ("rp_sci2", "Dr. Chukwuma Obi", "rp.obi@demo.nbte.local", "industry", "chief-lecturer",
     [("science-environmental", True)], "active", False, "Second computing assessor"),
    ("rp_nominated", "Mrs. Halima Sule", "rp.halima@demo.nbte.local", "academia", "principal-lecturer",
     [("engineering", True)], "nominated", False, "Awaiting vetting"),
    ("rp_coi", "Mr. Emeka Nwosu", "rp.emeka@demo.nbte.local", "academia", "chief-lecturer",
     [("engineering", True)], "active", False, "Works at the demo institution (conflict of interest)"),
    ("rp_lowrank", "Mr. Garba Lawal", "rp.garba@demo.nbte.local", "academia", "lecturer-i",
     [("engineering", True)], "active", False, "Rank no longer qualifies (badge)"),
    ("rp_health", "Dr. Ifeoma Eze", "rp.ifeoma@demo.nbte.local", "professional_body", "chief-lecturer",
     [("health", True)], "active", False, "Health discipline only"),
    ("rp_capped", "Prof. Bashir Musa", "rp.bashir@demo.nbte.local", "academia", "chief-lecturer",
     [("engineering", True)], "active", False, "Annual visit cap already reached (backfilled history)"),
]
