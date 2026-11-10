"""v2.1 Phase B platform settings (pb-platform). Seeded by `seed_nbte`
(set `settings`, which reads every `<app>/settings_catalogue.py` SETTINGS
list); edited in Settings. Columns as qa_core/settings_catalogue.py:
key, value, type, group, description, editable_by, dual_control.
"""
import json

QA_EDIT = "qa_core.edit_qa_settings"
G_SECURITY = "Security"
G_SIGNATURES = "Signatures and documents"
G_BANK = "Profile"
G_IMPORT = "Imports"

SETTINGS = [
    # S-07 recent authentication
    ("security.reauth_minutes", "10", "int", G_SECURITY,
     "Minutes a re-entered password unlocks sensitive personal changes (signature, bank details).", "", False),
    # S-24 session ages
    ("security.session.staff_idle_minutes", "480", "int", G_SECURITY,
     "Staff are signed out after this many minutes without using the system (8 h).", "", False),
    ("security.session.staff_absolute_minutes", "720", "int", G_SECURITY,
     "Staff are signed out this many minutes after signing in, even if active (12 h).", "", False),
    ("security.session.portal_idle_minutes", "120", "int", G_SECURITY,
     "Institution portal users are signed out after this many idle minutes (2 h).", "", False),
    ("security.session.portal_absolute_minutes", "720", "int", G_SECURITY,
     "Institution portal sessions end this many minutes after sign-in (and when the browser closes).", "", False),
    # S-26 optional e-mail sign-in code (no OTP library is installed)
    ("security.2fa.email_code_enabled", "false", "bool", G_SECURITY,
     "Ask for a 6-digit code e-mailed after a password sign-in, for roles flagged 'requires 2FA' (ES, Directors, "
     "Deputy Directors) and the roles/permissions below. Passkey sign-ins skip it. Off by default.", "", False),
    ("security.2fa.role_codes", json.dumps(["qa-finance-officer", "director", "pb-admin"]), "json",
     G_SECURITY, "Extra role codes that need the e-mailed sign-in code (when it is switched on).", "", False),
    ("security.2fa.permissions", json.dumps(["honoraria.*", "documents.revoke_document",
                                             "qa_core.edit_qa_settings"]), "json", G_SECURITY,
     "Anyone holding one of these permissions needs the e-mailed sign-in code (app.* = any of that app).", "", False),
    # X23 signatures
    ("qa.signatures.required_min_rank", "100", "int", G_SIGNATURES,
     "Role rank from which a signature is required (100 = Director, 999 = Executive Secretary).", QA_EDIT, False),
    ("qa.signatures.block_approvals_without_signature", "true", "bool", G_SIGNATURES,
     "Block approvals that generate a document or memo until a required signature is on file.", QA_EDIT, False),
    ("qa.signatures.remind_others", "true", "bool", G_SIGNATURES,
     "Show a gentle reminder to staff below the required rank who have no signature yet.", QA_EDIT, False),
    ("qa.signatures.reminder_days", "7", "int", G_SIGNATURES,
     "Days between in-app reminders to Directors (and above) without a signature. 0 = off.", QA_EDIT, False),
    # X33 bank details
    ("profile.bank.reconfirm_days", "365", "int", G_BANK,
     "Staff are asked to confirm their bank details are still correct this often (days). 0 = only once.", "", False),
    ("profile.bank.banner_enabled", "true", "bool", G_BANK,
     "Show the 'add / confirm your bank details' banner to staff.", "", False),
    # X45 imports
    ("qa.import.max_rows", "5000", "int", G_IMPORT, "Most rows one import file may have.", QA_EDIT, False),
    ("qa.import.preview_ttl_hours", "24", "int", G_IMPORT,
     "Hours an import preview stays open before the file must be uploaded again.", QA_EDIT, False),
    ("qa.import.blank_means_no_change", "true", "bool", G_IMPORT,
     "A blank cell in an import leaves the current value unchanged.", QA_EDIT, False),
    ("qa.import.max_file_mb", "10", "int", G_IMPORT, "Largest import file accepted (MB).", QA_EDIT, False),
]

SETTING_KEYS = [row[0] for row in SETTINGS]
