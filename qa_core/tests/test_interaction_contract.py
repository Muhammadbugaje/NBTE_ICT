"""§20 interaction contract, server side: every Phase 1 page renders as a full
page and as an HTMX partial; list views honour URL filters and enforce the
Show-all limit on the server."""
import pytest
from django.core.cache import cache
from django.urls import reverse

from config.models import ConfigEntry
from qa_core.models import NotificationTemplate

from .conftest import hx

INTERNAL_LISTS = [
    ("qa:settings_rules", {"qa-panel": "qa-tabs", "qa-rows": "sh-section"}),  # Phase 7: grouped cards
    ("qa:settings_formulas", {"qa-panel": "qa-test"}),
    ("qa:settings_references", {"qa-panel": "qa-ref-sample"}),
    ("qa:settings_workflows", {"qa-panel": "qa-flow-card"}),
    ("qa:settings_templates", {"qa-panel": "qa-tabs", "qa-rows": "qa-switch"}),
    ("qa:pending_list", {"qa-panel": "qa-tabs", "qa-rows": "qa-empty"}),
]


@pytest.mark.parametrize("name, partials", INTERNAL_LISTS)
def test_internal_pages_full_and_partial(officer, client_for, name, partials):
    c = client_for(officer)
    full = c.get(reverse(name))
    assert full.status_code == 200
    body = full.content.decode()
    assert "<html" in body and "app-sidebar" in body and "qa.js" in body
    for target, marker in partials.items():
        part = c.get(reverse(name), **hx(target))
        assert part.status_code == 200
        text = part.content.decode()
        assert "<html" not in text and "app-sidebar" not in text, (name, target)
        assert marker in text, (name, target, marker)


def test_portal_pages_full_and_partial(qa_seed, make_institution, make_user, portal_host):
    from django.test import Client

    inst = make_institution("DMP")
    user = make_user("a@dmp.local", user_type="institution", institution=inst, role="inst-admin")
    c = Client(HTTP_HOST=portal_host)
    c.force_login(user)
    for path in ("/", "/account/"):
        full = c.get(path).content.decode()
        assert "<html" in full and "qa-topbar" in full and "qa-tabbar" in full
        part = c.get(path, **hx("qa-main")).content.decode()
        assert "<html" not in part and "qa-topbar" not in part and "<title>" in part


def test_rules_filters_live_in_the_url(officer, client_for):
    c = client_for(officer)
    url = reverse("qa:settings_rules")
    text = c.get(url + "?control=dual", **hx("qa-rows")).content.decode()
    assert "qa.fee.programme_processing" not in text  # formulas are on their own tab
    assert "qa.impersonation.max_minutes" in text and "qa.stage.default_sla_hours" not in text
    text = c.get(url + "?q=impersonation", **hx("qa-rows")).content.decode()
    assert "qa.impersonation.allow_write" in text and "qa.ui.max_show_all_rows" not in text
    text = c.get(url + "?type=json", **hx("qa-rows")).content.decode()
    assert "qa.stage.reminder_at_percent" in text and "qa.stage.default_sla_hours" not in text
    text = c.get(url + "?control=locked", **hx("qa-rows")).content.decode()
    assert "provider_confirmation_is_evidence_only" in text and "qa.ui." not in text
    text = c.get(url + "?q=zzzz-nothing", **hx("qa-rows")).content.decode()
    assert "No rules match these filters" in text
    # The full page keeps the filter values so the view is bookmarkable.
    assert 'value="impersonation"' in c.get(url + "?q=impersonation").content.decode()


def test_pending_filters(officer, director, client_for):
    from qa_core import settings_service

    settings_service.save_setting(officer, ConfigEntry.objects.get(key="qa.impersonation.max_minutes"), "20")
    c = client_for(director)
    url = reverse("qa:pending_list")
    assert "qa.impersonation.max_minutes" in c.get(url, **hx("qa-rows")).content.decode()
    assert "qa.impersonation.max_minutes" not in c.get(url + "?who=mine", **hx("qa-rows")).content.decode()
    assert "qa.impersonation.max_minutes" not in c.get(url + "?status=applied", **hx("qa-rows")).content.decode()
    assert "qa.impersonation.max_minutes" in c.get(url + "?age=24h&q=impersonation", **hx("qa-rows")).content.decode()


