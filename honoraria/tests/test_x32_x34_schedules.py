"""v2.1 X32–X34: honoraria payment schedules.

Rates come from settings, lines are built from completed service rows, each
payee confirms receipt, and the income-vs-expenditure report adds up.
"""
import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from qa_core.tests.phase2_fixtures import p2  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db


@pytest.fixture
def p4(p2, make_institution, make_user):  # noqa: F811
    people = SimpleNamespace(
        desk_officer=p2.officer,
        director=p2.director,
        finance_director=p2.fin_director,
        es=make_user("honoraria.es@t.local", role="executive-secretary"),
    )
    return SimpleNamespace(people=people, inst=make_institution("HNR"))


def _record(staff, *, ssq=0, visit=0, day=None):
    from inspection.models import ServiceRecord

    day = day or datetime.date.today()
    return ServiceRecord.unscoped.create(
        person_type="staff", person_id=staff.pk, person_name=str(staff), staff=staff,
        duty="member", served_from=day, served_to=day, ssq_count=ssq, visit_count=visit,
        source="ssq_review" if ssq else "inspection_closed")


def _payment_accounts(user):
    from core import fieldcrypto
    from honoraria.models import Bank, PayeeBankAccount
    from payments.models import BankAccount

    payee_bank = Bank.objects.create(code="test-payee-bank", name="Test Payee Bank", cbn_code="999")
    PayeeBankAccount.objects.create(
        user=user, bank=payee_bank, account_number_enc=fieldcrypto.encrypt("0123456789"),
        account_last4="6789", account_name=user.get_full_name() or user.email,
        confirmed_at=timezone.now(),
    )
    return BankAccount.objects.create(
        bank_name="Test NBTE Bank", account_name="NBTE Payments", account_number="1234567890",
        is_active=True, is_placeholder=False,
    )


def test_schedule_uses_the_settings_rates_and_lines_are_visible(p4):  # noqa: F811
    from accounts.models import User
    from honoraria import schedules
    from inspection.models import ResourcePerson, ServiceRecord

    boss = p4.people.finance_director
    staff = p4.people.desk_officer.staff_profile
    _record(staff, ssq=3)
    person = ResourcePerson.objects.create(full_name="Dr Ada Obi", email="ada.obi@example.org",
                                           source="academia", status="active")
    ServiceRecord.unscoped.create(person_type="resource_person", person_id=person.pk,
                                  person_name=person.full_name, resource_person=person, duty="resource_inspection",
                                  served_from=datetime.date.today(), served_to=datetime.date.today(),
                                  visit_count=1, source="inspection_closed")

    schedule = schedules.build_schedule(datetime.date.today().replace(day=1), datetime.date.today(),
                                        user=boss, title="Test run")
    assert schedule.lines.count() == 2
    internal = schedule.lines.get(source="internal_item")
    assert internal.quantity == 3 and internal.rate == Decimal("10000")
    assert internal.amount == Decimal("30000.00")
    academic = schedule.lines.get(source="academic_resource_person")
    assert academic.rate == Decimal("30000") and academic.amount == Decimal("30000.00")
    assert schedule.total == Decimal("60000.00")

    # The payee sees their own line; a stranger sees none.
    assert list(schedules.lines_for(p4.people.desk_officer, schedule)) == [internal]
    stranger = User.objects.create(email="stranger@nbte.local", first_name="No", last_name="Body",
                                   user_type="staff", must_change_password=False)
    assert not schedules.lines_for(stranger, schedule).exists()


def test_payment_obligation_permission_does_not_grant_all_honoraria(p4):  # noqa: F811
    from accounts.models import User
    from django.contrib.auth.models import Permission
    from honoraria import schedules
    from honoraria.models import PaymentSchedule

    user = User.objects.create_user(
        email="honoraria.viewer@t.local", password="test-password",
        user_type="staff", must_change_password=False)
    permission = Permission.objects.get(
        content_type__app_label="payments", codename="view_obligation")
    user.user_permissions.add(permission)
    schedule = PaymentSchedule.objects.create(
        reference="HNR-VISIBILITY-1", period_start=datetime.date.today(),
        period_end=datetime.date.today())

    assert not schedules.can_view_all(user)
    assert not schedules.schedules_for(user).filter(pk=schedule.pk).exists()


