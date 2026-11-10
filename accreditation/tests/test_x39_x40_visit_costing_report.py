"""v2.1 X39/X40: visit cost sheet from state rates, and the structured visit
report (NBTE/QA/ICT/VVT/001) with conflict declarations and falsified-item
fines.
"""
import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from qa_core.tests.phase4_fixtures import advance_to
from qa_core.tests.phase5_fixtures import p5_visit  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db


class NS:
    pass


def _superuser():
    from accounts.models import User

    return User.objects.create(email="phaseh.boss@nbte.local", first_name="Phase", last_name="H",
                               user_type="staff", is_superuser=True, must_change_password=False)


def _rate(kind, amount, *, state=None, role="Visit panel"):
    from accreditation.models import RateTable

    return RateTable.objects.create(code=f"x39-{kind}-{state.pk if state else 'all'}", role=role,
                                    allowance_type=kind, amount=Decimal(amount), state=state)


def _record_visit(p5_visit):
    from inspection import services
    from inspection.models import Inspection, PostVisitPersonnel

    inspection = p5_visit.inspection
    PostVisitPersonnel.unscoped.filter(inspection=inspection).delete()
    for member in services.team(inspection):
        mapping = member.qa_mapping
        if mapping is not None:
            people = ({"resource_person": mapping.resource_person} if mapping.resource_person_id
                      else {"qa_personnel": mapping.qa_personnel})
            duty = mapping.duty
        else:
            people, duty = {"staff": member.staff}, "physical_verification"
        PostVisitPersonnel.unscoped.create(inspection=inspection, role_on_team=member.role_on_team,
                                           duty=duty, attended=True, **people)
    today = timezone.localdate()
    Inspection.unscoped.filter(pk=inspection.pk).update(
        status="closed", actual_start=today - datetime.timedelta(days=2),
        actual_end=today - datetime.timedelta(days=1), closed_at=timezone.now())
    inspection.refresh_from_db()
    return inspection


def _submit_catalogue_value(report):
    from accreditation import visit_report
    from accreditation.models import SSQ, Submission

    rows = visit_report.catalogue_rows(report)
    item = rows[0]["item"]
    ssq = SSQ.unscoped.filter(qa_request_programme=report.qa_request_programme,
                              is_current=True).order_by("-version").first()
    field_key = next(key for key, field in ssq.template.field_map().items() if field["type"] == "catalogue")
    previous = Submission.unscoped.filter(ssq=ssq).order_by("-version").first()
    data = dict(previous.data_snapshot if previous else ssq.data or {})
    catalogue = dict(data.get(field_key) or {})
    catalogue[str(item.pk)] = "7"
    data[field_key] = catalogue
    submission = Submission.unscoped.create(
        ssq=ssq, version=(previous.version + 1 if previous else 1),
        template_version=ssq.template.version, data_snapshot=data,
        submitted_by=report.inspection.team_lead)
    return item, submission


@pytest.fixture
def states(db):
    """Seeded states (staff.StateOfOrigin is filled by seed_nbte; the test DB
    seeds skip the base seeders). Only what visit costing needs."""
    from staff.models import StateOfOrigin

    kd, _ = StateOfOrigin.objects.get_or_create(
        code="kd", defaults={"name": "KADUNA", "zone": "NW", "travel_mode": "road"})
    la, _ = StateOfOrigin.objects.get_or_create(
        code="la", defaults={"name": "LAGOS", "zone": "SW", "travel_mode": "air"})
    return SimpleNamespace(kd=kd, la=la)


def test_cost_sheet_is_built_from_state_rates(p4, states):
    from accreditation import visit_costing
    from accreditation.models import CostSheet
    from staff.models import StateOfOrigin

    case = p4.case
    case.institution.state = states.kd
    case.institution.save(update_fields=["state", "updated_at"])
    advance_to(p4, 2)  # stage 2: the cost sheet is open
    kaduna = case.institution.state
    _rate("dta", "20000")
    state_dta = _rate("dta", "25000", state=kaduna)
    _rate("transport", "50000")
    _rate("fuel", "30000")
    _rate("airport_taxi", "15000")  # not used: Kaduna is road

    lines = visit_costing.build_from_rates(case, user=p4.people.desk_officer, nights=2)
    assert len(lines) == 3
    assert {line.kind for line in lines} == {"dta", "transport", "fuel"}
    dta = next(line for line in lines if line.kind == "dta")
    assert dta.rate_id == state_dta.pk and dta.amount == Decimal("50000.00")
    sheet = CostSheet.unscoped.get(case=case, is_current=True)
    assert sheet.total == Decimal("130000.00")


