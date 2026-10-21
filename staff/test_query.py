"""Advanced query engine (staff.query) — Phase 12."""
from datetime import date

import pytest
from django.urls import reverse

from staff.models import Department, Designation, SavedQuery, Staff, StaffState
from staff.query import QueryError, build_queryset
from staff.testing import user_with_perms

pytestmark = pytest.mark.django_db


@pytest.fixture
def data():
    ict = Department.objects.create(name="ICT", code="ict")
    hr = Department.objects.create(name="HUMAN RESOURCE", code="hr")
    prob = StaffState.objects.create(name="Probation")
    conf = StaffState.objects.create(name="Confirmed")
    d = Designation.objects.create(name="DIRECTOR", rank=90)
    po = Designation.objects.create(name="PROG. OFF. II", rank=35)

    def mk(no, dept, state, desig, gl, joined, gender="F"):
        return Staff.objects.create(employee_no=no, first_name=no, last_name="X", department=dept, state=state,
                                    designation=desig, grade_level=gl, date_joined_board=joined, gender=gender)
    return {
        "ict": ict, "hr": hr,
        "s1": mk("1", ict, prob, po, "7", date(2024, 1, 1)),
        "s2": mk("2", ict, prob, po, "13", date(2026, 9, 1)),
        "s3": mk("3", hr, prob, po, "8", date(2023, 1, 1)),
        "s4": mk("4", ict, conf, d, "15", date(2010, 1, 1), "M"),
    }


def test_three_condition_and(data):
    conds = [{"field": "state", "op": "eq", "value": "probation"},
             {"field": "department", "op": "eq", "value": "ICT"},
             {"field": "date_joined_board", "op": "lt", "value": "2025-01-01"}]
    assert set(build_queryset(conds, "AND").values_list("employee_no", flat=True)) == {"1"}


def test_or_and_numeric_grade(data):
    conds = [{"field": "grade_level", "op": "gte", "value": "13"},
             {"field": "gender", "op": "eq", "value": "M"}]
    assert set(build_queryset(conds, "OR").values_list("employee_no", flat=True)) == {"2", "4"}
    between = [{"field": "grade_level", "op": "between", "value": "7", "value2": "8"}]
    assert set(build_queryset(between).values_list("employee_no", flat=True)) == {"1", "3"}   # not '13'


def test_operators_and_relative_dates(data):
    assert build_queryset([{"field": "designation", "op": "in", "value": "director, nothing"}]).count() == 1
    assert build_queryset([{"field": "lga", "op": "empty"}]).count() == 4
    assert build_queryset([{"field": "designation_rank", "op": "gte", "value": "90"}]).count() == 1
    assert build_queryset([{"field": "date_joined_board", "op": "gt", "value": "today-12m"}]).count() >= 1


@pytest.mark.parametrize("cond", [
    {"field": "password", "op": "eq", "value": "x"},
    {"field": "user__email", "op": "eq", "value": "x"},
    {"field": "gender", "op": "contains", "value": "x"},          # op not valid for kind
    {"field": "date_of_birth", "op": "eq", "value": "not a date"},
])
def test_rejects_unknown_fields_and_bad_values(data, cond):
    with pytest.raises(QueryError):
        list(build_queryset([cond]))


def test_view_scopes_to_department(client, data):
    head = user_with_perms("head@x.org", "staff.view_staff", department=data["ict"])
    client.force_login(head)
    resp = client.get(reverse("staff:query"), {"field": "state", "op": "eq", "value": "Probation"})
    assert resp.status_code == 200
    page = resp.context["page"]
    assert {s.employee_no for s in page.object_list} == {"1", "2"}      # not the HR one


def test_view_rejects_unknown_field_gracefully(client, data):
    client.force_login(user_with_perms("v@x.org", "staff.view_staff", department=data["ict"]))
    resp = client.get(reverse("staff:query"), {"field": "is_superuser", "op": "eq", "value": "1"})
    assert resp.status_code == 200 and resp.context["error"]


def test_save_and_share_permissions(client, data):
    u = user_with_perms("q@x.org", "staff.view_staff", department=data["ict"])
    client.force_login(u)
    payload = {"name": "Probation", "logic": "AND", "field": "state", "op": "eq", "value": "Probation",
               "value2": ""}
    assert client.post(reverse("staff:query_save"), payload).status_code == 302
    sq = SavedQuery.objects.get(owner=u, name="Probation")
    assert sq.conditions[0]["field"] == "state" and not sq.is_shared
    assert client.post(reverse("staff:query_save"), {**payload, "is_shared": "on"}).status_code == 403


def test_export_needs_permission(client, data):
    client.force_login(user_with_perms("n@x.org", "staff.view_staff", department=data["ict"]))
    assert client.get(reverse("staff:query_export"), {"field": "gender", "op": "eq", "value": "F"}).status_code == 403
    client.force_login(user_with_perms("e@x.org", "staff.view_staff", "staff.export_nominal_roll",
                                       department=data["ict"]))
    resp = client.get(reverse("staff:query_export"), {"field": "gender", "op": "eq", "value": "F"})
    assert resp.status_code == 200 and resp["Content-Type"].startswith("application/vnd.openxml")
