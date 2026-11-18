"""v2.3/v2.4: one honorarium file per institution (SOP-01 -> SOP-03), the pay
officer prepares -> DoF reviews and sends to the ES -> ES approves -> DoF pays
(all or some) -> payee confirms or disputes flow, and the "no active reference
format" fix."""
import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from qa_core.tests.phase2_fixtures import p2  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _complete_work(inst, kind, user, subject=None):
    from django.contrib.contenttypes.models import ContentType

    from assignments import work
    from assignments.models import WorkAssignment

    subject = subject or inst
    wa = WorkAssignment.objects.create(
        institution=inst, kind=kind, subject_ct=ContentType.objects.get_for_model(subject, for_concrete_model=False),
        subject_id=str(subject.pk), assignee=user, role_on_work="analyst", status=WorkAssignment.IN_PROGRESS,
        accepted_at=timezone.now(), payable=True, department=user.department)
    work.complete(wa)
    return wa


def _bank(user):
    from core import fieldcrypto
    from honoraria.models import Bank, PayeeBankAccount

    bank, _ = Bank.objects.get_or_create(code="v23-bank", defaults={"name": "V23 Bank", "cbn_code": "998"})
    return PayeeBankAccount.objects.create(
        user=user, bank=bank, account_number_enc=fieldcrypto.encrypt("0123456789"), account_last4="6789",
        account_name=user.get_full_name() or user.email, confirmed_at=timezone.now())


def _funding():
    from payments.models import BankAccount

    return BankAccount.objects.create(bank_name="NBTE Bank", account_name="NBTE Honoraria",
                                      account_number="1234567890", is_active=True, is_placeholder=False)


def _visit_case(inst, staff_user, *, days="3"):
    """A completed SOP-03 case with one cost-sheet line for ``staff_user``."""
    from accreditation.models import CostLine, CostSheet, QACase, QARequest, QAType, RateTable

    req = QARequest.unscoped.create(reference=f"QR-{inst.code}", institution=inst,
                                    qa_type=QAType.objects.first())
    case = QACase.unscoped.create(reference=f"QC-{inst.code}", qa_request=req, institution=inst,
                                  completed_at=timezone.now())
    rate = RateTable.objects.create(code="v23-dta", role="Team member", allowance_type="dta",
                                    amount=Decimal("25000"))
    sheet = CostSheet.unscoped.create(case=case, status="es_approved")
    return CostLine.unscoped.create(sheet=sheet, staff=staff_user.staff_profile, role_on_exercise="Team member",
                                    rate=rate, kind="dta", days=Decimal(days), unit_amount=Decimal("25000"),
                                    amount=Decimal("25000") * Decimal(days))


@pytest.fixture
def world(p2, make_institution, make_user):  # noqa: F811
    from types import SimpleNamespace

    es = make_user("v23.es@t.local", role="executive-secretary")
    admin = make_user("v23.sa@t.local", role="super-admin")
    pay_officer = make_user("v23.payofficer@t.local", role="officer", dept="finance")
    reviewer = make_user("v23.reviewer@t.local", role="officer", dept="dpap")
    return SimpleNamespace(dof=p2.fin_director, es=es, admin=admin, officer=p2.officer, reviewer=reviewer,
                           pay_officer=pay_officer, inst=make_institution("HFV"))


