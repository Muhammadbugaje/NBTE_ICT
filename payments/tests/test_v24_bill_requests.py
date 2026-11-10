"""v2.4: Issue a bill — the institution's programmes vs the master list,
priced by the fee rules, approved by the Executive Secretary before the
institution sees it."""
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def table(lookups):
    from institutions.models import InstitutionType
    from payments.fee_table_seed import seed_type_fees
    from payments.models import InstitutionTypeFee

    seed_type_fees(InstitutionType, InstitutionTypeFee, activate=True)
    row = InstitutionTypeFee.objects.get(institution_type__code="polytechnic")
    row.programme_fee, row.programme_processing_fee, row.extra_stream_fee = (Decimal("100000"), Decimal("10000"),
                                                                              Decimal("5000"))
    row.programme_processing_per_programme = False
    row.qa_request_base_fee, row.qa_request_fee_per_programme, row.qa_request_processing_fee = (
        Decimal("50000"), Decimal("20000"), Decimal("2000"))
    row.save()
    return row


@pytest.fixture
def masters(lookups):
    from institutions.models import ProgrammeLevel, ProgrammeMaster

    nd, _ = ProgrammeLevel.objects.get_or_create(code="nd", defaults={"name": "National Diploma"})
    hnd, _ = ProgrammeLevel.objects.get_or_create(code="hnd", defaults={"name": "Higher National Diploma"})

    class M:
        cs = ProgrammeMaster.objects.create(name="Computer Science Test", level=nd)
        acc = ProgrammeMaster.objects.create(name="Accountancy Test", level=nd)
        eee = ProgrammeMaster.objects.create(name="Electrical Engineering Test", level=hnd)
    M.nd, M.hnd = nd, hnd
    return M


@pytest.fixture
def es(p2, make_user):
    return make_user("es@t.local", role="executive-secretary")


@pytest.fixture
def inst_with_programme(proposed_inst, masters):
    from institutions.models import Programme

    Programme.unscoped.create(institution=proposed_inst, name=masters.cs.name, level=masters.nd, master=masters.cs,
                              status="accredited", approved_streams=1)
    Programme.unscoped.create(institution=proposed_inst, name="Accountancy test", level=masters.nd,
                              status="proposed")  # matched by name, still only proposed
    return proposed_inst


def _rows(cat):
    return {r["name"]: r for r in cat["has"] + cat["can_offer"]}


def test_catalogue_flags_programmes_the_institution_has_and_can_offer(inst_with_programme, masters):
    from payments import bill_requests as br

    cat = br.catalogue(inst_with_programme)
    rows = _rows(cat)
    assert rows[masters.cs.name]["has"] is True and rows[masters.cs.name]["kind"] == br.REACC
    assert rows[masters.acc.name]["has"] is False and rows[masters.acc.name]["proposed"] is True
    assert rows[masters.acc.name]["kind"] == br.NEW
    assert rows[masters.eee.name]["has"] is False and rows[masters.eee.name]["programme_id"] is None
    assert masters.cs.name in [r["name"] for r in cat["has"]]
    assert {masters.acc.name, masters.eee.name} <= {r["name"] for r in cat["can_offer"]}


def _items(inst, picks, fees=()):
    from django.http import QueryDict

    from payments import bill_requests as br

    q = QueryDict(mutable=True)
    for key, streams in picks:
        q.appendlist("prog", key)
        q[f"streams_{key}"] = str(streams)
    for f in fees:
        q.appendlist("fee", f)
    return br.items_from_post(inst, q)


def test_price_uses_the_category_fee_table(inst_with_programme, masters, table):
    from payments import bill_requests as br

    inst = inst_with_programme
    items = _items(inst, [(f"m{masters.acc.pk}", 1), (f"m{masters.eee.pk}", 2), (f"m{masters.cs.pk}", 1)])
    assert [i["kind"] for i in items] == [br.NEW, br.NEW, br.REACC]
    priced = br.price(inst, items)
    assert not priced["errors"]
    # New: 2 × 100,000 + 1 extra stream × 5,000 + processing 10,000 once = 215,000
    new = next(g for g in priced["groups"] if g["kind"] == br.NEW)
    assert new["subtotal"] == Decimal("215000.00")
    # Re-accreditation: base 50,000 + 1 × 20,000 + processing 2,000 = 72,000
    reacc = next(g for g in priced["groups"] if g["kind"] == br.REACC)
    assert reacc["subtotal"] == Decimal("72000.00")
    assert priced["total"] == Decimal("287000.00")


def test_bill_reaches_the_institution_only_after_es_approval(inst_with_programme, masters, table, p2, es,
                                                              django_capture_on_commit_callbacks):
    from notifications.models import Notification
    from payments import bill_requests as br
    from payments.models import BillRequest, PaymentObligation

    inst = inst_with_programme
    items = _items(inst, [(f"m{masters.eee.pk}", 1)])
    with django_capture_on_commit_callbacks(execute=True):
        req = br.save(inst, items, actor=p2.fin_officer, reason="New HND requested", submit=True)
    assert req.status == BillRequest.AWAITING and req.total == Decimal("110000.00")
    assert Notification.objects.filter(recipient=es, title__icontains=req.reference).exists()
    # Nothing issued yet: the institution has no bill for it.
    assert not PaymentObligation.unscoped.filter(institution=inst, source_reference=req.reference).exists()
    with django_capture_on_commit_callbacks(execute=True):
        req = br.approve(req, actor=es)
    assert req.status == BillRequest.APPROVED and req.obligation is not None
    ob = PaymentObligation.unscoped.get(pk=req.obligation_id)
    assert ob.amount == Decimal("110000.00") and ob.status == "awaiting_payment"
    assert [line["label"] for line in ob.breakdown][0].startswith("New programme approval")
    assert Notification.objects.filter(recipient=p2.fin_officer, title__icontains="approved").exists()


