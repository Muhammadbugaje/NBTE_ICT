"""v2.3: every curriculum in the ND/HND register is on the programme master
list (what institutions pick from), automatically and idempotently."""
import pytest

from facilities import services
from facilities.models import FacilityProgramme
from institutions.models import ProgrammeLevel, ProgrammeMaster

pytestmark = pytest.mark.django_db


def _levels():
    for code, name in (("nd", "ND"), ("hnd", "HND")):
        ProgrammeLevel.objects.get_or_create(code=code, defaults={"name": name})


def test_register_programmes_become_master_rows_per_level_and_link():
    _levels()
    prog = FacilityProgramme.objects.create(name="Zzv23 Welding Technology", levels="ND, HND")
    created, linked = services.import_master_list()
    assert created >= 2 and linked >= 1
    rows = ProgrammeMaster.objects.filter(name="Zzv23 Welding Technology")
    assert set(rows.values_list("level__code", flat=True)) == {"nd", "hnd"}
    prog.refresh_from_db()
    assert prog.programme_master.level.code == "nd"
    # Idempotent: a second run adds nothing.
    assert services.import_master_list() == (0, 0)


def test_post_migrate_sync_fills_a_database_that_already_has_the_register():
    _levels()
    FacilityProgramme.objects.create(name="Zzv23 Glass Technology", levels="HND")
    services.sync_master_list_after_migrate(using="default")
    assert ProgrammeMaster.objects.filter(name="Zzv23 Glass Technology", level__code="hnd").exists()