# ---------------------------------------------------------------------------
# 1. accrual per stage into ONE file, no double counting, notifications
# ---------------------------------------------------------------------------
def test_stages_accrue_into_one_file_without_double_counting(world, django_capture_on_commit_callbacks):
    from honoraria import files
    from honoraria.models import HonorariumEntry, HonorariumFile, HonorariumFileLine
    from notifications.models import Notification

    inst = world.inst
    # SOP-01: two document reviews approved -> accrue quietly (documents not all cleared yet).
    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(inst, "document_analysis", world.officer)
        _complete_work(inst, "document_analysis", world.reviewer, subject=world.reviewer)
    file = HonorariumFile.objects.get(institution=inst)
    assert file.lines.filter(stage="sop01").count() == 2
    assert not Notification.objects.filter(title__startswith="Honoraria ready to settle").exists()

    # All documents cleared completes SOP-01 (onboarding_v2.maybe_clear hook): DoF, ES and super admin told.
    with django_capture_on_commit_callbacks(execute=True):
        files.stage_completed(inst, "sop01")
    title = f"Honoraria ready to settle — {inst.name} (SOP-01)"
    told = set(Notification.objects.filter(title=title).values_list("recipient_id", flat=True))
    assert {world.dof.pk, world.es.pk, world.admin.pk} <= told
    assert not Notification.objects.filter(title=title, recipient=world.officer).exists()

    # SOP-02: an SSQ review (the institution's last open one) adds to the SAME file and announces SOP-02.
    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(inst, "ssq_review", world.reviewer, subject=world.dof)
    assert HonorariumFile.objects.filter(institution=inst).count() == 1
    assert file.lines.filter(stage="sop02").count() == 1
    assert Notification.objects.filter(title=f"Honoraria ready to settle — {inst.name} (SOP-02)",
                                       recipient=world.dof).exists()

    # SOP-03: the visit personnel per day from the cost sheet.
    cost = _visit_case(inst, world.reviewer, days="3")
    with django_capture_on_commit_callbacks(execute=True):
        files.stage_completed(inst, "sop03")
    line = file.lines.get(stage="sop03")
    assert line.source_key == f"cost:{cost.pk}"
    assert line.quantity == Decimal("3") and line.rate == Decimal("25000") and line.amount == Decimal("75000")
    assert Notification.objects.filter(title=f"Honoraria ready to settle — {inst.name} (SOP-03)").exists()

    # Rates from settings: internal ₦10,000 per document / SSQ.
    assert set(file.lines.filter(stage__in=("sop01", "sop02")).values_list("amount", flat=True)) == {
        Decimal("10000.00")}
    # Re-running every hook never double counts and never re-announces.
    before = Notification.objects.count()
    with django_capture_on_commit_callbacks(execute=True):
        for stage in ("sop01", "sop02", "sop03"):
            files.stage_completed(inst, stage)
    files.sync(inst, announce=True)
    assert file.lines.count() == 4 == HonorariumFileLine.objects.filter(file__institution=inst).count()
    assert Notification.objects.count() == before
    assert HonorariumEntry.objects.filter(institution=inst).count() == 3
    s = files.summary(file)
    assert s["total"] == Decimal("105000") and s["outstanding"] == Decimal("105000") and s["phase"] == "generated"


def test_external_resource_person_ssq_and_visit_rates(world, make_user, django_capture_on_commit_callbacks):
    from honoraria import files
    from inspection.models import ResourcePerson, ServiceRecord

    rp_user = make_user("v23.rp@t.local", role="resource-person")
    rp_user.user_type = "resource_person"
    rp_user.save(update_fields=["user_type"])
    person = ResourcePerson.objects.create(full_name="Dr Ada Obi", email="v23.rp@t.local", source="academia",
                                           status="active", user=rp_user)
    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(world.inst, "ssq_review", rp_user, subject=world.dof)
    ServiceRecord.unscoped.create(person_type="resource_person", person_id=person.pk, person_name=person.full_name,
                                  resource_person=person, institution=world.inst, duty="resource_inspection",
                                  served_from=datetime.date.today(), served_to=datetime.date.today(),
                                  visit_count=1, source="inspection_closed")
    file, _new = files.sync(world.inst)
    ssq = file.lines.get(stage="sop02")
    visit = file.lines.get(stage="sop03")
    assert ssq.amount == Decimal("30000") and ssq.resource_person == person   # honoraria.rate.external_ssq_review
    assert visit.amount == Decimal("30000") and visit.resource_person == person


