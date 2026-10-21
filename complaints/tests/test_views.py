"""Phase 7 — the complaint pages: §20 contract (full page and partials,
URL filters, pagination), portal confidentiality (TR-12), isolation (404),
settings CRUD without code, the SLA dashboard and the leadership dashboards."""
import pytest
from django.test import Client
from django.urls import reverse

from complaints import services
from complaints.models import ComplaintLayer, RoutingRule
from qa_core.tests.conftest import hx


def _raise(user, code, subject="Payment not confirmed after a week", **kw):
    return services.raise_complaint(user, layer=ComplaintLayer.objects.get(code=code), subject=subject,
                                    body=kw.pop("body", "We paid and uploaded the teller a week ago."), **kw)


@pytest.fixture
def portal(portal_host):
    def _c(user):
        c = Client(HTTP_HOST=portal_host)
        c.force_login(user)
        return c
    return _c


# =========================================================================== internal
@pytest.mark.django_db
def test_queue_full_page_partials_and_filters(world, client_for):
    pay = _raise(world["a_user"], "payment-issues")
    _raise(world["a_user"], "portal-access", subject="Cannot sign in at all", priority="critical")
    c = client_for(world["fin_officer"])
    url = reverse("qa_complaints:queue")
    full = c.get(url).content.decode()
    assert "<html" in full and "app-sidebar" in full and "complaints.css" in full and "qa.js" in full
    assert pay.reference in full and "Cannot sign in" not in full  # ICT's complaint is not Finance's
    assert "Complaint queue" in full and 'data-group="qa-complaints"' in full
    panel = c.get(url, **hx("qa-panel")).content.decode()
    assert "<html" not in panel and "qa-tabs" in panel and pay.reference in panel
    rows = c.get(url + "?q=" + pay.reference, **hx("qa-rows")).content.decode()
    assert "<html" not in rows and pay.reference in rows and "cx-sla" in rows
    assert pay.reference not in c.get(url + "?q=zzz-nothing", **hx("qa-rows")).content.decode()
    assert pay.reference not in c.get(url + "?status=closed", **hx("qa-rows")).content.decode()
    assert pay.reference in c.get(url + "?tab=all&sla=on_track", **hx("qa-rows")).content.decode()
    assert pay.reference not in c.get(url + "?tab=all&sla=breached", **hx("qa-rows")).content.decode()
    assert pay.reference in c.get(url + "?layer=payment-issues&priority=high", **hx("qa-rows")).content.decode()
    # The bookmarkable full page keeps the filter values.
    assert f'value="{pay.reference}"' in c.get(url + "?q=" + pay.reference).content.decode()
    # ICT sees the other one; the ES (oversight) sees both.
    es = client_for(world["es"]).get(url + "?tab=all").content.decode()
    assert pay.reference in es and "Cannot sign in" in es


@pytest.mark.django_db
def test_queue_pagination_25_and_show_all(world, client_for):
    for i in range(27):
        _raise(world["a_user"], "payment-issues", subject=f"Bulk payment complaint {i:02d}")
    c = client_for(world["fin_officer"])
    url = reverse("qa_complaints:queue") + "?tab=all"
    page1 = c.get(url, **hx("qa-rows")).content.decode()
    assert page1.count("cx-rowlink") == 25 and "Show all" in page1
    assert c.get(url + "&page=2", **hx("qa-rows")).content.decode().count("cx-rowlink") == 2
    assert c.get(url + "&all=1", **hx("qa-rows")).content.decode().count("cx-rowlink") == 27


