from django.apps import AppConfig


class TicketsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tickets"
    verbose_name = "Tickets & Helpdesk"

    def ready(self):
        # v2.1 Phase C: the shared work.visibility rule, refined for tickets
        # (division routing, members of an accepted division).
        from .access import register_spec
        register_spec()
