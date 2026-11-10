"""v2.1 R.5 (X42, X43): professional-body import/export through the diff
engine (with programmes), mapping template, PB portal accounts, dashboard
scoping, letter upload visibility, notifications."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import load_workbook

from qa_core.tests.phase2_fixtures import pdf_upload

pytestmark = pytest.mark.django_db


@pytest.fixture
def pb(qa_seed, make_institution, make_user):
    from institutions.models import ProfessionalBody, ProgrammeLevel, ProgrammeMaster
    from institutions.programme_master import add_programme

    nd = ProgrammeLevel.objects.get(code="nd")
    cpn = ProfessionalBody.objects.create(name="Demo Computing Council", acronym="CPN")
    pcn = ProfessionalBody.objects.create(name="Demo Pharmacy Council", acronym="PCN",
                                          requires_letter_before_commencement=True)
    cs = ProgrammeMaster.objects.create(name="Computer Science", level=nd, professional_body=cpn)
    ph = ProgrammeMaster.objects.create(name="Pharmacy Technician", level=nd, professional_body=pcn)
    sl = ProgrammeMaster.objects.create(name="Science Laboratory Technology", level=nd)
    a, b = make_institution("pba"), make_institution("pbb")
    p_cs = add_programme(a, master=cs)
    p_ph = add_programme(b, master=ph)
    from accounts.models import Role, User

    def pb_user(email, role, body):
        u = User(email=email, first_name="Demo", last_name="Body", user_type="professional_body",
                 professional_body=body, role=Role.objects.get(code=role), must_change_password=False)
        u.set_password("Test-pass-2026!")
        u.save()
        return User.objects.get(pk=u.pk)

    admin = pb_user("registrar@demo-cpn.local", "pb-admin", cpn)
    viewer = pb_user("viewer@demo-pcn.local", "pb-viewer", pcn)

    class D:
        pass

    d = D()
    d.nd, d.cpn, d.pcn, d.cs, d.ph, d.sl, d.a, d.b, d.p_cs, d.p_ph = nd, cpn, pcn, cs, ph, sl, a, b, p_cs, p_ph
    d.admin, d.viewer = admin, viewer
    return d


def _upload(data, name="file.xlsx"):
    return SimpleUploadedFile(name, data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml"
                                                       ".sheet")


def _edit(data, fn):
    wb = load_workbook(io.BytesIO(data))
    ws = wb.worksheets[0]
    header = [c.value for c in ws[1]]
    fn(ws, header)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_x42_export_import_roundtrip_with_programmes(pb, officer):
    from core import importdiff

    spec = importdiff.get_spec("professional_bodies")
    data = importdiff.export_workbook(spec)
    # unchanged file -> nothing to apply
    run = importdiff.preview(spec, _upload(data), user=officer)
    assert run.counts.get("changed", 0) == 0 and run.counts.get("unchanged") == 2

    def change(ws, header):
        col_prog, col_phone = header.index("Programmes") + 1, header.index("Phone") + 1
        for r in range(2, ws.max_row + 1):
            if ws.cell(r, 2).value == "CPN":
                ws.cell(r, col_prog).value = f"{pb.cs.code}; {pb.sl.code}"
                ws.cell(r, col_phone).value = "0800 000 0000"
        ws.append(["Demo Surveyors Council", "SURCON"])
    run = importdiff.preview(spec, _upload(_edit(data, change)), user=officer)
    assert run.counts.get("changed") == 1 and run.counts.get("new") == 1
    row = run.rows.get(action="changed")
    assert set(row.diff) == {"programmes", "phone"}  # field-level changes only
    officer.is_superuser = True
    officer.save()
    importdiff.apply(run, user=officer)
    pb.sl.refresh_from_db()
    assert pb.sl.professional_body == pb.cpn
    from institutions.models import ProfessionalBody

    assert ProfessionalBody.objects.filter(acronym="SURCON").exists()


def test_x42_mapping_template_flow(pb, officer):
    from core import importdiff

    spec = importdiff.get_spec("programme_master")
    data = importdiff.export_workbook(spec)

    def assign(ws, header):
        col = header.index("Professional body") + 1
        for r in range(2, ws.max_row + 1):
            if ws.cell(r, 1).value == "Science Laboratory Technology":
                ws.cell(r, col).value = "PCN"
    run = importdiff.preview(spec, _upload(_edit(data, assign)), user=officer)
    assert run.counts.get("changed") == 1
    row = run.rows.get(action="changed")
    assert row.diff["professional_body"]["new"] == "PCN"
    importdiff.apply(run, user=officer)
    pb.sl.refresh_from_db()
    assert pb.sl.professional_body == pb.pcn


def test_x43_pb_user_scoping(pb, client_for, portal_host):
    from accreditation.models import SSQ
    from institutions.models import Programme

    c = client_for(pb.admin, host=portal_host)
    r = c.get("/pb/")
    assert r.status_code == 200
    assert b"pba Polytechnic" in r.content and b"Pharmacy Technician" not in r.content
    assert c.get(f"/pb/programmes/{pb.p_cs.pk}/").status_code == 200
    assert c.get(f"/pb/programmes/{pb.p_ph.pk}/").status_code == 404  # another body's programme
    # the generic institution scope gives a body nothing (never SSQ content)
    assert not Programme.objects.for_user(pb.admin).exists()
    assert not SSQ.objects.for_user(pb.admin).exists()
    # institution pages are not for body accounts; the internal host refuses them
    assert c.get("/application/").status_code == 404
    assert client_for(pb.admin).get("/qa/professional-bodies/").status_code in (302, 403)


def test_x43_letter_upload_visibility(pb, client_for, portal_host, make_user):
    from institutions.models import BodyLetter

    # a viewer cannot upload
    v = client_for(pb.viewer, host=portal_host)
    r = v.post(f"/pb/programmes/{pb.p_ph.pk}/letters/", {"kind": "approval_letter", "file": pdf_upload()})
    assert r.status_code == 403
    pb.p_ph.refresh_from_db()
    assert pb.p_ph.awaiting_body_letter
    pb.viewer.role = type(pb.admin.role).objects.get(code="pb-admin")
    pb.viewer.save()
    from accounts.models import User

    v = client_for(User.objects.get(pk=pb.viewer.pk), host=portal_host)
    r = v.post(f"/pb/programmes/{pb.p_ph.pk}/letters/", {"kind": "approval_letter", "reference": "PCN/1",
                                                          "file": pdf_upload("letter.pdf")})
    assert r.status_code == 200, r.content[:300]
    letter = BodyLetter.unscoped.get(programme=pb.p_ph)
    pb.p_ph.refresh_from_db()
    assert not pb.p_ph.awaiting_body_letter  # the approval letter completes the wait
    inst_user = make_user("ce@pbb.local", user_type="institution", institution=pb.b)
    other_inst = make_user("ce@pba.local", user_type="institution", institution=pb.a)
    assert letter.can_download(inst_user) and not letter.can_download(other_inst)
    assert not letter.can_download(pb.admin)  # another body
    ic = client_for(inst_user, host=portal_host)
    assert ic.get(f"/download/institutions.bodyletter/{letter.pk}/").status_code == 200
    assert client_for(pb.admin, host=portal_host).get(f"/pb/letters/{letter.pk}/").status_code == 404


def test_x43_body_notified_on_programme_proposed(pb, make_institution):
    from institutions.programme_master import add_programme
    from notifications.models import Notification

    inst = make_institution("pbc")
    from django.db import transaction  # noqa: F401

    add_programme(inst, master=pb.cs)
    # on_commit callbacks run at the end of the test transaction; call directly too
    from institutions import pb_portal

    from institutions.models import Programme

    pb_portal.notify_body(Programme.unscoped.get(institution=inst, master=pb.cs), "programme_proposed")
    assert Notification.objects.filter(recipient=pb.admin, title__icontains="New programme").exists()
    assert not Notification.objects.filter(recipient=pb.viewer, title__icontains="pbc").exists()
