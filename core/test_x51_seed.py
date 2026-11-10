"""X51: seed_nbte (seed spec §Tests)."""
import io
import json
import shutil

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core import seeding

pytestmark = pytest.mark.django_db


def _run(*args):
    out = io.StringIO()
    call_command("seed_nbte", *args, stdout=out, stderr=io.StringIO())
    return out.getvalue()


def _json(*args):
    return json.loads(_run(*args, "--format", "json"))


def test_seed_empty_db_then_noop():
    first = _json()
    assert first["changes"] > 900
    assert all(s["status"] == "ok" for s in first["sets"].values()), {k: v["error"] for k, v in first["sets"].items()}
    second = _json()
    assert second["changes"] == 0, {k: (v["created"], v["filled"]) for k, v in second["sets"].items() if
                                    v["created"] or v["filled"]}
    assert "No changes" in _run()


def test_seed_counts():
    from staff.models import LGA, StateOfOrigin, ZonalOffice

    _run("--only", "geo,org")
    assert StateOfOrigin.objects.count() == 37
    assert LGA.objects.count() == 774
    assert LGA.objects.filter(state__code="fc").count() == 6
    assert LGA.objects.filter(state__code="kn").count() == 44
    assert LGA.objects.filter(state__code="kd").count() == 23
    assert ZonalOffice.objects.count() == 7
    assert set(ZonalOffice.objects.get(code="nc").states.values_list("code", flat=True)) == \
        {"be", "fc", "ko", "kw", "na", "ni", "pl"}


def test_seed_never_overwrites_edits():
    from config.models import ConfigEntry
    from staff.models import LGA

    _run("--only", "settings,geo")
    entry = ConfigEntry.objects.get(key="qa.import.max_rows")
    entry.value = "123"
    entry.save()
    lga = LGA.objects.get(state__code="kd", name="Zaria")
    lga.headquarters = "Zaria City"
    lga.sort_order = 999
    lga.save()
    for args in ((), ("--update-unedited",)):
        _run("--only", "settings,geo", *args)
        entry.refresh_from_db()
        lga.refresh_from_db()
        assert entry.value == "123" and lga.sort_order == 999 and lga.headquarters == "Zaria City"
    data = _json("--only", "geo.lgas", "--update-unedited")
    assert data["sets"]["geo.lgas"]["kept_edited"] >= 1


def test_seed_update_unedited(tmp_path, settings):
    from honoraria.models import Bank

    src = seeding.DATA_DIR
    dst = tmp_path / "seed_data"
    shutil.copytree(src, dst)
    _run("--only", "payments.banks", "--data-dir", str(dst))
    path = dst / "finance" / "banks.json"
    data = json.loads(path.read_text())
    data["rows"][0]["name"] = "Access Bank Plc (renamed)"
    path.write_text(json.dumps(data))
    _run("--only", "payments.banks", "--data-dir", str(dst))
    assert Bank.objects.get(code="access").name == "Access Bank"          # fill-only: untouched
    _run("--only", "payments.banks", "--data-dir", str(dst), "--update-unedited")
    assert Bank.objects.get(code="access").name == "Access Bank Plc (renamed)"


def test_seed_only_and_deps():
    from staff.models import LGA, StateOfOrigin

    data = _json("--only", "geo.lgas")
    assert data["sets"]["geo.states"]["as_dependency"] is True
    assert LGA.objects.count() == 774
    LGA.objects.all().delete()
    StateOfOrigin.objects.all().delete()
    data = _json("--only", "geo.lgas", "--no-deps")
    assert "geo.states" not in data["sets"]
    assert data["sets"]["geo.lgas"]["skipped"] == 774 and data["sets"]["geo.lgas"]["warnings"]


def test_seed_dry_run_writes_nothing():
    from core.models import SeedRecord
    from staff.models import LGA

    before = (LGA.objects.count(), SeedRecord.objects.count())
    data = _json("--only", "geo", "--dry-run")
    assert data["dry_run"] is True and data["changes"] > 0
    assert (LGA.objects.count(), SeedRecord.objects.count()) == before


def test_seed_reset_demo_guard(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        _run("--reset-demo")


def test_seed_files_valid_schema():
    assert seeding.validate_files() == {}
    assert "All data files are valid" in _run("--list")


def test_old_name_shim():
    from staff.models import LGA, Designation

    out = _run("--with-lgas", "--quiet")
    assert "seed_staff_reference" in out
    assert LGA.objects.count() == 774 and Designation.objects.exists()
    out = io.StringIO()
    call_command("seed_staff_reference", "--quiet", stdout=out)
    assert "Seed complete" in out.getvalue()


def test_seed_startup_wrapper():
    out = io.StringIO()
    call_command("seed_startup", stdout=out, stderr=io.StringIO())
    text = out.getvalue()
    assert "seed_startup now runs: python manage.py seed_nbte" in text and "NBTE Digital seed" in text
    out = io.StringIO()
    call_command("seed_startup", "--skip-base-seeders", stdout=out)
    assert "seed_startup complete" in out.getvalue()                  # the fast path is unchanged


def test_seed_strict_exit_code():
    with pytest.raises(CommandError) as exc:
        _run("--only", "geo.zones", "--strict")
    assert exc.value.returncode == 2
