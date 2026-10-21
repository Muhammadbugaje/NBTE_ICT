"""Phase 6 Checkpoint B: the workspace switcher (main system ↔ QA sidebar)."""
import pytest
from django.test import Client

from qa_core.tests.conftest import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

MAIN_ONLY = "Attendance"          # a main-system sidebar group
QA_ONLY = "QA cases (SOP-03)"     # a QA sidebar group


@pytest.fixture
def qa_staff(qa_seed, make_user):
    return make_user("director.dpap@nbte.local", role="director", dept="dpap")


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _sidebar(html):
    start = html.index('id="app-sidebar"')
    return html[start:html.index("</aside>", start)]


def test_default_is_the_main_system_with_the_switcher(qa_staff):
    html = _sidebar(_client(qa_staff).get("/home/").content.decode())
    assert 'class="app-workspace"' in html
    assert MAIN_ONLY in html and QA_ONLY not in html
    assert "Return to Main System" not in html


def test_switching_to_qa_swaps_the_sidebar_and_is_remembered(qa_staff):
    c = _client(qa_staff)
    response = c.post("/qa/workspace/", {"workspace": "qa", "here": "/home/"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="app-sidebar"' in html and QA_ONLY in html and MAIN_ONLY not in html
    assert "Return to Main System" in html
    assert "qa:toast" in response["HX-Trigger"]
    qa_staff.refresh_from_db()
    assert qa_staff.workspace == "qa"
    # Remembered on the next page load (and on any device: it is on the user).
    again = _sidebar(_client(qa_staff).get("/home/").content.decode())
    assert QA_ONLY in again and MAIN_ONLY not in again


def test_returning_to_main(qa_staff):
    c = _client(qa_staff)
    c.post("/qa/workspace/", {"workspace": "qa", "here": "/home/"}, HTTP_HX_REQUEST="true")
    response = c.post("/qa/workspace/", {"workspace": "main", "here": "/home/"}, HTTP_HX_REQUEST="true")
    html = response.content.decode()
    assert MAIN_ONLY in html and QA_ONLY not in html
    qa_staff.refresh_from_db()
    assert qa_staff.workspace == "main"


def test_returning_to_main_from_a_qa_screen_goes_home(qa_staff):
    c = _client(qa_staff)
    response = c.post("/qa/workspace/", {"workspace": "main", "here": "/qa/cases/"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 204 and response["HX-Redirect"] == "/home/"


def test_qa_pages_always_show_the_qa_sidebar(qa_staff):
    html = _sidebar(_client(qa_staff).get("/qa/cases/").content.decode())
    assert QA_ONLY in html and MAIN_ONLY not in html and "Return to Main System" in html


def test_without_javascript_it_redirects_back(qa_staff):
    response = _client(qa_staff).post("/qa/workspace/", {"workspace": "qa", "here": "/tasks/"})
    assert response.status_code == 302 and response["Location"] == "/tasks/"


def test_here_cannot_redirect_off_site(qa_staff):
    response = _client(qa_staff).post("/qa/workspace/", {"workspace": "qa", "here": "//evil.example/x"})
    assert response["Location"] == "/"


def test_staff_without_qa_access_see_no_switcher_and_cannot_switch(qa_seed, make_user):
    user = make_user("plain@nbte.local")
    c = _client(user)
    assert 'class="app-workspace"' not in c.get("/home/").content.decode()
    assert c.post("/qa/workspace/", {"workspace": "qa", "here": "/home/"}).status_code == 403


def test_unknown_workspace_is_refused(qa_staff):
    assert _client(qa_staff).post("/qa/workspace/", {"workspace": "hr"}).status_code == 400


def test_switch_needs_post_and_login(qa_seed):
    assert Client().post("/qa/workspace/", {"workspace": "qa"}).status_code == 302
