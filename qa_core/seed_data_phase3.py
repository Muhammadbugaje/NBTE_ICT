"""Phase 3 seed data (PHASES.md Phase 3; Master Plan §5.4, §5.7, §12, §15).

Everything here is *data*: QA types, SSQ templates, the curriculum catalogue,
the SOP-02 and SSQ flows, permissions and notification templates. All of it
is editable afterwards (Settings → SSQ templates, Curriculum catalogue,
Workflows, Templates); ``seed_startup`` only creates what is missing.

The two SSQ templates are built from the field lists available to the
project (the Institutional Administration template and the D2 reaccreditation
fields). Their wording is a starting point for DPAP and the Curriculum Office
to refine in the builder — a published template is never edited in place, so
refinements become version 2.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# QA types (D1). code, name, description, statuses, window, new, admin-only
# ---------------------------------------------------------------------------
QA_TYPES = [
    ("resource_inspection", "Resource Inspection",
     "First inspection of a new (proposed) programme's resources before it may commence.",
     ["proposed"], False, True, False),
    ("accreditation", "Accreditation",
     "Full accreditation of a programme that has approval to commence or interim accreditation.",
     ["approval_to_commence", "resource_inspected", "interim"], False, False, False),
    ("reaccreditation", "Reaccreditation",
     "Renewal for accredited programmes whose accreditation expires within the reassessment window "
     "(qa.programme.reassessment_window_months) or has expired.",
     ["accredited", "interim", "expired"], True, False, False),
    ("validation", "Validation", "Validation visit for an accredited programme.", ["accredited"], False, False, False),
    ("verification", "Verification", "Verification of facts reported by an institution.",
     ["accredited", "interim", "approval_to_commence"], False, False, False),
    ("increase_capacity", "Increase in Carrying Capacity",
     "Request to admit more streams for an accredited programme.", ["accredited"], False, False, False),
    ("institutional_administration", "Institutional Administration",
     "The non-academic Institutional Administration 'programme' (Spec §6.3) on the same expiry cycle.",
     ["accredited", "interim", "expired", "approval_to_commence"], True, False, True),
]

INSTITUTIONAL_ADMIN_PROGRAMME = "Institutional Administration"


# ---------------------------------------------------------------------------
# SSQ templates
# ---------------------------------------------------------------------------
def _f(key, ftype, label, **extra):
    field = {"key": key, "type": ftype, "label": label, "required": extra.pop("required", False)}
    field.update(extra)
    return field


YES_NO = "yes_no"

INSTITUTIONAL_ADMIN_SSQ = {
    "intro": "Self-study questionnaire for the Institutional Administration programme. Fields marked "
             "'from your record' are filled from your NBTE record; check them and correct anything that "
             "has changed.",
    "sections": [
        {"key": "particulars", "title": "Institution particulars", "department": False,
         "description": "Basic facts about the institution.",
         "fields": [
             _f("institution_name", "text", "Name of institution", required=True, prefill_from="institution.name"),
             _f("ownership", "text", "Ownership", required=True, prefill_from="institution.ownership",
                width="half"),
             _f("year_established", "integer", "Year established", required=True,
                prefill_from="institution.year_established", width="half",
                validation={"min": 1900, "max": 2100}),
             _f("address", "textarea", "Permanent-site address", required=True, prefill_from="institution.address"),
             _f("state", "text", "State", prefill_from="institution.state", width="half"),
             _f("lga", "text", "Local government area", prefill_from="institution.lga", width="half"),
             _f("official_email", "email", "Official e-mail", required=True,
                prefill_from="institution.contacts.official_email", width="half"),
             _f("phone", "phone", "Telephone", prefill_from="institution.contacts.phone", width="half"),
             _f("website", "url", "Website", prefill_from="institution.website"),
         ]},
        {"key": "governance", "title": "Governance", "department": False,
         "description": "The Governing Council, Academic Board and principal officers.",
         "fields": [
             _f("council_constituted", YES_NO, "Is the Governing Council (or Board) constituted?", required=True,
                width="half"),
             _f("council_inaugurated", "date", "Date the current council was inaugurated", width="half"),
             _f("council_chair", "text", "Chairman of Council", required=True),
             _f("council_meetings", "integer", "Council meetings held in the last academic session",
                validation={"min": 0, "max": 50}, width="half"),
             _f("academic_board", YES_NO, "Is the Academic Board constituted?", required=True, width="half"),
             _f("council_members", "table", "Members of Council", min_rows=3, required=True,
                columns=[{"key": "name", "label": "Name", "type": "text"},
                         {"key": "designation", "label": "Designation", "type": "text"},
                         {"key": "representing", "label": "Representing", "type": "text"}]),
             _f("head_of_institution", "text", "Head of institution (Rector / Provost / Principal)", required=True,
                prefill_from="institution.contacts.head_of_institution.name"),
             _f("registrar", "text", "Registrar", required=True, prefill_from="institution.contacts.registrar.name",
                width="half"),
             _f("bursar", "text", "Bursar", prefill_from="institution.contacts.bursar.name", width="half"),
             _f("librarian", "text", "Librarian", prefill_from="institution.contacts.librarian.name"),
             _f("organogram", "file", "Organogram", required=True, document_kind="organogram",
                prefill_from="document.organogram",
                help="Re-use the organogram NBTE accepted, or upload a newer one."),
         ]},
        {"key": "statutory", "title": "Statutory documents", "department": False,
         "description": "Evidence of legal establishment and land title.",
         "fields": [
             _f("cac_certificate", "file", "CAC certificate (private institutions)", document_kind="cac",
                prefill_from="document.cac"),
             _f("enabling_law", "file", "Enabling law / edict (public institutions)", document_kind="enabling-law",
                prefill_from="document.enabling-law"),
             _f("land_title", "file", "Certificate of Occupancy / land title", required=True, document_kind="c-of-o",
                prefill_from="document.c-of-o"),
             _f("land_size", "number", "Land area (hectares)", required=True, validation={"min": 0},
                width="half"),
             _f("physical_master_plan", "file", "Physical master plan", document_kind="physical-master-plan",
                prefill_from="document.physical-master-plan"),
         ]},
        {"key": "academic_administration", "title": "Academic administration", "department": True,
         "description": "Schools, departments and students.",
         "fields": [
             _f("schools", "table", "Schools / faculties and departments", min_rows=1, required=True,
                columns=[{"key": "school", "label": "School", "type": "text"},
                         {"key": "department", "label": "Department", "type": "text"},
                         {"key": "head", "label": "Head of department", "type": "text"}]),
             _f("academic_calendar", YES_NO, "Is there an approved academic calendar for the current session?",
                required=True, width="half"),
             _f("total_students", "integer", "Total students enrolled (current session)", required=True,
                validation={"min": 0}, width="half"),
             _f("academic_master_plan", "file", "Academic master plan", document_kind="academic-master-plan",
                prefill_from="document.academic-master-plan"),
         ]},
        {"key": "staffing", "title": "Staffing", "department": True,
         "fields": [
             _f("academic_staff_total", "integer", "Academic staff (full-time)", required=True,
                validation={"min": 0}, width="half"),
             _f("non_academic_staff_total", "integer", "Non-academic staff", required=True,
                validation={"min": 0}, width="half"),
             _f("principal_officers", "table", "Principal officers", min_rows=1,
                columns=[{"key": "name", "label": "Name", "type": "text"},
                         {"key": "post", "label": "Post", "type": "text"},
                         {"key": "qualification", "label": "Highest qualification", "type": "text"},
                         {"key": "appointed", "label": "Date appointed", "type": "date"}]),
             _f("staff_development", "textarea", "Staff development and training in the last two years"),
         ]},
        {"key": "finance", "title": "Finance", "department": False,
         "fields": [
             _f("annual_budget", "number", "Approved budget for the current year (₦)", required=True,
                validation={"min": 0}, width="half"),
             _f("funding_sources", "multi_choice", "Sources of funding", required=True,
                options=["Government subvention", "Proprietor", "Internally generated revenue", "Grants and donors",
                         "Other"]),
             _f("audited_accounts", YES_NO, "Are the last year's accounts audited?", required=True, width="half"),
             _f("audited_accounts_file", "file", "Audited accounts (last financial year)"),
         ]},
        {"key": "facilities", "title": "Library, ICT and student services", "department": True,
         "fields": [
             _f("library_seats", "integer", "Library seating capacity", required=True, validation={"min": 0},
                width="half"),
             _f("library_volumes", "integer", "Library volumes (books)", required=True, validation={"min": 0},
                width="half"),
             _f("ict_facilities", "textarea", "ICT facilities (internet, computer centre, MIS)"),
             _f("hostel", YES_NO, "Student hostel accommodation", width="half"),
             _f("health_centre", YES_NO, "Health centre / clinic", width="half"),
             _f("counselling", YES_NO, "Guidance and counselling unit", width="half"),
         ]},
    ],
}

REACCREDITATION_SSQ = {
    "intro": "Self-study questionnaire for programme reaccreditation (D2). One questionnaire per programme.",
    "sections": [
        {"key": "programme", "title": "Programme particulars", "department": True,
         "fields": [
             _f("programme_name", "text", "Programme", required=True, prefill_from="programme.name"),
             _f("programme_level", "text", "Level", prefill_from="programme.level", width="half"),
             _f("department_name", "text", "Department offering the programme", required=True, width="half"),
             _f("head_of_department", "text", "Head of department", required=True),
             _f("last_accredited", "date", "Commencement of current accreditation",
                prefill_from="programme.commencement_date", width="half"),
             _f("expiry_date", "date", "Accreditation expires", prefill_from="programme.expiry_date",
                width="half"),
             _f("approved_streams", "integer", "Approved streams", prefill_from="programme.approved_streams",
                width="half", validation={"min": 0, "max": 20}),
             _f("running_streams", "integer", "Streams currently running", required=True, width="half",
                validation={"min": 0, "max": 20}),
         ]},
        {"key": "students", "title": "Students", "department": True,
         "fields": [
             _f("enrolment", "table", "Enrolment for the last three sessions", required=True, min_rows=1,
                columns=[{"key": "session", "label": "Session", "type": "text"},
                         {"key": "year_one", "label": "Year I", "type": "integer"},
                         {"key": "year_two", "label": "Year II", "type": "integer"}]),
             _f("graduates_last_3", "integer", "Graduates in the last three years", required=True,
                validation={"min": 0}, width="half"),
             _f("siwes", "textarea", "SIWES / industrial attachment arrangements", required=True),
         ]},
        {"key": "staff", "title": "Academic and technical staff", "department": True,
         "fields": [
             _f("academic_staff", "table", "Academic staff", required=True, min_rows=2,
                columns=[{"key": "name", "label": "Name", "type": "text"},
                         {"key": "qualification", "label": "Qualifications", "type": "text"},
                         {"key": "rank", "label": "Rank", "type": "text"},
                         {"key": "status", "label": "Status", "type": "choice", "options": ["Full-time", "Part-time"]}]),
             _f("technologists", "integer", "Technologists", validation={"min": 0}, width="half"),
             _f("technicians", "integer", "Technicians / instructors", validation={"min": 0}, width="half"),
         ]},
        {"key": "curriculum", "title": "Curriculum", "department": True,
         "fields": [
             _f("nbte_curriculum", YES_NO, "Is the current NBTE curriculum in use?", required=True, width="half"),
             _f("curriculum_year", "integer", "Year of the curriculum edition", validation={"min": 1990, "max": 2100},
                width="half"),
             _f("deviations", "textarea", "Deviations from the curriculum, and why"),
         ]},
        {"key": "facilities", "title": "Facilities and equipment", "department": True,
         "description": "Quantities against the curriculum catalogue. The items come from NBTE's catalogue; "
                        "enter what you have.",
         "fields": [
             _f("classrooms", "integer", "Classrooms available to the programme", required=True,
                validation={"min": 0}, width="half"),
             _f("facilities", "catalogue", "Facilities (laboratories, workshops, studios)",
                catalogue={"kind": "facility"}),
             _f("equipment", "catalogue", "Equipment", catalogue={"kind": "equipment"}),
         ]},
        {"key": "library", "title": "Library", "department": True,
         "fields": [
             _f("titles", "integer", "Programme-specific titles", required=True, validation={"min": 0},
                width="half"),
             _f("journals", "integer", "Journals (print or electronic)", validation={"min": 0}, width="half"),
         ]},
        {"key": "previous_visit", "title": "Previous visit", "department": True,
         "fields": [
             _f("weaknesses_addressed", "textarea",
                "How were the weaknesses noted at the last accreditation visit addressed?", required=True),
             _f("corrective_evidence", "file", "Evidence of corrective action (optional)"),
         ]},
    ],
}

SSQ_TEMPLATES = [  # qa type code, name, schema
    ("institutional_administration", "Institutional Administration SSQ", INSTITUTIONAL_ADMIN_SSQ),
    ("reaccreditation", "Reaccreditation SSQ (D2)", REACCREDITATION_SSQ),
]

# ---------------------------------------------------------------------------
# Curriculum catalogue (B3/D3): a starter for two programmes
# programme name, programme category, kind, name, required quantity, unit
# ---------------------------------------------------------------------------
CATALOGUE = [
    ("Computer Science", "Computing & Information Technology", "facility", "Computer laboratory (40 seats)", 2, "no."),
    ("Computer Science", "Computing & Information Technology", "facility", "Hardware maintenance workshop", 1, "no."),
    ("Computer Science", "Computing & Information Technology", "facility", "Lecture room (60 seats)", 3, "no."),
    ("Computer Science", "Computing & Information Technology", "equipment", "Desktop computer", 40, "no."),
    ("Computer Science", "Computing & Information Technology", "equipment", "Network switch (24-port)", 4, "no."),
    ("Computer Science", "Computing & Information Technology", "equipment", "Multimedia projector", 3, "no."),
    ("Computer Science", "Computing & Information Technology", "equipment", "UPS (1 kVA)", 40, "no."),
    ("Computer Science", "Computing & Information Technology", "equipment", "Laser printer", 2, "no."),
    ("Electrical/Electronic Engineering Technology", "Engineering Technology", "facility",
     "Electrical machines laboratory", 1, "no."),
    ("Electrical/Electronic Engineering Technology", "Engineering Technology", "facility",
     "Electronics laboratory", 1, "no."),
    ("Electrical/Electronic Engineering Technology", "Engineering Technology", "facility",
     "Electrical installation workshop", 1, "no."),
    ("Electrical/Electronic Engineering Technology", "Engineering Technology", "equipment",
     "Digital oscilloscope", 10, "no."),
    ("Electrical/Electronic Engineering Technology", "Engineering Technology", "equipment",
     "Function / signal generator", 10, "no."),
    ("Electrical/Electronic Engineering Technology", "Engineering Technology", "equipment",
     "Digital multimeter", 20, "no."),
    ("Electrical/Electronic Engineering Technology", "Engineering Technology", "equipment",
     "DC motor-generator training set", 2, "sets"),
    ("Electrical/Electronic Engineering Technology", "Engineering Technology", "equipment",
     "Single-phase transformer trainer", 4, "no."),
]

# ---------------------------------------------------------------------------
# Flows (§5.7) — stage: code, label, public label, kind, actor kind, extras
# ---------------------------------------------------------------------------
R25 = ("draft", "submitted", "fee_due", "payment_pending_confirmation")  # B6 / R-25
CLEAR_PROG = {"required_permission": "accreditation.clear_qa_programmes"}

QA_REQUEST_FLOW = {
    "code": "qa_request",
    "name": "QA request (SOP-02)",
    "subject_model": "accreditation.QARequest",
    "description": "draft → submitted → [new programmes → programme clearance] → fee due → payment awaiting "
                   "Finance → fee confirmed → SSQ in progress → SSQ submitted (QA case created) → in workflow → "
                   "decided. Cancel, drop and replace exist only on draft, submitted, fee due and payment "
                   "pending confirmation (B6/R-25).",
    "stages": [
        ("draft", "Draft", "Draft — choose your programmes, then submit", "start", "institution", {}),
        ("submitted", "Submitted", "Submitted to NBTE", "system", "system", {}),
        ("programme_clearance", "Programme clearance", "New programmes under review by NBTE", "action",
         "role_in_department", {"actor_scope": "ntird", "sla_hours": 120}),
        ("fee_due", "QA fee due", "QA processing fee due", "action", "institution", {}),
        ("payment_pending_confirmation", "Payment awaiting Finance confirmation",
         "Institution claims paid — awaiting Finance confirmation", "action", "finance_director", {}),
        ("fee_confirmed", "Fee confirmed", "Fee confirmed", "system", "system", {}),
        ("ssq_in_progress", "SSQ in progress", "Complete your self-study questionnaires (SSQ)", "action",
         "institution", {}),
        ("ssq_submitted", "SSQ submitted", "Questionnaires submitted to NBTE", "system", "system", {}),
        ("in_workflow", "In the QA workflow", "Under review by NBTE", "action", "role_in_department",
         {"actor_scope": "owning"}),
        ("decided", "Decided", "Decision issued", "end", "system", {"is_terminal_success": True}),
        ("cancelled", "Cancelled", "Request cancelled", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": (
        [("draft", "submitted", "submit", "Submit request", {"effects": ["qa_request_submitted"]}),
         ("submitted", "programme_clearance", "route_to_clearance", "New programmes: programme clearance",
          {"effects": ["qa_request_clearance_opened"]}),
         ("submitted", "fee_due", "route_to_fee", "Issue the QA fee", {"effects": ["issue_qa_request_fee"]}),
         ("programme_clearance", "fee_due", "clear_programmes", "Clear programmes",
          {**CLEAR_PROG, "effects": ["issue_qa_request_fee"]}),
         ("programme_clearance", "draft", "return", "Return to the institution",
          {**CLEAR_PROG, "requires_remark": True, "effects": ["qa_request_returned"]}),
         ("fee_due", "payment_pending_confirmation", "payment_claimed", "Payment claimed", {}),
         ("payment_pending_confirmation", "fee_due", "payment_rejected", "Payment rejected", {}),
         ("payment_pending_confirmation", "fee_confirmed", "payment_confirmed", "Payment confirmed", {}),
         ("fee_due", "fee_confirmed", "payment_confirmed", "Payment confirmed (waived/matched)", {}),
         ("fee_confirmed", "ssq_in_progress", "auto_open_ssq", "Open the SSQs", {"effects": ["open_ssqs"]}),
         ("ssq_in_progress", "ssq_submitted", "submit_ssqs", "All SSQs submitted", {}),
         ("ssq_submitted", "in_workflow", "auto_open_case", "Create the QA case", {"effects": ["spawn_qa_case"]}),
         ("in_workflow", "decided", "decide", "Record the decision",
          {"required_permission": "accreditation.es_final_decide"})]
        + [(stage, "cancelled", "cancel", "Cancel request", {"requires_remark": True,
                                                            "effects": ["qa_request_cancelled"]}) for stage in R25]
        + [(stage, stage, "drop_programme", "Drop a programme", {"requires_remark": True}) for stage in R25]
        + [(stage, stage, "replace_programme", "Replace a programme", {"requires_remark": True}) for stage in R25]
        + [(stage, stage, "add_programme", "Add a programme", {}) for stage in ("draft", "submitted", "fee_due")]
    ),
}

REVIEW = {"required_permission": "accreditation.review_ssq"}

SSQ_FLOW = {
    "code": "ssq",
    "name": "Self-study questionnaire (SSQ)",
    "subject_model": "accreditation.SSQ",
    "description": "draft ⇄ with the academic department → submitted to NBTE → accepted | returned. A returned "
                   "SSQ is superseded by a new version (Requirement 16); it is never edited.",
    "stages": [
        ("draft", "Draft", "In progress", "start", "institution", {}),
        ("returned_to_department", "With the academic department", "With your academic department", "action",
         "institution", {}),
        ("submitted_by_department", "Completed by the department", "Completed by your department — ready to submit",
         "action", "institution", {}),
        ("submitted", "Submitted", "Submitted to NBTE", "action", "role_in_department",
         {"actor_scope": "owning", "sla_hours": 240}),
        ("returned", "Returned", "Returned by NBTE — revise and resubmit", "end", "institution", {}),
        ("accepted", "Accepted", "Accepted by NBTE", "action", "role_in_department", {"actor_scope": "owning"}),
        ("analysed", "Analysed", "Analysed", "end", "system", {"is_terminal_success": True}),
    ],
    "transitions": [
        ("draft", "returned_to_department", "return_to_department", "Send sections to the academic department",
         {"requires_remark": True}),
        ("submitted_by_department", "returned_to_department", "return_to_department",
         "Send back to the academic department", {"requires_remark": True}),
        ("returned_to_department", "draft", "recall", "Take back from the department", {}),
        ("returned_to_department", "submitted_by_department", "department_submit", "Send to the institution", {}),
        ("draft", "submitted", "submit", "Submit to NBTE", {"effects": ["ssq_submitted"]}),
        ("submitted_by_department", "submitted", "submit", "Submit to NBTE", {"effects": ["ssq_submitted"]}),
        ("submitted", "accepted", "accept", "Accept", {**REVIEW, "effects": ["ssq_accepted"]}),
        ("submitted", "returned", "return", "Return to the institution",
         {**REVIEW, "requires_remark": True, "effects": ["ssq_returned"]}),
        ("accepted", "analysed", "analyse", "Analysed", {"required_permission": "accreditation.act_on_qa_stage"}),
    ],
}

FLOWS = [QA_REQUEST_FLOW, SSQ_FLOW]

# ---------------------------------------------------------------------------
# Permissions per role (additive)
# ---------------------------------------------------------------------------
_REVIEW = ["accreditation.view_qa_case", "accreditation.raise_query", "accreditation.review_ssq"]
ROLE_PERMISSIONS = {
    "executive-secretary": ["accreditation.view_qa_case"],
    "director": _REVIEW + ["accreditation.clear_qa_programmes", "accreditation.publish_ssq_template",
                           "accreditation.manage_ssq_templates", "accreditation.manage_catalogue",
                           "institutions.initiate_add_programme_to_qa", "institutions.confirm_add_programme_to_qa"],
    "deputy-director": _REVIEW + ["accreditation.clear_qa_programmes",
                                  "institutions.confirm_add_programme_to_qa"],
    "division-head": _REVIEW + ["accreditation.clear_qa_programmes", "institutions.confirm_add_programme_to_qa"],
    "officer": _REVIEW + ["institutions.initiate_add_programme_to_qa"],
    "curriculum-officer": ["accreditation.manage_catalogue", "accreditation.manage_ssq_templates",
                           "accreditation.view_qa_case"],
    "ict-qa-monitor": ["accreditation.view_qa_case"],
    "qa-audit-officer": ["accreditation.view_qa_case"],
}
GROUP_PERMISSIONS = {  # group name → permissions added (additive; never removed)
    "QA Settings Admin": ["accreditation.manage_ssq_templates", "accreditation.manage_catalogue"],
}

ADD_PROGRAMME_POLICY = {
    "action_code": "institutions.add_programme_to_qa",
    "description": "Adds one of the institution's programmes to an open QA request (draft, submitted or fee "
                   "due). A bill not yet paid is re-issued for the new programme count.",
}

# ---------------------------------------------------------------------------
# Notification templates and rules
# ---------------------------------------------------------------------------
TEMPLATES = [
    ("qa_request.submitted", "in_app", "QA request {{ reference }}: {{ institution }}",
     "{{ institution }} submitted a {{ qa_type }} request for {{ programmes }} programme(s).",
     "Tells the handling department a QA request was submitted."),
    ("qa_request.clearance", "in_app", "New programmes to clear: {{ reference }}",
     "{{ institution }} included new programme(s) in {{ reference }}. Clear or return them.",
     "Tells the clearing department new programmes are waiting."),
    ("qa_request.returned", "in_app", "Your QA request {{ reference }} was returned",
     "{{ remark }}", "Tells the institution a request was returned at programme clearance."),
    ("qa_request.ssq_open", "in_app", "Complete your SSQ for {{ reference }}",
     "Your fee is confirmed. Open QA requests → {{ reference }} to complete the self-study questionnaires.",
     "Tells the institution its SSQs are open."),
    ("qa_request.ssq_open", "email", "NBTE: complete your SSQ for {{ reference }}",
     "<p>Your fee for <strong>{{ reference }}</strong> is confirmed.</p><p>Sign in to the NBTE Quality Assurance "
     "Portal and open <em>QA requests</em> to complete the self-study questionnaires.</p>",
     "E-mail when SSQs open."),
    ("qa_request.cancelled", "in_app", "QA request {{ reference }} cancelled", "{{ remark }}",
     "Tells the institution and department a request was cancelled."),
    ("ssq.department", "in_app", "SSQ sections sent to you: {{ programme }}",
     "{{ sender }} asked you to complete: {{ sections }}. {{ remark }}",
     "Tells an academic-department sub-account it has SSQ sections to complete."),
    ("ssq.department_done", "in_app", "Your department completed the SSQ for {{ programme }}",
     "{{ sender }} sent the SSQ back. Review it and submit it to NBTE.",
     "Tells the institution administrator the department finished."),
    ("ssq.submitted", "in_app", "SSQ submitted: {{ institution }} — {{ programme }}",
     "Version {{ version }} of the {{ programme }} SSQ ({{ reference }}) is waiting for review.",
     "Tells the handling department an SSQ is waiting."),
    ("ssq.returned", "in_app", "NBTE returned the SSQ for {{ programme }}",
     "{{ remark }} Open the SSQ to revise it and submit again.", "Tells the institution an SSQ was returned."),
    ("ssq.returned", "email", "NBTE returned your SSQ for {{ programme }} ({{ reference }})",
     "<p>NBTE returned the self-study questionnaire for <strong>{{ programme }}</strong>.</p><p>{{ remark }}</p>"
     "<p>Sign in to the QA Portal to revise it and submit again.</p>", "E-mail when an SSQ is returned."),
    ("ssq.accepted", "in_app", "NBTE accepted the SSQ for {{ programme }}", "{{ reference }}: accepted.",
     "Tells the institution an SSQ was accepted."),
    ("query.raised", "in_app", "NBTE query {{ query }}: {{ title }}",
     "{{ body }}{% if due %} Please respond by {{ due }}.{% endif %}", "Tells the institution about a query."),
    ("query.raised", "email", "NBTE query {{ query }}: {{ title }}",
     "<p>NBTE has a question about {{ subject }}:</p><blockquote>{{ body }}</blockquote>"
     "{% if due %}<p>Please respond by {{ due }}.</p>{% endif %}<p>Sign in to the QA Portal → Queries to "
     "respond.</p>", "E-mail when a query is raised."),
    ("query.answered", "in_app", "{{ institution }} answered {{ query }}", "{{ body }}",
     "Tells the officer who raised a query that it was answered."),
]

RULES = [
    ("qa_request.submitted", "role", "qa_request.submitted", "in_app"),
    ("qa_request.clearance", "role", "qa_request.clearance", "in_app"),
    ("qa_request.returned", "institution_users", "qa_request.returned", "in_app"),
    ("qa_request.ssq_open", "institution_users", "qa_request.ssq_open", "in_app"),
    ("qa_request.ssq_open", "institution_users", "qa_request.ssq_open", "email"),
    ("qa_request.cancelled", "institution_users", "qa_request.cancelled", "in_app"),
    ("qa_request.cancelled", "role", "qa_request.cancelled", "in_app"),
    ("ssq.department", "role", "ssq.department", "in_app"),
    ("ssq.department_done", "role", "ssq.department_done", "in_app"),
    ("ssq.submitted", "role", "ssq.submitted", "in_app"),
    ("ssq.returned", "institution_users", "ssq.returned", "in_app"),
    ("ssq.returned", "institution_users", "ssq.returned", "email"),
    ("ssq.accepted", "institution_users", "ssq.accepted", "in_app"),
    ("query.raised", "institution_users", "query.raised", "in_app"),
    ("query.raised", "institution_users", "query.raised", "email"),
    ("query.answered", "role", "query.answered", "in_app"),
]

REFERENCE_FORMATS = [
    ("query", "Query to an institution", "NBTE-QRY-{YEAR}-{SEQ:5}", "yearly", "none", "", "global"),
]
# The Phase 1 default for QA requests repeated the onboarding pattern, so the
# first request and the first application of an institution printed the same
# reference. A never-used format is switched to this one (data fix).
QA_REQUEST_PATTERN_OLD = "NBTE/QA/{YEAR}/{INST}/{SEQ:5}"
QA_REQUEST_PATTERN_NEW = "NBTE/QAR/{YEAR}/{INST}/{SEQ:5}"

# ---------------------------------------------------------------------------
# Demo additions (--with-demo)
# ---------------------------------------------------------------------------
DEMO_ACADEMIC_USER = ("academic@demo-poly.local", "Bala", "Academic Dept (Demo Poly)", "institution", None,
                      "inst-academic-dept", [])
DEMO_REVIEW_FEE = {"qa.fee.qa_request.base": "50000", "qa.fee.qa_request.per_programme": "150000",
                   "qa.fee.qa_request_processing.confirmed": "true"}
DEMO_DOCUMENTS = [  # kind code, status — accepted ones prefill; the submitted one must not
    ("organogram", "accepted"), ("cac", "accepted"), ("c-of-o", "accepted"),
    ("physical-master-plan", "submitted"),
]