def test_overlapping_schedule_build_cannot_reuse_service_work(p4):  # noqa: F811
    from honoraria import schedules
    from honoraria.models import PaymentSchedule

    today = datetime.date.today()
    _record(p4.people.desk_officer.staff_profile, ssq=2, day=today)
    finance = p4.people.finance_director
    first = schedules.build_schedule(today, today, user=finance)
    assert first.lines.get().quantity == 2
    assert first.lines.get().service_record_claims.filter(status="reserved").count() == 1

    with pytest.raises(ValidationError, match="No unpaid payable work"):
        schedules.build_schedule(today, today, user=finance)
    assert PaymentSchedule.objects.count() == 1


def test_service_record_claim_constraint_blocks_a_duplicate_schedule(p4):  # noqa: F811
    from honoraria import schedules
    from honoraria.models import HonorariumServiceRecordClaim, PaymentSchedule, PaymentScheduleLine

    today = datetime.date.today()
    record = _record(p4.people.desk_officer.staff_profile, ssq=1, day=today)
    schedule = schedules.build_schedule(today, today, user=p4.people.finance_director)
    line = schedule.lines.get()
    duplicate_schedule = PaymentSchedule.objects.create(
        reference="HNR-DUPLICATE-CLAIM", period_start=today, period_end=today)
    duplicate_line = PaymentScheduleLine.objects.create(
        schedule=duplicate_schedule, user=line.user, name=line.name, role=line.role,
        source=line.source, quantity=line.quantity, rate=line.rate)

    with pytest.raises(ValidationError, match="already claimed"):
        schedules._reserve_service_records(duplicate_line, [record])

    assert HonorariumServiceRecordClaim.objects.filter(
        service_record=record, status=HonorariumServiceRecordClaim.RESERVED).count() == 1


def test_resource_person_can_view_and_confirm_their_visit_allowance(p4, portal_host):  # noqa: F811
    from accounts.models import User
    from django.test import Client
    from honoraria import schedules, services
    from honoraria.models import Bank, PayeeBankAccount
    from inspection.models import ResourcePerson, ServiceRecord
    from core import fieldcrypto
    from qa_core import home

    today = datetime.date.today()
    user = User.objects.create_user(
        email="rp.earnings@t.local", password="test-password", user_type="resource_person",
        first_name="Ada", last_name="Obi", must_change_password=False)
    person = ResourcePerson.objects.create(
        user=user, full_name="Dr Ada Obi", email=user.email, source="academia", status="active")
    ServiceRecord.unscoped.create(
        person_type="resource_person", person_id=person.pk, person_name=person.full_name,
        resource_person=person, duty="resource_inspection", served_from=today, served_to=today,
        visit_count=1, source="inspection_closed")
    payee_bank = Bank.objects.create(code="rp-test-bank", name="RP Test Bank", cbn_code="999")
    PayeeBankAccount.objects.create(
        resource_person=person, bank=payee_bank, account_number_enc=fieldcrypto.encrypt("0123456789"),
        account_last4="6789", account_name=person.full_name, confirmed_at=timezone.now())

    schedule = schedules.build_schedule(today, today, user=p4.people.finance_director)
    line = schedule.lines.get(resource_person=person)
    assert schedules.lines_for(user, schedule).get(pk=line.pk)
    assert schedules.payee_lines_for(user).get(pk=line.pk)
    assert services.active_account(user).resource_person_id == person.pk

    client = Client(HTTP_HOST=portal_host)
    client.force_login(user)
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b'href="/me/earnings/"' in dashboard.content
    assert b'href="/me/bank/"' in dashboard.content
    assert client.get("/me/bank/").status_code == 200
    earnings = client.get("/me/earnings/")
    assert earnings.status_code == 200
    assert b"Visit allowances and payments" in earnings.content
    assert b"Awaiting schedule" in earnings.content

    funding = _payment_accounts(p4.people.finance_director)
    schedules.approve(schedule, user=p4.people.finance_director)
    schedules.mark_paid(
        schedule, user=p4.people.finance_director, funding_account_id=funding.pk,
        payment_reference="RP-TRANSFER-001")
    schedules.confirm_receipt(line, user=user)
    line.refresh_from_db()
    assert line.confirmed_by_id == user.pk
    assert line.confirmed_at is not None
    assert home.w_honoraria(user, home.profile(user)) is not None