# ---------------------------------------------------------------------------
# 2. pay officer prepares (masked) -> DoF sends to the ES -> ES approves ->
#    DoF pays -> payee confirms
# ---------------------------------------------------------------------------
def test_pay_officer_prepares_dof_sends_to_es_es_approves_payee_confirms(world, client,
                                                                        django_capture_on_commit_callbacks):
    from honoraria import files, schedules
    from honoraria.models import BankDetailsLog, HonorariumEntry, HonorariumFileLine
    from notifications.models import Notification
    from payments.models import LedgerJournal

    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(world.inst, "document_analysis", world.officer)
    file, _ = files.sync(world.inst)
    line = file.lines.get()
    _bank(world.officer)
    funding = _funding()

    # Only the DoF names the pay officer; it is audited and the officer gets the work.
    with pytest.raises(PermissionDenied):
        files.assign_pay_officer(world.pay_officer, user=world.es, standing=True)
    with django_capture_on_commit_callbacks(execute=True):
        files.assign_pay_officer(world.pay_officer, user=world.dof, standing=True)
    assert files.officer_for(file) == world.pay_officer and files.can_prepare(world.pay_officer, file)
    assert not files.can_prepare(world.reviewer, file)

    # The pay officer sees the file with MASKED numbers only and cannot reveal the full one.
    client.force_login(world.pay_officer)
    page = client.get(f"/qa/finance/honoraria/files/{file.pk}/")
    assert page.status_code == 200
    assert b"0123456789" not in page.content and "•••• 6789".encode() in page.content
    assert client.post("/qa/finance/honoraria/roller/bank/", {"payee": f"user:{world.officer.pk}"}).status_code == 403
    assert not BankDetailsLog.objects.filter(action=BankDetailsLog.REVEALED, actor=world.pay_officer).exists()

    # He prepares the batch; the DoF is asked to review it and send it to the ES.
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post(f"/qa/finance/honoraria/files/{file.pk}/prepare/",
                        {"line": [line.pk], "funding_account": funding.pk, "payment_reference": "TRF-001"})
    assert r.status_code == 302
    line.refresh_from_db()
    assert line.status == HonorariumFileLine.PREPARED
    batch = line.schedule_line.schedule
    assert batch.status == "draft" and batch.honorarium_file == file and batch.total == Decimal("10000")
    assert batch.reference.startswith("NBTE-HON-")
    assert HonorariumEntry.objects.get(pk=line.entry_id).status == HonorariumEntry.SCHEDULED
    assert Notification.objects.filter(recipient=world.dof, title__startswith="Review honoraria").exists()
    # The roller can no longer pick the same work (no double payment).
    with pytest.raises(ValidationError):
        schedules.build_schedule(datetime.date(2000, 1, 1), datetime.date.today(), user=world.dof,
                                 payee_keys=[("user", world.officer.pk)])
    # Neither the pay officer nor the ES may send; only the DoF.
    with pytest.raises(PermissionDenied):
        files.send_to_es(batch, user=world.pay_officer)
    with pytest.raises(PermissionDenied):
        files.send_to_es(batch, user=world.es)

    # DoF: review -> send to the ES. Nothing is paid yet.
    client.force_login(world.dof)
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post(f"/qa/finance/honoraria/batch/{batch.pk}/send/", {"remark": "Please approve"})
    assert r.status_code == 302
    batch.refresh_from_db()
    line.refresh_from_db()
    assert batch.status == "awaiting_es" and batch.sent_to_es_by == world.dof
    assert line.status == HonorariumFileLine.PREPARED
    assert batch.paid_at is None and not batch.lines.filter(paid_at__isnull=False).exists()
    assert Notification.objects.filter(recipient=world.es, title__startswith="Approve honoraria").exists()
    # The DoF cannot jump the gate.
    with pytest.raises(ValidationError):
        files.pay_batch(batch, user=world.dof, funding_account_id=funding.pk, payment_reference="TRF-001")

    # ES: approve. It comes back to the DoF to pay.
    client.force_login(world.es)
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post(f"/qa/finance/honoraria/batch/{batch.pk}/es-approve/", {"remark": "Checked"})
    assert r.status_code == 302
    batch.refresh_from_db()
    line.refresh_from_db()
    assert batch.status == "approved" and batch.approved_by == world.es and batch.es_decided_by == world.es
    assert batch.es_remark == "Checked"
    assert Notification.objects.filter(recipient=world.dof, title__startswith="ES approved").exists()

    # DoF: pay everyone. Ledger posted, payee told "Paid — please confirm receipt".
    client.force_login(world.dof)
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post(f"/qa/finance/honoraria/batch/{batch.pk}/pay/",
                        {"scope": "all", "funding_account": funding.pk, "payment_reference": "TRF-001"})
    assert r.status_code == 302
    batch.refresh_from_db()
    line.refresh_from_db()
    assert batch.status == "paid" and batch.payment_reference == "TRF-001"
    assert line.status == HonorariumFileLine.PAID
    assert LedgerJournal.objects.filter(key__startswith=f"hon:{batch.reference}:", source="honorarium").exists()
    assert Notification.objects.filter(recipient=world.officer, title="Paid — please confirm receipt").exists()
    assert Notification.objects.filter(recipient=world.pay_officer, title__startswith="Paid").exists()

    # The payee confirms; the file is settled.
    schedules.confirm_receipt(line.schedule_line, user=world.officer)
    files.reconcile(file)
    line.refresh_from_db()
    assert line.status == HonorariumFileLine.CONFIRMED
    assert files.summary(file)["phase"] == "confirmed"
    assert HonorariumEntry.objects.get(pk=line.entry_id).status == HonorariumEntry.RECEIVED