def _many_templates(n):
    NotificationTemplate.objects.bulk_create(
        [NotificationTemplate(code=f"zz.sample.{i:03d}", channel="in_app", body="x") for i in range(n)])


def test_pagination_25_per_page_and_show_all(officer, client_for):
    _many_templates(40)
    c = client_for(officer)
    # Phase 2 seeds more templates, so count only the 40 made here (the
    # search filter also proves pagination keeps the filter).
    url = reverse("qa:settings_templates") + "?q=zz.sample"
    page1 = c.get(url, **hx("qa-rows")).content.decode()
    assert page1.count('class="qa-rule"') == 25
    assert "Showing <strong>1–25</strong>" in page1 and "Show all" in page1
    page2 = c.get(url + "&page=2", **hx("qa-rows")).content.decode()
    assert page2.count('class="qa-rule"') == 40 - 25
    every = c.get(url + "&all=1", **hx("qa-rows")).content.decode()
    assert every.count('class="qa-rule"') == 40 and "25 per page" in every


def test_show_all_limit_is_enforced_on_the_server(officer, client_for):
    _many_templates(40)
    ConfigEntry.objects.filter(key="qa.ui.max_show_all_rows").update(value="30")
    cache.clear()
    c = client_for(officer)
    text = c.get(reverse("qa:settings_templates") + "?all=1", **hx("qa-rows")).content.decode()
    assert text.count('class="qa-rule"') == 25  # a crafted URL cannot bypass the limit
    assert 'aria-disabled="true"' in text and "limited to 30 rows" in text


def test_timeout_setting_reaches_the_client(officer, client_for):
    ConfigEntry.objects.filter(key="qa.ui.request_timeout_seconds").update(value="9")
    cache.clear()
    page = client_for(officer).get(reverse("qa:settings_rules")).content.decode()
    assert '"data-qa-timeout", "9000"' in page


def test_mutations_answer_with_toast_headers(officer, client_for):
    c = client_for(officer)
    sla = ConfigEntry.objects.get(key="qa.stage.default_sla_hours")
    r = c.post(reverse("qa:setting_save", args=[sla.pk]), {"value": "40"}, **hx("x"))
    assert r.status_code == 200 and "qa:toast" in r["HX-Trigger"]
    r = c.post(reverse("qa:setting_save", args=[sla.pk]), {"value": "forty"}, **hx("x"))
    assert r.status_code == 200 and 'role="alert"' in r.content.decode()  # validation stays in place


def test_optimistic_toggle_endpoint(officer, client_for):
    t = NotificationTemplate.objects.first()
    c = client_for(officer)
    r = c.post(reverse("qa:template_toggle", args=[t.pk]), {}, **hx("x"))
    assert r.status_code == 204
    t.refresh_from_db()
    assert t.is_active is False


def test_settings_screens_need_permission(qa_seed, make_user, client_for):
    plain = make_user("plain@t.local", role="officer", dept="dpap")
    c = client_for(plain)
    for name in ("qa:settings_rules", "qa:pending_list"):
        assert c.get(reverse(name)).status_code == 403
    assert 'data-group="quality-assurance"' not in c.get("/home/").content.decode()  # no QA menu


def test_template_preview_is_sandboxed(officer, client_for):
    t = NotificationTemplate.objects.get(code="pending_action.created", channel="email")
    c = client_for(officer)
    r = c.post(reverse("qa:template_preview", args=[t.pk]),
               {"subject": "{{ reference }}", "body": "<script>alert(1)</script><p>{{ action }}</p>"}, **hx("x"))
    text = r.content.decode()
    assert "<script>alert(1)</script>" not in text  # never inlined into the staff page
    assert 'srcdoc="&lt;script&gt;' in text  # escaped into the attribute, shown only in the sandboxed frame
    assert "sandbox" in text and "srcdoc" in text
    r = c.post(reverse("qa:template_preview", args=[t.pk]), {"subject": "<img src=x onerror=alert(1)>", "body": "x"},
               **hx("x"))
    assert "<img src=x" not in r.content.decode()  # subject is escaped too
    r = c.post(reverse("qa:template_preview", args=[t.pk]), {"subject": "{% include 'base.html' %}", "body": ""},
               **hx("x"))
    assert "would fail" in r.content.decode()
