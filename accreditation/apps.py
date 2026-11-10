"""Accreditation (QA): SOP-01 onboarding in Phase 2 (Master Plan §5.4).

QA requests (SOP-02), SSQs and the SOP-03 case arrive in Phases 3–4 and 6.
"""
from django.apps import AppConfig


class AccreditationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accreditation"
    verbose_name = "Accreditation (QA)"

    def ready(self):
        from . import effects  # noqa: F401  (registers flow effects and payment receivers)
        from . import visit_invitations  # noqa: F401  (visit approved -> invitations to bodies / RPs)
        from config.models import register_key_validator
        from django.core.exceptions import ValidationError

        def validate_cqac_recommendation_mode(entry):
            if entry.value_type != "str" or entry.value not in ("shared", "individual"):
                raise ValidationError({"value": "Choose shared or individual CQAC recommendations."})

        register_key_validator("qa.cqac.recommendation_mode", validate_cqac_recommendation_mode)

        # v2.4 speed pass: the case-page per-request memos (open task links,
        # holder rows, flow stages — accreditation/case_services.py) live for
        # one web request on one thread and are cleared on the same events.
        from django.core.signals import request_finished, request_started
        from django.db.models.signals import post_delete, post_save

        from . import case_services

        from .models import CostLine, CostSheet, FundRelease, PersonnelEntry, PersonnelList, QACase, RateTable, \
            VisitDetail

        from qa_core.models import FlowInstance, FlowStage, QATaskLink
        from tasks.models import Task, TaskAssignee

        request_started.connect(case_services.case_memo_started, dispatch_uid="qa_case_memo_start")
        request_finished.connect(case_services.case_memo_finished, dispatch_uid="qa_case_memo_finish")
        for label, model in (("link-s", QATaskLink), ("assignee-s", TaskAssignee), ("task-s", Task),
                             ("instance-s", FlowInstance), ("stage-s", FlowStage), ("case-s", QACase),
                             ("sheet-s", CostSheet), ("line-s", CostLine), ("plist-s", PersonnelList),
                             ("entry-s", PersonnelEntry), ("visit-s", VisitDetail), ("release-s", FundRelease),
                             ("rate-s", RateTable)):
            post_save.connect(case_services.case_memo_bump, sender=model, dispatch_uid=f"qa_case_memo_{label}")
            post_delete.connect(case_services.case_memo_bump, sender=model,
                                dispatch_uid=f"qa_case_memo_{label}-d")
