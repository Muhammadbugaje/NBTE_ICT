"""Phase 8 (Master Plan Phase 6) — the fast tests: the capacity engine, stored
assessments, approval-type expiry, streams in the fee formulas, the live fee
estimate, a new fee and a manual bill under two officers, the expiry job and
the approval-types settings page."""
import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from qa_core.tests.phase3_fixtures import set_config


# ---------------------------------------------------------------------------
# Capacity engine (Spec §7) — a worked example done by hand
# ---------------------------------------------------------------------------
# Constants (the seeded defaults): 8 hours × 5 days × 15 weeks; classroom use
# factor 0.8, lab 0.75; size factors (classroom) 30–40 seats → 1.0, 45–60 →
# 1.5; (lab) 30 places → 1.0; library seat ratio 0.1, material ratio 0.3333;
# lecturer 18 h/week, admin post 8 h/week; service staff ratio 20.
#
# Classroom: 2 rooms × 40 seats (factor 1.0 each) + 1 room × 50 seats (1.5)
#   standard_hours    = 8 × 5 × 15 × 0.8              = 480
#   total_size_factor = 2 × 1.0 + 1 × 1.5             = 3.5
#   contact_hours     = 20 h/week × 15                = 300
#   other_load        = 4 h/week × 15                 = 60
#   additional        = (480 × 3.5 − (300 × 1 + 60)) / 300 = (1680 − 360) / 300 = 4.4
#   supportable       = 1 approved + ⌊4.4⌋            = 5  → 3 asked: adequate
# Laboratory: 1 workshop × 30 places (1.0), 10 h/week, nothing approved yet
#   standard = 8 × 5 × 15 × 0.75 = 450; contact = 150; additional = 450 / 150 = 3 → 3
# Library: 40 students per stream × 3 streams = 120 students
#   seats needed ⌈120 × 0.1⌉ = 12; titles needed ⌈120 × 0.3333⌉ = ⌈39.996⌉ = 40
#   10 seats → ⌊10 / (0.1 × 40)⌋ = 2 streams; 60 titles → ⌊60 / 13.332⌋ = 4 → supportable 2 (short by 1)
# Teaching staff: 7 full-time lecturers, 2 with an admin post, 20 h per stream
#   hours = 5 × 18 + 2 × 8 = 106 → ⌊106 / 20⌋ = 5 streams; rule of thumb for 3 streams = 4 + 2 × 2 = 8
# Support staff: 5 technologists × 20 ÷ 40 students per stream = 2.5 → 2 streams; 120 / 20 = 6 needed
CLASSROOM = {"requested_streams": 3, "approved_streams": 1, "contact_hours_week": 20, "other_load_week": 4,
             "rooms": [{"label": "LT", "seats": 40, "count": 2}, {"label": "Hall", "seats": 50, "count": 1}]}


@pytest.fixture
def params(qa_seed):
    from accreditation import capacity

    return capacity.parameters()


def test_capacity_worked_example_classroom(params):
    from accreditation import capacity

    r = capacity.compute("classroom", CLASSROOM, params)
    assert r["detail"]["standard_hours"] == "480.0"
    assert Decimal(r["detail"]["total_size_factor"]) == Decimal("3.5")
    assert Decimal(r["detail"]["contact_hours"]) == 300 and Decimal(r["detail"]["other_load"]) == 60
    assert r["detail"]["additional_streams"] == "4.40"
    assert (r["supportable_streams"], r["shortfall"], r["adequacy"]) == (5, 0, "adequate")


def test_capacity_worked_example_other_kinds(params):
    from accreditation import capacity

    lab = capacity.compute("laboratory", {"requested_streams": 1, "approved_streams": 0, "contact_hours_week": 10,
                                          "rooms": [{"seats": 30, "count": 1}]}, params)
    assert lab["supportable_streams"] == 3 and lab["adequate"]
    lib = capacity.compute("library", {"requested_streams": 3, "students_per_stream": 40, "seats": 10, "titles": 60},
                           params)
    assert lib["detail"]["seats_needed"] == 12 and lib["detail"]["titles_needed"] == 40
    assert (lib["supportable_streams"], lib["shortfall"], lib["adequacy"]) == (2, 1, "inadequate")
    staff = capacity.compute("teaching_staff", {"requested_streams": 3, "students_per_stream": 40,
                                                "full_time_lecturers": 7, "admin_post_lecturers": 2,
                                                "contact_hours_week": 20}, params)
    assert staff["detail"]["teaching_hours_available"] == "106"
    assert staff["supportable_streams"] == 5 and staff["detail"]["rule_of_thumb_lecturers"] == 8
    assert staff["detail"]["meets_rule_of_thumb"] is False
    support = capacity.compute("support_staff", {"requested_streams": 3, "students_per_stream": 40,
                                                 "support_staff": 5}, params)
    assert support["supportable_streams"] == 2 and support["detail"]["support_staff_needed"] == 6
    assert not support["adequate"]


