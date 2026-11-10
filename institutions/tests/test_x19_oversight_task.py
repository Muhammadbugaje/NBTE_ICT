"""v2.1 X19: registration opens "Assign an ICT supervisor" for the Director of
ICT (with a rotation suggestion in the description and the oversight card), and
appointing a supervisor closes the task in QA."""
from django.test import Client

from institutions.tests.test_x50_registration import _data


def _departments():
    from staff.models import Department

    for code in ("ict", "ntird", "dpap"):
        Department.objects.get_or_create(code=code, defaults={"name": code.upper()})
    return Department.objects.get(code="ict")


def _register(lookups, portal_host, django_capture_on_commit_callbacks, name="Zonkwa Tech"):
    slug = name.lower().replace(" ", "")
    with django_capture_on_commit_callbacks(execute=True):
        r = Client(HTTP_HOST=portal_host).post("/register/", _data(
            lookups, name=name, official_email=f"info@{slug}.edu.ng",
            ce_email=f"ce@{slug}.edu.ng", admin_email=f"ict@{slug}.edu.ng"))
    assert r.status_code == 302, r.content[:2000]
    from institutions.models import Institution

    return Institution.unscoped.get(name=name)


def test_registration_opens_oversight_task(lookups, portal_host, django_capture_on_commit_callbacks, make_user):
    from notifications.models import Notification
    from qa_core.models import QATaskLink
    from tasks.models import Task, TaskAssignee

    ict = _departments()
    director = make_user("ict.director@t.local", role="director", dept="ict")
    ict.director = director
    ict.save(update_fields=["director"])

    inst = _register(lookups, portal_host, django_capture_on_commit_callbacks, name="Oversight One Poly")
    link = QATaskLink.objects.filter(object_id=inst.pk, stage_code="oversight_ict", closed_at__isnull=True).first()
    assert link is not None, "registration must open the oversight task"
    task = link.task
    assert task.title.startswith("[QA] Assign an ICT supervisor — "), task.title
    assert task.state not in task.CLOSED_STATES
    assert TaskAssignee.objects.filter(task=task, user=director).exists(), "the ICT director is the assignee"
    assert Notification.objects.filter(recipient=director).count(), "the director is notified"


def test_oversight_card_carries_the_rotation_suggestion(lookups, portal_host, django_capture_on_commit_callbacks,
                                                        make_user, client_for):
    import assignments.rotation as rotation
    from assignments.models_rotation import RotationMember, RotationPool

    ict = _departments()
    director = make_user("ict.director2@t.local", role="director", dept="ict")
    ict.director = director
    ict.save(update_fields=["director"])
    officer = make_user("ict.officer@t.local", role="officer", dept="ict")
    pool = RotationPool.objects.create(code=rotation.pool_code("oversight_ict", ict), kind="oversight_ict",
                                       department=ict, name="ICT oversight (t)")
    RotationMember.objects.create(pool=pool, user=officer)

    inst = _register(lookups, portal_host, django_capture_on_commit_callbacks, name="Suggestion Poly")
    r = client_for(director).get(f"/qa/institutions/{inst.code}/")
    assert r.status_code == 200, r.content[:2000]
    assert r.context["oversight_suggestion"] == officer, "the rotation suggestion is prefilled"
    html = r.content.decode()
    assert "Suggested by rotation" in html
    assert f'value="{officer.pk}" selected' in html, "the suggested person is selected"

    # No pool (e.g. not seeded): the card still renders, without a suggestion.
    from assignments.models_rotation import RotationDecision

    RotationDecision.objects.filter(pool=pool).delete()
    pool.delete()
    r2 = client_for(director).get(f"/qa/institutions/{inst.code}/")
    assert r2.status_code == 200 and r2.context["oversight_suggestion"] is None


def test_department_mode_director_of_ict_sees_and_opens_registered_institutions(
        lookups, portal_host, django_capture_on_commit_callbacks, make_user):
    """qa.privacy.mode = 'department': the Director of ICT sees every
    institution (X2 full viewer) — otherwise he could not appoint an ICT
    supervisor for a proposed one; a director of another department still sees
    only his department's institutions."""
    from assignments.access import mode_override

    with mode_override("department"):
        ict = _departments()
        director = make_user("x19.d-ict@t.local", role="director", dept="ict", superuser=False)
        ict.director = director
        ict.save(update_fields=["director"])
        other = make_user("x19.d-legal@t.local", role="director", dept="legal")

        inst = _register(lookups, portal_host, django_capture_on_commit_callbacks,
                         name="Visible Oversight Poly")

        mine = Client()
        mine.force_login(director)
        r = mine.get("/qa/institutions/")
        assert r.status_code == 200, r.content[:800]
        assert inst.name in r.content.decode(), "the registered institution must appear in the D-ICT list"
        r = mine.get(f"/qa/institutions/{inst.code}/")
        assert r.status_code == 200, r.content[:2000]
        assert "ICT oversight" in r.content.decode(), "the card that appoints the supervisor"
        other_client = Client()
        other_client.force_login(other)
        r = other_client.get("/qa/institutions/")
        assert inst.name not in r.content.decode(), "another department's director must not see the proposed institution"


def test_appointing_supervisor_closes_the_task(lookups, portal_host, django_capture_on_commit_callbacks,
                                               make_user, client_for):
    from assignments.models import WorkAssignment
    from qa_core.models import QATaskLink

    ict = _departments()
    director = make_user("ict.director3@t.local", role="director", dept="ict")
    ict.director = director
    ict.save(update_fields=["director"])
    officer = make_user("ict.officer3@t.local", role="officer", dept="ict")

    inst = _register(lookups, portal_host, django_capture_on_commit_callbacks, name="Close Task Poly")
    QATaskLink.objects.get(object_id=inst.pk, stage_code="oversight_ict", closed_at__isnull=True)

    r = client_for(director).post(f"/qa/institutions/{inst.code}/assign-oversight/", {"assignee": officer.pk})
    assert r.status_code == 302, r.content[:2000]
    wa = WorkAssignment.objects.filter(institution=inst, kind=WorkAssignment.OVERSIGHT_ICT,
                                       assignee=officer).first()
    assert wa is not None and wa.status in WorkAssignment.OPEN, "the oversight work exists"
    link = QATaskLink.objects.filter(object_id=inst.pk, stage_code="oversight_ict").latest("id")
    assert link.closed_at is not None, "the QA task closed by itself"
    assert link.task.state in link.task.CLOSED_STATES, link.task.state