@pytest.mark.django_db
def test_detail_actions_messages_and_escalation(world, client_for):
    comp = _raise(world["a_user"], "payment-issues")
    c = client_for(world["fin_officer"])
    url = reverse("qa_complaints:detail", args=[comp.pk])
    full = c.get(url).content.decode()
    assert "<html" in full and comp.subject in full and "Acknowledge" in full and "data-cx-countdown" in full
    assert "<html" not in c.get(url, **hx("qa-panel")).content.decode()
    r = c.post(reverse("qa_complaints:act", args=[comp.pk, "acknowledge"]), {}, **hx("qa-panel"))
    assert r.status_code == 200 and "qa:toast" in r["HX-Trigger"]
    comp.refresh_from_db()
    assert comp.status == "in_progress"
    # A remark is required to resolve: the error stays in place (422, role=alert).
    r = c.post(reverse("qa_complaints:act", args=[comp.pk, "resolve"]), {"remark": ""}, **hx("qa-panel"))
    assert r.status_code == 422 and 'role="alert"' in r.content.decode()
    c.post(reverse("qa_complaints:message", args=[comp.pk]), {"body": "Checked the statement: no line yet.",
                                                             "visibility": "internal"}, **hx("qa-panel"))
    c.post(reverse("qa_complaints:message", args=[comp.pk]), {"body": "We are checking with the bank today.",
                                                             "visibility": "public"}, **hx("qa-panel"))
    page = c.get(url).content.decode()
    assert "Checked the statement" in page and "cx-msg--internal" in page
    # Manual escalation needs the permission; the Director of Finance has it.
    r = c.post(reverse("qa_complaints:escalate", args=[comp.pk]), {"reason": "Urgent"}, **hx("qa-panel"))
    assert r.status_code == 422
    d = client_for(world["fin_director"])
    r = d.post(reverse("qa_complaints:escalate", args=[comp.pk]), {"reason": "Board tomorrow"}, **hx("qa-panel"))
    assert r.status_code == 200 and "Escalations" in r.content.decode()
    # Holds are off: no hold form, and posting one is refused.
    assert "Place the related record on hold" not in d.get(url).content.decode()
    assert d.post(reverse("qa_complaints:hold", args=[comp.pk]), {"reason": "x"}, **hx("qa-panel")).status_code == 422


@pytest.mark.django_db
def test_internal_routes_answer_404_across_departments(world, client_for):
    comp = _raise(world["a_user"], "payment-issues")  # routed to Finance
    c = client_for(world["vtsd_officer"])
    assert c.get(reverse("qa_complaints:detail", args=[comp.pk])).status_code == 404
    for name in ("message", "escalate", "hold"):
        assert c.post(reverse(f"qa_complaints:{name}", args=[comp.pk]), {"body": "x", "reason": "x"}).status_code == 404
    assert c.post(reverse("qa_complaints:act", args=[comp.pk, "acknowledge"])).status_code == 404
    comp.refresh_from_db()
    assert comp.status == "received"
    # External accounts never reach the internal host.
    assert Client(HTTP_HOST="localhost").get(reverse("qa_complaints:queue")).status_code == 302
    plain = client_for(world["a_user"])
    assert plain.get(reverse("qa_complaints:queue")).status_code in (302, 403)


@pytest.mark.django_db
def test_sla_dashboard_and_csv(world, client_for):
    comp = _raise(world["a_user"], "payment-issues")
    d = client_for(world["fin_director"])
    page = d.get(reverse("qa_complaints:dashboard")).content.decode()
    assert "<html" in page and "Complaint deadlines" in page and "cx-trend" in page and "chart-4.4.1" in page
    assert "<html" not in d.get(reverse("qa_complaints:dashboard") + "?days=30", **hx("qa-panel")).content.decode()
    csv = d.get(reverse("qa_complaints:report_csv"))
    assert csv["Content-Type"] == "text/csv" and comp.reference in csv.content.decode()
    # Officers without view_complaint_reports cannot open it.
    assert client_for(world["fin_officer"]).get(reverse("qa_complaints:dashboard")).status_code == 403


@pytest.mark.django_db
def test_leadership_dashboards_show_the_qa_overview(world, client_for, make_user):
    _raise(world["a_user"], "payment-issues")
    director = client_for(world["dpap_director"]).get("/dashboards/director/")
    if director.status_code == 404:  # dashboard routes are named; resolve them
        director = client_for(world["dpap_director"]).get(reverse("dashboards:director"))
    text = director.content.decode()
    assert director.status_code == 200
    assert 'class="qi"' in text and 'id="qi-data"' in text and "qa-insights.js" in text
    assert reverse("qa_complaints:queue") in text
    es = client_for(world["es"]).get(reverse("dashboards:es"))
    assert es.status_code == 200 and 'class="qi"' in es.content.decode()


