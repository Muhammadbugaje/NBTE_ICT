"""Phase 6 Checkpoint D: the §20 interaction contract on the lists that fell
short of it (search + three facets + 25 rows with "Show all"), and the design
layer (tab bar, workspace switcher) on every internal page."""
import pytest
from django.test import Client

from qa_core.tests.conftest import *  # noqa: F401,F403
from qa_core.tests.phase4_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db


def _assert_contract(html, facets, search="q"):
    assert f'name="{search}"' in html, f"no search box ({search})"
    for facet in facets:
        assert f'name="{facet}"' in html, f"missing facet {facet}"


INTERNAL_LISTS = [
    ("/qa/requests/ssq/", ["status", "type", "level"], {"status": "all", "type": "accreditation", "level": "nd",
                                                         "q": "x"}),
    ("/qa/finance/webhooks/", ["provider", "valid", "period"], {"provider": "paystack", "valid": "yes",
                                                               "period": "7d", "q": "x"}),
    ("/qa/outbox/", ["category", "period", "to"], {"category": "qa", "period": "1d", "to": "a@b.c", "q": "x"}),
    ("/qa/settings/catalogue/", ["show", "programme", "kind"], {"show": "all", "kind": "facility", "q": "x"}),
]


@pytest.mark.parametrize("url,facets,params", INTERNAL_LISTS)
def test_internal_lists_meet_the_contract(qa_seed, make_user, url, facets, params):
    c = Client()
    c.force_login(make_user("root@nbte.local", superuser=True))
    response = c.get(url)
    assert response.status_code == 200, url
    _assert_contract(response.content.decode(), facets)
    filtered = c.get(url, params)
    assert filtered.status_code == 200


@pytest.fixture
def inst_client(qa_seed, make_user, make_institution, portal_host):
    inst = make_institution("KDP")
    user = make_user("admin@kdp.local", user_type="institution", institution=inst, role="inst-admin")
    c = Client(HTTP_HOST=portal_host)
    c.force_login(user)
    return c


PORTAL_LISTS = [
    ("/qa-requests/", ["show", "type", "level"], {"show": "all", "type": "accreditation", "level": "nd", "q": "x"}),
    ("/queries/", ["status", "about", "due"], {"status": "all", "about": "request", "due": "overdue", "q": "x"}),
    ("/inspections/", ["status", "when", "year"], {"status": "scheduled", "when": "past", "year": "2026", "q": "x"}),
    ("/timeline/", ["kind", "period", "by"], {"kind": "payment", "period": "30d", "by": "nbte", "q": "x"}),
    ("/documents/", ["state", "need", "dept"], {"state": "missing", "q": "x"}),
]


@pytest.mark.parametrize("url,facets,params", PORTAL_LISTS)
def test_portal_lists_meet_the_contract(inst_client, url, facets, params):
    response = inst_client.get(url)
    assert response.status_code == 200, url
    _assert_contract(response.content.decode(), facets)
    assert inst_client.get(url, params).status_code == 200
    rows = inst_client.get(url, params, HTTP_HX_REQUEST="true")
    assert rows.status_code == 200


def test_portal_request_list_pages_at_25_with_show_all(inst_client):
    """A long list shows 25 rows and a 'Show all' link; ?all=1 shows every row."""
    from accreditation.models import QARequest, QAType
    from institutions.models import Institution

    inst = Institution.unscoped.get(code="KDP")
    qa_type = QAType.objects.first()
    for i in range(30):
        QARequest.unscoped.create(reference=f"T/QAR/{i:03d}", institution=inst, qa_type=qa_type, status="draft")
    html = inst_client.get("/qa-requests/", {"show": "all"}).content.decode()
    assert html.count('class="qa-rowlink"') == 25 and "all=1" in html
    every = inst_client.get("/qa-requests/", {"show": "all", "all": "1"}).content.decode()
    assert every.count('class="qa-rowlink"') == 30


def test_internal_pages_carry_the_tab_bar_and_design_layer(qa_seed, make_user):
    c = Client()
    c.force_login(make_user("director.dpap@nbte.local", role="director", dept="dpap"))
    html = c.get("/home/").content.decode()
    assert 'class="app-tabbar"' in html and ("css/apple.css" in html or "css/nbte-finish.bundle.css" in html) and "js/apple.js" in html
    assert 'data-drawer-toggle="app-sidebar"' in html
    qa = c.get("/qa/cases/").content.decode()
    assert ">Cases<" in qa and ">Requests<" in qa  # the QA workspace tabs


def test_case_detail_names_the_stage_out_of_seventeen(p4):
    c = Client()
    director = p4.people.director if hasattr(p4.people, "director") else None
    from accounts.models import User

    c.force_login(director or User.objects.filter(is_superuser=True).first()
                  or User.objects.create_superuser("root2@nbte.local", "x", first_name="R", last_name="T"))
    html = c.get(f"/qa/cases/{p4.case.pk}/").content.decode()
    assert "of 17 ·" in html
