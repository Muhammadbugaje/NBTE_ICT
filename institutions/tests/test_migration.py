"""Phase 8 — migrate from the previous system (institutions/migration.py).

Approved institutions straight into their handling department (no SOP-01),
programmes with statuses and dates (derived expiry), professional bodies
linked, history on the timeline at its dates, portal administrators created
inactive and invited on request; idempotent on re-run; one bad row rejects
the workbook; two officers as for every import."""
import datetime
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from qa_core import actions
from qa_core.models import PendingAction

pytestmark = pytest.mark.django_db
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook(institutions=(), programmes=(), history=()):
    from openpyxl import load_workbook

    from institutions import migration

    wb = load_workbook(io.BytesIO(migration.template_xlsx()))
    for title, rows in (("Institutions", institutions), ("Programmes", programmes),
                        ("Accreditation history", history)):
        for row in rows:
            wb[title].append(list(row))
    out = io.BytesIO()
    wb.save(out)
    return SimpleUploadedFile("migration.xlsx", out.getvalue(), content_type=XLSX)


def inst_row(code="LEG", name="Legacy Federal Polytechnic", email="admin@legacy.edu.ng", status="approved",
             lga=None):
    from institutions import services
    from staff.models import StateOfOrigin

    lga = lga or services.lgas_for(StateOfOrigin.objects.get(name="KADUNA"))[0]
    return [code, name, "Federal", "Polytechnic", "Polytechnic", "KADUNA", lga.upper(), "1 Old Road", "Zaria",
            "info@legacy.edu.ng", "08030000001", "Dr. Old Rector", 1980, "", "", "Ada Obi", email, status,
            "2001-05-04"]


@pytest.fixture
def body(qa_seed):
    from institutions.models import ProfessionalBody

    return ProfessionalBody.objects.create(name="Pharmacy Council of Nigeria", acronym="PCN", email="pcn@x.ng")


def stage(p2, **sheets):
    from institutions import importexport

    return importexport.stage_batch(workbook(**sheets), "legacy", user=p2.officer)


def full(p2, body):
    return stage(p2, institutions=[inst_row()],
                 programmes=[["LEG", "Computer Science", "ND", "", "accredited", "Full", 2, "2022-10-01", "", ""],
                             ["LEG", "Pharmaceutical Technology", "ND", "", "interim", "Interim", 1, "2024-01-15",
                              "2025-01-15", "PCN"]],
                 history=[["LEG", "Computer Science", "ND", "2016-09-30", "accredited", "Full", "Letter 114"]])


def send(batch, p2, **options):
    pa = actions.initiate("institutions.bulk_import", actor=p2.officer, target=batch,
                          payload={"reason": "migration"}, reason="migration")
    if options:
        pa.payload = {**pa.payload, "confirm_options": options}
        PendingAction.objects.filter(pk=pa.pk).update(payload=pa.payload)
    return actions.confirm(pa, p2.director)


def test_template_has_instructions_examples_and_reference(qa_seed):
    from openpyxl import load_workbook

    from institutions import migration

    wb = load_workbook(io.BytesIO(migration.template_xlsx()))
    assert wb.sheetnames == ["Instructions", "Institutions", "Programmes", "Accreditation history", "Examples",
                             "Reference"]
    assert wb["Institutions"].max_row == 1 and wb["Examples"].max_row > 5
    headers = [c.value for c in wb["Programmes"][1]]
    assert "Approval type" in headers and "Professional body" in headers


def test_migration_creates_approved_institutions_programmes_history(p2, lookups, body):
    from accreditation.models import Application
    from institutions import migration
    from institutions.models import Institution, Programme, ProgrammeBodyMapping
    from institutions.professional_bodies import bodies_for_programme
    from qa_core.models import TimelineEvent

    batch = full(p2, body)
    assert batch.status == "validated", batch.errors
    assert batch.summary["create"] == 4
    pa = send(batch, p2)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    inst = Institution.unscoped.get(code="LEG")
    assert inst.status == "approved" and inst.origin == "legacy"
    assert inst.handling_department.code != "ntird"
    assert inst.approved_at.date() == datetime.date(2001, 5, 4)
    assert not Application.unscoped.filter(institution=inst).exists()
    cs = Programme.unscoped.get(institution=inst, name="Computer Science")
    assert cs.status == "accredited" and cs.approved_streams == 2
    assert cs.expiry_date == datetime.date(2027, 10, 1)  # Full: commencement + 60 months
    pharm = Programme.unscoped.get(institution=inst, name="Pharmaceutical Technology")
    assert pharm.expiry_date == datetime.date(2025, 1, 15)
    assert body in bodies_for_programme(pharm)
    assert ProgrammeBodyMapping.objects.filter(body=body, programme_name="Pharmaceutical Technology").exists()
    admin = inst.primary_user()
    assert admin.email == "admin@legacy.edu.ng" and not admin.is_active and not admin.has_usable_password()
    events = TimelineEvent.unscoped.filter(institution=inst)
    assert events.filter(event_code="institution.migrated", visibility="public",
                         public_text__icontains="previous system").exists()
    assert events.filter(event_code="programme.migrated").count() == 2
    history = events.get(event_code=migration.HISTORY_EVENT)
    assert history.at.date() == datetime.date(2016, 9, 30) and "Letter 114" in history.public_text
    if migration.programme_has_approval_type() and migration.approval_type_model() is not None:
        cs.refresh_from_db()
        assert cs.approval_type is not None
    else:  # before accreditation.ApprovalType exists, the name is kept on the timeline
        assert "Full" in events.get(event_code="programme.migrated", object_id=str(cs.pk)).public_text


