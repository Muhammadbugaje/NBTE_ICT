"""v2.2: the honoraria roller, external SSQ accrual, Finance bank view."""
from decimal import Decimal

import pytest

pytestmark = pytest.mark.django_db


_N = [0]


def _bump():
    _N[0] += 1
    return _N[0]


def _entry(user, amount="10000"):
    from django.contrib.contenttypes.models import ContentType

    from honoraria.models import HonorariumEntry

    return HonorariumEntry.objects.create(payee_user=user, kind=HonorariumEntry.DOCUMENT_ANALYSIS,
                                          source_ct=ContentType.objects.get_for_model(user), source_id=f"{user.pk}-{_bump()}",
                                          rate_key="honoraria.rate.internal_item", rate=Decimal(amount),
                                          amount=Decimal(amount))


def test_roller_orders_and_totals(make_user):
    from honoraria import finance_view

    a = make_user("roll.a@t.local", role="officer", dept="dpap")
    b = make_user("roll.b@t.local", role="officer", dept="dpap")
    _entry(a)
    _entry(b, "20000")
    _entry(a)
    rows = finance_view.roller()
    by_name = {r["user"].pk: r for r in rows}
    assert by_name[a.pk]["amount"] == Decimal("20000") and by_name[a.pk]["items"] == 2
    assert [r["position"] for r in rows] == list(range(1, len(rows) + 1))


def test_only_finance_es_admin_view_bank_details(p2, make_user, rf):
    from honoraria import finance_view

    officer = make_user("roll.o@t.local", role="officer", dept="dpap")
    req = rf.get("/")
    req.user = officer
    assert not finance_view.can_view_bank_details(officer, req)
    req.user = p2.fin_director
    assert finance_view.can_view_bank_details(p2.fin_director, req)
    req.is_impersonating = True
    assert not finance_view.can_view_bank_details(p2.fin_director, req)


def test_roller_page_for_dof(p2, client, make_user):
    a = make_user("roll.c@t.local", role="officer", dept="dpap")
    _entry(a)
    client.force_login(p2.fin_director)
    r = client.get("/qa/finance/honoraria/roller/")
    assert r.status_code == 200 and b"next to be paid" in r.content
    client.force_login(a)
    assert client.get("/qa/finance/honoraria/roller/").status_code == 404