def test_size_factor_bands(params):
    from accreditation import capacity

    bands = params["constants"]["qa.capacity.classroom.size_factors"]
    assert [capacity.size_factor(s, bands, "classroom") for s in (10, 15, 25, 40, 44, 45, 60, 200)] == \
        [0, Decimal("0.5"), Decimal("0.5"), Decimal("1.0"), Decimal("1.0"), Decimal("1.5"), Decimal("2.0"),
         Decimal("2.0")]


def test_capacity_rejects_bad_inputs(params):
    from accreditation import capacity

    with pytest.raises(ValidationError):
        capacity.compute("classroom", {**CLASSROOM, "contact_hours_week": 0}, params)
    with pytest.raises(ValidationError):
        capacity.compute("library", {"requested_streams": 1, "students_per_stream": 30, "seats": -1}, params)


# ---------------------------------------------------------------------------
# A request programme without the slow SOP-03 walk
# ---------------------------------------------------------------------------
@pytest.fixture
def row(p3):
    from accreditation.models import QARequest, QARequestProgramme, QAType

    req = QARequest.unscoped.create(reference="NBTE/QAR/TEST/0001", institution=p3.inst,
                                    qa_type=QAType.objects.get(code="reaccreditation"), status="in_workflow")
    return QARequestProgramme.unscoped.create(qa_request=req, programme=p3.cs, requested_streams=3)


def test_changing_a_constant_never_changes_a_stored_assessment(row):
    from accreditation import capacity
    from accreditation.models import CapacityAssessment

    first = capacity.run(row, "classroom", CLASSROOM)
    assert first.result["supportable_streams"] == 5 and first.version == 1
    set_config("qa.capacity.classroom.use_factor", "0.5")  # 480 → 300 standard hours
    first.refresh_from_db()
    assert first.result["supportable_streams"] == 5
    assert first.parameters_snapshot["constants"]["qa.capacity.classroom.use_factor"] == "0.8"
    assert capacity.reproduce(first) == first.result  # reproducible from its own snapshot
    second = capacity.run(row, "classroom", CLASSROOM)
    # (300 × 3.5 − 360) / 300 = 2.3 → 1 + 2 = 3
    assert second.version == 2 and second.result["supportable_streams"] == 3
    first.refresh_from_db()
    assert not first.is_current and second.is_current
    assert CapacityAssessment.unscoped.filter(qa_request_programme=row).count() == 2


def test_capacity_prefill_reads_the_ssq_and_the_request(row):
    from accreditation import capacity

    inputs = capacity.prefill(row)
    assert set(inputs) == set(capacity.KINDS)
    assert inputs["classroom"]["requested_streams"] == 3 and inputs["classroom"]["approved_streams"] == 1
    assert inputs["library"]["students_per_stream"] == row.programme.category.students_per_stream


def test_decisions_board_does_not_depend_on_rebound_current_helper(row, monkeypatch):
    from accreditation import capacity, decisions
    from accreditation.models import QACase

    case = QACase.unscoped.create(reference="NBTE/QA/TEST/CAPACITY/0001", qa_request=row.qa_request,
                                  institution=row.qa_request.institution)
    monkeypatch.setattr(capacity, "current", None)

    stage_code = next(iter(decisions.STAGE_OF_FLOW))
    board_rows = decisions.board(case, stage_code, user=None)

    assert len(board_rows) == 1
    assert board_rows[0]["capacity"] is None


# ---------------------------------------------------------------------------
# Approval types and expiry derivation (B18)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code, months, expected", [
    ("full", 60, datetime.date(2031, 10, 15)),
    ("interim", 12, datetime.date(2027, 10, 15)),
    ("approval_to_commence", 24, datetime.date(2028, 10, 15)),
])
def test_expiry_is_derived_from_the_approval_type(qa_seed, code, months, expected):
    from accreditation.models import ApprovalType

    t = ApprovalType.objects.get(code=code)
    assert t.duration_months == months
    assert t.expiry_for(datetime.date(2026, 10, 15)) == expected


def test_expiry_at_month_end(qa_seed):
    from accreditation.models import add_months

    assert add_months(datetime.date(2024, 1, 31), 1) == datetime.date(2024, 2, 29)
    assert add_months(datetime.date(2026, 8, 31), -6) == datetime.date(2026, 2, 28)


