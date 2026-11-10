"""v2.1 R.4 (X44): programme master list, type-ahead add, manual entry scope
check, unmapped queue, back-fill."""
import pytest
from django.core.exceptions import ValidationError

from accreditation.tests.test_v21_phase0 import selecting_app  # noqa: F401
from qa_core.tests.conftest import hx

pytestmark = pytest.mark.django_db


@pytest.fixture
def master_data(qa_seed):
    from institutions.models import (Discipline, ProfessionalBody, ProgrammeCategory, ProgrammeLevel,
                                     ProgrammeMaster)

    nd, hnd = ProgrammeLevel.objects.get(code="nd"), ProgrammeLevel.objects.get(code="hnd")
    eng = Discipline.objects.get_or_create(code="engineering", defaults={"name": "Engineering"})[0]
    health = Discipline.objects.get_or_create(code="health", defaults={"name": "Health Sciences"})[0]
    comp_cat = ProgrammeCategory.objects.get_or_create(name="Computing X", defaults={"discipline": eng})[0]
    health_cat = ProgrammeCategory.objects.get_or_create(name="Health X", defaults={"discipline": health})[0]
    cpn = ProfessionalBody.objects.create(name="Demo Computing Council", acronym="CPN")
    cpn.scope_categories.set([comp_cat])
    coren = ProfessionalBody.objects.create(name="Demo Engineering Council", acronym="COREN")
    coren.scope_disciplines.set([eng])
    coren.scope_levels.set([nd, hnd])
    pcn = ProfessionalBody.objects.create(name="Demo Pharmacy Council", acronym="PCN",
                                          requires_letter_before_commencement=True)
    pcn.scope_disciplines.set([health])
    rows = {}
    for name, lv, body, aliases in (("Computer Science", nd, cpn, "Comp Sci"), ("Computer Science", hnd, cpn, ""),
                                    ("Office Technology and Computer Studies", nd, None, ""),
                                    ("Computer Engineering Technology", nd, coren, ""),
                                    ("Agricultural Technology", nd, None, "")):
        rows[(name, lv.code)] = ProgrammeMaster.objects.create(name=name, level=lv, professional_body=body,
                                                               aliases=aliases, programme_category=comp_cat)

    class D:
        pass

    d = D()
    d.nd, d.hnd, d.cpn, d.coren, d.pcn, d.rows = nd, hnd, cpn, coren, pcn, rows
    d.comp_cat, d.health_cat, d.eng, d.health = comp_cat, health_cat, eng, health
    return d


def test_x44_typeahead_ranking(master_data):
    from institutions.programme_master import search_master

    names = [str(m) for m in search_master("comp sci")]
    assert names[:2] == ["ND Computer Science", "HND Computer Science"]
    assert "ND Agricultural Technology" not in names
    starts = [str(m) for m in search_master("comp")]
    # name starts with "comp" ranks above a word inside the name
    assert starts.index("ND Computer Engineering Technology") < starts.index(
        "ND Office Technology and Computer Studies")
    assert search_master("c") == []


def test_x44_manual_scope_validation(master_data, make_institution):
    from institutions.programme_master import add_programme, body_covers

    inst = make_institution("x44a")
    d = master_data
    assert not body_covers(d.coren, category=d.health_cat, level=d.nd)
    with pytest.raises(ValidationError) as exc:
        add_programme(inst, typed_name="Pharmacy Technician", level=d.nd, category=d.health_cat, body=d.coren)
    assert "COREN does not cover Pharmacy Technician" in " ".join(exc.value.messages)
    assert "PCN" in " ".join(exc.value.messages)
    with pytest.raises(ValidationError):  # a body is required
        add_programme(inst, typed_name="Pharmacy Technician", level=d.nd, category=d.health_cat)
    p = add_programme(inst, typed_name="Pharmacy Technician", level=d.nd, category=d.health_cat, body=d.pcn)
    assert p.not_in_master and p.body_source == "manual" and p.professional_body == d.pcn
    assert p.awaiting_body_letter  # PCN requires its letter before commencement


def test_x44_add_from_master_fills_body(master_data, make_institution):
    from institutions.programme_master import add_programme

    inst = make_institution("x44b")
    m = master_data.rows[("Computer Science", "nd")]
    p = add_programme(inst, master=m)
    assert (p.master, p.professional_body, p.body_source, p.not_in_master) == (m, master_data.cpn, "master", False)
    with pytest.raises(ValidationError):
        add_programme(inst, master=m)  # duplicate