# =========================================================================== portal
@pytest.mark.django_db
def test_portal_raise_list_detail_and_reply(world, portal):
    c = portal(world["a_user"])
    page = c.get("/complaints/new/").content.decode()
    assert "<html" in page and "Raise a complaint" in page and "Payment issues" in page
    assert "Resource-person assignment" in page  # institutions may raise it too
    assert "<html" not in c.get("/complaints/new/", **hx("qa-main")).content.decode()
    # Knowledge-base suggestions while typing (TR-17).
    tip = c.get("/complaints/suggest/?subject=payment+not+confirmed").content.decode()
    assert "My payment has not been confirmed yet" in tip
    layer = ComplaintLayer.objects.get(code="payment-issues")
    r = c.post("/complaints/new/", {"layer": layer.pk, "subject": "Fee paid, still pending",
                                    "body": "Paid on Monday by transfer; teller uploaded.",
                                    f"q_{layer.pk}_payment_reference": "NBTE-QAP-2026-AAA-000001"})
    assert r.status_code == 302
    comp = services.visible_to(world["a_user"]).get(subject="Fee paid, still pending")
    assert comp.answers == {"payment_reference": "NBTE-QAP-2026-AAA-000001", "amount": ""} or \
        comp.answers == {"payment_reference": "NBTE-QAP-2026-AAA-000001"}
    assert r["Location"].endswith(f"/complaints/{comp.pk}/")
    listing = c.get("/complaints/").content.decode()
    assert comp.reference in listing and "qa-topbar" in listing
    assert comp.reference in c.get("/complaints/?q=" + comp.reference, **hx("cx-rows")).content.decode()
    assert "<html" not in c.get("/complaints/", **hx("qa-main")).content.decode()
    # The validation error stays on the form.
    bad = c.post("/complaints/new/", {"layer": layer.pk, "subject": "x", "body": "short"})
    assert bad.status_code == 422 and 'role="alert"' in bad.content.decode()

    # Internal notes never reach the portal (TR-12).
    services.add_message(comp, world["fin_officer"], "INTERNAL: bank statement not imported yet",
                         visibility="internal")
    services.add_message(comp, world["fin_officer"], "We are checking with the bank.", visibility="public")
    detail = c.get(f"/complaints/{comp.pk}/").content.decode()
    assert "We are checking with the bank." in detail and "INTERNAL" not in detail
    assert "Finance" in detail  # the department is shown …
    assert world["fin_officer"].email not in detail and "Fo Test" not in detail  # … the officer is not (default)
    # Request information → the raiser answers on the portal → clock resumes.
    services.act(comp, "acknowledge", world["fin_officer"])
    services.act(comp, "request_info", world["fin_officer"], remark="Which bank did you pay from?")
    detail = c.get(f"/complaints/{comp.pk}/").content.decode()
    assert "Which bank did you pay from?" in detail and "Waiting for your reply" in detail
    r = c.post(f"/complaints/{comp.pk}/reply/", {"body": "First Bank, Kaduna branch."}, **hx("qa-main"))
    assert r.status_code == 200 and "qa:toast" in r["HX-Trigger"]
    comp.refresh_from_db()
    assert comp.status == "in_progress"
    # Resolve → the raiser confirms with a rating.
    services.act(comp, "resolve", world["fin_officer"], remark="Confirmed; receipt issued.")
    page = c.get(f"/complaints/{comp.pk}/").content.decode()
    assert "NBTE says this is resolved" in page and "Reopen" in page
    r = c.post(f"/complaints/{comp.pk}/act/confirm/", {"satisfaction": "5"}, **hx("qa-main"))
    assert r.status_code == 200
    comp.refresh_from_db()
    assert comp.status == "closed" and comp.satisfaction == 5
    # Handler steps cannot be taken from the portal.
    assert c.post(f"/complaints/{comp.pk}/act/resolve/").status_code == 422


@pytest.mark.django_db
def test_portal_officer_name_setting(world, portal):
    from django.core.cache import cache

    from config.models import ConfigEntry

    comp = _raise(world["a_user"], "payment-issues")
    c = portal(world["a_user"])
    officer_name = world["fin_officer"].get_full_name()
    assert officer_name not in c.get(f"/complaints/{comp.pk}/").content.decode()
    ConfigEntry.objects.filter(key="qa.complaints.show_officer_name_to_raiser").update(value="true")
    cache.clear()
    assert officer_name in c.get(f"/complaints/{comp.pk}/").content.decode()


@pytest.mark.django_db
def test_portal_isolation_and_about_prefill(world, portal):
    from qa_core import timeline

    a_event = timeline.record(world["a"], "x", public_text="Alpha milestone", visibility="public")
    comp = _raise(world["a_user"], "payment-issues")
    b = portal(world["b_user"])
    for path in (f"/complaints/{comp.pk}/", f"/complaints/{comp.pk}/reply/", f"/complaints/{comp.pk}/act/withdraw/"):
        assert b.get(path).status_code in (404, 405)
        assert b.post(path, {"body": "let me in"}).status_code in (404, 405)  # never 200 / 403
    assert comp.reference not in b.get("/complaints/").content.decode()
    # "Raise a complaint about this": A's own record is linked, B's is ignored.
    a = portal(world["a_user"])
    page = a.get(f"/complaints/new/?about=timeline:{a_event.pk}").content.decode()
    assert "About:" in page and "Alpha milestone" in page
    page = b.get(f"/complaints/new/?about=timeline:{a_event.pk}").content.decode()
    assert "Alpha milestone" not in page
    # The timeline row carries the link.
    assert "about=timeline:" in a.get("/timeline/").content.decode()


