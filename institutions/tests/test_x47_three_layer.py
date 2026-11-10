"""Part 1b/1c (X47): rank is not a permission; initiate → confirm → approve."""
import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db
API = "/api/v1/qa/"
BODY = {"name": "Council of Walkers", "acronym": "COW", "contact_person": "", "email": "", "phone": "",
        "address": "", "is_active": "on"}


def _set(key, value):
    from config.models import ConfigEntry
    ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": value, "value_type": "str"})
    from django.core.cache import cache
    cache.clear()


def _fresh(u):
    from accounts.models import User
    return User.objects.get(pk=u.pk)


@pytest.fixture
def x47(qa_seed, make_user):
    from staff.models import Department

    class NS:
        pass
    ns = NS()
    ns.initiator = make_user("init@t.local", role="officer", dept="dpap", groups=["TVET Support – Initiators"])
    ns.confirmer = make_user("conf@t.local", role="officer", dept="dpap", groups=["TVET Support – Confirmers"])
    ns.low = make_user("low@t.local", role="staff", dept="dpap", groups=["TVET Support – Confirmers"])
    ns.approver = make_user("appr@t.local", role="director", dept="dpap", groups=["TVET Support – Approvers"])
    Department.objects.filter(code="dpap").update(director=ns.approver)
    ns.other_dir = make_user("vdir@t.local", role="director", dept="vtsd", groups=["TVET Support – Approvers"])
    Department.objects.filter(code="vtsd").update(director=ns.other_dir)
    ns.plain_dir = make_user("plain@t.local", role="director", dept="legal")
    ns.es = make_user("es2@t.local", role="executive-secretary", dept="es-office")
    return ns


def web(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


def api(user):
    from api.tokens import refresh_for
    c = APIClient(HTTP_HOST="localhost")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_for(user).access_token}")
    return c


def _start(user):
    from qa_core import actions
    return actions.initiate("institutions.edit_professional_body", actor=_fresh(user), target=None,
                            payload={"op": "form", "data": dict(BODY)})


def test_rank_gives_no_change_rights(x47):
    from institutions import tvet_access
    d = _fresh(x47.plain_dir)
    assert not d.has_perm("institutions.initiate_add_tvet")
    assert not tvet_access.can_initiate(d, "bodies") and not tvet_access.can_initiate(d, "programmes")
    assert web(d).get(reverse("qa_inst:add_tvet")).status_code == 403
    assert web(d).get(reverse("qa_pb:body_new")).status_code == 403
    assert web(d).post(reverse("qa_pb:mapping_new"), {"programme_name": "X"}).status_code == 403
    assert web(d).post(reverse("qa_pb:master_map", args=[1]), {"master": "new"}).status_code == 403
    from django.contrib.auth.models import Permission
    from accounts.models import Role
    perms = Permission.objects.filter(content_type__app_label="institutions", codename__in=tvet_access.CODENAMES)
    assert not Role.objects.filter(code__in=("director", "deputy-director", "officer"), permissions__in=perms).exists()
    assert set(Group.objects.filter(name__startswith="TVET Support").values_list("name", flat=True)) == set(
        tvet_access.GROUPS)


def test_three_layers_apply_only_at_the_last(x47):
    from institutions.models import ProfessionalBody
    from qa_core import actions
    _set("qa.tvet_support.bodies.layers", "3")
    pa = _start(x47.initiator)
    assert pa.status == pa.PENDING and not ProfessionalBody.objects.filter(name=BODY["name"]).exists()
    with pytest.raises(PermissionDenied):                     # self-confirm
        actions.confirm(pa, _fresh(x47.initiator))
    with pytest.raises(PermissionDenied, match="same or higher rank"):
        actions.confirm(pa, _fresh(x47.low))
    pa = actions.confirm(pa, _fresh(x47.confirmer))
    assert pa.stage == "approve" and pa.status == pa.PENDING
    assert not ProfessionalBody.objects.filter(name=BODY["name"]).exists()
    with pytest.raises(PermissionDenied):                     # the confirmer cannot approve too
        actions.confirm(pa, _fresh(x47.confirmer))
    with pytest.raises(PermissionDenied, match="own department"):
        actions.confirm(pa, _fresh(x47.other_dir))
    pa = actions.approve(pa, _fresh(x47.approver))
    assert pa.status == pa.APPLIED and ProfessionalBody.objects.filter(name=BODY["name"]).exists()
    assert [what.split(":")[0] for _w, _u, what in actions.trail(pa)] == ["Initiated", "Confirmed", "Approved",
                                                                         "Applied"]


def test_two_layers_by_default(x47):
    from institutions.models import ProfessionalBody
    from qa_core import actions
    pa = actions.confirm(_start(x47.initiator), _fresh(x47.confirmer))
    assert pa.status == pa.APPLIED and ProfessionalBody.objects.filter(name=BODY["name"]).exists()


def test_es_applies_at_once(x47):
    from institutions.models import ProfessionalBody
    _set("qa.tvet_support.bodies.layers", "3")
    pa = _start(x47.es)
    assert pa.status == pa.APPLIED and ProfessionalBody.objects.filter(name=BODY["name"]).exists()


def test_api_gives_the_same_results(x47):
    from institutions.models import ProfessionalBody
    _set("qa.tvet_support.bodies.layers", "3")
    pa = _start(x47.initiator)
    url = f"{API}confirmations/{pa.pk}/approve/"
    assert api(x47.initiator).post(url, {"remark": ""}, format="json").status_code == 403   # self
    assert api(x47.low).post(url, {"remark": ""}, format="json").status_code == 403        # lower rank
    r = api(x47.confirmer).post(url, {"remark": "ok"}, format="json")
    assert r.status_code == 200, r.content
    detail = api(x47.approver).get(f"{API}confirmations/{pa.pk}/").json()
    assert detail["stage"] == "approve" and detail["can_decide"] is True
    assert not ProfessionalBody.objects.filter(name=BODY["name"]).exists()
    assert api(x47.approver).post(url, {"remark": "approved"}, format="json").status_code == 200
    assert ProfessionalBody.objects.filter(name=BODY["name"]).exists()