def test_institution_portal_does_not_list_unapproved_bill(inst_with_programme, masters, table, p2, es, inst_user,
                                                          client_for, portal_host):
    from payments import bill_requests as br

    inst = inst_with_programme
    req = br.save(inst, _items(inst, [(f"m{masters.eee.pk}", 1)]), actor=p2.fin_officer, reason="New HND", submit=True)
    c = client_for(inst_user, host=portal_host)
    assert req.reference not in c.get("/payments/").content.decode()
    br.approve(req, actor=es)
    req.refresh_from_db()
    body = c.get("/payments/").content.decode()
    assert req.obligation.reference in body


def test_only_the_es_approves(inst_with_programme, masters, table, p2, es):
    from payments import bill_requests as br
    from payments.models import BillRequest

    inst = inst_with_programme
    req = br.save(inst, _items(inst, [(f"m{masters.cs.pk}", 1)]), actor=p2.fin_officer, reason="Re-accreditation",
                  submit=True)
    for user in (p2.fin_director, p2.fin_officer, p2.director):
        with pytest.raises(PermissionDenied):
            br.approve(req, actor=user)
    with pytest.raises(PermissionDenied):
        br.reject(req, actor=p2.fin_director, remark="Not mine to reject")
    assert BillRequest.unscoped.get(pk=req.pk).status == BillRequest.AWAITING
    with pytest.raises(ValidationError):
        br.reject(req, actor=es, remark="no")  # a reason is required
    req = br.reject(req, actor=es, remark="Wrong programme list")
    assert req.status == BillRequest.REJECTED and req.obligation_id is None


def test_draft_is_not_sent_and_needs_items(inst_with_programme, masters, table, p2):
    from payments import bill_requests as br
    from payments.models import BillRequest

    inst = inst_with_programme
    with pytest.raises(ValidationError):
        br.save(inst, [], actor=p2.fin_officer, reason="nothing")
    draft = br.save(inst, _items(inst, [(f"m{masters.acc.pk}", 1)]), actor=p2.fin_officer)
    assert draft.status == BillRequest.DRAFT and draft.total == Decimal("110000.00")
    with pytest.raises(PermissionDenied):
        br.approve(draft, actor=p2.fin_officer)


def test_issue_bill_page_flow_and_es_approval_view(inst_with_programme, masters, table, p2, es, client_for):
    from institutions.models import Institution
    from payments.models import BillRequest

    inst = inst_with_programme
    # Owner decision: anyone who may issue bills picks ANY institution on the
    # Issue-a-bill page, even a never-billed one outside their assignment scope.
    assert not Institution.objects.for_user(p2.fin_officer).filter(pk=inst.pk).exists()
    c = client_for(p2.fin_officer)
    r = c.get(f"/qa/finance/obligations/issue/?institution={inst.code}")
    body = r.content.decode()
    assert r.status_code == 200 and "Already on the institution" in body and "Not offered yet" in body
    assert masters.eee.name in body and "Has it" in body
    # Live price (HTMX into the summary)
    r = c.post("/qa/finance/obligations/issue/", {"institution": inst.code, "prog": [f"m{masters.eee.pk}"],
                                                   f"streams_m{masters.eee.pk}": "1"},
               HTTP_HX_REQUEST="true", HTTP_HX_TARGET="ib-summary")
    assert r.status_code == 200 and "110,000" in r.content.decode() and "<html" not in r.content.decode()
    # Send to the ES
    r = c.post("/qa/finance/obligations/issue/", {"institution": inst.code, "prog": [f"m{masters.eee.pk}"],
                                                   "reason": "New HND requested", "op": "send"},
               HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200 and "qa:toast" in r["HX-Trigger"] and "Executive Secretary" in r["HX-Trigger"]
    req = BillRequest.unscoped.get(institution=inst)
    assert req.status == BillRequest.AWAITING
    # The finance director cannot approve over HTTP either: 403, or 404 when the
    # institution is outside their assignment-mode scope (never billed before).
    r = client_for(p2.fin_director).post(f"/qa/finance/bill-requests/{req.pk}/approve/", HTTP_HX_REQUEST="true",
                                         HTTP_HX_TARGET="qa-panel")
    assert r.status_code in (403, 404)
    # The ES sees it and approves.
    ces = client_for(es)
    assert req.reference in ces.get("/qa/finance/bill-requests/").content.decode()
    r = ces.post(f"/qa/finance/bill-requests/{req.pk}/approve/", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200 and "Bill approved and sent" in r["HX-Trigger"]
    req.refresh_from_db()
    assert req.status == BillRequest.APPROVED and req.obligation_id


def test_reconciliation_page_renders(p2, client_for):
    r = client_for(p2.fin_director).get("/qa/finance/reconciliation/")
    body = r.content.decode()
    assert r.status_code == 200 and "Trial balance" in body and "rec-tb" in body