def test_x44_portal_typeahead_and_add(master_data, selecting_app, inst_user, client_for, portal_host):
    from institutions.models import Programme

    c = client_for(inst_user, host=portal_host)
    r = c.get("/application/programmes/master/?q=comp+sci", **hx("qa-prog-list"))
    assert r.status_code == 200 and b"Computer Science" in r.content and b"CPN" in r.content
    m = master_data.rows[("Computer Science", "hnd")]
    r = c.get(f"/application/programmes/master/{m.pk}/", **hx("qa-prog-pick"))
    assert f'name="master" value="{m.pk}"'.encode() in r.content and b"Demo Computing Council" in r.content
    r = c.post("/application/programmes/", {"master": m.pk, "streams": 1}, **hx("qa-prog-card"))
    assert r.status_code == 200, r.content[:400]
    p = Programme.unscoped.get(institution=selecting_app.institution, master=m)
    assert p.professional_body == master_data.cpn
    # manual entry with a body that does not cover it is refused with the scope message
    r = c.post("/application/programmes/", {"name": "Robotics", "level": master_data.nd.pk,
                                            "category": master_data.health_cat.pk, "body": master_data.cpn.pk,
                                            "streams": 1}, **hx("qa-prog-card"))
    assert r.status_code == 422 and b"does not cover Robotics" in r.content


def test_x44_unmapped_queue_and_map(master_data, make_institution, p2, client_for):
    from django.contrib.auth.models import Permission

    from institutions.models import ProgrammeMaster
    from institutions.programme_master import add_programme
    from notifications.models import Notification

    inst = make_institution("x44c")
    p = add_programme(inst, typed_name="Pharmacy Technician", level=master_data.nd,
                      category=master_data.health_cat, body=master_data.pcn)
    assert Notification.objects.filter(recipient=p2.ntird_officer, title__icontains="not in master").exists()
    user = p2.ntird_officer
    user.user_permissions.add(Permission.objects.get(codename="manage_programme_master"))
    from accounts.models import User

    c = client_for(User.objects.get(pk=user.pk))
    r = c.get("/qa/professional-bodies/master/unmapped/")
    assert r.status_code == 200 and b"Pharmacy Technician" in r.content
    r = c.post(f"/qa/professional-bodies/master/unmapped/{p.pk}/map/", {"master": "new"}, **hx("qa-panel"))
    assert r.status_code == 200
    p.refresh_from_db()
    assert not p.not_in_master and p.master == ProgrammeMaster.objects.get(name="Pharmacy Technician")
    assert p.master.professional_body == master_data.pcn


def test_x44_backfill_master_from_programmes(qa_seed, make_institution):
    from institutions.models import (ProfessionalBody, Programme, ProgrammeBodyMapping, ProgrammeLevel,
                                     ProgrammeMaster)
    from institutions.programme_master import backfill

    nd = ProgrammeLevel.objects.get(code="nd")
    a, b = make_institution("bfa"), make_institution("bfb")
    body = ProfessionalBody.objects.create(name="Back Fill Council", acronym="BFC")
    other = ProfessionalBody.objects.create(name="Other Council", acronym="OC")
    ProgrammeBodyMapping.objects.create(body=body, programme_name="Welding Technology", level=nd)
    ProgrammeBodyMapping.objects.create(body=body, programme_name="Twin Body", level=nd)
    ProgrammeBodyMapping.objects.create(body=other, programme_name="Twin Body", level=nd)
    p1 = Programme.unscoped.create(institution=a, name="Welding Technology", level=nd)
    p2 = Programme.unscoped.create(institution=b, name="welding  technology", level=nd)
    Programme.unscoped.create(institution=a, name="Twin Body", level=nd)
    ambiguous = backfill(ProgrammeMaster, Programme, ProgrammeBodyMapping, ProgrammeLevel)
    p1.refresh_from_db(), p2.refresh_from_db()
    assert p1.master_id and p1.master_id == p2.master_id
    assert p1.professional_body == body and p1.body_source == "master"
    assert any("Twin Body" in x for x in ambiguous)
    assert ProgrammeMaster.objects.get(name="Twin Body").professional_body is None
    n = ProgrammeMaster.objects.count()
    backfill(ProgrammeMaster, Programme, ProgrammeBodyMapping, ProgrammeLevel)
    assert ProgrammeMaster.objects.count() == n  # idempotent


def test_x44_programme_landing_handles_master_rows_without_a_body(master_data, p2, client_for):
    from django.contrib.auth.models import Permission
    from accounts.models import User

    user = p2.ntird_officer
    user.user_permissions.add(Permission.objects.get(codename="manage_programme_master"))
    c = client_for(User.objects.get(pk=user.pk))
    response = c.get("/qa/programmes/")
    assert response.status_code == 200
    assert b"Office Technology and Computer Studies" in response.content
    assert b"\xe2\x80\x94" in response.content
