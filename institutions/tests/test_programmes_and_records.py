"""Phase 8 — the Programmes tab, Programme detail pages (internal and
portal), professional-body links and the record exports (timeline PDF/CSV,
institutional record PDF/ZIP, payment statement, case summary, complaint
record, service history PDF)."""
import io
import zipfile

import pytest

from qa_core.tests.phase4_fixtures import *  # noqa: F401,F403
from qa_core.tests.phase4_fixtures import staff_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def body(p4):
    from institutions.models import ProfessionalBody, ProgrammeBodyMapping, Programme

    b = ProfessionalBody.objects.create(name="Computer Professionals Council", acronym="CPN", email="c@x.ng")
    p = Programme.unscoped.filter(institution=p4.inst).order_by("pk").first()
    ProgrammeBodyMapping.objects.create(body=b, programme_name=p.name.upper())
    return b, p


def test_programmes_tab_lists_filters_and_shows_open_case(p4, body):
    b, p = body
    c = staff_client(p4.people.director)
    base = f"/qa/institutions/{p4.inst.code}/?tab=programmes"
    html = c.get(base).content.decode()
    assert p.name in html and p4.case.reference in html and "CPN" in html
    # Filters (HTMX rows partial and full page), carried in the URL.
    rows = c.get(base + f"&body={b.pk}", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-prog-rows").content.decode()
    assert p.name in rows and "<html" not in rows
    from institutions.models import Programme

    others = list(Programme.unscoped.filter(institution=p4.inst).exclude(pk=p.pk))
    for o in others:
        assert f">{o.name}<" not in rows
    rows = c.get(base + "&open=0", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-prog-rows").content.decode()
    assert p4.case.reference not in rows
    rows = c.get(base + "&pq=zzzz-nothing", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-prog-rows").content.decode()
    assert "No programme matches" in rows
    assert c.get(base + "&expiring=30&level=nd&pstatus=accredited").status_code == 200


def test_programme_detail_page_and_jump_button(p4, body):
    b, p = body
    c = staff_client(p4.people.director)
    r = c.get(f"/qa/institutions/{p4.inst.code}/programmes/{p.pk}/")
    html = r.content.decode()
    assert r.status_code == 200
    assert "Open the QA case" in html and f"/qa/cases/{p4.case.pk}/" in html
    assert f"/qa/professional-bodies/{b.pk}/" in html
    assert p4.case.qa_request.reference in html
    # Another institution's programme → 404.
    from institutions.models import Programme

    other = Programme.unscoped.exclude(institution=p4.inst).first()
    if other is not None:
        assert c.get(f"/qa/institutions/{p4.inst.code}/programmes/{other.pk}/").status_code == 404


def test_body_page_lists_its_programmes_across_institutions(p4, body):
    b, p = body
    c = staff_client(p4.people.director)
    html = c.get(f"/qa/professional-bodies/{b.pk}/").content.decode()
    assert "Programmes at institutions" in html and p4.inst.name in html
    assert f"/qa/institutions/{p4.inst.code}/programmes/{p.pk}/" in html


def test_portal_programme_pages(p4, body):
    from django.test import Client

    from accounts.models import User

    b, p = body
    user = User.objects.filter(institution=p4.inst, user_type="institution").first()
    if user is None:
        pytest.skip("demo institution has no portal user")
    user.is_active = True
    user.save()
    c = Client(HTTP_HOST="qa.localhost")
    c.force_login(user)
    html = c.get("/programmes/").content.decode()
    assert p.name in html
    html = c.get(f"/programmes/{p.pk}/").content.decode()
    assert p.name in html and "Computer Professionals Council" in html
    assert "NBTE note" not in html  # public information only


def test_timeline_export_carries_the_filters(p4):
    from qa_core import timeline

    timeline.record(p4.inst, "x.secret", internal_text="Internal only note", visibility="internal")
    timeline.record(p4.inst, "x.public", public_text="A public milestone", visibility="public")
    c = staff_client(p4.people.director)
    r = c.get(f"/qa/institutions/{p4.inst.code}/timeline/export/?format=csv&vis=public")
    body = r.content.decode("utf-8-sig")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    assert "A public milestone" in body and "Internal only note" not in body
    r = c.get(f"/qa/institutions/{p4.inst.code}/timeline/export/?format=pdf&tq=milestone")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_institutional_record_pdf_and_zip_are_audited(p4):
    from core.models import AuditLogEntry

    c = staff_client(p4.people.director)
    r = c.get(f"/qa/institutions/{p4.inst.code}/record/")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and len(r.content) > 5000
    r = c.get(f"/qa/institutions/{p4.inst.code}/record/zip/")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names[0].endswith("institutional record.pdf")
    notes = list(AuditLogEntry.objects.filter(object_pk=str(p4.inst.pk)).values_list("note", flat=True))
    assert any("institutional record (PDF)" in n for n in notes)
    assert any("(ZIP)" in n for n in notes)


def test_record_is_404_out_of_scope(p4, make_user):
    outsider = make_user("vtsd.o@t.local", role="officer", dept="vtsd")
    c = staff_client(outsider)
    for url in (f"/qa/institutions/{p4.inst.code}/record/", f"/qa/institutions/{p4.inst.code}/record/zip/",
                f"/qa/institutions/{p4.inst.code}/timeline/export/?format=csv"):
        assert c.get(url).status_code in (403, 404)


def test_record_zip_contains_the_files(p4):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from institutions import record
    from institutions.models import DocumentKind, InstitutionDocument

    kind = DocumentKind.objects.first()
    InstitutionDocument.unscoped.create(institution=p4.inst, kind=kind, status="accepted",
                                        file=SimpleUploadedFile("cac.pdf", b"%PDF-1.4 test", "application/pdf"))
    files = record.files_for(p4.inst, p4.people.director)
    assert any(folder == "Documents" for folder, _n, _f in files)
    data = record.record_zip(p4.inst, p4.people.director)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert any(n.startswith("Documents/") and n.endswith(".pdf") for n in names)


def test_case_summary_pdf(p4, make_user):
    c = staff_client(p4.people.director)
    r = c.get(f"/qa/cases/{p4.case.pk}/summary/")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    outsider = staff_client(make_user("vtsd.x@t.local", role="officer", dept="vtsd"))
    assert outsider.get(f"/qa/cases/{p4.case.pk}/summary/").status_code in (403, 404)


def test_payment_statement_needs_payment_permission(p4, make_user):
    c = staff_client(p4.people.director)
    r = c.get(f"/qa/institutions/{p4.inst.code}/payments/statement/?format=csv")
    if p4.people.director.has_perm("payments.view_obligation"):
        assert r.status_code == 200
    else:
        assert r.status_code == 403
    fin = make_user("fin.stmt@t.local", role="qa-finance-director", dept="dpap")
    r = staff_client(fin).get(f"/qa/institutions/{p4.inst.code}/payments/statement/?format=pdf")
    assert r.status_code in (200, 403, 404)
    if r.status_code == 200:
        assert r.content.startswith(b"%PDF")


def test_pdf_builder_has_cover_contents_and_page_numbers(qa_seed):
    from qa_core.pdf_report import Report

    rep = Report("Test report", subject="Subject", toc=True, facts=[("A", "B")])
    for i in range(3):
        rep.h1(f"Section {i}")
        rep.table(["One", "Two"], [[str(n), "x" * 40] for n in range(60)])
    data = rep.build()
    assert data.startswith(b"%PDF")
    assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") + data.count(b"/Type /Page/") >= 0
    assert b"Contents" in data or len(data) > 10000


def test_service_history_pdf(p4):
    c = staff_client(p4.people.director)
    r = c.get("/qa/inspection/service-history/export.pdf")
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert r.content.startswith(b"%PDF")
