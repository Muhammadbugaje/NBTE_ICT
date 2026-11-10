import os

from celery import Celery
from celery.schedules import crontab
from datetime import timedelta
# Review M19: the worker used to fall back to DEV settings even on a
# production server (wsgi.py/asgi.py fall back to prod). An explicit
# DJANGO_SETTINGS_MODULE always wins. Otherwise production is recognised by
# REDIS_URL, which only settings.prod reads (it is required there); dev
# never uses it, so `celery -A nbte_ict worker` in dev is unchanged.
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "nbte_ict.settings.prod" if os.environ.get("REDIS_URL") else "nbte_ict.settings.dev",
)

app = Celery("nbte_ict")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# ---------------------------------------------------------------------------
# Periodic tasks (Celery Beat)
# ---------------------------------------------------------------------------
# Run in dev with:
#   celery -A nbte_ict beat -l info
#   celery -A nbte_ict worker -l info
app.conf.beat_schedule = {
    "cleanup-import-tmp": {
        "task": "core.tasks.cleanup_import_tmp",
        "schedule": crontab(minute=0),              # hourly, at :00
        "kwargs": {"max_age_hours": 24},
    },
    # Phase 8: files the orphan clean-up moved to .quarantine/<date>/ are
    # removed for good after files.quarantine_retention_days (default 30).
    "media-purge-quarantine": {
        "task": "core.tasks.purge_media_quarantine",
        "schedule": crontab(hour=2, minute=17),    # daily
    },
    # v2.1 X5: access grants - extend open work, revoke closed, expire, report.
    "assignments.sweep_grants": {
        "task": "assignments.tasks.sweep_grants",
        "schedule": crontab(minute=7),              # hourly
    },
    # v2.1 Part 2.2: SSQ review offers not accepted / reviews past due (one reminder a day each).
    "accreditation.ssq_assignment_reminders": {
        "task": "accreditation.ssq_assignment_reminders",
        "schedule": crontab(minute=23, hour="8-17"),   # hourly in working hours
    },
    # Linear onboarding: overdue self-study stages (qa.onboarding.stage_sla_days) — remind, then escalate.
    "accreditation.onboarding_stage_reminders": {
        "task": "accreditation.onboarding_stage_reminders",
        "schedule": crontab(minute=41, hour="8-17"),   # hourly in working hours; each level is sent once
    },
    # v2.1 P.5: weekly access digest to the Director of ICT.
    "assignments.weekly_access_digest": {
        "task": "assignments.tasks.weekly_access_digest",
        "schedule": crontab(minute=17, hour=7, day_of_week=1),  # Monday 07:17
    },
    # v2.1 I4: remind ticket openers to rate resolved tickets.
    "tickets.rating_reminders": {
        "task": "tickets.tasks.rating_reminders",
        "schedule": crontab(hour=9, minute=13),     # daily
    },
    "tickets.sla_check": {
        "task": "tickets.tasks.sla_check",
        "schedule": timedelta(seconds=300),  # 5 minutes
    },    
    
    # QA Phase 7: complaint SLA reminders, breaches, escalations, auto-close.
    # Runs every minute; the task honours qa.complaints.sla_poll_minutes.
    "complaints.sla_sweep": {
        "task": "qa_core.complaints.sla_sweep",
        "schedule": timedelta(minutes=1),
    },
    "tasks.reminder_sweep": {
        "task": "tasks.tasks.reminder_sweep",
        "schedule": timedelta(hours=1),
    },
    "tasks.spawn_recurring": {
        "task": "tasks.tasks.spawn_recurring",
        "schedule": timedelta(minutes=15),
    },
    "tasks.escalate_overdue": {
        "task": "tasks.tasks.escalate_overdue",
        "schedule": timedelta(hours=6),
    },    
    
    "attendance.auto_mark_absent": {
        "task": "attendance.tasks.auto_mark_absent",
        # Every day at 20:00 Africa/Lagos. The view swaps the hour if
        # attendance.auto_mark_absent_hour is set differently.
        "schedule": crontab(hour=20, minute=0),
    },
    "attendance.nightly_checkout_and_duty": {
        # v2.1 Phase D (G3, G7): flag missing check-outs; re-sync visit duty days.
        "task": "attendance.tasks.nightly_checkout_and_duty",
        "schedule": crontab(hour=23, minute=40),
    },
    "attendance.weekly_summary": {
        "task": "attendance.tasks.weekly_summary",
        # Monday 08:00 Africa/Lagos
        "schedule": crontab(day_of_week="mon", hour=8, minute=0),
    },    
    
    "portal-expire-announcements": {
        "task": "portal.expire_announcements", "schedule": 300.0,
    },
    "portal-publish-due-announcements": {
        "task": "portal.publish_due_announcements", "schedule": 60.0,
    },
    "portal-fire-due-reminders": {
        "task": "portal.fire_due_reminders", "schedule": 60.0,
    },    
      
    "portal-document-review-reminders": {
        "task": "portal.document_review_reminders",
        "schedule": 86400.0,  # once a day
    },      
    "messaging.delete-expired-attachments": {
        "task": "messaging.delete_expired_attachments",
        "schedule": 86400.0,
    },
    # v2.1 Phase E (L7): message reminders ("remind me about this message").
    "messaging.send-due-reminders": {
        "task": "messaging.send_due_reminders",
        "schedule": timedelta(minutes=1),
    },
      
    # QA Phase 2: payments sweep (reminders, overdue Finance confirmations,
    # provider polls). It never confirms a payment.
    "qa.payments.sweep": {
        "task": "payments.sweep",
        "schedule": timedelta(minutes=15),
    },
    # v2.2: nightly reconciliation — provider vs NBTE records vs the ledger.
    "qa.payments.reconcile": {
        "task": "payments.reconcile",
        "schedule": crontab(hour=1, minute=45),
    },

    # Phase 8 (Master Plan §22): once a day, take a backup if
    # qa.backup.interval_days (default 30, i.e. monthly) have passed.
    "backups.scheduled_check": {
        "task": "backups.scheduled_check",
        "schedule": crontab(hour=1, minute=30),  # 01:30 (beat time zone), a quiet hour
    },
    # QA Phase 8 (Master Plan Phase 6): programmes past their expiry date
    # become expired; renewal reminders at qa.programme.renewal_reminder_days.
    "accreditation.programme_expiry": {
        "task": "accreditation.programme_expiry",
        "schedule": crontab(hour=1, minute=17),  # daily, 01:17
    },

    # Phase B (D10): weekly promotion overdue notice to HR and the ES.
    "staff.promotion_overdue_notice": {
        "task": "staff.tasks.promotion_overdue_notice",
        "schedule": crontab(day_of_week="mon", hour=7, minute=43),
    },

    # Review M12: the duplicate "attendance-auto-mark-absent" (18:30 Mon-Fri)
    # and "attendance-weekly-summary" (Mon 07:00) entries were removed; each
    # job now runs once, from the entries above.
      
      
      
}