def test_prepare_needs_confirmed_bank_details_and_dof_can_do_it_alone(world, django_capture_on_commit_callbacks):
    from honoraria import files
    from honoraria.models import HonorariumFileLine

    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(world.inst, "document_analysis", world.officer)
    file, _ = files.sync(world.inst)
    line = file.lines.get()
    with pytest.raises(ValidationError):
        files.prepare_batch(file, user=world.dof, line_ids=[line.pk])
    _bank(world.officer)
    funding = _funding()
    batch = files.prepare_batch(file, user=world.dof, line_ids=[line.pk])
    # The teller number is auto-issued at preparation and is not the batch reference.
    assert batch.payment_reference.startswith("NBTE-TEL-")
    assert batch.payment_reference != batch.reference
    # Withdraw and prepare again: the line is ready again, nothing doubled.
    files.cancel_batch(batch, user=world.dof)
    line.refresh_from_db()
    assert line.status == HonorariumFileLine.PENDING
    batch = files.prepare_batch(file, user=world.dof, line_ids=[line.pk])
    files.send_to_es(batch, user=world.dof)
    files.es_approve(batch, user=world.es)
    # The DoF may still edit the teller number when recording the payment.
    files.pay_batch(batch, user=world.dof, funding_account_id=funding.pk, payment_reference="TELLER-EDIT-1")
    batch.refresh_from_db()
    line.refresh_from_db()
    assert batch.status == "paid" and batch.payment_reference == "TELLER-EDIT-1"
    assert line.schedule_line.payment_reference == "TELLER-EDIT-1"


