"""Temporary review probe (deleted after the audit)."""
from types import SimpleNamespace

import pytest

from accreditation.tests.test_linear_onboarding import _cleared_for_qa, w  # noqa: F401
from accreditation.tests.test_onboarding_ssq import v3  # noqa: F401

pytestmark = pytest.mark.django_db


def test_probe_premature_approval(w, django_capture_on_commit_callbacks):
    from accreditation.onboarding_linear import approval_blocker, ready_for_approval
    from institutions.handlers import ApproveInstitutionHandler

    _cleared_for_qa(w, django_capture_on_commit_callbacks)
    print("STATUS", w.inst.status, "LABEL", w.inst.status_label, "CLEARED", w.inst.cleared_for_qa_at)
    print("READY", ready_for_approval(w.app))
    print("BLOCKER", repr(approval_blocker(w.inst)))
    handler = ApproveInstitutionHandler()
    print("AVAILABLE", handler.available_for(w.inst))
    handler.validate(w.inst, {"reason": "probe"})
    print("VALIDATE PASSED")


def test_probe_cleared_portal_copy(w, client_for, django_capture_on_commit_callbacks):
    from django.test import Client

    _cleared_for_qa(w, django_capture_on_commit_callbacks)
    c = Client(HTTP_HOST="qa.localhost")
    c.force_login(w.ce)
    html = c.get("/application/").content.decode()
    for needle in ("NBTE is checking your self-studies", "Your self-studies have reached NBTE",
                   "then approves your institution", "Start Resource Inspection"):
        print("CLEARED_HAS", repr(needle), needle in html)
    print("CLEARED_LABEL", w.inst.status_label)


def test_probe_finalised_portal_copy(w, django_capture_on_commit_callbacks):
    from django.test import Client

    _cleared_for_qa(w, django_capture_on_commit_callbacks)
    from accreditation import onboarding_v4
    from institutions.models import Programme

    Programme.unscoped.filter(pk=w.programme.pk).update(status="accredited")
    w.inst.refresh_from_db()
    case = SimpleNamespace(institution_id=w.inst.pk, reference="PROBE-CASE")
    print("FINALISED", onboarding_v4.finalise_institution(case, actor=None))
    w.inst.refresh_from_db()
    w.app.refresh_from_db()
    print("AFTER_STATUS", w.inst.status, "LABEL", w.inst.status_label, "APP_STATUS", w.app.status)
    c = Client(HTTP_HOST="qa.localhost")
    c.force_login(w.ce)
    html = c.get("/application/").content.decode()
    for needle in ("NBTE approved your institution", "Start Resource Inspection",
                   "NBTE is checking your self-studies"):
        print("APPROVED_HAS", repr(needle), needle in html)