def test_airport_taxi_only_for_air_states(p4, states):
    from accreditation import visit_costing
    from staff.models import StateOfOrigin

    case = p4.case
    case.institution.state = states.la  # Lagos: air
    case.institution.save(update_fields=["state", "updated_at"])
    advance_to(p4, 2)
    for kind, amount in (("dta", "20000"), ("transport", "50000"), ("airport_taxi", "15000")):
        _rate(kind, amount)
    lines = visit_costing.build_from_rates(case, user=p4.people.desk_officer, nights=1)
    assert "airport_taxi" in {line.kind for line in lines}


def test_actual_spend_is_recorded_after_es_approval(p4):
    from accreditation import visit_costing
    from accreditation.models import CostSheet

    advance_to(p4, 2)
    from accreditation.models import RateTable

    RateTable.objects.update(is_active=False)  # only the rate below (the seeded table has transport too)
    _rate("dta", "20000")
    visit_costing.build_from_rates(case=p4.case, user=p4.people.desk_officer, nights=1)
    sheet = CostSheet.unscoped.get(case=p4.case, is_current=True)
    boss = _superuser()
    with pytest.raises(ValidationError):
        visit_costing.record_actual(p4.case, user=boss, amount="1000")  # not ES-approved yet
    sheet.status = "es_approved"
    sheet.save(update_fields=["status", "updated_at"])
    visit_costing.record_actual(p4.case, user=boss, amount="19000", note="Two nights less")
    sheet.refresh_from_db()
    assert sheet.actual_total == Decimal("19000.00")
    summary = visit_costing.summary(p4.case)
    assert summary["variance"] == Decimal("-1000.00")


def test_report_needs_every_conflict_declaration(p5_visit):  # noqa: F811
    from accreditation import visit_report
    from inspection import services

    _record_visit(p5_visit)
    report = services.reports_of(p5_visit.case)[0]
    client = Client(HTTP_HOST="localhost")
    client.force_login(p5_visit.people.team_lead)
    page = client.get(f"/qa/inspection/reports/{report.pk}/")
    assert page.status_code == 200 and b"Findings are locked until all" in page.content
    with pytest.raises(ValidationError):
        visit_report.save_findings(report, user=p5_visit.people.team_lead,
                                   rows=[{"name": "Microscope", "declared_quantity": 5, "found_quantity": 2}])
    for person in visit_report.panel_people(p5_visit.inspection):
        visit_report.declare(p5_visit.inspection, user=p5_visit.people.team_lead,
                             staff=person.staff, resource_person=person.resource_person)
    visit_report.save_findings(report, user=p5_visit.people.team_lead,
                               rows=[{"name": "Microscope", "declared_quantity": 5, "found_quantity": 2},
                                     {"name": "Autoclave", "declared_quantity": 4, "found_quantity": 0}])
    report.refresh_from_db()
    from accreditation.models import VisitFinding
    assert VisitFinding.unscoped.filter(report=report).count() == 2
    assert report.fines_total == Decimal("500000.00")


def test_facilities_declared_values_are_read_only_from_the_submitted_ssq(p5_visit):  # noqa: F811
    from accreditation import visit_report
    from accreditation import ssq as engine
    from inspection import services
    from accreditation.models import SSQ, VisitFinding

    _record_visit(p5_visit)
    report = services.reports_of(p5_visit.case)[0]
    item, _submission = _submit_catalogue_value(report)
    ssq = SSQ.unscoped.filter(qa_request_programme=report.qa_request_programme, is_current=True).first()
    catalogue_fields = [field for field in ssq.template.field_map().values() if field["type"] == "catalogue"]
    assert {row["item"].pk for row in visit_report.catalogue_rows(report)} == {
        item.pk for field in catalogue_fields
        for item in engine.catalogue_items(field, report.qa_request_programme.programme)}
    for person in visit_report.panel_people(p5_visit.inspection):
        visit_report.declare(p5_visit.inspection, user=p5_visit.people.team_lead,
                             staff=person.staff, resource_person=person.resource_person)
    row = next(r for r in visit_report.catalogue_rows(report) if r["item"].pk == item.pk)
    visit_report.save_findings(
        report, user=p5_visit.people.team_lead,
        rows=[{"item_id": row["item"].pk, "name": row["item"].name, "declared_quantity": "999",
               "found_quantity": "1"}])
    finding = VisitFinding.unscoped.get(report=report, item=row["item"])
    assert finding.declared_quantity == Decimal(str(row["declared"]))


