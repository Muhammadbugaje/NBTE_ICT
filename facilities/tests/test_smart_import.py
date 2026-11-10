"""Smart Excel import of the facilities register: preview counts, master-list
matching, creating missing programmes, mapping, idempotency, export round trip
and the screens."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from facilities import smart_import as si
from facilities.models import FacilityItem, FacilityProgramme, FacilitySection
from institutions.models import ProgrammeLevel, ProgrammeMaster

pytestmark = pytest.mark.django_db

HEAD = ["Programme", "Levels (ND/HND)", "Facility code", "Facility (lab / workshop / studio)", "Group / sub-heading",
        "S/N", "Equipment / item", "ND qty", "HND qty", "Remarks", "Critical (Y/N)", "Sort order"]


def _levels():
    for code, name in (("nd", "ND"), ("hnd", "HND")):
        ProgrammeLevel.objects.get_or_create(code=code, defaults={"name": name})
    return ProgrammeLevel.objects.get(code="nd"), ProgrammeLevel.objects.get(code="hnd")


def _xlsx(rows, programmes=None):
    wb = Workbook()
    ws = wb.active
    ws.title = si.ITEM_SHEET
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    if programmes:
        ps = wb.create_sheet(si.PROGRAMME_SHEET)
        ps.append(["Programme", "Levels (ND/HND)", "Source curricula", "Notes"])
        for p in programmes:
            ps.append(p)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


ROWS = [
    ["Zzsm Statistics", "ND, HND", "A", "Statistics Laboratory", "", "1.", "Desktop computers", "20", "30", "", "N", ""],
    ["Zzsm Statistics", "ND, HND", "A", "statistics laboratory", "", "2.", "Statistical kit", "1 set", "1 set", "",
     "Y", ""],
    ["Zzsm Statistics", "ND, HND", "A", "Statistics Laboratory", "", "2.", "Statistical Kit", "9", "9", "", "N", ""],
    ["Zzsm Glasss Blowing Tech", "HND", "A", "Glass Workshop", "Tools", "1.", "Blowpipe", "5", "", "", "", ""],
    ["Zzsm Glasss Blowing Tech", "HND", "B", "Furnace Room", "", "1.", "Furnace", "-", "1", "", "N", ""],
    ["Zzsm Pottery", "ND", "A", "Pottery Studio", "", "", "Wheel", "4", "", "", "", ""],
    ["Zzsm Pottery", "ND", "B", "Kiln Room", "", "", "", "", "", "See Ceramics curriculum", "", ""],
    ["", "", "", "No programme here", "", "", "Orphan", "1", "", "", "", ""],
]


def _masters():
    nd, hnd = _levels()
    stats = ProgrammeMaster.objects.create(name="Zzsm Statistics", level=nd)
    ProgrammeMaster.objects.create(name="Zzsm Statistics", level=hnd)
    glass = ProgrammeMaster.objects.create(name="Zzsm Glass Blowing Technology", level=hnd)
    return stats, glass


def test_read_folds_duplicates_and_skips_bad_rows():
    data = si.read_workbook(_xlsx(ROWS))
    assert list(data.programmes) == ["zzsm statistics", "zzsm glasss blowing tech", "zzsm pottery"]
    stats = data.programmes["zzsm statistics"]
    assert len(stats.facilities) == 1 and stats.item_count == 2          # facility + kit folded case-insensitively
    assert len(data.merged) == 1 and data.skipped == [(9, "no Programme")]
    pottery = data.programmes["zzsm pottery"]
    assert pottery.facilities["kiln room"].items == OrderedDictEmpty() and pottery.item_count == 1


def OrderedDictEmpty():
    from collections import OrderedDict

    return OrderedDict()


def test_preview_counts_and_master_matching():
    stats, glass = _masters()
    data = si.read_workbook(_xlsx(ROWS))
    p = si.build_preview(data)
    rows = {r["key"]: r for r in p["programmes"]}
    assert rows["zzsm statistics"]["match"]["status"] == "exact"
    assert rows["zzsm statistics"]["match"]["master_id"] == stats.pk                      # ND row first
    g = rows["zzsm glasss blowing tech"]["match"]
    assert g["status"] == "candidate" and g["candidate_id"] == glass.pk                    # fuzzy: Tech → Technology
    assert rows["zzsm pottery"]["match"]["status"] in ("none", "candidate")
    assert rows["zzsm pottery"]["needs_decision"] and not rows["zzsm statistics"]["needs_decision"]
    assert p["totals"]["items_new"] == 5 and p["totals"]["programmes_new"] == 3
    assert p["totals"]["facilities_new"] == 5


def test_apply_creates_missing_programme_maps_and_is_idempotent(make_user):
    stats, glass = _masters()
    admin = make_user("sm.admin@t.local", superuser=True)
    before = ProgrammeMaster.objects.count()
    data = si.read_workbook(_xlsx(ROWS, programmes=[["Zzsm Pottery", "ND", "ND Pottery", "Kiln list in Ceramics"]]))
    decisions = {"zzsm glasss blowing tech": f"map:{glass.pk}", "zzsm pottery": "create"}
    out = si.apply(data, decisions, actor=admin)
    assert out["programmes_created"] == 3 and out["items_created"] == 5 and out["masters_created"] == 1
    assert ProgrammeMaster.objects.count() == before + 1
    pottery = FacilityProgramme.objects.get(name="Zzsm Pottery")
    assert pottery.programme_master.name == "Zzsm Pottery" and pottery.programme_master.level.code == "nd"
    assert pottery.source == "ND Pottery" and "Ceramics" in pottery.notes
    assert pottery.sections.count() == 2 and pottery.items.count() == 1                  # facility with no items kept
    assert FacilityProgramme.objects.get(name="Zzsm Glasss Blowing Tech").programme_master == glass
    assert FacilityProgramme.objects.get(name="Zzsm Statistics").programme_master == stats
    kit = FacilityItem.objects.get(name="Statistical kit")
    assert kit.is_critical and kit.nd_qty_num == 1 and kit.section.code == "A"
    furnace = FacilityItem.objects.get(name="Furnace")
    assert furnace.nd_qty_num is None and furnace.hnd_qty_num == 1 and furnace.section.sort_order == 2

    # Same file again: nothing created or changed.
    counts = (FacilityProgramme.objects.count(), FacilitySection.objects.count(), FacilityItem.objects.count(),
              ProgrammeMaster.objects.count())
    data = si.read_workbook(_xlsx(ROWS, programmes=[["Zzsm Pottery", "ND", "ND Pottery", "Kiln list in Ceramics"]]))
    p = si.build_preview(data)
    assert p["totals"]["items_new"] == 0 and p["totals"]["items_updated"] == 0 and p["totals"]["items_unchanged"] == 5
    assert p["totals"]["need_decision"] == 0                                              # all linked now
    again = si.apply(data, actor=admin)
    assert again["items_created"] == again["items_updated"] == again["masters_created"] == 0
    assert counts == (FacilityProgramme.objects.count(), FacilitySection.objects.count(),
                      FacilityItem.objects.count(), ProgrammeMaster.objects.count())

    # A changed quantity is an update, not a new row.
    changed = [list(r) for r in ROWS]
    changed[0][7] = "25"
    p = si.build_preview(si.read_workbook(_xlsx(changed)))
    assert p["totals"]["items_updated"] == 1 and p["totals"]["items_new"] == 0
    si.apply(si.read_workbook(_xlsx(changed)), actor=admin)
    assert FacilityItem.objects.get(name="Desktop computers").nd_qty_num == 25
    assert FacilityItem.objects.filter(name__iexact="desktop computers").count() == 1


def test_skip_and_unlinked_decisions():
    _masters()
    data = si.read_workbook(_xlsx(ROWS))
    out = si.apply(data, {"zzsm pottery": "skip", "zzsm glasss blowing tech": "none"})
    assert out["programmes_skipped"] == 1 and out["masters_created"] == 0
    assert not FacilityProgramme.objects.filter(name="Zzsm Pottery").exists()
    assert FacilityProgramme.objects.get(name="Zzsm Glasss Blowing Tech").programme_master is None


def test_export_round_trip_is_unchanged():
    _masters()
    si.apply(si.read_workbook(_xlsx(ROWS)), {"zzsm pottery": "none", "zzsm glasss blowing tech": "none"})
    blob = si.export_bytes()
    assert len(si.read_workbook(io.BytesIO(blob)).programmes) == 3      # the "Total" line is not a programme
    p = si.build_preview(si.read_workbook(io.BytesIO(blob)))
    assert p["totals"]["items_new"] == 0 and p["totals"]["items_updated"] == 0
    assert p["totals"]["items_unchanged"] == 5 and p["totals"]["facilities_new"] == 0


def test_from_parsed_keeps_differing_duplicates():
    parsed = [{"programme": "Zzsm Dental", "levels": ["ND"], "source": "", "notes": [], "file": "x.docx",
               "sections": [{"code": "A", "name": "Room", "items": [
                   {"sn": "1.", "name": "Scoop", "nd": "10", "hnd": "", "group": "", "remarks": ""},
                   {"sn": "2.", "name": "Scoop", "nd": "6", "hnd": "", "group": "", "remarks": ""},
                   {"sn": "3.", "name": "scoop", "nd": "10", "hnd": "", "group": "", "remarks": ""}]},
                   {"code": "B", "name": "Mosque", "note": "No equipment list given", "items": []}]}]
    data = si.from_parsed(parsed)
    names = [i.name for i in data.programmes["zzsm dental"].facilities["room"].items.values()]
    assert names == ["Scoop", "Scoop (listed again, S/N 2)"]
    assert "Mosque: No equipment list given" in data.programmes["zzsm dental"].notes
    wb = si.read_workbook(io.BytesIO(si.build_workbook(data, matches={})))
    assert wb.item_count == 2 and "mosque" in wb.programmes["zzsm dental"].facilities


def test_screens_and_permissions(client, make_user):
    _masters()
    officer = make_user("sm.officer@t.local", role="officer", dept="dpap")
    client.force_login(officer)
    assert client.get("/qa/facilities/import/excel/").status_code == 403
    admin = make_user("sm.boss@t.local", superuser=True)
    client.force_login(admin)
    page = client.get("/qa/facilities/import/excel/")
    assert page.status_code == 200 and b"qa-hero" in page.content
    resp = client.post("/qa/facilities/import/excel/", {"file": SimpleUploadedFile("f.xlsx", _xlsx(ROWS).read())})
    assert resp.status_code == 302
    preview_url = resp["Location"]
    page = client.get(preview_url)
    assert page.status_code == 200
    assert "The programme “Zzsm Pottery” (ND) does not exist on the system. Create it?" in page.content.decode()
    rows = {r["key"]: r for r in page.context["p"]["programmes"]}
    form = {f"d-{rows['zzsm pottery']['idx']}": "create", f"d-{rows['zzsm glasss blowing tech']['idx']}": "map",
            f"m-{rows['zzsm glasss blowing tech']['idx']}": str(rows["zzsm glasss blowing tech"]["match"]["candidate_id"])}
    done = client.post(preview_url, form)
    assert done.status_code == 302 and done["Location"].endswith("/import/excel/done/")
    assert client.get(done["Location"]).status_code == 200
    assert FacilityProgramme.objects.get(name="Zzsm Pottery").programme_master.name == "Zzsm Pottery"
    assert client.get(preview_url).status_code == 404                          # token used up
    export = client.get("/qa/facilities/export/workbook.xlsx")
    assert export.status_code == 200 and export["Content-Type"].startswith("application/vnd.openxml")
    assert client.post("/qa/facilities/import/excel/", {"file": SimpleUploadedFile("f.txt", b"x")}).status_code == 400
