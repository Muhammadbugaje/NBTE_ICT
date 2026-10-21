"""Phase 7 seed data — Complaints (Master Plan §5.6, §11, §14; PHASES.md Phase 7).

* the default ``complaint`` flow (received → in progress ⇄ awaiting the raiser
  / referred → resolved → closed; withdraw from any open stage);
* the ten §8.5 layers with routing rules, three escalation levels;
* five knowledge-base articles;
* every ``qa.complaints.*`` key, ``qa.calendar.business_hours`` and
  ``qa.suspension.enabled_for`` (in ``settings_catalogue.SETTINGS_PHASE7``);
* notification templates and rules, role and group permissions, the
  ``qa-complaint`` task category.

``seed_startup`` creates every row with get-or-create semantics: an admin's
edits are never overwritten.
"""

H = {"required_permission": "complaints.handle_complaint"}
HR = {"required_permission": "complaints.handle_complaint", "requires_remark": True}
TASK = {"creates_task": True, "sla_business_hours": True, "task_category": "qa-complaint"}

COMPLAINT_FLOW = {
    "code": "complaint",
    "name": "Complaint (default)",
    "subject_model": "complaints.complaint",
    "description": "The default complaint flow (Master Plan §11). Each layer may use its own flow instead.",
    "stages": [
        ("received", "Received — to acknowledge", "Received", "start", "role_in_department",
         dict(TASK, sla_hours=8)),
        ("in_progress", "Being worked on", "Being worked on", "action", "role_in_department",
         dict(TASK, sla_hours=40)),
        ("awaiting_raiser", "Waiting for the raiser", "Waiting for your reply", "action", "institution",
         {"pauses_sla": True}),
        ("referred", "Referred internally", "Being worked on", "action", "role_in_department",
         dict(TASK, sla_hours=24)),
        ("resolved", "Resolved — awaiting confirmation", "Resolved — please confirm", "action", "institution", {}),
        ("closed", "Closed", "Closed", "end", "system", {"is_terminal_success": True}),
        ("withdrawn", "Withdrawn", "Withdrawn", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        ("received", "in_progress", "acknowledge", "Acknowledge", H),
        ("received", "resolved", "resolve", "Resolve", HR),
        ("received", "closed", "close", "Close (duplicate or out of scope)", HR),
        ("in_progress", "awaiting_raiser", "request_info", "Request information from the raiser", HR),
        ("in_progress", "referred", "refer", "Refer internally", HR),
        ("in_progress", "resolved", "resolve", "Resolve", HR),
        ("in_progress", "closed", "close", "Close (duplicate or out of scope)", HR),
        ("referred", "in_progress", "refer_back", "Send back to the handler", HR),
        ("referred", "resolved", "resolve", "Resolve", HR),
        ("awaiting_raiser", "in_progress", "raiser_replied", "Raiser replied", {}),
        ("awaiting_raiser", "in_progress", "resume", "Resume without a reply", HR),
        ("awaiting_raiser", "closed", "close", "Close (no reply)", HR),
        ("resolved", "closed", "confirm", "Confirm it is resolved", {}),
        ("resolved", "closed", "auto_close", "Close automatically", {}),
        ("resolved", "in_progress", "reopen", "Reopen", {}),
        ("received", "withdrawn", "withdraw", "Withdraw the complaint", {}),
        ("in_progress", "withdrawn", "withdraw", "Withdraw the complaint", {}),
        ("awaiting_raiser", "withdrawn", "withdraw", "Withdraw the complaint", {}),
        ("referred", "withdrawn", "withdraw", "Withdraw the complaint", {}),
    ],
}
FLOWS = [COMPLAINT_FLOW]

TASK_CATEGORIES = [
    ("qa-complaint", "QA — complaint",
     "Created by the QA system for a complaint stage or an escalation. Worked from QA (Open in QA)."),
]

ALL = []  # empty applies_to = everyone
INSTITUTION = ["institution"]
EXTERNAL = ["institution", "resource_person"]

# code, name, description, applies_to, default priority, custom fields
LAYERS = [
    ("payment-issues", "Payment issues",
     "A bill, a payment that is not confirmed, a wrong amount, a receipt or a refund.", INSTITUTION, "high",
     [{"key": "payment_reference", "label": "Payment reference (if you have one)", "type": "text",
       "required": False},
      {"key": "amount", "label": "Amount involved (₦)", "type": "text", "required": False}]),
    ("ssq-submission", "SSQ submission issues",
     "Problems filling, uploading or submitting a self-study questionnaire.", INSTITUTION, "normal", []),
    ("portal-access", "Portal login and access",
     "Signing in, set-password links, locked accounts and user management.", ALL, "high", []),
    ("visit-scheduling", "Visit scheduling",
     "Dates and arrangements of a QA visit.", EXTERNAL, "normal",
     [{"key": "preferred_dates", "label": "Dates that would work", "type": "text", "required": False}]),
    ("rp-assignment", "Resource-person assignment",
     "Who was assigned to a visit, conflicts of interest, engagement letters.", EXTERNAL, "normal", []),
    ("assessment-dispute", "Assessment or report dispute",
     "You disagree with a finding, a score or the report of a visit.", INSTITUTION, "high", []),
    ("workflow-delays", "Workflow delays",
     "A request, application or case that has not moved for too long.", ALL, "normal", []),
    ("system-bugs", "System problems",
     "Something in the portal does not work as it should.", ALL, "normal",
     [{"key": "page", "label": "Which page (copy the address if you can)", "type": "text", "required": False}]),
    ("permission-role", "Permission or role",
     "You cannot see or do something you should be able to.", ALL, "normal", []),
    ("others", "Others", "Anything else.", ALL, "low", []),
]

# layer, stage ("" = every task stage), condition, target kind, department code, role code, order, note
ROUTING = [
    ("payment-issues", "", "", "department", "finance", "", 10, "Payments go to Finance."),
    ("ssq-submission", "", "", "institution_handling_department", "", "", 20,
     "SSQs go to the institution's handling department."),
    ("portal-access", "", "", "department", "ict", "", 30, "Sign-in problems go to ICT."),
    ("visit-scheduling", "", "", "institution_handling_department", "", "", 40, ""),
    ("rp-assignment", "", "", "institution_handling_department", "", "", 50, ""),
    ("assessment-dispute", "", 'priority == "critical"', "role", "", "director", 55,
     "A critical dispute goes straight to the handling department's Director."),
    ("assessment-dispute", "", "", "institution_handling_department", "", "", 60, ""),
    ("workflow-delays", "", "", "desk_officer", "", "", 70, "The desk officer of the latest QA case."),
    ("workflow-delays", "", "", "institution_handling_department", "", "", 71, ""),
    ("system-bugs", "", "", "department", "ict", "", 80, ""),
    ("permission-role", "", "", "department", "ict", "", 90, ""),
    # "Others" has no rule on purpose: it lands in qa.complaints.fallback_department.
]

# level, minutes after the breach, target kind, only_if (TR-06/TR-07)
ESCALATION = [
    (1, 0, "department_director", ""),
    (2, 480, "deputy_director", ""),
    (3, 960, "es", "critical_or_repeat"),
]

# title, slug, summary, keywords, layer code, audience, body
KB_ARTICLES = [
    ("My payment has not been confirmed yet", "payment-not-confirmed",
     "Why a payment shows as 'awaiting confirmation' and what you can do.",
     "payment confirm confirmed pending remita paystack teller receipt bank transfer evidence",
     "payment-issues", "institution",
     "A payment is confirmed only by NBTE's Director of Finance, after a Finance officer has checked the "
     "evidence (the provider's report, your uploaded teller or the bank statement).\n\n"
     "What you can do:\n"
     "1. Open Payments and check that the bill shows your proof or the provider reference.\n"
     "2. If you paid by bank transfer, upload a clear teller or transfer slip with the bill's reference.\n"
     "3. Allow three working days from the upload.\n\n"
     "If it is still not confirmed after that, raise a complaint under Payment issues and quote the bill's "
     "reference."),
    ("I cannot sign in to the QA portal", "cannot-sign-in",
     "Forgotten passwords, expired set-password links and locked accounts.",
     "login sign in password forgot reset link expired locked account access email",
     "portal-access", "all",
     "Use 'Forgot password' on the sign-in page: a new set-password link is e-mailed to the address NBTE "
     "holds for you. Links expire; request a new one if yours has.\n\n"
     "If the e-mail never arrives, check your spam folder and ask your institution's administrator to confirm "
     "your address under Institution → Users. If your institution's portal access was suspended, you will see "
     "a message saying so when you sign in."),
    ("Submitting or revising an SSQ", "ssq-submit-revise",
     "How the self-study questionnaire is filled, sent to departments and submitted.",
     "ssq questionnaire submit revise upload evidence department section",
     "ssq-submission", "institution",
     "Open QA requests, choose the request and the programme, then fill each section. Sections can be sent to "
     "an academic department to complete; the Chief Executive submits the whole SSQ.\n\n"
     "After submission the SSQ is read-only. If NBTE returns it for revision you will see the comments on each "
     "section; correct them and submit again."),
    ("Changing the date of a QA visit", "visit-dates",
     "Who sets visit dates and how to ask for a change.",
     "visit inspection date reschedule postpone calendar team",
     "visit-scheduling", "all",
     "Visit dates are set by the handling department when the case reaches the visit stage. If a date does not "
     "work, raise a complaint under Visit scheduling as early as you can and give dates that would work. The "
     "department confirms any new date on your timeline."),
    ("How complaints are handled", "how-complaints-work",
     "What happens after you raise a complaint, and how long it should take.",
     "complaint status sla escalation delay reply resolved reopen withdraw how long",
     "others", "all",
     "Every complaint goes to the NBTE department that handles that kind of problem. It is acknowledged within "
     "one working day and worked within five working days. If NBTE asks you for information, the clock stops "
     "until you reply.\n\n"
     "If a deadline is missed, the complaint is escalated to the department's Director automatically. When it "
     "is resolved you can confirm it, or reopen it within 14 days if the problem is still there."),
]

TEMPLATES = [
    ("complaint.received", "in_app", "Complaint {{ reference }} received",
     "We received your complaint '{{ subject }}' ({{ layer }}). You can follow it under Complaints.",
     "To the raiser, when a complaint is raised."),
    ("complaint.received", "email", "NBTE complaint {{ reference }} received",
     "Thank you. We received your complaint '{{ subject }}' ({{ layer }}) with reference {{ reference }}. "
     "You can follow it, and reply, from the Complaints page of the NBTE QA portal.",
     "To the raiser, when a complaint is raised."),
    ("complaint.updated", "in_app", "Complaint {{ reference }}: {{ status }}",
     "Your complaint '{{ subject }}' is now: {{ status }}.", "To the raiser, when the status changes."),
    ("complaint.message", "in_app", "New message on complaint {{ reference }}",
     "{{ who }}: {{ excerpt }}", "To the other side of a complaint conversation."),
    ("complaint.resolved", "in_app", "Complaint {{ reference }} resolved",
     "Your complaint '{{ subject }}' was resolved: {{ resolution }} Please confirm, or reopen it if the "
     "problem is still there.", "To the raiser."),
    ("complaint.reminder", "in_app", "Complaint {{ reference }} is half-way to its deadline",
     "'{{ stage }}' on {{ reference }} ({{ subject }}) is due {{ due }}.", "To the handler, at 50% of the SLA."),
    ("complaint.breached", "in_app", "Complaint {{ reference }} is overdue",
     "'{{ stage }}' on {{ reference }} ({{ subject }}) was due {{ due }}. It is being escalated.",
     "To the handler, when the SLA is breached."),
    ("complaint.escalated", "in_app", "Escalated to you (level {{ level }}): complaint {{ reference }}",
     "{{ reference }} ({{ subject }}, {{ priority }}) breached '{{ stage }}'. {{ reason }} You are now watching it.",
     "To the escalation target."),
    ("complaint.escalated", "email", "Escalated to you (level {{ level }}): NBTE complaint {{ reference }}",
     "{{ reference }} ({{ subject }}, {{ priority }}) breached '{{ stage }}'. {{ reason }} You are now watching it; "
     "open it in the QA system.", "To the escalation target."),
]
RULES = [
    ("complaint.received", "role", "complaint.received", "in_app"),
    ("complaint.received", "role", "complaint.received", "email"),
    ("complaint.updated", "role", "complaint.updated", "in_app"),
    ("complaint.message", "role", "complaint.message", "in_app"),
    ("complaint.resolved", "role", "complaint.resolved", "in_app"),
    ("complaint.reminder", "role", "complaint.reminder", "in_app"),
    ("complaint.breached", "role", "complaint.breached", "in_app"),
    ("complaint.escalated", "role", "complaint.escalated", "in_app"),
    ("complaint.escalated", "role", "complaint.escalated", "email"),
]

_HANDLE = ["complaints.view_complaint_queue", "complaints.handle_complaint", "complaints.view_internal_messages"]
_LEAD = _HANDLE + ["complaints.escalate_complaint", "complaints.assign_complaint", "complaints.view_complaint_reports"]
ROLE_PERMISSIONS = {
    "executive-secretary": ["complaints.view_complaint_reports", "complaints.view_internal_messages",
                            "complaints.escalate_complaint"],
    "director": _LEAD,
    "deputy-director": _LEAD,
    "division-head": _HANDLE + ["complaints.escalate_complaint", "complaints.assign_complaint"],
    "officer": _HANDLE,
    "qa-finance-officer": _HANDLE,
    "qa-finance-director": _LEAD,
    "ict-qa-monitor": ["complaints.view_complaint_reports", "complaints.view_internal_messages"],
    "qa-settings-admin": ["complaints.manage_knowledge_base"],
}
GROUP_PERMISSIONS = {
    "Complaints Handlers": ["complaints.view_internal_messages"],
}

# ---------------------------------------------------------------------------
# Demo (seed_phase7_demo): three open complaints with different SLA states
# ---------------------------------------------------------------------------
DEMO_COMPLAINTS = [
    # layer, subject, body, priority, hours ago, state
    ("payment-issues", "Processing fee paid but still awaiting confirmation",
     "We paid the institutional processing fee by bank transfer two weeks ago and uploaded the teller, but the "
     "bill still says awaiting confirmation.", "high", 1, "on_track"),
    ("ssq-submission", "Cannot upload the laboratory inventory to the SSQ",
     "The upload button on the laboratory section returns an error for our 4 MB PDF.", "normal", 30, "near"),
    ("workflow-delays", "QA request has not moved in three weeks",
     "Our QA request for two programmes has shown 'Under review' for three weeks with no query.", "critical", 120,
     "breached"),
]