def test_decision_expiry_is_never_typed(row, p3):
    from accreditation.models import ApprovalType, Decision, QACase

    case = QACase.unscoped.create(reference="NBTE/QAC/TEST/0001", qa_request=row.qa_request, institution=p3.inst)
    d = Decision.unscoped.create(case=case, qa_request_programme=row, programme=row.programme,
                                 stage=Decision.ES, outcome="approve",
                                 approval_type=ApprovalType.objects.get(code="interim"), approved_streams=2,
                                 commencement_date=datetime.date(2026, 10, 15),
                                 expiry_date=datetime.date(2099, 1, 1))  # a typed value is ignored
    assert d.expiry_date == datetime.date(2027, 10, 15)
    d.outcome = "deny"
    d.save()
    assert d.expiry_date is None


# ---------------------------------------------------------------------------
# Streams in the fee formulas
# ---------------------------------------------------------------------------
def test_fee_formulas_declare_the_documented_variables(qa_seed):
    from config.models import ConfigEntry
    from qa_core.settings_catalogue import FEE_VARIABLE_NAMES

    for key in ("qa.fee.institutional_processing", "qa.fee.programme_processing", "qa.fee.qa_request_processing"):
        data = ConfigEntry.objects.get(key=key).parsed()
        assert set(FEE_VARIABLE_NAMES) <= set(data["vars"]), key
    assert "streams" in ConfigEntry.objects.get(key="qa.fee.programme_processing").parsed()["expr"]


def test_programme_fee_uses_streams(p3):
    """SOP-01 programme bill: 2 programmes (private) at ₦2,000,000 each, 5 streams asked for in all (3 + 2),
    so 3 beyond one per programme at ₦250,000 → ₦4,750,000. The inputs are frozen on the bill."""
    from institutions.models import InstitutionOwnership, Programme
    from payments import services as pay

    inst = p3.other
    inst.ownership = InstitutionOwnership.objects.get(code="private")
    inst.save()
    Programme.unscoped.filter(institution=inst).delete()
    for name, streams in (("Statistics", 3), ("Accountancy", 2)):
        Programme.unscoped.create(institution=inst, name=name, status="proposed", requested_streams=streams)
    assert pay.fee_variables(inst)["streams"] == 5 and pay.fee_variables(inst)["max_streams"] == 3
    set_config("qa.fee.programme.per_additional_stream", "250000")
    bill = pay.issue_obligation(inst, "programme_processing", actor=p3.p2.fin_officer)
    assert bill.amount == Decimal("4750000.00")
    assert bill.formula_snapshot["inputs"]["streams"] == "5"
    set_config("qa.fee.programme.per_additional_stream", "0")
    bill.refresh_from_db()
    assert bill.amount == Decimal("4750000.00")  # the snapshot protects the issued bill


def test_qa_request_fee_and_estimate_use_streams(p3):
    """Increase in carrying capacity: CS (1 approved) asks for 3 streams. Fee = ₦50,000 base + 1 × ₦150,000
    + (3 − 1) × ₦40,000 = ₦280,000; the estimate shows one line per programme and the fixed base."""
    from accreditation import qa_services as svc
    from accreditation.models import QAType

    set_config("qa.fee.qa_request.per_additional_stream", "40000")
    qa_type = QAType.objects.get(code="increase_capacity")
    assert qa_type.asks_streams
    qa_type.ssq_template = QAType.objects.get(code="reaccreditation").ssq_template  # none seeded for this type
    qa_type.save()
    est = svc.fee_estimate(p3.inst, qa_type, [(p3.cs, 3)])
    assert est["total"] == Decimal("280000.00")
    assert est["lines"][0] == ("Fixed amount", Decimal("50000.00"))
    assert sum(a for _l, a in est["lines"]) == est["total"]
    with pytest.raises(ValidationError):  # must ask for more than already approved
        svc.create_request(p3.inst, qa_type, [p3.cs], user=p3.admin, streams={p3.cs.pk: "1"})
    with pytest.raises(ValidationError):  # above qa.streams.max_per_programme
        svc.create_request(p3.inst, qa_type, [p3.cs], user=p3.admin, streams={p3.cs.pk: "99"})
    req = svc.create_request(p3.inst, qa_type, [p3.cs], user=p3.admin, streams={p3.cs.pk: "3"})
    from accreditation.models import QARequestProgramme

    assert QARequestProgramme.unscoped.get(qa_request=req).requested_streams == 3
    assert svc.fee_variables(req) == {"programmes": 1, "new_programmes": 0, "streams": 3, "max_streams": 3}


