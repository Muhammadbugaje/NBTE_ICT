"""Phase 7 tests share the Phase 1 fixtures plus the complaint ones."""
import datetime

import pytest
from django.utils import timezone

from qa_core.tests.conftest import *  # noqa: F401,F403


@pytest.fixture
def world(qa_seed, make_institution, make_user):
    """Two institutions in different departments, handlers in Finance, DPAP,
    VTSD and ICT, the directors, deputies and the ES."""
    from institutions.models import Institution

    a = make_institution("AAA", "dpap", "Alpha Polytechnic")
    b = make_institution("BBB", "vtsd", "Bravo Technical College")
    Institution.unscoped.filter(pk=a.pk).update(zone="NW")
    a.refresh_from_db()
    w = {
        "a": a, "b": b,
        "a_user": make_user("admin@aaa.local", user_type="institution", institution=a, role="inst-admin"),
        "a_user2": make_user("ce@aaa.local", user_type="institution", institution=a, role="inst-chief-executive"),
        "b_user": make_user("admin@bbb.local", user_type="institution", institution=b, role="inst-admin"),
        "rp": make_user("rp@x.local", user_type="resource_person", role="resource-person"),
        "fin_officer": make_user("fo@fin.local", role="qa-finance-officer", dept="finance"),
        "fin_director": make_user("fd@fin.local", role="director", dept="finance"),  # Phase B: Finance seat
        "fin_deputy": make_user("dd@fin.local", role="deputy-director", dept="finance"),
        "dpap_officer": make_user("o@dpap.local", role="officer", dept="dpap"),
        "dpap_director": make_user("d@dpap.local", role="director", dept="dpap"),
        "vtsd_officer": make_user("o@vtsd.local", role="officer", dept="vtsd"),
        "ict_officer": make_user("o@ict.local", role="officer", dept="ict"),
        "es": make_user("es@nbte.local", role="executive-secretary", dept="es-office"),
        "admin": make_user("cfg@nbte.local", role="qa-settings-admin", dept="ict", groups=["Complaints Admin"]),
    }
    return w


@pytest.fixture
def layer():
    from complaints.models import ComplaintLayer

    def _get(code):
        return ComplaintLayer.objects.get(code=code)

    return _get


def monday(hour=9, minute=0, weeks_ahead=1):
    """A Monday in the future at the given local time (no holidays seeded)."""
    today = timezone.localdate()
    day = today + datetime.timedelta(days=(7 - today.weekday()) + 7 * (weeks_ahead - 1))
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))
