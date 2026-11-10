"""Phase 0 (U3): workflow request record PDFs live in restricted storage, are
served only by the protected view, and old files move with move_record_pdfs."""
import io

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from workflow.test_fix_m16 import _req, _type, ict, mk, roles  # noqa: F401

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def test_u3_record_pdf_in_restricted_storage(ict, roles, settings):
    from core.storage import RestrictedMediaStorage
    from workflow.models import Request

    field = Request._meta.get_field("record_pdf")
    assert isinstance(field.storage, RestrictedMediaStorage)
    rt, _chain, _step = _type(ict, "u3a")
    owner = mk("owner.u3@t.local", ict, roles["officer"])
    stranger = mk("stranger.u3@t.local", None, roles["officer"])
    req = _req(rt, owner, ict, "REQ-U3-1")
    req.record_pdf.save("record.pdf", ContentFile(PDF), save=True)
    name = req.record_pdf.name
    assert name.startswith("workflow/records/")
    assert storages["restricted"].exists(name) and not storages["default"].exists(name)
    with pytest.raises(ValueError):
        req.record_pdf.url  # no public URL exists

    # The protected view serves it to people who may see the request...
    c = Client()
    c.force_login(owner)
    r = c.get(reverse("workflow:record_pdf", args=[req.pk]))
    assert r.status_code == 200 and b"".join(r.streaming_content).startswith(b"%PDF")
    # ... and not to others.
    c2 = Client()
    c2.force_login(stranger)
    assert c2.get(reverse("workflow:record_pdf", args=[req.pk])).status_code in (403, 404)
    # /media/workflow/records/... is never served.
    assert Client().get(f"/media/{name}").status_code == 404


def test_u3_media_records_url_404_even_if_a_file_is_left(settings):
    settings.SERVE_FILES_LOCALLY = True
    storages["default"].save("workflow/records/leftover.pdf", ContentFile(PDF))
    assert Client().get("/media/workflow/records/leftover.pdf").status_code == 404


def test_u3_move_record_pdfs_command(ict, roles):
    from workflow.models import Request

    rt, _chain, _step = _type(ict, "u3b")
    owner = mk("owner2.u3@t.local", ict, roles["officer"])
    req = _req(rt, owner, ict, "REQ-U3-2")
    # An old row whose file still sits in public media (before migration 0007).
    public = storages["default"]
    old = public.save("workflow/records/oldrecord.pdf", ContentFile(PDF))
    Request.objects.filter(pk=req.pk).update(record_pdf=old)
    orphan = public.save("workflow/records/orphan.pdf", ContentFile(PDF))

    out = io.StringIO()
    call_command("move_record_pdfs", "--dry-run", stdout=out)
    assert "would move 1" in out.getvalue() and public.exists(old)

    call_command("move_record_pdfs", stdout=io.StringIO())
    assert not public.exists(old) and storages["restricted"].exists(old)
    assert not public.exists(orphan) and storages["restricted"].exists(orphan)
    req.refresh_from_db()
    with req.record_pdf.open("rb") as fh:
        assert fh.read().startswith(b"%PDF")
    # Idempotent.
    out = io.StringIO()
    call_command("move_record_pdfs", stdout=out)
    assert "moved 0" in out.getvalue()
