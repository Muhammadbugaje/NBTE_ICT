from django.apps import AppConfig


class StaffConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "staff"

    def ready(self):
        # Importing the specs module registers StaffImport / StaffExport with
        # the shared importexport registry.
        from . import imports  # noqa: F401        # v2.2: the QA department lookup memo is cleared on every request and
        # whenever a department changes.
        from django.core.signals import request_started
        from django.db.models.signals import post_delete, post_save

        from qa_core.departments import clear_cache

        from .models import Department

        request_started.connect(clear_cache, dispatch_uid="qa_department_memo_request")
        post_save.connect(clear_cache, sender=Department, dispatch_uid="qa_department_memo_save")
        post_delete.connect(clear_cache, sender=Department, dispatch_uid="qa_department_memo_delete")

        # v2.4: the no-query-per-code department map (qa_core/departments.py)
        # lives for one web request on one thread as well.
        from qa_core.departments import dept_request_finished, dept_request_started

        from django.core.signals import request_finished  # noqa: F811  (same names, same app ready)
        request_started.connect(dept_request_started, dispatch_uid="qa_department_map_start")
        request_finished.connect(dept_request_finished, dispatch_uid="qa_department_map_finish")

        # v2.2: the director-seat memo lives for one request on one thread only.
        from django.core.signals import request_finished

        from org.services import seat_request_finished, seat_request_started

        request_started.connect(seat_request_started, dispatch_uid="org_seat_memo_start")
        request_finished.connect(seat_request_finished, dispatch_uid="org_seat_memo_finish")
