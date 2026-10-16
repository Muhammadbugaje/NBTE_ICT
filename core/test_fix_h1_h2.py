"""Regression tests for review findings H1 (attachments) and H2 (streams)."""
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import User
from staff.models import Department

pytestmark = pytest.mark.django_db


def _user(email, dept=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789",
                                 first_name="F", last_name="L", department=dept, **kw)
    u.must_change_password = False
    u.save()
    return u


@pytest.fixture
def setup_request():
    from workflow.models import ApprovalChain, Request, RequestCategory, RequestType
    dept = Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]
    requester = _user("req-h1@x.org", dept)
    cat = RequestCategory.objects.create(name="C-h1", code="c-h1")
    chain = ApprovalChain.objects.create(name="ch-h1", code="ch-h1")
    rt = RequestType.objects.create(name="T-h1", code="t-h1", category=cat,
                                    handling_department=dept, approval_chain=chain)
    req = Request.objects.create(reference="REQ-H1", request_type=rt, requester=requester,
                                 subject="s", body="b", receiving_department=dept)
    return requester, req


# ----------------------------------------------------------------- H1
def test_form_rejects_html_upload():
    from workflow.forms import AttachmentForm
    form = AttachmentForm(data={}, files={"file": SimpleUploadedFile("x.html", b"<script>")})
    assert not form.is_valid()
    assert "file" in form.errors


def test_form_accepts_pdf_upload():
    from workflow.forms import AttachmentForm
    form = AttachmentForm(data={}, files={"file": SimpleUploadedFile("x.pdf", b"%PDF-1.4")})
    assert form.is_valid(), form.errors


def test_model_layer_rejects_svg(setup_request):
    from workflow import services
    requester, req = setup_request
    with pytest.raises(ValidationError):
        services.add_attachment(req, requester, SimpleUploadedFile("x.svg", b"<svg/>"))


@pytest.mark.parametrize("label", [
    "workflow.requestattachment", "tasks.taskattachment",
    "tickets.ticketattachment", "assets.assetattachment",
])
def test_attachment_models_use_restricted_storage(label):
    from django.apps import apps
    from core.storage import RestrictedMediaStorage
    model = apps.get_model(label)
    assert isinstance(model._meta.get_field("file").storage, RestrictedMediaStorage)
    assert getattr(model, "PROTECTED_DOWNLOAD", False)


def test_attachment_stored_outside_media_and_downloaded_with_checks(client, settings, setup_request):
    from workflow import services
    requester, req = setup_request
    att = services.add_attachment(req, requester, SimpleUploadedFile("report.pdf", b"%PDF-1.4"))
    assert (Path(settings.PROTECTED_MEDIA_ROOT) / att.file.name).exists()
    assert not (Path(settings.MEDIA_ROOT) / att.file.name).exists()

    url = att.download_url()
    assert url.startswith("/files/")

    # Anonymous -> login redirect
    assert client.get(url).status_code == 302
    # Unrelated user -> forbidden
    client.force_login(_user("stranger-h1@x.org"))
    assert client.get(url).status_code == 403
    # Requester -> file as an attachment
    client.force_login(requester)
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp["Content-Disposition"].startswith("attachment;")
    assert resp["X-Content-Type-Options"] == "nosniff"


def test_detail_page_links_to_protected_download(client, setup_request):
    from workflow import services
    requester, req = setup_request
    att = services.add_attachment(req, requester, SimpleUploadedFile("a.png", b"\x89PNG"))
    client.force_login(requester)
    html = client.get(reverse("workflow:detail", args=[req.pk])).content.decode()
    assert att.download_url() in html
    assert "/media/workflow/attachments" not in html


# ----------------------------------------------------------------- H2
def test_notification_stream_answers_immediately_with_json(client):
    u = _user("h2@x.org")
    client.force_login(u)
    resp = client.get(reverse("notifications:stream"), HTTP_ACCEPT="application/json")
    assert resp.status_code == 200
    assert resp.json() == {"unread": 0}
    assert not resp.streaming


def test_notification_stream_old_eventsource_gets_one_frame(client):
    client.force_login(_user("h2b@x.org"))
    resp = client.get(reverse("notifications:stream"), HTTP_ACCEPT="text/event-stream")
    assert not resp.streaming
    body = resp.content.decode()
    assert body.startswith("retry: ") and "event: unread" in body


def test_request_activity_poll(client, setup_request):
    from workflow import services
    requester, req = setup_request
    client.force_login(requester)
    url = reverse("workflow:request_stream", args=[req.pk])
    first = client.get(url + "?since=0", HTTP_ACCEPT="application/json").json()
    services.add_comment(req, requester, "hello")
    second = client.get(url + f"?since={first['last_pk']}", HTTP_ACCEPT="application/json").json()
    assert second["count"] == 1 and second["last_pk"] > first["last_pk"]
    third = client.get(url + f"?since={second['last_pk']}", HTTP_ACCEPT="application/json").json()
    assert third["count"] == 0


def test_last_event_id_prevents_repeats(client, setup_request):
    from workflow import services
    requester, req = setup_request
    services.add_comment(req, requester, "hello")
    client.force_login(requester)
    url = reverse("workflow:request_stream", args=[req.pk]) + "?since=0"
    body = client.get(url, HTTP_ACCEPT="text/event-stream").content.decode()
    last = int(body.split("id: ")[1].split("\n")[0])
    again = client.get(url, HTTP_ACCEPT="text/event-stream", HTTP_LAST_EVENT_ID=str(last))
    assert "event: activity" not in again.content.decode()


def test_no_stream_view_sleeps():
    import inspect
    import notifications.views, tasks.views, tickets.views, workflow.views
    for mod, name in [(notifications.views, "stream"), (workflow.views, "request_stream"),
                      (tasks.views, "task_stream"), (tickets.views, "ticket_stream")]:
        src = inspect.getsource(getattr(mod, name))
        assert "time.sleep" not in src and "while True" not in src
