from django.apps import AppConfig


class OrgConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "org"
    verbose_name = "Organisation"

    def ready(self):
        from django.db.models.signals import post_migrate

        from . import signals  # noqa: F401

        post_migrate.connect(_after_migrate, sender=self, dispatch_uid="org_after_migrate")


def _after_migrate(sender, using="default", plan=None, **kwargs):
    """Fill the permission-dependent org data sets once Django has created
    the Meta permissions (fresh installs, test databases). Idempotent."""
    from django.contrib.auth.management import create_permissions
    from django.apps import apps as global_apps

    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, verbosity=0, using=using)
    from . import seed
    for func in seed.POST_MIGRATE_SETS:
        try:
            func()
        except Exception:  # never block migrate on reference data
            import logging
            logging.getLogger(__name__).exception("org seed %s failed after migrate", func.__name__)
