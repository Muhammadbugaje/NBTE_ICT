"""Registered settings (doc 30 §5.3). `seed_config` creates missing rows and
never overwrites an admin's edits. Secrets/site identity are NOT here (env vars).

Every entry is a 5-tuple:
    (key, value, type, section, description)

Types:  str | int | json
Sections are used purely for grouping in the config admin UI — they are not
enforced and you can add new sections freely.
"""

DEFAULTS = [
    # --- Site content --------------------------------------------------
    ("site.department_name", "ICT Directorate", "str", "Site",
     "Department name shown in the nav, footer, and page titles."),
    ("site.organisation_name", "National Board for Technical Education", "str", "Site",
     "Parent organisation name shown in the nav and footer."),
    ("site.tagline", "The directorate's digital workspace.", "str", "Site",
     "Short hero tagline."),
    ("site.email", "ict@nbte.gov.ng", "str", "Site",
     "Public contact email."),
    ("site.phone", "02013306282", "str", "Site",
     "Public contact phone number."),
    ("site.address", "NBTE Headquarters, Kaduna, Nigeria", "str", "Site",
     "Public physical address."),
    ("site.footer_note", "Authorized access only. All actions logged and audited.",
     "str", "Site", "Small disclaimer text in the footer."),
    # Phase 8 UI: names the templates show (blank = the built-in default).
    ("site.name", "NBTE ICT Directorate", "str", "Site",
     "Name of the internal workspace, shown in the top bar and the browser tab."),
    ("site.qa_name", "Quality Assurance", "str", "Site",
     "Name of the Quality Assurance workspace, shown on QA pages."),
    ("site.portal_name", "NBTE Quality Assurance Portal", "str", "Site",
     "Full name of the institutions' portal, used in page titles and messages."),
    ("site.portal_short_name", "Quality Assurance Portal", "str", "Site",
     "Short portal name shown next to the logo in the portal's top bar."),

    # --- Profile completeness ------------------------------------------
    ("profile.completion_threshold", "70", "int", "Profile",
     "Minimum profile completeness percentage. Below it, staff see a reminder banner, get periodic "
     "reminders and are asked to complete their profile when they start a request (they may continue)."),
    ("profile.reminder_days", "7", "int", "Profile",
     "Days between in-app reminders to staff whose profile is below the threshold. 0 turns reminders off."),
    ("profile.banner_enabled", "1", "int", "Profile",
     "1 = show the profile completeness banner on every page (staff can hide it for the session); 0 = off."),
    ("profile.interrupt_enabled", "1", "int", "Profile",
     "1 = ask staff below the threshold to complete their profile when they start a new request; 0 = off."),
    ("profile.weight.photo", "4", "int", "Profile",
     "Weight of the profile-photo field when computing completeness."),
    ("profile.weight.phone", "3", "int", "Profile",
     "Weight of the phone field when computing completeness."),
    ("profile.weight.department", "3", "int", "Profile",
     "Weight of the department field when computing completeness."),
    ("profile.weight.role", "2", "int", "Profile",
     "Weight of the role field when computing completeness."),
    ("profile.weight.designation", "3", "int", "Profile",
     "Weight of the designation field when computing completeness."),
    ("profile.weight.grade_level", "3", "int", "Profile",
     "Weight of the grade level field when computing completeness."),
    ("profile.weight.subsection", "2", "int", "Profile",
     "Weight of the subsection field when computing completeness."),
    ("profile.weight.office_location", "2", "int", "Profile",
     "Weight of the office location field when computing completeness."),
    ("profile.weight.duties", "2", "int", "Profile",
     "Weight of the duties field when computing completeness."),
    ("profile.weight.emergency_contact_name", "3", "int", "Profile",
     "Weight of the emergency contact name field."),
    ("profile.weight.emergency_contact_phone", "3", "int", "Profile",
     "Weight of the emergency contact phone field."),
    ("profile.weight.contact_preference", "2", "int", "Profile",
     "Weight of the contact preference field."),

    # --- Email delivery engine -----------------------------------------
    ("email.provider_override", "auto", "str", "Email",
     "auto = Brevo first for every category; force a provider only for emergency routing."),
    ("email.brevo_daily_limit", "300", "int", "Email",
     "Emails per day before failing over from Brevo."),
    ("email.gmail_daily_limit", "500", "int", "Email",
     "Emails per day before failing over from Gmail."),
    ("email.critical_categories",
     '["credentials", "reset", "task", "approval", "test"]', "json", "Email",
     "Legacy setting; all categories now use the same Brevo-first provider order."),
    ("email.sensitive_categories",
     '["credentials", "reset"]', "json", "Email",
     "Categories whose email BODIES are never stored in the log."),
    ("email.initial_delay_seconds", "10", "int", "Email",
     "Every email waits this long before sending."),
    ("email.min_gap_seconds", "5", "int", "Email",
     "Minimum seconds between consecutive emails."),

    # --- File / media --------------------------------------------------
    ("media.thumbnail_sizes", "[300, 64]", "json", "Files",
     "Longest-edge pixel sizes generated for uploaded images."),
    ("media.disk_warn_percent", "80", "int", "Files",
     "Health check reports 'degraded' above this disk-usage percentage."),
    ("uploads.allowed_extensions",
     '["pdf", "doc", "docx", "xls", "xlsx", "jpg", "jpeg", "png"]', "json", "Files",
     "File types allowed as request, task, ticket and asset attachments."),

    # --- Workflow ------------------------------------------------------
    # How the receiving department's approval chain behaves for cross-dept
    # requests. Read by workflow.services._cross_dept_routing_mode().
    ("workflow.cross_dept_routing", "full_chain", "str", "Workflow",
     "Cross-department routing mode. 'full_chain' (default) runs the receiving "
     "chain in full regardless of the requester's rank. 'rank_aware' also "
     "applies rank-skipping to the receiving chain. 'director_direct' lets "
     "directors (rank 100..998) route straight to the receiving director, "
     "skipping that department's junior steps. ES and superusers always get "
     "the full receiving chain."),

    # --- Leave ----------------------------------------------------------
    ("leave.entitlement_by_role", "{}", "json", "Leave",
     "Default annual leave days by system role. Configure role values in the "
     "role entitlement section above the settings registry."),
    
    # --- Tickets --------------------------------------------------------
    ("tickets.sla.default_response_seconds", "3600", "int", "Tickets",
     "Fallback base response window (seconds) for ticket categories that "
     "don't set their own."),
    ("tickets.sla.default_resolution_seconds", "86400", "int", "Tickets",
     "Fallback base resolution window (seconds) for ticket categories that "
     "don't set their own."),
    ("tickets.sla.multiplier.urgent", "1", "int", "Tickets",
     "SLA multiplier applied to urgent tickets (1 = base window unchanged)."),
    ("tickets.sla.multiplier.high", "2", "int", "Tickets",
     "SLA multiplier applied to high-priority tickets."),
    ("tickets.sla.multiplier.normal", "4", "int", "Tickets",
     "SLA multiplier applied to normal-priority tickets."),
    ("tickets.sla.multiplier.low", "8", "int", "Tickets",
     "SLA multiplier applied to low-priority tickets."),
    ("tickets.escalation.poll_seconds", "300", "int", "Tickets",
     "How often the Celery Beat job checks for SLA breaches (seconds)."),
    ("tickets.escalation.auto_reopen_window_days", "14", "int", "Tickets",
     "How long after closing a ticket the raiser can still reopen it."),    
    
    # --- Ticket resolution emails --------------------------------------
    ("tickets.email.resolution_enabled", "1", "int", "Tickets",
     "Send an email to the raiser when their ticket is resolved (1=on, 0=off)."),
    ("tickets.email.resolution_subject_prefix", "[NBTE ICT]", "str", "Tickets",
     "Prefix prepended to resolution email subjects."),
    ("tickets.email.resolution_intro", 
     "Your ticket has been resolved. Details below.", "str", "Tickets",
     "Intro paragraph in the resolution email."),
    ("tickets.email.resolution_signoff",
     "National Board for Technical Education — ICT Directorate",
     "str", "Tickets",
     "Signature line at the bottom of resolution emails."),    
    
    ("workflow.email.subject_prefix", "[NBTE ICT]", "str", "Workflow",
     "Prefix prepended to workflow email subjects."),
    ("workflow.email.signoff",
     "National Board for Technical Education — ICT Directorate",
     "str", "Workflow",
     "Signature line in workflow emails."),
    
    # --- Tasks ----------------------------------------------------------
    ("tasks.default_max_open", "5", "int", "Tasks",
     "Fallback max simultaneously-open tasks per user (used if their "
     "role has no max_open_tasks)."),
    ("tasks.email.subject_prefix", "[NBTE ICT]", "str", "Tasks",
     "Prefix for task email subjects."),
    ("tasks.email.signoff",
     "National Board for Technical Education — ICT Directorate",
     "str", "Tasks", "Signature line in task emails."),
    ("tasks.reminders.enabled", "1", "int", "Tasks",
     "Enable in-app reminders (1=on, 0=off)."),
    ("tasks.reminders.days_before_due", "[3, 1]", "json", "Tasks",
     "Days-before-due at which an in-app reminder fires."),
    ("tasks.reminders.email_enabled", "0", "int", "Tasks",
     "Also email reminders (0=in-app only, 1=email too)."),
    ("tasks.reminders.overdue_daily", "1", "int", "Tasks",
     "Send a daily in-app reminder for overdue tasks."),
    ("tasks.escalation.halfway_bump", "1", "int", "Tasks",
     "Auto-bump task priority once it's halfway past due (1=on, 0=off)."),
    ("tasks.escalation.overdue_bump", "1", "int", "Tasks",
     "Auto-escalate to urgent once well past due (1=on, 0=off)."),    
    
     # --- Attendance -----------------------------------------------------
    ("attendance.work_week", "[0, 1, 2, 3, 4]", "json", "Attendance",
     "Days considered working days (0=Monday, 6=Sunday). Default Mon-Fri."),
    ("attendance.default_radius_meters", "200", "int", "Attendance",
     "Fallback radius when creating a new location."),
    ("attendance.default_gps_offset_meters", "50", "int", "Attendance",
     "Fallback GPS offset when creating a new location."),
    ("attendance.default_grace_minutes", "15", "int", "Attendance",
     "Fallback grace window before a check-in is marked 'late'."),
    ("attendance.allow_manual_edit_days", "7", "int", "Attendance",
     "How far back a director or office head may edit a record manually."),
    ("attendance.auto_mark_absent_hour", "20", "int", "Attendance",
     "Hour of day (0-23) when a Celery job marks anyone who didn't check "
     "in as absent. Set to 0 to disable."),
    ("attendance.max_gps_accuracy_m", "200", "int", "Attendance",
     "GPS check-ins whose reported accuracy is worse than this many metres are rejected."),
    ("attendance.all_access_department_codes", '["hr"]', "json", "Attendance",
     "Department codes whose directors / office heads may view attendance for "
     "ALL departments (default: Human Resources). Anyone else is given the "
     "'view_all_attendance' permission explicitly."),
    ("attendance.report_timezone", "Africa/Lagos", "str", "Attendance",
     "Timezone used to interpret check-in times and day boundaries."),   
    

    ("attendance.manual_checkin_enabled", "true", "bool", "Attendance",
     "Master switch for the code-based manual check-in flow. Turn off to "
     "force everyone through GPS check-in."),
    ("attendance.manual_code_length", "6", "int", "Attendance",
     "Number of characters in each rotating office code. Between 4 and 12."),
    ("attendance.manual_code_rotation_minutes", "30", "int", "Attendance",
     "How often office codes rotate, in minutes. Lower means tighter "
     "security but more coordination with the office contact."),
    ("attendance.manual_code_grace_minutes_before_open", "30", "int", "Attendance",
     "Minutes before the location's work_start_time when codes are already "
     "valid — for early arrivals and morning setup."),
    ("attendance.manual_code_grace_minutes_after_close", "30", "int", "Attendance",
     "Minutes after the location's work_end_time when codes are still "
     "valid — for late finishes and shift handover."),
    ("attendance.manual_code_max_attempts_per_hour", "10", "int", "Attendance",
     "Maximum failed code attempts per user per hour before they're locked "
     "out until the next hour."),


    # --- File management ----------------------------------------------
    ("files.orphan_scan_enabled", "true", "bool", "Files",
     "Allow the settings page to scan for orphaned media files."),
    ("files.orphan_purge_min_age_days", "30", "int", "Files",
     "Minimum age (in days) before an orphaned file can be deleted "
     "from the settings page. Prevents accidental deletion of files "
     "from a recent upload that hasn't committed yet."),
    ("files.orphan_scan_max_files", "5000", "int", "Files",
     "Maximum number of files to enumerate in a single scan. "
     "Guards against very large media roots."),
    ("files.quarantine_retention_days", "30", "int", "Files",
     "Days a file stays in the quarantine folder (.quarantine/<date>/) "
     "after the orphan clean-up moves it there, before it is removed for good."),
    ("files.orphan_protected_folders", "", "str", "Files",
     "Extra media folders whose files are never treated as orphans, "
     "separated by commas (e.g. qa/guides, protected:showcase). Adds to "
     "MEDIA_SCAN_PROTECTED_FOLDERS in settings."),
]