def test_finance_can_record_partial_batches_and_adjust_line_amounts(p4):  # noqa: F811
    from accounts.models import User
    from core import fieldcrypto
    from django.test import Client
    from django.urls import reverse
    from honoraria import schedules
    from honoraria.models import Bank, PayeeBankAccount
    from inspection.models import ResourcePerson, ServiceRecord

    today = datetime.date.today()
    staff_user = p4.people.desk_officer
    _record(staff_user.staff_profile, ssq=2, day=today)
    rp_user = User.objects.create_user(
        email="rp.batch@t.local", password="test-password", user_type="resource_person",
        first_name="Ada", last_name="Obi", must_change_password=False)
    rp = ResourcePerson.objects.create(
        user=rp_user, full_name="Dr Ada Obi", email=rp_user.email, source="academia", status="active")
    ServiceRecord.unscoped.create(
        person_type="resource_person", person_id=rp.pk, person_name=rp.full_name, resource_person=rp,
        duty="resource_inspection", served_from=today, served_to=today, visit_count=1,
        source="inspection_closed")
    payee_bank = Bank.objects.create(code="partial-test-bank", name="Partial Test Bank", cbn_code="999")
    for owner_user in (staff_user,):
        PayeeBankAccount.objects.create(
            user=owner_user, bank=payee_bank, account_number_enc=fieldcrypto.encrypt("0123456789"),
            account_last4="6789", account_name=owner_user.get_full_name() or owner_user.email,
            confirmed_at=timezone.now())
    PayeeBankAccount.objects.create(
        resource_person=rp, bank=payee_bank, account_number_enc=fieldcrypto.encrypt("9876543210"),
        account_last4="3210", account_name=rp.full_name, confirmed_at=timezone.now())
    from payments.models import BankAccount

    funding = BankAccount.objects.create(
        bank_name="Test NBTE Bank", account_name="NBTE Payments", account_number="1234567890",
        is_active=True, is_placeholder=False)
    finance = p4.people.finance_director
    schedule = schedules.build_schedule(today, today, user=finance)
    staff_line = schedule.lines.get(user=staff_user)
    rp_line = schedule.lines.get(resource_person=rp)
    assert staff_line.amount == Decimal("20000.00")

    schedules.approve(schedule, user=finance)
    client = Client()
    client.force_login(finance)
    detail = client.get(reverse("honoraria_sched:detail", args=[schedule.pk]))
    assert detail.status_code == 200
    assert b"Print schedule / payment slip" in detail.content
    assert f'name="amount_{staff_line.pk}"'.encode() in detail.content

    schedules.mark_paid(
        schedule, user=finance, funding_account_id=funding.pk, payment_reference="FIRST-BATCH",
        line_ids=[str(staff_line.pk)], amounts={str(staff_line.pk): "8000.00"})
    schedule.refresh_from_db()
    staff_line.refresh_from_db()
    rp_line.refresh_from_db()
    assert schedule.status == "approved"
    assert schedule.total == Decimal("38000.00")
    assert staff_line.amount == Decimal("8000.00") and staff_line.amount_override
    assert staff_line.payment_reference == "FIRST-BATCH"
    assert rp_line.paid_at is None
    from qa_core import home

    finance_widget = home.w_honoraria_financial(finance, home.profile(finance))
    assert dict(finance_widget.stats)["Earned"] == "₦50,000.00"
    assert dict(finance_widget.stats)["Outstanding"] == "₦30,000.00"
    assert dict(finance_widget.stats)["Honoraria paid out"] == "₦8,000.00"

    schedules.confirm_receipt(staff_line, user=staff_user)
    schedules.mark_paid(
        schedule, user=finance, funding_account_id=funding.pk, payment_reference="SECOND-BATCH",
        line_ids=[str(rp_line.pk)])
    schedule.refresh_from_db()
    rp_line.refresh_from_db()
    assert schedule.status == "paid"
    assert rp_line.payment_reference == "SECOND-BATCH"
    report = schedules.income_expenditure(today.replace(day=1), today)
    assert report["expenditure"] == Decimal("38000.00")


