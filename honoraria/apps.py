from django.apps import AppConfig


class HonorariaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "honoraria"
    verbose_name = "Honoraria and bank details"

    def ready(self):
        from core.profile_tabs import register

        def _visible(user):
            return getattr(user, "user_type", "staff") == "staff"

        def _badge(user):
            from . import services

            banner = services.banner_for(user)
            if banner is None:
                return ""
            return "Add" if banner["kind"] == "add" else "Confirm"

        register("bank", "Bank details", "honoraria:my_bank", order=30, visible=_visible, badge=_badge)
        register("earnings", "My earnings", "honoraria_earnings:list", order=25, visible=_visible)

        # v2.3: a closed visit completes SOP-03's payable work (honorarium file).
        from django.db.models.signals import post_save

        def _visit_closed(sender, instance, raw=False, **kwargs):
            if raw or instance.closed_at is None:
                return
            try:
                from . import files

                files.stage_completed(instance.case.institution_id, files.SOP03)
            except Exception:  # pragma: no cover - never break the visit
                import logging

                logging.getLogger(__name__).exception("honoraria visit hook failed")

        post_save.connect(_visit_closed, sender="inspection.Inspection", dispatch_uid="honoraria_visit_closed",
                          weak=False)
