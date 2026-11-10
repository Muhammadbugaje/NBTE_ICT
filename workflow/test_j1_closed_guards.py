"""v2.1 J1 / T-CONS-6: closed requests refuse comments and files in the
service layer (web and API share it); the record PDF exists for every closed request."""
import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from tickets.conftest import org_c  # noqa: F401
from tasks.conftest import depts, people, roles  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def closed_req(org_c):
    from workflow.models import ApprovalChain, Request, RequestCategory, RequestType
    chain = ApprovalChain.objects.create(name="c", code="c-j1")
    rt = RequestType.objects.create(name="Fix", code="fix-j1", category=RequestCategory.objects.create(
        name="Fix", code="fix-j1"), handling_department=org_c["ict"], approval_chain=chain)
    req = Request.objects.create(reference="REQ-J1-1", request_type=rt, requester=org_c["hr_officer"],
                                 subject="Closed one", body="b", receiving_department=org_c["ict"],
                                 status=Request.CANCELLED)
    return req


def test_j1_service_refuses_comment_and_attachment(closed_req, org_c):
    from workflow import services as ws
    with pytest.raises(ValidationError):
        ws.add_comment(closed_req, org_c["hr_officer"], "late")
    with pytest.raises(ValidationError):
        ws.add_attachment(closed_req, org_c["hr_officer"], SimpleUploadedFile("a.pdf", b"%PDF-1.4"))
    assert not closed_req.comments.exists() and not closed_req.attachments.exists()


def test_j1_api_cannot_comment_on_closed(closed_req, org_c):
    from rest_framework.test import APIClient
    c = APIClient()
    c.force_authenticate(org_c["hr_officer"])
    r = c.post(f"/api/v1/requests/{closed_req.pk}/comments/", {"body": "late", "is_internal": False}, format="json")
    assert r.status_code in (400, 403, 404)
    assert not closed_req.comments.exists()


def test_j1_closed_request_page_read_only_with_pdf(closed_req, org_c, client):
    client.force_login(org_c["hr_officer"])
    html = client.get(f"/workflow/{closed_req.pk}/").content.decode()
    assert "data-closed-readonly" in html and f"/workflow/{closed_req.pk}/record.pdf" in html
    r = client.get(f"/workflow/{closed_req.pk}/record.pdf")
    assert r.status_code == 200