def test_payee_confirms_receipt_and_finance_pays(p4):  # noqa: F811
    from honoraria import schedules

    boss = p4.people.finance_director
    staff = p4.people.desk_officer.staff_profile
    _record(staff, ssq=1)
    schedule = schedules.build_schedule(datetime.date.today().replace(day=1), datetime.date.today(), user=boss)
    line = schedule.lines.get()
    funding_account = _payment_accounts(p4.people.desk_officer)
    with pytest.raises(PermissionDenied):
        schedules.confirm_receipt(line, user=boss)  # not the payee
    with pytest.raises(PermissionDenied):
        schedules.approve(schedule, user=p4.people.desk_officer)
    schedules.approve(schedule, user=boss)
    with pytest.raises(PermissionDenied):
        schedules.payment_details(schedule, user=p4.people.desk_officer)
    details, missing = schedules.payment_details(schedule, user=boss)
    assert not missing and details[line.pk]["account_number"] == "0123456789"
    schedules.mark_paid(schedule, user=boss, funding_account_id=funding_account.pk,
                        payment_reference="BANK-TRANSFER-001")
    schedule.refresh_from_db()
    assert schedule.status == "paid" and schedule.paid_at is not None
    assert schedule.funding_bank_name == "Test NBTE Bank"
    assert schedule.funding_account_last4 == "7890"
    assert schedule.payment_reference == "BANK-TRANSFER-001"
    assert schedule.paid_by_id == boss.pk
    schedules.confirm_receipt(line, user=p4.people.desk_officer)
    line.refresh_from_db()
    assert line.confirmed_at is not None


def test_income_expenditure_adds_up(p4):  # noqa: F811
    from django.test import Client
    from django.urls import reverse
    from honoraria import schedules
    from qa_core import home
    from payments.models import BankAccount, FeeSchedule, PaymentObligation

    boss = p4.people.finance_director
    staff = p4.people.desk_officer.staff_profile
    _record(staff, ssq=2)
    schedule = schedules.build_schedule(datetime.date.today().replace(day=1), datetime.date.today(), user=boss)
    funding_account = _payment_accounts(p4.people.desk_officer)
    other_account = BankAccount.objects.create(
        bank_name="Other NBTE Bank", account_name="Other Account", account_number="9876543210",
        is_active=True, is_placeholder=False,
    )
    fee = FeeSchedule.objects.create(
        code="honoraria-report-test", name="QA application fee",
        description="QA application fee", formula="qa.fee.test",
        revenue_account=funding_account)
    PaymentObligation.unscoped.create(
        reference="HNR-REPORT-INCOME", institution=p4.inst, fee_schedule=fee,
        description="QA application processing fee", amount=Decimal("12500"),
        amount_in_words="Twelve thousand five hundred naira",
        formula_snapshot={"components": [
            {"label": "Institution fee", "kind": "base", "amount": "10000.00"},
            {"label": "Processing fee", "kind": "processing", "amount": "2500.00"},
        ]}, due_at=timezone.now(),
        status="confirmed", amount_paid=Decimal("12500"), confirmed_at=timezone.now())
    schedules.approve(schedule, user=boss)
    schedules.mark_paid(schedule, user=boss, funding_account_id=funding_account.pk,
                        payment_reference="REPORT-TRANSFER-001")
    report = schedules.income_expenditure(datetime.date.today().replace(day=1), datetime.date.today())
    bank_report = schedules.income_expenditure(
        datetime.date.today().replace(day=1), datetime.date.today(), bank_account_id=funding_account.pk)
    other_bank_report = schedules.income_expenditure(
        datetime.date.today().replace(day=1), datetime.date.today(), bank_account_id=other_account.pk)
    assert report["expenditure"] == Decimal("20000.00")
    assert report["income"] == Decimal("12500.00")
    assert report["income_by_fee"] == [
        {"label": "Institution fee", "count": 1, "amount": Decimal("10000.00")},
        {"label": "Processing fee", "count": 1, "amount": Decimal("2500.00")},
    ]
    assert report["paid_lines"][0].report_payee == p4.people.desk_officer.get_full_name()
    assert report["paid_lines"][0].report_activities == ["SSQ review × 2"]
    assert report["paid_lines"][0].payment_reference == "REPORT-TRANSFER-001"
    assert report["balance"] == Decimal("-7500.00")
    finance_widget = home.w_honoraria_financial(boss, home.profile(boss))
    assert dict(finance_widget.stats)["Confirmed fee income"] == "₦12,500.00"
    assert dict(finance_widget.stats)["Honoraria paid out"] == "₦20,000.00"
    client = Client()
    client.force_login(boss)
    page = client.get(reverse("honoraria_sched:income"))
    assert page.status_code == 200
    assert b"Income by fee" in page.content
    assert b"Processing fee" in page.content
    assert b"Honoraria paid by person and work" in page.content
    assert b"SSQ review" in page.content and b"REPORT-TRANSFER-001" in page.content
    client.force_login(p4.people.es)
    assert client.get(reverse("honoraria_sched:income")).status_code == 200
    client.force_login(p4.people.director)
    assert client.get(reverse("honoraria_sched:income")).status_code == 404
    assert bank_report["expenditure"] == Decimal("20000.00")
    assert other_bank_report["expenditure"] == Decimal("0")


