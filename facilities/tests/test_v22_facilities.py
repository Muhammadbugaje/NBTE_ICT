"""v2.2: ND/HND facilities & equipment register — Word parsing, Excel round
trip with preview, analysis and access."""
import io
import zipfile
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db
ZIP = Path(settings.BASE_DIR) / "core/seed_data/facilities/nbte_nd_hnd_facilities.zip"


def _docx(name):
    with zipfile.ZipFile(ZIP) as zf:
        member = next(m for m in zf.namelist() if m.endswith(name))
        return io.BytesIO(zf.read(member))


def test_parser_reads_sections_items_and_levels():
    from facilities.docx_parser import parse

    p = parse(_docx("Statistics_Facilities_and_Equipment.docx"))
    assert p["programme"] == "Statistics" and p["levels"] == ["ND", "HND"]
    items = p["sections"][0]["items"]
    assert items[0]["name"] == "Statistical kit" and items[0]["nd"] == "1" and items[0]["hnd"] == "2"


def test_parser_keeps_subgroups():
    from facilities.docx_parser import parse

    p = parse(_docx("Insurance_Facilities_and_Equipment.docx"))
    groups = {it["group"] for s in p["sections"] for it in s["items"]}
    assert "ICT Equipment" in groups


def test_load_and_excel_round_trip(make_user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core import importdiff
    from facilities import docx_parser, services
    from facilities.diffspecs import FacilityItemDiff
    from facilities.models import FacilityItem

    out = services.load_parsed([docx_parser.parse(_docx("Statistics_Facilities_and_Equipment.docx"))])
    assert out["items"] == 4
    again = services.load_parsed([docx_parser.parse(_docx("Statistics_Facilities_and_Equipment.docx"))])
    assert again["items"] == 0                                          # idempotent
    data = importdiff.export_workbook(FacilityItemDiff())
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    ws = wb.worksheets[0]
    header = [c.value for c in ws[1]]
    nd_col = header.index("ND quantity") + 1
    ws.cell(row=2, column=nd_col, value="7")
    buf = io.BytesIO()
    wb.save(buf)
    admin = make_user("fe.admin@t.local", superuser=True)
    run = importdiff.preview(FacilityItemDiff(), SimpleUploadedFile("x.xlsx", buf.getvalue()), user=admin)
    assert run.counts.get("changed") == 1 and run.counts.get("unchanged") == 3
    importdiff.apply(run, user=admin)
    assert FacilityItem.objects.filter(nd_qty="7", nd_qty_num=7).count() == 1


def test_pages_and_permissions(client, make_user):
    from facilities import docx_parser, services
    from facilities.models import FacilityProgramme

    services.load_parsed([docx_parser.parse(_docx("Statistics_Facilities_and_Equipment.docx"))])
    prog = FacilityProgramme.objects.get(name="Statistics")
    officer = make_user("fe.officer@t.local", role="officer", dept="dpap")
    client.force_login(officer)
    assert client.get("/qa/facilities/").status_code == 200
    page = client.get(f"/qa/facilities/{prog.slug}/")
    assert page.status_code == 200 and b"Statistical kit" in page.content
    item = prog.items.first()
    assert client.post(f"/qa/facilities/items/{item.pk}/", {"nd_qty": "9"}).status_code == 403
    services.managers_group().user_set.add(officer)
    officer = type(officer).objects.get(pk=officer.pk)
    client.force_login(officer)
    assert client.post(f"/qa/facilities/items/{item.pk}/", {"nd_qty": "9"}).status_code == 200
    item.refresh_from_db()
    assert item.nd_qty_num == 9
    assert client.get(f"/qa/facilities/{prog.slug}/export.xlsx").status_code == 200