def test_es_can_return_a_file_batch_with_a_note(world, client, django_capture_on_commit_callbacks):
    from honoraria import files
    from notifications.models import Notification

    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(world.inst, "document_analysis", world.officer)
    file, _ = files.sync(world.inst)
    _bank(world.officer)
    batch = files.prepare_batch(file, user=world.dof, line_ids=list(file.lines.values_list("pk", flat=True)))
    files.send_to_es(batch, user=world.dof)

    # Only the ES (not the DoF or the pay officer) may decide, and a reason is required.
    with pytest.raises(PermissionDenied):
        files.es_return(batch, user=world.dof, remark="Take it back")
    with pytest.raises(ValidationError):
        files.es_return(batch, user=world.es, remark="no")

    with django_capture_on_commit_callbacks(execute=True):
        files.es_return(batch, user=world.es, remark="The visit rate is wrong for this officer")
    batch.refresh_from_db()
    assert batch.status == "draft" and batch.es_decided_by == world.es
    assert batch.es_remark == "The visit rate is wrong for this officer"
    assert Notification.objects.filter(recipient=world.dof,
                                       title__startswith="ES returned the honoraria batch").exists()

    # The DoF sees the note and can send it again.
    client.force_login(world.dof)
    page = client.get(f"/qa/finance/honoraria/{batch.pk}/")
    assert page.status_code == 200 and b"The visit rate is wrong" in page.content


def test_dof_pays_some_payees_now_and_the_rest_later(world, django_capture_on_commit_callbacks):
    from honoraria import files
    from honoraria.models import HonorariumFileLine

    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(world.inst, "document_analysis", world.officer)
        _complete_work(world.inst, "document_analysis", world.reviewer, subject=world.reviewer)
    file, _ = files.sync(world.inst)
    _bank(world.officer)
    _bank(world.reviewer)
    funding = _funding()
    batch = files.prepare_batch(file, user=world.dof,
                                line_ids=list(file.lines.values_list("pk", flat=True)))
    files.send_to_es(batch, user=world.dof)
    files.es_approve(batch, user=world.es)
    schedule_lines = list(batch.lines.order_by("pk"))
    assert len(schedule_lines) == 2

    with django_capture_on_commit_callbacks(execute=True):
        files.pay_batch(batch, user=world.dof, funding_account_id=funding.pk,
                        payment_reference="PART-1", line_ids=[schedule_lines[0].pk])
    batch.refresh_from_db()
    files.reconcile(file)
    assert batch.status == "approved"       # one line still unpaid on the same batch
    assert batch.lines.filter(paid_at__isnull=False).count() == 1
    assert batch.lines.filter(paid_at__isnull=True).count() == 1
    statuses = set(file.lines.values_list("status", flat=True))
    assert statuses == {HonorariumFileLine.PAID, HonorariumFileLine.APPROVED}

    with django_capture_on_commit_callbacks(execute=True):
        files.pay_batch(batch, user=world.dof, funding_account_id=funding.pk,
                        payment_reference="PART-2", line_ids=[schedule_lines[1].pk])
    batch.refresh_from_db()
    files.reconcile(file)
    assert batch.status == "paid"
    assert set(file.lines.values_list("status", flat=True)) == {HonorariumFileLine.PAID}


def test_dof_es_admin_may_reveal_bank_number_and_it_is_logged(world, client):
    from honoraria.models import BankDetailsLog

    _bank(world.officer)
    client.force_login(world.dof)
    r = client.post("/qa/finance/honoraria/roller/bank/", {"payee": f"user:{world.officer.pk}"})
    assert r.status_code == 200 and b"0123456789" in r.content
    assert BankDetailsLog.objects.filter(action=BankDetailsLog.REVEALED, actor=world.dof).exists()