# Phase 8 UI: editable section titles on the public home page and the QA
# portal landing page. Templates read them with {% site_text "key" %}
# (config/templatetags/site_text.py); a blank value falls back to the default.
HOME_TEXT = [
    ("home.title.about", "One workspace for the whole directorate.", "Home page: title of the 'What it is' section."),
    ("home.title.how", "From report to resolution, in four steps.", "Home page: title of the 'How it works' section."),
    ("home.title.leaders", "Meet the people in charge.", "Home page: title of the leadership section."),
    ("home.title.qa_portal", "The NBTE Quality Assurance Portal.", "Home page: title of the QA portal band."),
    ("home.title.news", "News & updates.", "Home page: title of the news section."),
    ("home.title.awards", "Awards & milestones.", "Home page: title of the awards section."),
    ("home.title.gallery", "Gallery.", "Home page: title of the gallery section."),
    ("home.title.documents", "Recent documents.", "Home page: title of the documents section."),
    ("home.title.contact", "We're here when you need us.", "Home page: title of the contact section."),
    ("portal.home.title.services", "Everything between your institution and NBTE, in one place.",
     "QA portal landing: title of the services section."),
    ("portal.home.title.follow", "Accreditation you can follow, step by step.",
     "QA portal landing: title of the tracking section."),
    ("portal.home.title.journey", "From registration to decision, in four stages.",
     "QA portal landing: title of the journey section."),
    ("portal.home.title.types", "The right review for each stage of a programme's life.",
     "QA portal landing: title of the review types section."),
    ("portal.home.title.pay", "Fees, paid and confirmed without the paperwork.",
     "QA portal landing: title of the payments section."),
    ("portal.home.title.who", "Built for everyone who takes part.", "QA portal landing: title of the audiences section."),
    ("portal.home.title.ready", "Have these to hand and registration takes about fifteen minutes.",
     "QA portal landing: title of the checklist section."),
    ("portal.home.title.guides", "Guides you can print or share.", "QA portal landing: title of the guides section."),
    ("portal.home.title.faq", "Common questions.", "QA portal landing: title of the questions section."),
    ("portal.home.title.cta", "Ready when you are.", "QA portal landing: title of the closing call to action."),
]
HOME_TEXT_DEFAULTS = {key: value for key, value, _desc in HOME_TEXT}
DEFAULTS += [(key, value, "str", "Homepage", desc) for key, value, desc in HOME_TEXT]
