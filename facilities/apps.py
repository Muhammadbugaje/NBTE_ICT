from django.apps import AppConfig


class FacilitiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "facilities"
    verbose_name = "Facilities and equipment (ND/HND minimum resources)"

    def ready(self):
        from . import diffspecs  # noqa: F401  (registers the Excel import spec)
        # v2.2: cached register statistics follow every change.
        from django.db.models.signals import post_delete, post_save

        from .models import FacilityItem, FacilityProgramme, FacilitySection
        from django.db.models.signals import post_migrate

        from .services import bump_stats, sync_master_list_after_migrate

        post_migrate.connect(sync_master_list_after_migrate, sender=self, dispatch_uid="facilities_master_sync")

        for model in (FacilityItem, FacilitySection, FacilityProgramme):
            post_save.connect(bump_stats, sender=model, dispatch_uid=f"facilities_stats_save_{model.__name__}")
            post_delete.connect(bump_stats, sender=model, dispatch_uid=f"facilities_stats_del_{model.__name__}")
