from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tasks'

    def ready(self):
        # Phase C (H11): a subtask handled as a request in another
        # department is driven by that request; mirror its outcome back.
        from django.db.models.signals import post_save

        from .hooks import register_external_manager

        register_external_manager(_is_bridged)
        try:
            from workflow.models import Request
        except Exception:  # pragma: no cover - workflow not installed
            return
        post_save.connect(_request_saved, sender=Request, dispatch_uid="tasks_bridge_request_saved")


def _is_bridged(task):
    return bool(getattr(task, "linked_request_id", None))


def _request_saved(sender, instance, created=False, **kwargs):
    if created or instance.status not in instance.CLOSED_STATUSES:
        return
    from .models import Task
    if not Task.objects.filter(linked_request_id=instance.pk).exists():
        return
    from work.bridge import sync_from_request
    sync_from_request(instance)