def test_rerun_changes_nothing(p2, lookups, body):
    from institutions.models import Institution, Programme
    from qa_core.models import TimelineEvent

    assert send(full(p2, body), p2).status == PendingAction.APPLIED
    counts = (Institution.unscoped.count(), Programme.unscoped.count(), TimelineEvent.unscoped.count())
    again = full(p2, body)
    assert again.status == "validated", again.errors
    assert again.summary.get("create", 0) == 0 and again.summary.get("update", 0) == 0
    assert again.summary["unchanged"] == 4
    assert send(again, p2).status == PendingAction.APPLIED
    assert (Institution.unscoped.count(), Programme.unscoped.count(), TimelineEvent.unscoped.count()) == counts


def test_rerun_with_a_change_updates_by_code(p2, lookups, body):
    from institutions.models import Institution

    assert send(full(p2, body), p2).status == PendingAction.APPLIED
    batch = stage(p2, institutions=[inst_row(name="Legacy Federal Polytechnic Zaria")],
                  programmes=[["LEG", "Computer Science", "ND", "", "expired", "Full", 2, "2022-10-01", "", ""]])
    assert batch.status == "validated", batch.errors
    assert batch.summary["update"] == 2
    assert send(batch, p2).status == PendingAction.APPLIED
    inst = Institution.unscoped.get(code="LEG")
    assert inst.name == "Legacy Federal Polytechnic Zaria"
    assert inst.former_names[0]["name"] == "Legacy Federal Polytechnic"
    from institutions.models import Programme

    assert Programme.unscoped.get(institution=inst, name="Computer Science").status == "expired"


def test_one_bad_row_rejects_the_workbook(p2, lookups, body):
    from institutions.models import Institution

    batch = stage(p2, institutions=[inst_row(), inst_row(code="BAD", name="Bad Poly", status="flying")],
                  programmes=[["ZZZ", "Computer Science", "ND", "", "accredited", "", 1, "", "", ""],
                              ["LEG", "Pharmacy", "ND", "", "accredited", "Forever", 1, "", "", "NOPE"]])
    assert batch.status == "invalid"
    text = " ".join(" ".join(e["errors"]) for e in batch.errors)
    assert "flying" in text and "ZZZ" in text and "Forever" in text and "NOPE" in text
    assert not Institution.unscoped.filter(code="LEG").exists()


def test_existing_status_is_not_changed_by_migration(p2, lookups, make_institution):
    from institutions.models import Institution

    make_institution("OLD", "ntird", "Old Proposed Poly")
    Institution.unscoped.filter(code="OLD").update(status="proposed")  # proposed in the new system
    batch = stage(p2, institutions=[inst_row(code="OLD", name="Old Proposed Poly")])
    assert batch.status == "invalid"
    assert "actions" in " ".join(batch.errors[0]["errors"])


def test_confirm_checkbox_sends_set_password_links(p2, lookups, body, client_for):
    from institutions import importexport
    from institutions.models import Institution

    batch = importexport.stage_batch(workbook(institutions=[inst_row()]), "legacy", user=p2.officer)
    pa = actions.initiate("institutions.bulk_import", actor=p2.officer, target=batch,
                          payload={"reason": "migration"}, reason="migration")
    c = client_for(p2.director)
    page = c.get(f"/qa/pending/{pa.pk}/").content.decode()
    assert "set-password link" in page and 'name="opt_send_invites"' in page
    r = c.post(f"/qa/pending/{pa.pk}/confirm/", {"has_options": "1", "opt_send_invites": "1", "remark": "ok"})
    assert r.status_code == 200
    pa.refresh_from_db()
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    user = Institution.unscoped.get(code="LEG").primary_user()
    assert user.is_active and user.institution_membership.credentials_last_sent_at is not None
    batch.refresh_from_db()
    assert batch.result["invites"] == {"LEG": "sent"}


def test_later_bulk_send_from_the_batch_page(p2, lookups, body, client_for):
    from institutions.models import Institution

    batch = stage(p2, institutions=[inst_row()])
    assert send(batch, p2).status == PendingAction.APPLIED
    user = Institution.unscoped.get(code="LEG").primary_user()
    assert not user.is_active
    c = client_for(p2.ntird_director if not p2.director.has_perm("institutions.send_login_credentials")
                   else p2.director)
    page = c.get(f"/qa/institutions/import/{batch.pk}/").content.decode()
    assert "not invited yet" in page
    r = c.post(f"/qa/institutions/import/{batch.pk}/send-credentials/")
    assert r.status_code == 200, r.status_code
    user.refresh_from_db()
    assert user.is_active


def test_screens(p2, lookups, client_for):
    c = client_for(p2.officer)
    r = c.get("/qa/institutions/import/template/legacy/")
    assert r.status_code == 200 and r["Content-Type"] == XLSX
    assert "migration" in r["Content-Disposition"]
    assert "Migration template" in c.get("/qa/institutions/import/").content.decode()
    r = c.post("/qa/institutions/import/", {"kind": "legacy", "file": workbook(institutions=[inst_row()])})
    assert r.status_code == 302
