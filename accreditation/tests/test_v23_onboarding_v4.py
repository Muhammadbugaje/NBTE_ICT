"""v2.3 SOP-01 v4: the SOP order (documents → fee → programmes), with the fee-timing switch.
No extra approval after the documents: the departments' clearances decide."""
import pytest

from qa_core.tests.phase2_fixtures import upload_all_documents

from .test_v21_onboarding import _complete_profile, _directors


@pytest.fixture
def v4(qa_seed):
    from accreditation import onboarding_v2, onboarding_v3, onboarding_v4
    from accreditation.seed_sets import install_notifications

    onboarding_v2.install_flow_v2()
    onboarding_v3.install_flow_v3()
    onboarding_v4.install_flow_v4()
    install_notifications()


def _set(key, value, vtype="str"):
    from django.core.cache import cache

    from config.models import ConfigEntry

    ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": value, "value_type": vtype})
    cache.clear()


def _fee(proposed_inst):
    from config.models import ConfigEntry
    from django.core.cache import cache
    from institutions.models import InstitutionType
    from payments.models import FeeComponent, FeeSchedule

    schedule = FeeSchedule.objects.get(code="institutional_processing_v3")
    itype = InstitutionType.objects.get(code="polytechnic")
    FeeComponent.objects.update_or_create(
        schedule=schedule, code="base-polytechnic",
        defaults={"label": "Institution fee", "kind": "base", "institution_type": itype, "order": 10,
                  "amount_setting": "qa.fee.institutional_processing_v3.base.polytechnic", "to_confirm": False})
    ConfigEntry.objects.update_or_create(key="qa.fee.institutional_processing_v3.base.polytechnic", scope="global",
                                         defaults={"value": "4000000", "value_type": "decimal"})
    cache.clear()


def _clear_all_documents(app, make_user, capture):
    from institutions import document_review as dr
    from institutions.models import DocumentReview

    reviews = list(DocumentReview.unscoped.filter(application=app).select_related("department"))
    directors = _directors(make_user, {r.department for r in reviews})
    with capture(execute=True):
        for r in DocumentReview.unscoped.filter(application=app, status="awaiting_assignment"):
            d = directors[r.department_id]
            o = make_user(f"an{r.pk}@t.local", role="officer")
            o.department = r.department
            o.save(update_fields=["department"])
            dr.assign(r, director=d, officer=o, reason="test")
            dr.recommend(r, analyst=o, recommendation="accept")
            dr.decide(r, director=d, decision="approve")


def _unused_ntird_director(make_user):
    from staff.models import Department

    ntird = Department.objects.get(code="ntird")
    if ntird.director_id is None:
        u = make_user("dir.ntird@t.local", role="director")
        u.department = ntird
        u.save(update_fields=["department"])
        Department.objects.filter(pk=ntird.pk).update(director=u)
    return Department.objects.get(pk=ntird.pk).director


def test_after_clearance_documents_first_then_fee_then_programmes(
        v4, p2, make_user, proposed_inst, inst_user, django_capture_on_commit_callbacks):
    from accreditation import onboarding_v2, onboarding_v3, onboarding_v4, services
    from notifications.models import Notification

    _set("qa.onboarding.fee_timing", "after_clearance")
    _fee(proposed_inst)
    ict = _complete_profile(proposed_inst, inst_user)
    app = services.start_application(proposed_inst, user=inst_user)
    assert onboarding_v4.is_v4(app)
    app = onboarding_v2.declare_and_pay(app, user=inst_user, declaration=True)
    assert app.status == "document_upload"          # no fee yet: documents first (SOP-01 order)
    upload_all_documents(proposed_inst, ict, app)
    app = onboarding_v3.submit_documents(app, user=inst_user)
    assert app.status == "document_review"
    _clear_all_documents(app, make_user, django_capture_on_commit_callbacks)
    app.refresh_from_db()
    # The departments' clearances decide: the fee falls due by itself, nobody approves again.
    assert app.status == "cleared_fee_due" and app.fee_obligation is not None
    assert Notification.objects.filter(recipient=inst_user, title="All your documents are approved").exists()
    with django_capture_on_commit_callbacks(execute=True):
        services.payment_settled(app.fee_obligation)
    app.refresh_from_db()
    assert app.status == "programme_selection"
    steps = onboarding_v2.header(app, proposed_inst)["steps"]
    assert [s["label"] for s in steps][:4] == ["Profile complete", "Upload and submit documents",
                                               "NBTE departments review", "Pay the institutional fee"]
    assert not any("principle" in s["label"].lower() for s in steps)


def test_before_review_fee_first_then_straight_to_programmes(
        v4, p2, make_user, proposed_inst, inst_user, django_capture_on_commit_callbacks):
    from accreditation import onboarding_v2, onboarding_v3, onboarding_v4, services

    _set("qa.onboarding.fee_timing", "before_review")
    _fee(proposed_inst)
    ict = _complete_profile(proposed_inst, inst_user)
    app = services.start_application(proposed_inst, user=inst_user)
    app = onboarding_v2.declare_and_pay(app, user=inst_user, declaration=True)
    assert app.status == "fee_due"
    with django_capture_on_commit_callbacks(execute=True):
        services.payment_settled(app.fee_obligation)
    app.refresh_from_db()
    assert app.status == "document_upload"
    upload_all_documents(proposed_inst, ict, app)
    onboarding_v3.submit_documents(app, user=inst_user)
    _clear_all_documents(app, make_user, django_capture_on_commit_callbacks)
    app.refresh_from_db()
    assert app.status == "programme_selection" and onboarding_v4.app_fee_timing(app) == "before_review"


def test_switch_changes_only_new_applications(v4, p2, make_user, proposed_inst, inst_user):
    from accreditation import onboarding_v2, onboarding_v4, services

    _set("qa.onboarding.fee_timing", "after_clearance")
    _fee(proposed_inst)
    _complete_profile(proposed_inst, inst_user)
    app = services.start_application(proposed_inst, user=inst_user)
    app = onboarding_v2.declare_and_pay(app, user=inst_user, declaration=True)
    _set("qa.onboarding.fee_timing", "before_review")   # changed half-way
    assert onboarding_v4.app_fee_timing(app) == "after_clearance"
    assert onboarding_v4.cleared_action(app) == "documents_cleared_pay"
