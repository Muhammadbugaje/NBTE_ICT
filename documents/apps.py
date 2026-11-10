from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "documents"
    verbose_name = "Documents and signatures"

    def ready(self):
        from core.files import register_file_cleanup
        from core.profile_tabs import register

        from .models import StaffSignature

        register_file_cleanup(StaffSignature, ["image", "source"])

        # Official letters: the preparer's task is completed from the letter page.
        from tasks.hooks import register_external_manager

        from .official_letters import is_linked_task

        register_external_manager(is_linked_task)

        def _visible(user):
            return getattr(user, "user_type", "staff") == "staff"

        def _badge(user):
            from . import signature

            if signature.active_for(user) is None:
                return "Required" if signature.signature_required(user) else "Add"
            return ""

        register("signature", "Signature", "documents:my_signature", order=20, visible=_visible, badge=_badge)