def test_payee_can_dispute_and_then_confirm_receipt(world, client, django_capture_on_commit_callbacks):
    from honoraria import files, schedules
    from notifications.models import Notification

    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(world.inst, "document_analysis", world.officer)
    file, _ = files.sync(world.inst)
    _bank(world.officer)
    funding = _funding()
    batch = files.prepare_batch(file, user=world.dof, line_ids=list(file.lines.values_list("pk", flat=True)))
    files.send_to_es(batch, user=world.dof)
    files.es_approve(batch, user=world.es)
    with django_capture_on_commit_callbacks(execute=True):
        files.pay_batch(batch, user=world.dof, funding_account_id=funding.pk, payment_reference="TRF-DISP")
    line = batch.lines.get()

    # Only the payee can report it, and they must say what happened.
    with pytest.raises(PermissionDenied):
        schedules.dispute_receipt(line, user=world.dof, note="Not mine to report")
    with pytest.raises(ValidationError):
        schedules.dispute_receipt(line, user=world.officer, note="no")
    with django_capture_on_commit_callbacks(execute=True):
        schedules.dispute_receipt(line, user=world.officer, note="Money is not in my account yet")
    line.refresh_from_db()
    assert line.disputed_by == world.officer and line.dispute_note == "Money is not in my account yet"
    assert Notification.objects.filter(
        recipient=world.dof, title="A payee says the honorarium did not arrive").exists()

    # Finance sees it on the batch and on the file.
    client.force_login(world.dof)
    batch_page = client.get(f"/qa/finance/honoraria/{batch.pk}/")
    assert batch_page.status_code == 200 and b"Payee says not received" in batch_page.content
    assert b"Money is not in my account yet" in batch_page.content
    file_page = client.get(f"/qa/finance/honoraria/files/{file.pk}/")
    assert b"Money is not in my account yet" in file_page.content

    # The payee later confirms; the dispute is cleared and the entry is received.
    from honoraria.models import HonorariumEntry

    schedules.confirm_receipt(line, user=world.officer)
    line.refresh_from_db()
    assert line.confirmed_at and not line.disputed_at and not line.dispute_note
    assert HonorariumEntry.objects.get(schedule_lines=line).status == HonorariumEntry.RECEIVED


def test_payment_list_print_and_csv_are_dof_only_and_logged(world, client):
    from honoraria import files
    from honoraria.models import BankDetailsLog

    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(world.inst, "document_analysis", world.officer)
    file, _ = files.sync(world.inst)
    _bank(world.officer)
    batch = files.prepare_batch(file, user=world.dof, line_ids=list(file.lines.values_list("pk", flat=True)))

    client.force_login(world.dof)
    page = client.get(f"/qa/finance/honoraria/batch/{batch.pk}/print/")
    assert page.status_code == 200
    assert b"0123456789" in page.content and batch.payment_reference.encode() in page.content
    csv_response = client.get(f"/qa/finance/honoraria/batch/{batch.pk}/export/")
    assert csv_response.status_code == 200
    assert csv_response["Content-Type"].startswith("text/csv")
    body = csv_response.content.decode()
    assert "0123456789" in body and "V23 Bank" in body
    assert BankDetailsLog.objects.filter(action=BankDetailsLog.RELEASED, actor=world.dof).count() >= 2

    # A pay officer, an ES and an unrelated reviewer may not print payee bank details.
    for user in (world.pay_officer, world.es, world.reviewer):
        client.force_login(user)
        assert client.get(f"/qa/finance/honoraria/batch/{batch.pk}/print/").status_code == 403
        assert client.get(f"/qa/finance/honoraria/batch/{batch.pk}/export/").status_code == 403

    # Paid batches no longer expose bank details.
    files.send_to_es(batch, user=world.dof)
    files.es_approve(batch, user=world.es)
    client.force_login(world.dof)
    files.pay_batch(batch, user=world.dof, funding_account_id=_funding().pk, payment_reference="TRF-CLOSE")
    assert client.get(f"/qa/finance/honoraria/batch/{batch.pk}/print/").status_code == 404


