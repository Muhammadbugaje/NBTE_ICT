"""v2.1 Phase E (M1-M5) over the API: the same rules as the website.

Staff door: a director's external memo with an institution filter waits for
the ES; only the ES approves it; compose options list the memo types.
Portal door: the institution lists, opens, acknowledges and downloads its
memo; another institution gets 404.
"""
import pytest
from rest_framework.test import APIClient

from messaging.models import Memo, MemoInstitutionRecipient

from .conftest import BASE, bearer

pytestmark = pytest.mark.django_db
M = BASE + "memos/"


@pytest.fixture(autouse=True)
def _signatures_on_file(monkeypatch, settings, tmp_path):
    # X23 is tested in messaging/test_x23_memo_signature.py; here the ES has one.
    monkeypatch.setattr("documents.signature.blocking_enabled", lambda: False)
    settings.PROTECTED_MEDIA_ROOT = str(tmp_path / "protected")


@pytest.fixture
def world(make_user, portal_host):
    from django.core.management import call_command

    from accounts.models import Role
    from institutions.models import Institution, InstitutionType, Programme
    from staff.models import Department

    call_command("seed_roles", "--sync", verbosity=0)
    Role.objects.get_or_create(code="executive-secretary", defaults={"name": "Executive Secretary", "rank": 999})
    dept = Department.objects.create(name="Programmes T", code="prog-t")
    poly = InstitutionType.objects.get_or_create(code="polytechnic", defaults={"name": "Polytechnic"})[0]

    def inst(code, n):
        i = Institution.unscoped.create(code=code, name=f"{code} Poly", handling_department=dept,
                                        status="approved", institution_type=poly)
        for k in range(n):
            Programme.unscoped.create(institution=i, name=f"P{k}")
        return i

    big, small = inst("BIGP", 6), inst("SMLP", 2)
    return {
        "es": make_user("es.api@t.local", role="executive-secretary"),
        "director": make_user("dir.api@t.local", role="director", dept="prog-t"),
        "officer": make_user("off.api@t.local", role="officer", dept="prog-t"),
        "admin": make_user("admin.api@t.local", superuser=True),
        "big": big, "small": small,
        "big_user": make_user("head@bigp.local", user_type="institution", institution=big),
        "small_user": make_user("head@smlp.local", user_type="institution", institution=small),
        "portal_host": portal_host,
    }


def staff(user):
    return bearer(APIClient(HTTP_HOST="localhost"), user)


def portal(w, user):
    return bearer(APIClient(HTTP_HOST=w["portal_host"]), user)


def test_m3_api_director_external_waits_for_es(world):
    w = world
    opts = staff(w["director"]).get(M + "compose-options/").json()
    assert [k["value"] for k in opts["kinds"]] == ["internal", "external", "general"]
    assert opts["can_send_external"] is False
    assert [k["value"] for k in staff(w["officer"]).get(M + "compose-options/").json()["kinds"]] == ["internal"]

    body = {"subject": "Programme returns", "body": "Please send your returns.", "visibility": "specific_user",
            "kind": "external", "institution_scope": "filter",
            "institution_filter": {"types": ["polytechnic"], "min_programmes": 5}, "action": "publish"}
    r = staff(w["director"]).post(M, body, format="json")
    assert r.status_code == 201, r.content
    memo = r.json()["memo"]
    assert memo["kind"] == "external" and memo["status"] == "awaiting"
    assert memo["institutions"]["label"] == "Polytechnics with more than 5 programmes"
    pk = memo["id"]
    # officers cannot write to institutions
    r = staff(w["officer"]).post(M, body, format="json")
    assert r.status_code in (400, 403)
    # the administrator cannot approve for the ES (404: not their memo to see, or 403)
    assert staff(w["admin"]).post(f"{M}{pk}/approve/", {}, format="json").status_code in (403, 404)
    r = staff(w["es"]).post(f"{M}{pk}/approve/", {"comment": "ok"}, format="json")
    assert r.status_code == 200, r.content
    m = Memo.objects.get(pk=pk)
    assert m.status == Memo.PUBLISHED and m.signed_as == "approved"
    assert list(m.institution_recipients.values_list("institution__code", flat=True)) == ["BIGP"]


def test_m4_api_portal_memos(world):
    from messaging import memos as svc

    w = world
    memo = svc.create_memo(sender=w["es"], subject="Calendar", body="Dates inside.", visibility=Memo.NO_STAFF,
                           kind=Memo.EXTERNAL, institution_scope=Memo.INST_SELECTED, target_institutions=[w["big"]])
    svc.publish_memo(memo, w["es"])
    c = portal(w, w["big_user"])
    r = c.get(BASE + "memos/")
    assert r.status_code == 200 and r.json()["results"][0]["subject"] == "Calendar"
    r = c.get(f"{BASE}memos/{memo.pk}/")
    assert r.status_code == 200 and r.json()["signed_as"] == "from" and r.json()["read_at"]
    r = c.post(f"{BASE}memos/{memo.pk}/acknowledge/")
    assert r.status_code == 200 and r.json()["acknowledged_at"]
    assert MemoInstitutionRecipient.objects.get(memo=memo).acknowledged_by == w["big_user"]
    r = c.get(f"{BASE}memos/{memo.pk}/pdf/")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    other = portal(w, w["small_user"])
    assert other.get(f"{BASE}memos/{memo.pk}/").status_code == 404
    assert other.get(BASE + "memos/").json()["results"] == []
    # staff accounts on the portal door have no institution memos
    assert portal(w, w["officer"]).get(BASE + "memos/").status_code in (403, 404)
