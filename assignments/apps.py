from django.apps import AppConfig


class AssignmentsConfig(AppConfig):
    """v2.1 X5: time-boxed access grants that follow from work (AccessGrant,
    AccessEvent). Phase P adds WorkAssignment, rotation and the QA wiring."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "assignments"
    verbose_name = "Assignments and access grants"

    def ready(self):
        # Consumers register their sources / native-visibility resolvers here.
        from . import registry  # noqa: F401
        registry.autodiscover()
        from . import signals
        signals.connect()
        from . import sync
        sync.connect()
        # P.4: QA models in the Django admin are for superusers only.
        from django.contrib import admin
        admin.autodiscover()
        from . import admin_guard
        admin_guard.guard_admin()
