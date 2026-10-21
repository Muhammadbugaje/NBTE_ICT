"""Request and ticket visibility (Issue 2).

Run:  pytest workflow/test_request_ticket_visibility.py -q
"""
import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def fin():
    return Department.objects.get_or_create(code="fin", defaults={"name": "Finance"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def drop_perm(role, codename):
    role.permissions.remove(Permission.objects.get(codename=codename))

from tickets.models import Ticket, TicketCategory
from workflow.models import ApprovalChain, Request, RequestCategory, RequestType


@pytest.fixture
def world(ict, fin, roles):
    cat = RequestCategory.objects.create(name="Cv", code="cv")
    chain = ApprovalChain.objects.create(name="chv", code="chv")
    rt = RequestType.objects.create(name="Tv", code="tv", category=cat,
                                    handling_department=ict, approval_chain=chain)
    ict_staff = mk("ics@x.org", ict, roles["staff"])
    fin_staff = mk("fns@x.org", fin, roles["staff"])
    r_ict = Request.objects.create(reference="REQ-V1", request_type=rt, requester=ict_staff,
                                   subject="ICT one", body="b", receiving_department=ict)
    r_fin = Request.objects.create(reference="REQ-V2", request_type=rt, requester=fin_staff,
                                   subject="FIN one", body="b", receiving_department=ict)
    tcat = TicketCategory.objects.create(code="tv", name="TV", handling_department=ict)
    t_ict = Ticket.objects.create(reference="TK-V1", raised_by=ict_staff, category=tcat,
                                  handling_department=ict, subject="t ict", body="b")
    t_fin = Ticket.objects.create(reference="TK-V2", raised_by=fin_staff, category=tcat,
                                  handling_department=ict, subject="t fin", body="b")
    return dict(ict_staff=ict_staff, fin_staff=fin_staff, r_ict=r_ict, r_fin=r_fin,
                t_ict=t_ict, t_fin=t_fin)


def code(client, user, name, pk):
    client.force_login(user)
    return client.get(reverse(name, args=[pk])).status_code


def test_view_all_requests_not_on_director_role(roles):
    codes = set(roles["director"].permissions.values_list("codename", flat=True))
    assert "view_all_requests" not in codes
    assert "view_department_requests" in codes
    es = set(roles["executive-secretary"].permissions.values_list("codename", flat=True))
    assert "view_all_requests" in es


def test_requester_sees_own_other_staff_blocked(client, world, ict, roles):
    assert code(client, world["ict_staff"], "workflow:detail", world["r_ict"].pk) == 200
    other = mk("oth@x.org", ict, roles["staff"])
    assert code(client, other, "workflow:detail", world["r_ict"].pk) == 403


def test_director_sees_own_dept_not_other_directors_dept(client, world, fin, roles):
    other_dir = mk("od@x.org", fin, roles["director"])
    # Finance director: sees the request a Finance person raised (requester dept) ...
    assert code(client, other_dir, "workflow:detail", world["r_fin"].pk) == 200
    # ... but NOT the ICT-internal one (the old view_all_requests let him in).
    assert code(client, other_dir, "workflow:detail", world["r_ict"].pk) == 403


def test_receiving_department_director_sees_incoming(client, world, ict, roles):
    d = mk("rd@x.org", ict, roles["director"])
    assert code(client, d, "workflow:detail", world["r_fin"].pk) == 200
    assert code(client, d, "workflow:detail", world["r_ict"].pk) == 200


def test_es_with_and_without_all_permission(client, world, fin, roles):
    es = mk("es@x.org", fin, roles["executive-secretary"])
    assert code(client, es, "workflow:detail", world["r_ict"].pk) == 200
    drop_perm(roles["executive-secretary"], "view_all_requests")
    es2 = mk("es2@x.org", fin, roles["executive-secretary"])
    assert code(client, es2, "workflow:detail", world["r_ict"].pk) == 403


def test_super_admin_sees_all(client, world):
    su = User.objects.create_superuser(email="su@x.org", password="Xx-123456789")
    assert code(client, su, "workflow:detail", world["r_ict"].pk) == 200
    assert code(client, su, "tickets:detail", world["t_fin"].pk) == 200


def test_tickets_staff_own_only_and_director_department(client, world, ict, fin, roles):
    assert code(client, world["ict_staff"], "tickets:detail", world["t_ict"].pk) == 200
    assert code(client, world["ict_staff"], "tickets:detail", world["t_fin"].pk) == 404
    fin_dir = mk("fd@x.org", fin, roles["director"])
    assert code(client, fin_dir, "tickets:detail", world["t_fin"].pk) == 200   # raised in his dept
    assert code(client, fin_dir, "tickets:detail", world["t_ict"].pk) == 404   # other dept
    ict_dir = mk("id@x.org", ict, roles["director"])
    assert code(client, ict_dir, "tickets:detail", world["t_fin"].pk) == 200   # handled by his dept


def test_search_respects_same_rules(world, fin, roles):
    from core.search import search
    fin_dir = mk("fd2@x.org", fin, roles["director"])
    fin_dir = User.objects.get(pk=fin_dir.pk)
    res = search(fin_dir, "ICT one", types=["request"])["groups"]
    assert not res or not res[0]["results"]
    res = search(fin_dir, "FIN one", types=["request"])["groups"]
    assert res and res[0]["results"]


def test_director_dashboard_counts_only_own_scope(world, ict, fin, roles):
    from dashboards import services as dash
    fin_dir = mk("fd3@x.org", fin, roles["director"])
    seen = dash._requests(User.objects.get(pk=fin_dir.pk))
    assert set(seen.values_list("reference", flat=True)) == {"REQ-V2"}
    es = mk("es3@x.org", fin, roles["executive-secretary"])
    assert dash._requests(User.objects.get(pk=es.pk)).count() == 2
