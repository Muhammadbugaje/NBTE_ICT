"""Phase 8 UX: inline file preview, the preview/bulk template tags and the
contact-inbox bulk endpoint."""
import json

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import reverse

from accounts.models import Role, User
from core import file_preview
from core.models import AuditLogEntry, ContactMessage
from staff.models import Department, Staff, StaffAttachment

pytestmark = pytest.mark.django_db


def _user(email, *perms, dept=None):
    role = None
    if perms:
        role = Role.objects.create(code=email, name=email)
        for p in perms:
            role.permissions.add(Permission.objects.get(codename=p))
    u = User.objects.create_user(email, "Pass-word-123", role=role, must_change_password=False)
    if dept is not None:
        u.department = dept
        u.save()
    return u


def _attachment(name, content, ctype):
    dept = Department.objects.create(name="HR p8", code="hr-p8")
    s = Staff.objects.create(employee_no="P8", first_name="A", last_name="B", department=dept)
    att = StaffAttachment.objects.create(staff=s, description="doc",
                                         file=SimpleUploadedFile(name, content, content_type=ctype))
    return att, dept


# ---------------------------------------------------------------- preview
def test_inline_pdf_is_framable_and_audited_as_preview():
    att, dept = _attachment("Report.pdf", b"%PDF-1.4 bytes", "application/pdf")
    c = Client()
    c.force_login(_user("viewer@p8.org", "view_staffattachment", dept=dept))
    r = c.get(att.download_url() + "?inline=1")
    assert r.status_code == 200
    assert r["Content-Disposition"].startswith("inline;")
    assert r["X-Frame-Options"] == "SAMEORIGIN"
    assert r["Cache-Control"] == "private, no-store"
    assert AuditLogEntry.objects.filter(note="file previewed", object_pk=str(att.pk)).exists()


def test_plain_download_keeps_attachment_and_deny_framing():
    att, dept = _attachment("Report.pdf", b"%PDF-1.4 bytes", "application/pdf")
    c = Client()
    c.force_login(_user("viewer2@p8.org", "view_staffattachment", dept=dept))
    r = c.get(att.download_url())
    assert r["Content-Disposition"].startswith("attachment;")
    assert r.get("X-Frame-Options") != "SAMEORIGIN"
    assert AuditLogEntry.objects.filter(note="file downloaded", object_pk=str(att.pk)).exists()


def test_word_file_is_never_served_inline():
    att, dept = _attachment("Letter.docx", b"PK\x03\x04", "application/octet-stream")
    c = Client()
    c.force_login(_user("viewer3@p8.org", "view_staffattachment", dept=dept))
    r = c.get(att.download_url() + "?inline=1")
    assert r["Content-Disposition"].startswith("attachment;")


def test_inline_image_and_text_are_sandboxed():
    att, dept = _attachment("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")
    c = Client()
    c.force_login(_user("viewer4@p8.org", "view_staffattachment", dept=dept))
    r = c.get(att.download_url() + "?inline=1")
    assert r["Content-Disposition"].startswith("inline;")
    assert "sandbox" in r["Content-Security-Policy"]


def test_inline_needs_the_same_permission_as_download():
    att, _dept = _attachment("Report.pdf", b"%PDF-secret", "application/pdf")
    c = Client()
    c.force_login(_user("nobody@p8.org"))
    r = c.get(att.download_url() + "?inline=1")
    assert r.status_code == 403
    assert b"secret" not in r.content


def test_svg_and_html_are_not_previewable():
    assert not file_preview.is_previewable("image/svg+xml")
    assert not file_preview.is_previewable("text/html")
    assert file_preview.is_previewable("application/pdf")
    assert file_preview.text_charset("text/plain") == "text/plain; charset=utf-8"


def test_preview_button_tag_renders_accessible_pill():
    html = Template("{% load ux_tags %}{% preview_button url name size %}").render(
        Context({"url": "/files/x/1/", "name": "Plan <b>.pdf", "size": 2048}))
    assert 'data-preview="/files/x/1/"' in html
    assert "Plan &lt;b&gt;.pdf" in html and "<b>" not in html
    assert "2 KB" in html
    assert 'aria-label="Preview Plan &lt;b&gt;.pdf"' in html
    assert Template("{% load ux_tags %}{% preview_button '' %}").render(Context()) == ""


