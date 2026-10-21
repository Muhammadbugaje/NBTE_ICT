"""Phase 6 Checkpoint C: the portal's public landing page, the registration
toggle, the how-to page with its diagrams, and guide downloads."""
import os

import pytest
from django.test import Client

from qa_core.tests.conftest import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal(portal_host):
    return Client(HTTP_HOST=portal_host)


def _set(key, value):
    from config.models import ConfigEntry

    ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": value})
    from django.core.cache import cache

    cache.clear()


def test_visitors_get_the_landing_page(qa_seed, portal):
    response = portal.get("/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Accreditation you can follow" in html
    assert 'href="/login/"' in html and 'href="/how-to/"' in html and 'href="/register/"' in html


def test_signed_in_institution_gets_the_dashboard(qa_seed, portal, make_user, make_institution):
    inst = make_institution("KDP")
    user = make_user("admin@kdp.local", user_type="institution", institution=inst, role="inst-admin")
    portal.force_login(user)
    html = portal.get("/").content.decode()
    assert "Accreditation you can follow" not in html


def test_registration_closed_hides_the_form_and_shows_the_contact(qa_seed, portal):
    _set("qa.portal.self_registration_enabled", "false")
    _set("qa.portal.registration_contact_email", "qa@nbte.example.gov.ng")
    _set("qa.portal.registration_contact_phone", "0800 000 0000")
    landing = portal.get("/").content.decode()
    assert 'href="/register/"' not in landing
    message = ("Institution registration is currently managed by NBTE. Please contact qa@nbte.example.gov.ng or "
               "0800 000 0000 to begin your registration.")
    assert message in landing
    register = portal.get("/register/").content.decode()
    assert message in register
    assert 'name="admin_email"' not in register
    # A POST cannot register while it is closed.
    assert portal.post("/register/", {"name": "Sneaky Poly"}).status_code == 200
    from institutions.models import Institution

    assert not Institution.unscoped.filter(name="Sneaky Poly").exists()


def test_registration_open_shows_the_form(qa_seed, portal):
    _set("qa.portal.self_registration_enabled", "true")
    html = portal.get("/register/").content.decode()
    assert 'name="admin_email"' in html


def test_closed_message_falls_back_to_the_site_contact(qa_seed, portal):
    _set("qa.portal.self_registration_enabled", "false")
    _set("qa.portal.registration_contact_email", "")
    _set("site.email", "ict@nbte.example.gov.ng")
    assert "Please contact ict@nbte.example.gov.ng" in portal.get("/").content.decode()


def test_how_to_has_every_section_and_accessible_diagrams(qa_seed, portal):
    response = portal.get("/how-to/")
    assert response.status_code == 200
    html = response.content.decode()
    for anchor in ("getting-started", "onboarding", "qa-requests", "qa-types", "inside-nbte", "payments",
                   "joint-qa", "downloads", "questions"):
        assert f'id="{anchor}"' in html
    assert html.count('role="img"') == 4
    assert "<title id=" in html and "Programme record update" not in html  # labels are plain words
    for name in ("Resource Inspection", "Accreditation", "Reaccreditation", "Increase in Carrying Capacity"):
        assert name in html
    assert "17-stage" in html or "17 stages" in html


def test_how_to_lists_guides_and_downloads_them(qa_seed, portal, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    html = portal.get("/how-to/").content.decode()
    assert "Coming soon" in html
    assert portal.get("/guides/starter/").status_code == 404
    from qa_core.portal_public import guide_path

    os.makedirs(os.path.dirname(guide_path("starter")), exist_ok=True)
    with open(guide_path("starter"), "wb") as fh:
        fh.write(b"PK\x03\x04 fake docx")
    html = portal.get("/how-to/").content.decode()
    assert 'href="/guides/starter/"' in html
    response = portal.get("/guides/starter/")
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    assert portal.get("/guides/../../etc/passwd/").status_code == 404
    assert portal.get("/guides/unknown/").status_code == 404


def test_public_pages_are_not_on_the_internal_host(qa_seed):
    assert Client().get("/how-to/").status_code == 404


# ---------------------------------------------------------------------------
# Checkpoint E: the Word guides
# ---------------------------------------------------------------------------
def test_generate_institution_guides_builds_four_word_files(qa_seed, tmp_path, settings, portal):
    import io

    from django.core.management import call_command
    from docx import Document

    settings.MEDIA_ROOT = tmp_path
    out = io.StringIO()
    call_command("generate_institution_guides", stdout=out)
    from qa_core.portal_public import GUIDES, guide_path

    for slug, title, _summary in GUIDES:
        path = guide_path(slug)
        assert os.path.exists(path), slug
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs)
        assert title.split(" (")[0] in text and "Getting help" in text
        assert len(doc.inline_shapes) >= 3  # logo + screenshots / diagrams
    # Offered for download on the how-to page.
    html = portal.get("/how-to/").content.decode()
    assert html.count('href="/guides/') == 4 and "Coming soon" not in html
    response = portal.get("/guides/payments/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")


def test_guides_build_without_screenshots(qa_seed, tmp_path):
    import io

    from django.core.management import call_command
    from docx import Document

    out = io.StringIO()
    call_command("generate_institution_guides", "--only", "payments", "--output-dir", str(tmp_path),
                 "--images-dir", str(tmp_path / "none"), stdout=out)
    assert "not found" in out.getvalue()
    text = "\n".join(p.text for p in Document(tmp_path / "nbte-qa-payments-guide.docx").paragraphs)
    assert "[Screenshot:" in text
