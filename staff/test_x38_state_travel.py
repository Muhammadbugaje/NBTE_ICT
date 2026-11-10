"""v2.1 X38: the states table carries the travel mode and the distance from HQ.

Seeded starting values are editable; a second seed run changes nothing.
"""
import io

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def test_states_are_seeded_with_travel_mode_and_distance(db):
    from staff.models import StateOfOrigin

    call_command("seed_nbte", "--only", "geo.states", stdout=io.StringIO())
    assert StateOfOrigin.objects.count() == 37
    lagos = StateOfOrigin.objects.get(code="la")
    kaduna = StateOfOrigin.objects.get(code="kd")
    assert lagos.travel_mode == "air" and lagos.distance_from_hq_km > 600
    assert kaduna.travel_mode == "road" and 0 < kaduna.distance_from_hq_km < 400


def test_second_seed_run_keeps_an_edited_value(db):
    from staff.models import StateOfOrigin

    call_command("seed_nbte", "--only", "geo.states", stdout=io.StringIO())
    lagos = StateOfOrigin.objects.get(code="la")
    lagos.travel_mode = "road"
    lagos.distance_from_hq_km = 700
    lagos.save()

    out = io.StringIO()
    call_command("seed_nbte", "--only", "geo.states", stdout=out)
    lagos.refresh_from_db()
    assert lagos.travel_mode == "road" and lagos.distance_from_hq_km == 700
    assert "No changes" in out.getvalue()
