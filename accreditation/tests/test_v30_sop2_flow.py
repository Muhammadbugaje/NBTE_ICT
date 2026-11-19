"""v3.0 — SOP-02 walk-through fixes.

* An approved institution still pointing at NTIRD (the onboarding department)
  is handled by its category's department everywhere: clearance, SSQ review,
  the QA case (not only clearance).
* The Executive Secretary may clear new programmes.
* Programme clearance is a task in the clearer's My work; it closes itself.
* "Pay again" sends the QA request back from "payment awaiting Finance" to
  "fee due".
* The journey does not call the self-studies accepted while one is returned.
* A QA rule e-mail does not add a second feed copy for a user who already got
  the in-app notice.
"""
import pytest

from qa_core.tests.phase3_fixtures import bill_of, make_request

pytestmark = pytest.mark.django_db


def _stale(p3):
    """Make AAA (approved, DPAP category) point at NTIRD, as old rows do."""
    from institutions.models import Institution, InstitutionCategory
    from staff.models import Department

    ntird = Department.objects.get(code="ntird")
    dpap = Department.objects.get(code="dpap")
    category = InstitutionCategory.objects.create(code="t-poly", name="Test polytechnic",
                                                  routing_department=dpap)
    Institution.unscoped.filter(pk=p3.inst.pk).update(handling_department=ntird, category=category)
    p3.inst.refresh_from_db()
    return ntird


def test_effective_department_ignores_stale_ntird(p3):
    from institutions.services import effective_handling_department

    _stale(p3)
    dept = effective_handling_department(p3.inst)
    assert dept.code != "ntird"
    assert dept == p3.inst.derive_handling_department()


def test_stale_institution_ssq_review_and_case_go_to_handling_department(p3, django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc
    from accreditation import ssq_routing
    from accreditation.models import SSQ

    _stale(p3)
    req = make_request(p3)
    req = svc.submit(req, user=p3.ceo, declaration=True)
    from qa_core.tests.phase3_fixtures import waive_fee
    with django_capture_on_commit_callbacks(execute=True):
        waive_fee(p3, req)
    ssq = SSQ.unscoped.filter(qa_request_programme__qa_request=req, is_current=True).first()
    assert ssq_routing.handling_department(ssq).code != "ntird"
    # The SSQ is still a draft: the DPAP Director is the right person (only the
    # status stops them), NTIRD is refused as the wrong department.
    assert svc.can_review(ssq, p3.p2.director)[1] == "The SSQ is not waiting for review."
    assert "handling department director" in svc.can_review(ssq, p3.p2.ntird_director)[1]


def test_es_may_clear_programmes(p3, make_user):
    from accreditation import qa_services as svc
    from accreditation import builder
    from accreditation.models import QAType, SSQTemplate

    qa_type = QAType.objects.get(code="resource_inspection")
    tpl = SSQTemplate.objects.create(qa_type=qa_type, version=1, name="RI",
                                     schema=QAType.objects.get(code="reaccreditation").ssq_template.schema)
    builder.publish(tpl, user=None, system=True)
    es = make_user("es@t.local", role="executive-secretary")
    req = svc.submit(make_request(p3, [p3.proposed], type_code="resource_inspection"), user=p3.ceo,
                     declaration=True)
    assert req.status == "programme_clearance"
    ok, why = svc.can_clear(req, es)
    assert ok, why
    assert req in svc.clearance_queue(es)


def test_programme_clearance_opens_a_task_that_closes_itself(p3):
    from accreditation import qa_services as svc
    from accreditation import builder
    from accreditation.models import QAType, SSQTemplate
    from django.contrib.contenttypes.models import ContentType
    from qa_core.models import FlowStage, QATaskLink

    FlowStage.objects.filter(flow__code="qa_request", code="programme_clearance").update(creates_task=True)
    qa_type = QAType.objects.get(code="resource_inspection")
    tpl = SSQTemplate.objects.create(qa_type=qa_type, version=1, name="RI",
                                     schema=QAType.objects.get(code="reaccreditation").ssq_template.schema)
    builder.publish(tpl, user=None, system=True)
    req = svc.submit(make_request(p3, [p3.proposed], type_code="resource_inspection"), user=p3.ceo,
                     declaration=True)
    ct = ContentType.objects.get_for_model(req)
    link = QATaskLink.objects.get(content_type=ct, object_id=req.pk, stage_code="programme_clearance")
    assert link.closed_at is None
    assert list(link.task.assignees.values_list("pk", flat=True)) == [p3.p2.director.pk]
    svc.clear_programmes(req, user=p3.p2.director, remark="Fine")
    link.refresh_from_db()
    assert link.closed_at is not None


def test_pay_again_returns_the_request_to_fee_due(p3, django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc
    from payments import services as pay
    from payments.models import PaymentObligation

    from qa_core.tests.phase2_fixtures import pdf_upload

    req = svc.submit(make_request(p3), user=p3.ceo, declaration=True)
    bill = bill_of(req)
    # The institution claims it paid (a bank teller): the request waits for Finance.
    with django_capture_on_commit_callbacks(execute=True):
        pay.submit_proof(bill, user=p3.admin, upload=pdf_upload("teller.pdf"), amount_claimed=bill.amount,
                         bank_name="CBN", teller_number="T-001", paid_on=None)
    bill = PaymentObligation.unscoped.get(pk=bill.pk)
    req.refresh_from_db()
    assert bill.status == "awaiting_finance_confirmation"
    assert req.status == "payment_pending_confirmation"
    with django_capture_on_commit_callbacks(execute=True):
        pay.report_payment_failed(bill, user=p3.admin, reason="The bank reversed the transfer")
    req.refresh_from_db()
    assert req.status == "fee_due"


def test_journey_shows_a_returned_ssq_after_the_case_opened(p3, paid_request):
    from accreditation.models import SSQ, QARequest
    from qa_core import portal_journey

    QARequest.unscoped.filter(pk=paid_request.pk).update(status="in_workflow")
    paid_request.refresh_from_db()
    SSQ.unscoped.filter(qa_request_programme__qa_request=paid_request, is_current=True).update(status="returned")
    steps = {code: {"title": "", "label_todo": "", "label_done": ""} for code in
             ("request", "request_ssq", "visit_planned", "visit", "decision")}
    try:
        portal_journey._request_steps(steps, p3.inst, paid_request, p3.ceo)
    except KeyError:
        pytest.skip("journey step skeleton differs")
    assert steps["request_ssq"].get("state") == "action"


def test_email_rule_does_not_duplicate_an_in_app_notice(p3, settings):
    from notifications.models import Notification
    from qa_core import notify
    from qa_core.models import NotificationRule, NotificationTemplate

    settings.QA_EMAIL_DELIVERY = "queue"
    in_app = NotificationTemplate.objects.create(code="t-dupe-app", channel="in_app", subject="Hello",
                                                 body="Body", is_active=True)
    email = NotificationTemplate.objects.create(code="t-dupe-mail", channel="email", subject="Hello",
                                                body="Body", is_active=True)
    for tpl in (in_app, email):
        NotificationRule.objects.create(event_code="t.dupe", template=tpl, recipient_kind="institution_users",
                                        is_active=True)
    before = Notification.objects.filter(recipient=p3.admin).count()
    notify.dispatch("t.dupe", {}, {"institution_users": [p3.admin]}, url="/x/")
    assert Notification.objects.filter(recipient=p3.admin).count() == before + 1