def test_bulk_tags_render_bar_and_checkbox():
    req = RequestFactory().get("/x/")
    html = Template('{% load ux_tags %}{% bulk_check "b1" 7 "Row" %}{% bulk_all "b1" %}'
                    '{% bulk_bar "b1" "/bulk/" actions="archive:Archive::remove,delete:Delete:danger" noun="row" %}'
                    ).render(Context({"request": req}))
    assert 'form="b1"' in html and 'value="7"' in html
    assert 'data-uxb-all="b1"' in html
    assert 'id="b1"' in html and 'action="/bulk/"' in html
    assert 'data-uxb-removes="1" data-uxb-tone="danger"' in html
    assert 'value="archive"' in html and "csrfmiddlewaretoken" in html


# ---------------------------------------------------------- contact bulk
def _contact(**kw):
    return ContactMessage.objects.create(name="A", email="a@x.org", subject="S", message="Hello", **kw)


def _json_post(client, url, data):
    return client.post(url, data, HTTP_ACCEPT="application/json")


def test_contact_bulk_requires_handler_permission():
    m = _contact()
    c = Client()
    c.force_login(_user("plain@p8.org"))
    r = _json_post(c, reverse("core:contact_bulk"), {"action": "mark_read", "ids": [m.pk]})
    assert r.status_code == 403


def test_contact_bulk_mark_handled_and_read():
    a, b = _contact(), _contact()
    c = Client()
    u = _user("handler@p8.org", "can_handle_contact_messages", "access_all_departments")
    c.force_login(u)
    r = _json_post(c, reverse("core:contact_bulk"), {"action": "mark_handled", "ids": [a.pk, b.pk]})
    body = json.loads(r.content)
    assert r.status_code == 200 and body["ok"] and sorted(body["done"]) == sorted([a.pk, b.pk])
    a.refresh_from_db()
    assert a.is_handled and a.handled_by == u and a.activities.filter(action="handled").exists()


def test_contact_bulk_delete_needs_delete_permission():
    m = _contact()
    c = Client()
    c.force_login(_user("handler2@p8.org", "can_handle_contact_messages", "access_all_departments"))
    r = _json_post(c, reverse("core:contact_bulk"), {"action": "delete", "ids": [m.pk]})
    assert r.status_code == 403
    assert ContactMessage.objects.filter(pk=m.pk).exists()


def test_contact_bulk_delete_with_permission_is_audited():
    m = _contact()
    c = Client()
    c.force_login(_user("admin@p8.org", "can_handle_contact_messages", "access_all_departments",
                        "delete_contactmessage"))
    r = _json_post(c, reverse("core:contact_bulk"), {"action": "delete", "ids": [m.pk]})
    assert json.loads(r.content)["done"] == [m.pk]
    assert not ContactMessage.objects.filter(pk=m.pk).exists()
    assert AuditLogEntry.objects.filter(note="contact message deleted (bulk)", object_pk=str(m.pk)).exists()


def test_contact_bulk_skips_messages_outside_scope():
    other = Department.objects.create(name="Other p8", code="oth-p8")
    mine_dept = Department.objects.create(name="Mine p8", code="mine-p8")
    outside = _contact(handling_department=other)
    c = Client()
    c.force_login(_user("scoped@p8.org", "can_handle_contact_messages", dept=mine_dept))
    r = _json_post(c, reverse("core:contact_bulk"), {"action": "mark_read", "ids": [outside.pk]})
    body = json.loads(r.content)
    assert body["done"] == [] and body["skipped"][0]["id"] == outside.pk
    outside.refresh_from_db()
    assert not outside.is_read


def test_contact_bulk_plain_post_redirects_back_with_message():
    m = _contact()
    c = Client()
    c.force_login(_user("handler3@p8.org", "can_handle_contact_messages", "access_all_departments"))
    r = c.post(reverse("core:contact_bulk"), {"action": "mark_read", "ids": [m.pk],
                                              "next": reverse("core:contact_inbox")})
    assert r.status_code == 302 and r["Location"] == reverse("core:contact_inbox")