def test_portal_estimate_recalculates_with_streams(p3, client_for, portal_host):
    set_config("qa.fee.qa_request.per_additional_stream", "40000")
    c = client_for(p3.admin, host=portal_host)
    url = "/qa-requests/estimate/"
    r = c.get(url, {"type": "increase_capacity", "programmes": [p3.cs.pk], f"streams_{p3.cs.pk}": "2"},
              HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and "240,000" in r.content.decode()
    r = c.get(url, {"type": "increase_capacity", "programmes": [p3.cs.pk], f"streams_{p3.cs.pk}": "4"},
              HTTP_HX_REQUEST="true")
    assert "320,000" in r.content.decode()
    # Another institution's programme is never counted.
    r = c.get(url, {"type": "increase_capacity", "programmes": [p3.bravo_prog.pk]}, HTTP_HX_REQUEST="true")
    assert "Tick at least one programme" in r.content.decode()
    page = c.get("/qa-requests/new/?type=increase_capacity")
    assert page.status_code == 200 and f'name="streams_{p3.cs.pk}"' in page.content.decode()


# ---------------------------------------------------------------------------
# A new fee and a manual bill, each confirmed by a second officer
# ---------------------------------------------------------------------------
def test_new_fee_needs_a_second_officer_then_bills_by_hand(p3, django_capture_on_commit_callbacks):
    from config.models import ConfigEntry
    from payments.models import FeeSchedule, PaymentObligation
    from qa_core import actions

    fin, director = p3.p2.fin_officer, p3.p2.fin_director
    payload = {"code": "late_submission", "name": "Late submission fee", "description": "Late SSQ submission",
               "expr": "100000 * programmes + 20000 * max(streams - programmes, 0)",
               "vars": ["programmes", "streams"], "due_days": 14, "remita_service_type_id": "",
               "revenue_account_id": None, "sample": {"programmes": "2", "streams": "3"}, "reason": "New policy"}
    pa = actions.initiate("payments.create_fee_schedule", actor=fin, payload=payload, reason="New policy")
    assert pa.status == "pending" and not FeeSchedule.objects.filter(code="late_submission").exists()
    with pytest.raises(Exception):
        actions.confirm(pa, fin)  # never by its initiator
    actions.confirm(pa, director)
    schedule = FeeSchedule.objects.get(code="late_submission")
    entry = ConfigEntry.objects.get(key="qa.fee.late_submission")
    assert entry.dual_control and schedule.formula == entry.key and schedule.due_days == 14
    # A bad formula is refused before anything is sent.
    bad = dict(payload, code="bad_fee", name="Bad", expr="__import__('os')")
    with pytest.raises(ValidationError):
        actions.initiate("payments.create_fee_schedule", actor=fin, payload=bad, reason="x" * 6)
    # Issue it by hand: 2 programmes, 3 streams → 200,000 + 20,000.
    pa = actions.initiate("payments.issue_bill", actor=fin, target=p3.inst,
                          payload={"fee_code": "late_submission", "variables": {"programmes": 2, "streams": 3},
                                   "description": "", "reason": "Late SSQ for CS and EEE"},
                          reason="Late SSQ for CS and EEE")
    assert not PaymentObligation.unscoped.filter(fee_schedule=schedule).exists()
    actions.confirm(pa, director)
    bill = PaymentObligation.unscoped.get(fee_schedule=schedule)
    assert bill.amount == Decimal("220000.00") and bill.institution == p3.inst


def test_fee_settings_page_explains_issuing_and_tests_formulas(p3, client_for):
    c = client_for(p3.p2.fin_officer)
    r = c.get("/qa/settings/fees/")
    body = r.content.decode()
    assert r.status_code == 200 and "Issued when" in body and "Add a fee" in body
    assert "create_payment_obligation" in body
    r = c.post("/qa/settings/fees/test/", {"expr": "50000 * streams", "vars": ["streams"], "sample_streams": "3"},
               HTTP_HX_REQUEST="true")
    assert "150,000" in r.content.decode()
    r = c.post("/qa/settings/fees/test/", {"expr": "50000 * streams", "vars": [], "sample_streams": "3"},
               HTTP_HX_REQUEST="true")
    assert "streams" in r.content.decode() and "would be" not in r.content.decode()
    assert c.get(f"/qa/finance/obligations/issue/?institution={p3.inst.code}").status_code == 200


# ---------------------------------------------------------------------------
# Expiry job and renewal reminders
# ---------------------------------------------------------------------------
def test_expiry_job_marks_expired_and_sends_each_reminder_once(p3, django_capture_on_commit_callbacks):
    from accreditation import expiry
    from institutions.models import Programme
    from notifications.models import Notification
    from qa_core.models import TimelineEvent

    today = timezone.localdate()
    Programme.unscoped.filter(pk=p3.cs.pk).update(expiry_date=today + datetime.timedelta(days=100))  # 180 crossed
    Programme.unscoped.filter(pk=p3.eee.pk).update(expiry_date=today - datetime.timedelta(days=1))
    Programme.unscoped.filter(pk=p3.far.pk).update(expiry_date=today + datetime.timedelta(days=20))  # 180/90/30
    with django_capture_on_commit_callbacks(execute=True):
        first = expiry.run(today)
    assert [p.pk for p in first["expired"]] == [p3.eee.pk]
    mine = sorted((p.pk, d) for p, d in first["reminded"] if p.institution_id == p3.inst.pk)
    assert mine == sorted([(p3.cs.pk, 180), (p3.far.pk, 30)])
    eee = Programme.unscoped.get(pk=p3.eee.pk)
    assert eee.status == "expired" and eee.expired_at is not None
    far = Programme.unscoped.get(pk=p3.far.pk)
    assert len(far.renewal_reminders_sent) == 3  # the 180, 90 and 30-day thresholds are all spent
    sent = Notification.objects.filter(recipient=p3.admin).count()
    assert sent >= 3
    with django_capture_on_commit_callbacks(execute=True):
        again = expiry.run(today)
    assert again["expired"] == [] and again["reminded"] == []
    assert Notification.objects.filter(recipient=p3.admin).count() == sent
    # The next threshold fires once, days later.
    with django_capture_on_commit_callbacks(execute=True):
        later = expiry.run(today + datetime.timedelta(days=14))  # CS at 86 days, Mechanical at 6
    assert sorted((p.pk, d) for p, d in later["reminded"] if p.institution_id == p3.inst.pk) == \
        sorted([(p3.cs.pk, 90), (p3.far.pk, 7)])
    events = TimelineEvent.unscoped.filter(institution=p3.inst, event_code__in=("programme.expired",
                                                                                 "programme.renewal_reminder"))
    assert events.filter(visibility="public").count() == events.count() == 5


def test_expiry_command_dry_run_changes_nothing(p3):
    import io

    from django.core.management import call_command

    from institutions.models import Programme

    Programme.unscoped.filter(pk=p3.eee.pk).update(expiry_date=timezone.localdate() - datetime.timedelta(days=3))
    out = io.StringIO()
    call_command("qa_programme_expiry", "--dry-run", stdout=out)
    assert "would be sent" in out.getvalue() and Programme.unscoped.get(pk=p3.eee.pk).status == "accredited"
    call_command("qa_programme_expiry", stdout=io.StringIO())
    assert Programme.unscoped.get(pk=p3.eee.pk).status == "expired"


# ---------------------------------------------------------------------------
# Settings → Approval types, and the portal programme board
# ---------------------------------------------------------------------------
def test_approval_types_page_needs_the_permission(p3, client_for, officer):
    from accreditation.models import ApprovalType

    plain = client_for(p3.p2.director)
    assert plain.get("/qa/settings/approval-types/").status_code in (200, 403)
    t = ApprovalType.objects.get(code="interim")
    r = plain.post(f"/qa/settings/approval-types/{t.pk}/", {"op": "save", "duration_months": "18"},
                   HTTP_HX_REQUEST="true")
    assert r.status_code in (403, 422)
    t.refresh_from_db()
    assert t.duration_months == 12
    admin = client_for(officer)  # QA Settings Admin holds manage_approval_types
    r = admin.post(f"/qa/settings/approval-types/{t.pk}/", {"op": "save", "duration_months": "18"},
                   HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    t.refresh_from_db()
    assert t.duration_months == 18
    assert admin.post(f"/qa/settings/approval-types/{t.pk}/", {"op": "save", "duration_months": "0"},
                      HTTP_HX_REQUEST="true").status_code == 422


def test_portal_programme_board_shows_countdown_and_renewal(p3, client_for, portal_host):
    c = client_for(p3.admin, host=portal_host)
    r = c.get("/programmes/")
    body = r.content.decode()
    assert r.status_code == 200
    assert "Computer Science" in body and "days left" in body
    assert "Request re-accreditation" in body  # CS expires in 120 days: inside the 6-month window
    assert "Mechanical Engineering Technology" in body and "re-accreditation opens" in body
    assert "Bravo" not in body
    assert c.get("/letters/").status_code == 200
