"""v2.1 Part 2.2: strict SSQ assignment (director of the involved department or the ES;
own staff only; cap; accept / return; reminders; unassigned staff never see it)."""
import datetime

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


class NS:
    pass


def _dept(code, name):
    from staff.models import Department
    return Department.objects.get_or_create(code=code, defaults={"name": name})[0]


def _role(code, name, rank):
    from accounts.models import Role
    return Role.objects.get_or_create(code=code, defaults={"name": name, "rank": rank})[0]


def _user(email, role, dept, superuser=False):
    from accounts.models import User
    return User.objects.create(email=email, first_name=email.split("@")[0].title(), last_name="T", user_type="staff",
                               role=role, department=dept, is_superuser=superuser, must_change_password=False)


@pytest.fixture
def w(db):
    from accreditation.models import QARequest, QARequestProgramme, QAType, SSQ, SSQTemplate
    from config.models import ConfigEntry
    from institutions.models import Institution, Programme

    assert ConfigEntry.objects.get(key="qa.privacy.mode").value == "assignment"
    ns = NS()
    ns.dpap, ns.legal, ns.es_office = _dept("dpap", "DPAP"), _dept("legal", "Legal"), _dept("es-office", "ES Office")
    director, officer = _role("director", "Director", 100), _role("officer", "Officer", 20)
    viewers, _ = Group.objects.get_or_create(name="SSQ test reviewers")
    viewers.permissions.add(*Permission.objects.filter(content_type__app_label="accreditation",
                                                       codename__in=("review_ssq", "view_qa_case")))
    director.groups.add(viewers)
    officer.groups.add(viewers)
    ns.dir = _user("dir@t.local", director, ns.dpap)
    ns.off = _user("off@t.local", officer, ns.dpap)
    ns.off2 = _user("off2@t.local", officer, ns.dpap)
    ns.legal_dir = _user("ldir@t.local", director, ns.legal)
    ns.legal_off = _user("loff@t.local", officer, ns.legal)
    ns.es = _user("es@t.local", _role("executive-secretary", "ES", 999), ns.es_office)
    type(ns.dpap).objects.filter(pk=ns.dpap.pk).update(director=ns.dir)
    type(ns.legal).objects.filter(pk=ns.legal.pk).update(director=ns.legal_dir)
    ns.inst = Institution.unscoped.create(code="ALP", name="Alpha Polytechnic", handling_department=ns.dpap,
                                          status="approved")
    qa_type = QAType.objects.create(code="ssq-strict", name="Accreditation")
    template = SSQTemplate.objects.create(qa_type=qa_type, name="SSQ")
    req = QARequest.unscoped.create(reference="T/QAR/9", institution=ns.inst, qa_type=qa_type, status="in_workflow")
    ns.ssqs = []
    for i in range(5):
        prog = Programme.unscoped.create(institution=ns.inst, name=f"Programme {i}")
        row = QARequestProgramme.unscoped.create(qa_request=req, programme=prog)
        ns.ssqs.append(SSQ.unscoped.create(qa_request_programme=row, template=template, status="submitted"))
    ns.ssq = ns.ssqs[0]
    return ns


def _fresh(u):
    from accounts.models import User
    return User.objects.get(pk=u.pk)


def web(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


def api(user):
    from api.tokens import refresh_for
    c = APIClient(HTTP_HOST="localhost")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_for(user).access_token}")
    return c


def test_director_assigns_and_only_the_accepted_officer_sees_it(w):
    from accreditation import ssq_assign
    from assignments import work
    from qa_core import home

    url = reverse("qa_req:ssq", args=[w.ssq.pk])
    assert web(w.off).get(url).status_code == 404                       # unassigned: no sign it exists
    api_url = f"/api/v1/qa/requests/ssq/{w.ssq.pk}/"
    assert api(w.off).get(api_url).status_code == 404
    wa = ssq_assign.assign(w.ssq, by=_fresh(w.dir), officer=w.off)
    assert wa.status == "offered"
    assert web(_fresh(w.off)).get(url).status_code == 404               # offered, not accepted yet
    assert home.w_my_work(_fresh(w.off), home.profile(_fresh(w.off))).count == 1
    work.accept(wa, _fresh(w.off))
    assert web(_fresh(w.off)).get(url).status_code == 200
    assert api(_fresh(w.off)).get(api_url).status_code == 200
    assert web(_fresh(w.off2)).get(url).status_code == 404              # colleague still blind
    assert api(_fresh(w.off2)).get(api_url).status_code == 404
    with pytest.raises(ValidationError, match="already has this SSQ"):
        ssq_assign.assign(w.ssq, by=_fresh(w.dir), officer=w.off2)


