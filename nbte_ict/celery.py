import os

from celery import Celery
from celery.schedules import crontab
from datetime import timedelta
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nbte_ict.settings.dev")

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
    "tickets.sla_check": {
        "task": "tickets.tasks.sla_check",
        "schedule": timedelta(seconds=300),  # 5 minutes
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
      
    # End-of-day auto-absent. Adjust hour to match your workday end.
    "attendance-auto-mark-absent": {
        "task": "attendance.tasks.auto_mark_absent",
        "schedule": crontab(hour=18, minute=30, day_of_week="mon-fri"),
    },

    # Monday morning weekly digest.
    "attendance-weekly-summary": {
        "task": "attendance.tasks.weekly_summary",
        "schedule": crontab(hour=7, minute=0, day_of_week="mon"),
    },      
      
      
      
}