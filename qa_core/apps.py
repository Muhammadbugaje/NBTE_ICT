from django.apps import AppConfig


class QaCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "qa_core"
    verbose_name = "QA core engines"

    def ready(self):
        # Formula-typed settings are validated by the QA evaluator on save
        # (Master Plan §14). `config` exposes a hook so it never imports qa_core.
        from config.models import register_value_validator

        from .formula import validate_formula_setting

        register_value_validator("formula", validate_formula_setting)

        from . import signals  # noqa: F401  (side-effect import)

        # v2.4 speed pass: the flow-instance per-request memos (qa_core/flow.py)
        # live for one web request on one thread; refreshed the same moment a
        # FlowInstance row is written.
        from django.core.signals import request_finished, request_started
        from django.db.models.signals import post_delete, post_save

        from . import flow

        from .models import FlowInstance

        request_started.connect(flow.flow_memo_started, dispatch_uid="qa_flow_memo_start")
        request_finished.connect(flow.flow_memo_finished, dispatch_uid="qa_flow_memo_finish")
        post_save.connect(flow.flow_memo_bump, sender=FlowInstance, dispatch_uid="qa_flow_memo_instance-s")
        post_delete.connect(flow.flow_memo_bump, sender=FlowInstance, dispatch_uid="qa_flow_memo_instance-d")