def test_ssq_visibility_is_not_inherited_from_department_or_office_permissions(w):
    from accreditation.models import Evidence, Query, SSQ
    from accreditation.qa_services import can_review
    from assignments import access
    from qa_core import home

    evidence = Evidence.unscoped.create(ssq=w.ssq, field_key="evidence")
    from django.contrib.contenttypes.models import ContentType

    query = Query.unscoped.create(
        reference="T/QUERY/SSQ-1", institution=w.inst, qa_request=w.ssq.qa_request,
        content_type=ContentType.objects.get_for_model(Evidence), object_id=str(evidence.pk),
        subject_label="SSQ evidence", title="Evidence question", body="Review this evidence.",
        raised_by=w.dir,
    )
    url = reverse("qa_req:ssq", args=[w.ssq.pk])
    with access.mode_override(access.MODE_DEPARTMENT):
        assert list(SSQ.objects.for_user(_fresh(w.off2))) == []
        assert not Evidence.objects.for_user(_fresh(w.off2)).exists()
        assert not Query.objects.for_user(_fresh(w.off2)).filter(pk=query.pk).exists()
        assert web(_fresh(w.off2)).get(url).status_code == 404
        assert not can_review(w.ssq, _fresh(w.off2))[0]
        assert home.w_ssq(_fresh(w.off2), home.profile(_fresh(w.off2))) is None
        assert set(SSQ.objects.for_user(_fresh(w.dir))) == set(w.ssqs)
        assert evidence in Evidence.objects.for_user(_fresh(w.dir))
        assert home.w_ssq(_fresh(w.dir), home.profile(_fresh(w.dir))).count == len(w.ssqs)
        assert not SSQ.objects.for_user(_fresh(w.legal_dir)).exists()
        assert set(SSQ.objects.for_user(_fresh(w.es))) == set(w.ssqs)
        superadmin = _user("superadmin@t.local", _role("super-admin", "Super admin", 1000),
                           w.es_office, superuser=True)
        assert set(SSQ.objects.for_user(superadmin)) == set(w.ssqs)


def test_assigned_reviewer_and_institution_supervisor_have_distinct_ssq_access(w):
    from accreditation import ssq_assign
    from accreditation.models import SSQ
    from accreditation.qa_services import can_review
    from assignments import work
    from assignments.models import WorkAssignment

    wa = ssq_assign.assign(w.ssq, by=_fresh(w.dir), officer=_fresh(w.off))
    assert list(SSQ.objects.for_user(_fresh(w.off))) == []
    assert web(_fresh(w.off)).get(reverse("qa_req:ssq", args=[w.ssq.pk])).status_code == 404
    assert WorkAssignment.objects.filter(pk=wa.pk, assignee=w.off).exists()
    work.accept(wa, _fresh(w.off))
    assert list(SSQ.objects.for_user(_fresh(w.off))) == [w.ssq]
    assert can_review(w.ssq, _fresh(w.off))[0]
    from qa_core import home
    assert home.w_ssq(_fresh(w.off), home.profile(_fresh(w.off))).count == 1
    assert list(SSQ.objects.for_user(_fresh(w.off2))) == []
    assert not can_review(w.ssq, _fresh(w.off2))[0]

    supervisor = _user("supervisor@t.local", _role("supervisor", "Supervisor", 30), w.es_office)
    work.assign(w.inst, WorkAssignment.OVERSIGHT_ICT, supervisor, department=w.es_office,
                assigned_by=_fresh(w.es), accept_now=True)
    supervisor = _fresh(supervisor)
    assert set(SSQ.objects.for_user(supervisor)) == set(w.ssqs)
    assert not can_review(w.ssq, supervisor)[0]