def test_ssq_declaration_prefill_does_not_replace_found_values(p5_visit):  # noqa: F811
    from accreditation import visit_report
    from inspection import services
    from accreditation.models import VisitFinding

    _record_visit(p5_visit)
    report = services.reports_of(p5_visit.case)[0]
    item, _submission = _submit_catalogue_value(report)
    for person in visit_report.panel_people(p5_visit.inspection):
        visit_report.declare(p5_visit.inspection, user=p5_visit.people.team_lead,
                             staff=person.staff, resource_person=person.resource_person)
    row = next(r for r in visit_report.catalogue_rows(report) if r["item"].pk == item.pk)
    visit_report.save_findings(
        report, user=p5_visit.people.team_lead,
        rows=[{"item_id": row["item"].pk, "name": row["item"].name, "declared_quantity": "999",
               "found_quantity": "3"}])
    visit_report.save_findings(
        report, user=p5_visit.people.team_lead,
        rows=[{"item_id": row["item"].pk, "name": row["item"].name,
               "declared_quantity": "999", "found_quantity": ""}])
    finding = VisitFinding.unscoped.get(report=report, item=row["item"])
    assert finding.declared_quantity == Decimal(str(row["declared"]))
    assert finding.found_quantity == Decimal("3")


def test_vvt_report_writes_are_gated_until_visit_recorded(p5_visit):  # noqa: F811
    from accreditation import visit_report
    from inspection import services

    report = services.reports_of(p5_visit.case)[0]
    with pytest.raises(PermissionDenied, match="Record the visit"):
        visit_report.save_findings(report, user=p5_visit.people.team_lead,
                                   rows=[{"name": "Microscope", "found_quantity": 2}])
    person = visit_report.panel_people(p5_visit.inspection)[0]
    with pytest.raises(ValidationError, match="Record the visit"):
        visit_report.declare(p5_visit.inspection, user=p5_visit.people.team_lead,
                             staff=person.staff, resource_person=person.resource_person)


def test_endorsed_reports_continue_through_recommendation_to_cqac(p5_visit):  # noqa: F811
    from accreditation import case_services as cs
    from inspection import demo

    _record_visit(p5_visit)
    demo.drive_reports(p5_visit.case)
    assert cs.current_stages(p5_visit.case) == ["post_visit_recommendation"]
    demo.recommend_all(p5_visit.case)
    from accreditation.case_demo import run_demo_capacity
    run_demo_capacity(p5_visit.case, p5_visit.case.desk_officer)
    holder = cs.holders(p5_visit.case, "post_visit_recommendation")[0]
    cs.act(p5_visit.case, user=holder, stage_code="post_visit_recommendation", action="approve")
    p5_visit.case.refresh_from_db()
    assert cs.current_stages(p5_visit.case) == ["cqac_recommendation"]


def test_a_declared_conflict_blocks_the_report(p5_visit):  # noqa: F811
    from accreditation import visit_report
    from inspection import services

    _record_visit(p5_visit)
    report = services.reports_of(p5_visit.case)[0]
    people = visit_report.panel_people(p5_visit.inspection)
    visit_report.declare(p5_visit.inspection, user=p5_visit.people.team_lead,
                         staff=people[0].staff, resource_person=people[0].resource_person,
                         has_conflict=True, details="Served at the institution last year.")
    with pytest.raises(ValidationError):
        visit_report.save_report(report, user=p5_visit.people.team_lead, summary="Attempt")


def test_falsified_items_are_billed(p5_visit):  # noqa: F811
    from accreditation import visit_report
    from inspection import services
    from payments.models import PaymentObligation

    _record_visit(p5_visit)
    report = services.reports_of(p5_visit.case)[0]
    for person in visit_report.panel_people(p5_visit.inspection):
        visit_report.declare(p5_visit.inspection, user=p5_visit.people.team_lead,
                             staff=person.staff, resource_person=person.resource_person)
    visit_report.save_findings(report, user=p5_visit.people.team_lead,
                               rows=[{"name": "Autoclave", "declared_quantity": 4, "found_quantity": 0}])
    boss = _superuser()
    obligation = visit_report.raise_fine(report, user=boss)
    assert obligation.amount == Decimal("500000.00")
    assert PaymentObligation.unscoped.filter(pk=obligation.pk, institution=p5_visit.inst).exists()
    with pytest.raises(ValidationError):
        visit_report.raise_fine(report, user=boss)  # already billed
