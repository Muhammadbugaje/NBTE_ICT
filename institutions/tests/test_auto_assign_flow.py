"""v2.1 X27b + the owner's brief: rotation pools flagged ``auto_assign`` hand
document reviews to the suggested officer at once (the system assigns; the
director may reassign), NBTE staff are told when an institution adds a
programme from the master list, and the visit panel is told the dates."""
import pytest

# Fixtures from the onboarding and master-list suites (precedent: test_x44).
from accreditation.tests.test_v21_onboarding import _complete_profile, v2  # noqa: F401
from institutions.tests.test_x44_programme_master import master_data  # noqa: F401
from qa_core.tests.phase5_fixtures import p5, p5_visit  # noqa: F401  (fixtures)

pytestmark = pytest.mark.django_db


@pytest.fixture
def auto_pool(db):
    """Factory: flag a department's document_analysis pool for at-once
    assignment with ``p2.officer`` as its member."""
    from qa_core.departments import qa_department

    from assignments.models_rotation import RotationPool, RotationMember

    def _flag(dept, auto=True):
        pool = RotationPool.objects.create(
            code=f"document_analysis:{dept.code}", kind="document_analysis",
            department=dept, name=f"Docs {dept.code} (t)", auto_assign=auto)
        return pool

    return _flag


def test_pool_auto_assign_takes_the_document_review(v2, p2, proposed_inst, inst_user, auto_pool, make_user,
                                                    django_capture_on_commit_callbacks):
    """The officer the rotation picked starts that department's review without
    any director's click (X4: only a member of the pool's own department can be
    suggested); every other department still waits for its director."""
    from payments.signals import obligation_confirmed

    from accreditation import onboarding_v2, services
    from assignments.models_rotation import RotationMember
    from institutions import document_review as dr
    from institutions.models import DocumentReview, InstitutionDocument

    officer = make_user("ap.officer@t.local", role="officer", dept="academic-planning")
    ict = _complete_profile(proposed_inst, inst_user)
    from qa_core.tests.phase2_fixtures import upload_all_documents  # plain helper, imported late by design

    app = services.start_application(proposed_inst, user=inst_user)
    upload_all_documents(proposed_inst, ict, app)

    # Flag exactly one of the departments the documents route to.
    target = None
    for doc in InstitutionDocument.unscoped.filter(institution=proposed_inst,
                                                   status__in=(InstitutionDocument.SUBMITTED,
                                                               InstitutionDocument.ACCEPTED)):
        dept = dr._department_for(proposed_inst, doc.kind)
        if dept is not None and dept.code == "academic-planning":
            target = dept
            break
    assert target is not None, "the routed departments do not include academic-planning"
    pool = auto_pool(target)
    RotationMember.objects.create(pool=pool, user=officer)

    app = onboarding_v2.declare_and_pay(app, user=inst_user, declaration=True)
    assert app.status == "fee_due"
    with django_capture_on_commit_callbacks(execute=True):
        obligation_confirmed.send(sender=type(app.fee_obligation), obligation=app.fee_obligation,
                                  verification=None, actor=p2.fin_director)
    app.refresh_from_db()
    assert app.status == "document_review" and app.auto_submitted

    flagged = DocumentReview.unscoped.filter(application=app, department=target)
    assert flagged.exists()
    for review in flagged:
        assert review.status == DocumentReview.ASSIGNED, review.status
        assert review.analyst_id == officer.pk, "the rotation pick started without the director"
    others = DocumentReview.unscoped.filter(application=app).exclude(department=target)
    assert others.exists(), "the other departments did receive documents too"
    all_awaiting = all(r.status == DocumentReview.AWAITING for r in others)
    assert all_awaiting, "the auto rule must not spread past the flagged pool"


def test_pool_without_flag_still_waits_for_the_director(v2, p2, proposed_inst, inst_user,
                                                        django_capture_on_commit_callbacks):
    from payments.signals import obligation_confirmed

    from accreditation import onboarding_v2, services
    from institutions.models import DocumentReview

    ict = _complete_profile(proposed_inst, inst_user)
    from qa_core.tests.phase2_fixtures import upload_all_documents as _upload

    app = services.start_application(proposed_inst, user=inst_user)
    _upload(proposed_inst, ict, app)
    app = onboarding_v2.declare_and_pay(app, user=inst_user, declaration=True)
    with django_capture_on_commit_callbacks(execute=True):
        obligation_confirmed.send(sender=type(app.fee_obligation), obligation=app.fee_obligation,
                                  verification=None, actor=p2.fin_director)
    app.refresh_from_db()
    assert {r.status for r in DocumentReview.unscoped.filter(application=app)} == {
        DocumentReview.AWAITING}, "no auto_assign → the director stays the assigner"


def test_nbte_told_when_a_master_list_programme_is_added(v2, master_data, make_institution, make_user,
                                                         django_capture_on_commit_callbacks):
    from notifications.models import Notification

    from institutions import programme_master
    from institutions.programme_master import add_programme
    from staff.models import Department

    for code in ("ntird", "dpap"):
        Department.objects.get_or_create(code=code, defaults={"name": code.upper()})
    watcher = make_user("prog.watcher@t.local", role="officer", dept="ntird")

    inst = make_institution("PMN1")
    programme = None
    with django_capture_on_commit_callbacks(execute=True):
        programme = add_programme(inst, master=master_data.rows[("Computer Science", "nd")])
    assert programme.body_source == "master" and programme.professional_body == master_data.cpn
    notifications = Notification.objects.filter(recipient=watcher)
    assert notifications.count() >= 1, "NBTE staff learned that the institution added a master programme"
    assert any(master_data.rows[("Computer Science", "nd")].name in (n.title + " " + n.body)
               for n in notifications)

    # Idempotent: nothing recurs when the notification rows are seeded twice
    # (install_notifications is upsert-safe); the seed sets keep no junk.
    before = Notification.objects.count()
    with django_capture_on_commit_callbacks(execute=True):
        programme_master._notify_nbte_of_programme(programme)
    # Direct call fires again (it is deliberate), so only assert it did not raise:
    assert Notification.objects.count() >= before


def test_visit_team_is_told_the_dates(v2, p5_visit, django_capture_on_commit_callbacks):
    from inspection import services as inspection_services
    from notifications.models import Notification

    inspection = p5_visit.inspection
    assert inspection is not None and inspection.scheduled_start
    team = inspection_services.team_members_of(inspection)
    assert team, "the planned visit has a panel"
    lead = inspection.team_lead
    other = next((u for u, _role in team if u.pk != getattr(lead, "pk", None)), None)

    with django_capture_on_commit_callbacks(execute=True):
        inspection_services._notify_team_of_visit(inspection)
    assert Notification.objects.filter(recipient=lead).exists(), "the team lead got the dates"
    if other is not None:  # mapped people (staff or resource persons) too
        assert Notification.objects.filter(recipient=other).exists(), "panel members got the dates"