@pytest.mark.django_db
def test_portal_knowledge_base_and_nav(world, portal):
    c = portal(world["a_user"])
    page = c.get("/help/").content.decode()
    assert "Help articles" in page and "My payment has not been confirmed yet" in page
    rows = c.get("/help/?q=password", **hx("cx-kb-rows")).content.decode()
    assert "I cannot sign in" in rows and "<html" not in rows
    art = c.get("/help/payment-not-confirmed/").content.decode()
    assert "Director of Finance" in art
    c.post("/help/payment-not-confirmed/", {"helpful": "yes"})
    from complaints.models import KnowledgeArticle
    assert KnowledgeArticle.objects.get(slug="payment-not-confirmed").helpful == 1
    # Resource persons do not see institution-only articles.
    rp = portal(world["rp"])
    assert rp.get("/help/payment-not-confirmed/").status_code == 404
    assert "Complaints" in rp.get("/").content.decode()
    assert 'href="/complaints/"' in c.get("/").content.decode()


@pytest.mark.django_db
def test_portal_resource_person_flow(world, portal):
    rp = portal(world["rp"])
    page = rp.get("/complaints/new/").content.decode()
    assert "Visit scheduling" in page and "Payment issues" not in page
    layer = ComplaintLayer.objects.get(code="visit-scheduling")
    r = rp.post("/complaints/new/", {"layer": layer.pk, "subject": "Visit clashes with exams",
                                     "body": "The visit week clashes with my university exams."})
    assert r.status_code == 302
    comp = services.visible_to(world["rp"]).get()
    assert comp.institution is None
    assert rp.get(f"/complaints/{comp.pk}/").status_code == 200


# =========================================================================== settings (no code)
@pytest.mark.django_db
def test_settings_complaints_crud(world, client_for):
    admin = client_for(world["admin"])
    url = reverse("qa_set:complaints")
    page = admin.get(url).content.decode()
    assert "<html" in page and "Payment issues" in page and "qa-tabs" in page
    panel = admin.get(url + "?section=routing", **hx("qa-panel")).content.decode()
    assert "<html" not in panel and "Finance and Account Department" in panel
    # A new layer, then a routing rule for it (with a formula condition).
    r = admin.post(url + "?section=layers", {"section": "layers", "name": "Certificates", "code": "",
                                              "default_priority": "normal", "sort_order": 200, "is_active": "on",
                                              "custom_fields_json": "[]"}, **hx("qa-panel"))
    assert r.status_code == 200, r.content.decode()[:500]
    layer = ComplaintLayer.objects.get(code="certificates")
    from staff.models import Department
    bad = admin.post(url + "?section=routing", {"section": "routing", "order": 5, "layer": layer.pk,
                                                 "condition": "priority ==", "target_kind": "department",
                                                 "target_department": Department.objects.get(code="legal").pk,
                                                 "is_active": "on"}, **hx("qa-panel"))
    assert bad.status_code == 422 and "cannot be checked" in bad.content.decode()
    ok = admin.post(url + "?section=routing", {"section": "routing", "order": 5, "layer": layer.pk,
                                                "condition": 'priority == "normal"', "target_kind": "department",
                                                "target_department": Department.objects.get(code="legal").pk,
                                                "is_active": "on"}, **hx("qa-panel"))
    assert ok.status_code == 200
    rule = RoutingRule.objects.get(layer=layer)
    # Edit form and save
    form = admin.get(reverse("qa_set:complaints_edit", args=["routing", rule.pk]), **hx("cxs-edit"))
    assert form.status_code == 200 and "Save" in form.content.decode()
    r = admin.post(reverse("qa_set:complaints_edit", args=["routing", rule.pk]),
                   {"order": 6, "layer": layer.pk, "condition": "", "target_kind": "department",
                    "target_department": Department.objects.get(code="legal").pk, "is_active": "on"},
                   **hx("cxs-edit"))
    assert r.status_code == 200 and r["HX-Retarget"] == "#qa-panel"
    rule.refresh_from_db()
    assert rule.order == 6 and rule.condition == ""
    # A plain officer can read but not change.
    reader = client_for(world["dpap_director"])
    assert reader.get(url).status_code in (200, 403)
    officer = client_for(world["fin_officer"])
    assert officer.get(url).status_code == 403  # no QA settings permission at all