def test_who_may_assign_whom(w):
    from accreditation import ssq_assign
    with pytest.raises(PermissionDenied):                               # forged: officer of another department
        ssq_assign.assign(w.ssq, by=_fresh(w.dir), officer=w.legal_off)
    with pytest.raises(PermissionDenied):                               # uninvolved director
        ssq_assign.assign(w.ssq, by=_fresh(w.legal_dir), officer=w.legal_off)
    with pytest.raises(PermissionDenied):                               # plain staff
        ssq_assign.assign(w.ssq, by=_fresh(w.off), officer=w.off2)
    wa = ssq_assign.assign(w.ssq, by=_fresh(w.es), officer=w.legal_off)  # the ES assigns anyone
    assert wa.assignee_id == w.legal_off.pk


def test_cap_of_three_open_reviews(w):
    from accreditation import ssq_assign
    for ssq in w.ssqs[:3]:
        ssq_assign.assign(ssq, by=_fresh(w.dir), officer=w.off)
    with pytest.raises(ValidationError, match="cap is 3"):
        ssq_assign.assign(w.ssqs[3], by=_fresh(w.dir), officer=w.off)
    with pytest.raises(ValidationError, match="cap is 3"):              # the ES needs a reason to go over
        ssq_assign.assign(w.ssqs[3], by=_fresh(w.es), officer=w.off)
    wa = ssq_assign.assign(w.ssqs[3], by=_fresh(w.es), officer=w.off, reason="Urgent; agreed with DPAP")
    assert wa.meta.get("over_cap") is True


def test_officer_returns_it_and_the_director_is_told(w, django_capture_on_commit_callbacks):
    from accreditation import ssq_assign
    from assignments import work
    from notifications.models import Notification
    wa = ssq_assign.assign(w.ssq, by=_fresh(w.dir), officer=w.off)
    work.accept(wa, _fresh(w.off))
    with pytest.raises(ValidationError):
        ssq_assign.return_work(wa, _fresh(w.off), "")
    with pytest.raises(PermissionDenied):
        ssq_assign.return_work(wa, _fresh(w.off2), "Not mine")
    ssq_assign.return_work(wa, _fresh(w.off), "Conflict of interest")
    wa.refresh_from_db()
    assert not wa.is_open
    assert Notification.objects.filter(recipient=w.dir, title__startswith="SSQ returned").exists()
    assert web(_fresh(w.off)).get(reverse("qa_req:ssq", args=[w.ssq.pk])).status_code == 404
    assert ssq_assign.current_assignment(w.ssq) is None                 # free to assign again


def test_reminders_once_a_day(w):
    from accreditation import ssq_assign
    from assignments.models import WorkAssignment
    from notifications.models import Notification
    wa = ssq_assign.assign(w.ssq, by=_fresh(w.dir), officer=w.off)
    WorkAssignment.objects.filter(pk=wa.pk).update(assigned_at=timezone.now() - datetime.timedelta(hours=30))
    assert ssq_assign.send_reminders() == 1
    assert Notification.objects.filter(recipient=w.off, title__startswith="Reminder: SSQ review").count() == 1
    assert ssq_assign.send_reminders() == 0


def test_website_and_api_agree(w):
    r = web(w.dir).post(reverse("qa_req:ssq_assign", args=[w.ssq.pk]), {"officer": w.legal_off.pk})
    assert r.status_code == 422                                          # forged on the website
    assert api(w.dir).post(f"/api/v1/qa/requests/ssq/{w.ssq.pk}/assign/", {"officer": w.legal_off.pk},
                           format="json").status_code == 403             # and through the API
    r = api(w.dir).post(f"/api/v1/qa/requests/ssq/{w.ssq.pk}/assign/", {"officer": w.off.pk}, format="json")
    assert r.status_code == 200, r.content
    page = web(w.dir).get(reverse("qa_req:ssq", args=[w.ssq.pk])).content.decode()
    assert "Off" in page and "Offered" in page