def test_building_needs_finance(p4):  # noqa: F811
    from honoraria import schedules

    with pytest.raises(PermissionDenied):
        schedules.build_schedule(datetime.date.today(), datetime.date.today(), user=p4.people.desk_officer)
    with pytest.raises(ValidationError):
        schedules.build_schedule(datetime.date.today(), datetime.date.today() - datetime.timedelta(days=1),
                                 user=p4.people.finance_director)


def test_completed_payable_assignment_accrues_and_tracks_receipt(p4):  # noqa: F811
    from accounts.models import User
    from assignments import work
    from assignments.models import WorkAssignment
    from honoraria import schedules
    from honoraria.models import HonorariumEntry

    payee = p4.people.desk_officer
    assignment = work.assign(
        p4.inst, WorkAssignment.DOCUMENT_ANALYSIS, payee, department=payee.department,
        payable=True, accept_now=True,
    )
    work.complete(assignment, actor=payee)

    entry = HonorariumEntry.objects.get(payee_user=payee)
    assert entry.amount == Decimal("10000")
    assert entry.status == HonorariumEntry.ACCRUED
    assert HonorariumEntry.objects.filter(source_id=str(assignment.pk), payee_user=payee).count() == 1
    from qa_core import home

    dashboard_widget = home.w_honoraria(payee, home.profile(payee))
    assert dashboard_widget is not None
    assert ("Accrued", "₦10,000.00") in dashboard_widget.stats
    department_widget = home.w_honoraria(p4.people.director, home.profile(p4.people.director))
    assert department_widget.title == "Department honoraria due"
    assert ("Department accrued", "₦10,000.00") in department_widget.stats
    es_widget = home.w_honoraria(p4.people.es, home.profile(p4.people.es))
    assert es_widget.title == "Honoraria due"
    assert ("All accrued", "₦10,000.00") in es_widget.stats
    super_admin = User.objects.create(
        email="honoraria.superadmin@nbte.local", user_type="staff", is_superuser=True,
        must_change_password=False,
    )
    finance_widget = home.w_honoraria_financial(super_admin, home.profile(super_admin))
    assert finance_widget is not None
    assert finance_widget.url_label == "Income / expenditure report"
    assert [item.label for item in finance_widget.items] == ["Payment schedules"]

    finance = p4.people.finance_director
    today = datetime.date.today()
    schedule = schedules.build_schedule(today.replace(day=1), today, user=finance)
    line = schedule.lines.get(user=payee)
    assert list(line.entries.all()) == [entry]
    entry.refresh_from_db()
    assert entry.status == HonorariumEntry.SCHEDULED

    schedules.approve(schedule, user=finance)
    funding_account = _payment_accounts(payee)
    schedules.mark_paid(schedule, user=finance, funding_account_id=funding_account.pk,
                        payment_reference="ACCRUAL-TRANSFER-001")
    entry.refresh_from_db()
    assert entry.status == HonorariumEntry.PAID
    schedules.confirm_receipt(line, user=payee)
    entry.refresh_from_db()
    assert entry.status == HonorariumEntry.RECEIVED
