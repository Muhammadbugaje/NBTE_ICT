from django.apps import AppConfig, apps


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from . import audit

        # One mechanism, applied consistently (doc 31 §4): every concrete model
        # that declares AUDITED = True gets the post_save/post_delete receivers.
        for model in apps.get_models():
            if getattr(model, "AUDITED", False):
                audit.register(model)
