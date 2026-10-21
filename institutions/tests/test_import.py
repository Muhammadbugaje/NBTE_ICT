"""Bulk import / export (openpyxl): valid sheet → second officer → applied
atomically; one bad row rejects the whole batch; export → re-import changes
nothing (idempotent round trip); new rows are always created as proposed."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from qa_core import actions
from qa_core.models import PendingAction

pytestmark = pytest.mark.django_db

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sheet(rows, kind="institutions"):
    from openpyxl import load_workbook

    from institutions import importexport

    data = importexport.template_xlsx(kind)
    data = data if isinstance(data, (bytes, bytearray)) else data.getvalue()
    wb = load_workbook(io.BytesIO(data))
    ws = wb.worksheets[0]
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return SimpleUploadedFile("import.xlsx", out.getvalue(), content_type=XLSX)


def _row(name, state="KADUNA", lga=None, status=""):
    from institutions import services
    from staff.models import StateOfOrigin

    lga = lga or services.lgas_for(StateOfOrigin.objects.get(name=state))[0]
    return ["", name, "Private", "Polytechnic", "Polytechnic", state, lga, "1 Road", "Town", "", "", "Head",
            2024, "", "", status]


def _send(batch, officer, director):
    pa = actions.initiate("institutions.bulk_import", actor=officer, target=batch, payload={"reason": "intake"},
                          reason="intake")
    batch.status = "pending"
    return actions.confirm(pa, director)


def test_valid_sheet_waits_for_second_officer_then_applies(p2, lookups):
    from institutions import importexport
    from institutions.models import Institution

    batch = importexport.stage_batch(_sheet([_row("Alpha Polytechnic"), _row("Beta Polytechnic"),
                                             _row("Gamma Polytechnic")]), "institutions", user=p2.officer)
    assert batch.status == "validated" and batch.summary["create"] == 3
    assert not Institution.unscoped.filter(name__in=["Alpha Polytechnic"]).exists()
    pa = actions.initiate("institutions.bulk_import", actor=p2.officer, target=batch, payload={"reason": "x"},
                          reason="x")
    assert pa.status == PendingAction.PENDING
    pa = actions.confirm(pa, p2.director)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail
    created = Institution.unscoped.filter(name__in=["Alpha Polytechnic", "Beta Polytechnic", "Gamma Polytechnic"])
    assert created.count() == 3
    assert set(created.values_list("status", flat=True)) == {"proposed"}
    assert set(created.values_list("handling_department__code", flat=True)) == {"ntird"}


def test_one_bad_row_rejects_everything(p2, lookups):
    from institutions import importexport
    from institutions.models import Institution

    before = Institution.unscoped.count()
    batch = importexport.stage_batch(_sheet([_row("Alpha Polytechnic"), _row("Bad Poly", lga="Nowhere"),
                                             _row("Gamma Polytechnic")]), "institutions", user=p2.officer)
    assert batch.status == "invalid"
    assert any("Nowhere" in " ".join(e["errors"]) for e in batch.errors)
    assert batch.rows == []
    with pytest.raises(Exception):
        actions.initiate("institutions.bulk_import", actor=p2.officer, target=batch, payload={}, reason="x")
    assert Institution.unscoped.count() == before


def test_failure_during_apply_rolls_back_every_row(p2, lookups, monkeypatch):
    from institutions import importexport
    from institutions.models import Institution

    batch = importexport.stage_batch(_sheet([_row("Alpha Polytechnic"), _row("Beta Polytechnic")]),
                                     "institutions", user=p2.officer)
    pa = actions.initiate("institutions.bulk_import", actor=p2.officer, target=batch, payload={"reason": "x"},
                          reason="x")
    real = importexport.services.create_institution
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(importexport.services, "create_institution", flaky)
    pa = actions.confirm(pa, p2.director)
    assert pa.status == PendingAction.FAILED
    assert not Institution.unscoped.filter(name__in=["Alpha Polytechnic", "Beta Polytechnic"]).exists()


def test_status_column_cannot_change_status(p2, lookups):
    from institutions import importexport

    batch = importexport.stage_batch(_sheet([_row("Alpha Polytechnic", status="approved")]), "institutions",
                                     user=p2.officer)
    assert batch.status == "invalid"


def test_round_trip_export_reimport_changes_nothing(p2, lookups):
    from institutions import importexport
    from institutions.models import Institution

    batch = importexport.stage_batch(_sheet([_row("Alpha Polytechnic"), _row("Beta Polytechnic")]),
                                     "institutions", user=p2.officer)
    assert _send(batch, p2.officer, p2.director).status == PendingAction.APPLIED
    insts = Institution.unscoped.filter(name__in=["Alpha Polytechnic", "Beta Polytechnic"]).order_by("name")
    data = importexport.export_institutions_xlsx(insts)
    data = data if isinstance(data, (bytes, bytearray)) else data.getvalue()
    again = importexport.stage_batch(SimpleUploadedFile("export.xlsx", data, content_type=XLSX), "institutions",
                                     user=p2.officer)
    assert again.status == "validated", again.rows
    assert again.summary.get("create", 0) == 0 and again.summary.get("update", 0) == 0
    assert again.summary.get("unchanged", 0) == 2
    count = Institution.unscoped.count()
    assert _send(again, p2.officer, p2.director).status == PendingAction.APPLIED
    assert Institution.unscoped.count() == count


def test_import_screen_flow(p2, lookups, client_for):
    from institutions.models import InstitutionImportBatch

    c = client_for(p2.officer)
    assert c.get("/qa/institutions/import/").status_code == 200
    response = c.post("/qa/institutions/import/", {"kind": "institutions",
                                                   "file": _sheet([_row("Alpha Polytechnic")])})
    assert response.status_code in (200, 302)
    batch = InstitutionImportBatch.objects.order_by("-pk").first()
    assert batch.status == "validated"
    response = c.post(f"/qa/institutions/import/{batch.pk}/", {"reason": "October intake"})
    assert response.status_code == 200
    batch.refresh_from_db()
    assert batch.status == "pending"
    pa = PendingAction.objects.get(policy_id="institutions.bulk_import", object_id=str(batch.pk))
    pa = actions.confirm(pa, p2.director)
    assert pa.status == PendingAction.APPLIED, pa.failure_detail  # not stale


def test_stale_batch_is_released_for_resending(p2, lookups):
    from institutions import importexport

    batch = importexport.stage_batch(_sheet([_row("Alpha Polytechnic")]), "institutions", user=p2.officer)
    pa = actions.initiate("institutions.bulk_import", actor=p2.officer, target=batch, payload={"reason": "x"},
                          reason="x")
    type(batch).objects.filter(pk=batch.pk).update(status="pending")
    batch.refresh_from_db()
    batch.save()  # touched after initiation → stale
    pa = actions.confirm(pa, p2.director)
    assert pa.status == PendingAction.FAILED
    batch.refresh_from_db()
    assert batch.status == "validated"