def test_es_gate_applies_only_to_file_batches(world):
    from django.contrib.contenttypes.models import ContentType

    from honoraria import files, schedules
    from honoraria.models import HonorariumEntry

    HonorariumEntry.objects.create(
        payee_user=world.officer, kind=HonorariumEntry.DOCUMENT_ANALYSIS,
        source_ct=ContentType.objects.get_for_model(world.officer), source_id="classic-es-gate",
        rate_key="honoraria.rate.internal_item", rate=Decimal("10000"), amount=Decimal("10000"))
    _bank(world.officer)
    classic = schedules.build_schedule(datetime.date(2000, 1, 1), datetime.date.today(), user=world.dof)
    assert classic.honorarium_file_id is None
    assert not files.can_send_to_es(world.dof, classic)
    assert not files.can_es_decide(world.es, classic)
    with pytest.raises(PermissionDenied):
        files.send_to_es(classic, user=world.dof)
    with pytest.raises(PermissionDenied):
        files.es_approve(classic, user=world.es)
    # The classic DoF flow still works: approve, then pay.
    schedules.approve(classic, user=world.dof)
    classic.refresh_from_db()
    assert classic.status == "approved"


def test_pages_render_for_finance_and_hide_from_others(world, client, django_capture_on_commit_callbacks):
    from honoraria import files

    with django_capture_on_commit_callbacks(execute=True):
        _complete_work(world.inst, "document_analysis", world.officer)
    file, _ = files.sync(world.inst)
    client.force_login(world.dof)
    for url in ("/qa/finance/honoraria/files/", f"/qa/finance/honoraria/files/{file.pk}/",
                "/qa/finance/honoraria/roller/", "/qa/finance/honoraria/", "/qa/finance/honoraria/income/",
                "/qa/finance/honoraria/income/?range=12m"):
        assert client.get(url).status_code == 200, url
    client.force_login(world.reviewer)
    assert client.get("/qa/finance/honoraria/files/").status_code == 404
    assert client.get(f"/qa/finance/honoraria/files/{file.pk}/").status_code == 404


# ---------------------------------------------------------------------------
# 3. "No active reference format" fix
# ---------------------------------------------------------------------------
def test_missing_reference_format_is_recreated_and_never_blocks(world):
    from honoraria import references
    from qa_core.models import ReferenceFormat

    ReferenceFormat.objects.filter(code__in=("honoraria", "honoraria_file", "honoraria_teller")).delete()
    ref = references.issue("honoraria")
    assert ref.startswith(f"NBTE-HON-{timezone.localdate().year}-")
    assert ReferenceFormat.objects.filter(code="honoraria", is_active=True).exists()
    assert ReferenceFormat.objects.filter(code="honoraria_file").exists()
    assert ReferenceFormat.objects.filter(code="honoraria_teller").exists()
    teller = references.issue("honoraria_teller")
    assert teller.startswith(f"NBTE-TEL-{timezone.localdate().year}-")
    # Switched off in Settings -> References: a unique fallback, still no error.
    ReferenceFormat.objects.filter(code="honoraria").update(is_active=False)
    first, second = references.issue("honoraria"), references.issue("honoraria")
    assert first.startswith("NBTE-HON-") and first != second


def test_build_schedule_works_without_the_seeded_format(world):
    from honoraria import schedules
    from qa_core.models import ReferenceFormat

    ReferenceFormat.objects.filter(code="honoraria").delete()
    from django.contrib.contenttypes.models import ContentType

    from honoraria.models import HonorariumEntry

    HonorariumEntry.objects.create(payee_user=world.officer, kind=HonorariumEntry.DOCUMENT_ANALYSIS,
                                   source_ct=ContentType.objects.get_for_model(world.officer),
                                   source_id="ref-fix", rate_key="honoraria.rate.internal_item",
                                   rate=Decimal("10000"), amount=Decimal("10000"))
    schedule = schedules.build_schedule(datetime.date(2000, 1, 1), datetime.date.today(), user=world.dof)
    assert schedule.reference.startswith("NBTE-HON-")


def test_migration_seed_set_creates_formats():
    from honoraria.references import ensure_formats
    from qa_core.models import ReferenceFormat

    ReferenceFormat.objects.filter(code__in=("honoraria", "honoraria_file", "honoraria_teller")).delete()
    assert ensure_formats() == 3
    assert ensure_formats() == 0
