"""Phase 8 UI redesign: grouped settings navigation, notification templates
grouped by family with a live preview, editable site names and home-page
section titles."""
from django.core.cache import cache
from django.template import Context, Template
from django.urls import reverse

from config.models import ConfigEntry
from qa_core.models import NotificationTemplate

from .conftest import hx


def _set(key, value):
    ConfigEntry.objects.update_or_create(key=key, scope="global",
                                         defaults={"value": value, "value_type": "str", "group": "Site"})
    cache.clear()


def test_settings_nav_is_grouped_and_has_the_guide(officer, client_for):
    body = client_for(officer).get(reverse("qa:settings_rules")).content.decode()
    assert "qa-setnav" in body and "qa-tabs" in body
    for group in ("General", "Money", "Institutions", "Accreditation", "Cases &amp; messages"):
        assert f'qa-setnav__group" aria-hidden="true">{group}<' in body
    assert "docs/NBTE_QA_Settings_Guide.pdf" in body and "docs/NBTE_QA_Settings_Guide.docx" in body
    assert "data-preview=" in body  # quick preview of the guide


def test_templates_are_grouped_by_family(officer, client_for):
    text = client_for(officer).get(reverse("qa:settings_templates") + "?all=1", **hx("qa-rows")).content.decode()
    assert 'class="tpl-group__title">Two-step changes<' in text
    assert "tpl-channel--email" in text and "tpl-channel--in_app" in text


def test_template_editor_has_chips_and_live_preview(officer, client_for):
    t = NotificationTemplate.objects.get(code="pending_action.created", channel="email")
    text = client_for(officer).get(reverse("qa:template_edit", args=[t.pk]), **hx("x")).content.decode()
    assert "data-tpl-insert=" in text and "data-tpl-form" in text
    assert 'hx-trigger="load, input changed' in text  # preview refreshes as you type
    # the preview knows sample values for placeholders beyond the basic set
    r = client_for(officer).post(reverse("qa:template_preview", args=[t.pk]),
                                 {"subject": "{{ programme }}", "body": "x"}, **hx("x"))
    assert "ND Computer Science" in r.content.decode()


def test_site_name_comes_from_settings(officer, client_for):
    _set("site.name", "NBTE Digital Office")
    _set("site.qa_name", "QA Directorate")
    body = client_for(officer).get(reverse("qa:settings_rules")).content.decode()
    assert "NBTE Digital Office" in body and "QA Directorate" in body


def test_portal_names_come_from_settings(qa_seed, portal_host):
    from django.test import Client

    _set("site.portal_short_name", "TVET QA Portal")
    body = Client(HTTP_HOST=portal_host).get("/login/").content.decode()
    assert "TVET QA Portal" in body


def test_site_text_falls_back_to_the_default(db):
    tpl = Template('{% load site_text %}{% site_text "home.title.about" %}')
    assert tpl.render(Context()) == "One workspace for the whole directorate."
    _set("home.title.about", "Everything in one place.")
    assert tpl.render(Context()) == "Everything in one place."
    _set("home.title.about", "  ")
    assert tpl.render(Context()) == "One workspace for the whole directorate."
