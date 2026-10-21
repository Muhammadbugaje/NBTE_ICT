"""Phase 5 — the visit record, the post-visit report (stage 12), the
endorsement chain (B14), B17, attendance overrides and the stage 12–13 flow."""
import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _complete(p5, absent_users=()):
    from inspection import services

    insp = p5.inspection
    absent = [m.pk for m in services.team(insp)
              if (m.staff_id and m.staff.user in absent_users)]
    today = timezone.localdate()
    services.complete(insp, actor=insp.team_lead, actual_start=today - datetime.timedelta(days=2),
                      actual_end=today - datetime.timedelta(days=1), absent=absent)
    insp.refresh_from_db()
    return insp


def _eee_report(p5):
    from inspection import services

    return next(r for r in services.reports_of(p5.case) if r.qa_request_programme_id == p5.eee.pk)


# ---------------------------------------------------------------------------
# Stage 10 → 12: the inspection comes from the case
# ---------------------------------------------------------------------------
def test_es_visit_approval_creates_the_inspection_with_the_mapped_team(p5_visit):
    from inspection import services

    insp = p5_visit.inspection
    assert insp is not None and insp.status == "scheduled"
    assert insp.team_lead == p5_visit.people.team_lead
    assert insp.reference.startswith("NBTE/INS/")
    people = {str(m.qa_mapping.person) if m.qa_mapping_id else str(m.staff) for m in services.team(insp)}
    assert p5_visit.rps["rp_eng"].full_name in people and p5_visit.rps["rp_sci"].full_name in people
    # Stage 12 opened: one draft report per programme, waiting for the Team Lead.
    reports = services.reports_of(p5_visit.case)
    assert len(reports) == 2 and {r.status for r in reports} == {"draft"}
    assert services.report_holders(reports[0])[0] == [p5_visit.people.team_lead]


def test_only_the_team_lead_records_the_visit(p5_visit):
    from inspection import services

    today = timezone.localdate()
    with pytest.raises(PermissionDenied):
        services.complete(p5_visit.inspection, actor=p5_visit.people.desk_officer,
                          actual_start=today, actual_end=today)
    with pytest.raises(ValidationError):  # not in the future
        services.complete(p5_visit.inspection, actor=p5_visit.people.team_lead,
                          actual_start=today, actual_end=today + datetime.timedelta(days=1))


def test_recording_the_visit_writes_the_service_history_for_attendees_only(p5_visit):
    from inspection.models import ServiceRecord

    insp = _complete(p5_visit, absent_users=[p5_visit.people.desk_officer])
    assert insp.status == "closed" and insp.closed_at
    names = set(ServiceRecord.unscoped.filter(inspection=insp).values_list("person_name", flat=True))
    assert p5_visit.rps["rp_eng"].full_name in names
    assert not any("Officer" in n and "Amina" in n for n in names)  # the absentee: no row
    with pytest.raises(Exception):
        ServiceRecord.unscoped.filter(inspection=insp).first().delete()  # append-only


# ---------------------------------------------------------------------------
# B17 — only attendees submit the report
# ---------------------------------------------------------------------------
def test_b17_only_attendees_write_and_submit(p5_visit):
    from inspection import services

    report = _eee_report(p5_visit)
    ok, why = services.can_write(report, p5_visit.people.team_lead)
    assert not ok and "Record the visit" in why  # the visit is not recorded yet
    _complete(p5_visit, absent_users=[p5_visit.people.desk_officer])
    report.refresh_from_db()
    assert services.can_write(report, p5_visit.people.team_lead)[0]
    for outsider in (p5_visit.people.desk_officer, p5_visit.people.division_head, p5_visit.people.director):
        ok, why = services.can_write(report, outsider)
        assert not ok and "B17" in why
        with pytest.raises(PermissionDenied):
            services.submit_report(report, user=outsider)
    # The attending resource person (portal account) is an attendee too.
    assert services.can_write(report, p5_visit.rps["rp_eng"].user)[0]


def test_submit_needs_a_summary_and_a_recommendation(p5_visit):
    from inspection import services

    _complete(p5_visit)
    report = _eee_report(p5_visit)
    with pytest.raises(ValidationError) as exc:
        services.submit_report(report, user=p5_visit.people.team_lead)
    assert {"summary", "recommendation"} <= set(exc.value.message_dict)


# ---------------------------------------------------------------------------
# The endorsement chain (B14): endorse / add content / return with a remark
# ---------------------------------------------------------------------------
def test_endorsement_chain_return_with_mandatory_remark_and_resubmit(p5_visit):
    from inspection import demo, services
    from inspection.models import ReportEndorsement
    from qa_core.models import QATaskLink

    _complete(p5_visit)
    people = p5_visit.people
    report = demo.write_and_submit(_eee_report(p5_visit), people.team_lead)
    assert report.status == "submitted"
    assert services.report_holders(report)[0] == [people.division_head]
    with pytest.raises(PermissionDenied):  # not this layer's holder
        services.endorse(report, user=people.deputy_director, action="endorse")
    report = services.endorse(report, user=people.division_head, action="add_content",
                              content="Add the workshop bench count to the facilities section.")
    assert report.status == "submitted"  # adding content keeps the layer
    report = services.endorse(report, user=people.division_head, action="endorse")
    assert report.status == "division_endorsed"
    assert services.report_holders(report)[0] == [people.deputy_director]
    with pytest.raises(ValidationError):  # a return needs a remark
        services.endorse(report, user=people.deputy_director, action="return", content="")
    report = services.endorse(report, user=people.deputy_director, action="return",
                              content="Quantify the deficiencies before I verify.")
    assert report.status == "returned"
    holders, link = services.report_holders(report)
    assert holders == [people.team_lead]  # back to the person who submitted
    assert link.task.comments.filter(is_pinned=True, body__icontains="Quantify").exists()
    # The Team Lead fixes and resubmits: the chain starts again at the Division Head.
    report = services.save_report(report, user=people.team_lead,
                                  summary=demo.DEMO_SUMMARY + " Two benches and one HOD post are short.")
    report = services.submit_report(report, user=people.team_lead)
    assert report.status == "submitted" and report.version == 2
    for _ in range(3):
        report = demo.endorse_once(report)
    assert report.status == "director_endorsed"
    trail = list(ReportEndorsement.unscoped.filter(report=report).values_list("layer", "action"))
    assert ("deputy_director", "return") in trail and ("division_head", "add_content") in trail
    assert trail.count(("team", "submit")) == 2  # first submission and the resubmission
    assert not QATaskLink.objects.filter(object_id=report.pk, closed_at__isnull=True,
                                         stage_code__in=("submitted", "division_endorsed", "deputy_verified",
                                                         "returned")).exists()


def test_stage_12_advances_only_when_every_report_is_director_endorsed(p5_visit):
    from accreditation import case_services as cs
    from inspection import demo, services

    _complete(p5_visit)
    eee = _eee_report(p5_visit)
    demo.write_and_submit(eee, p5_visit.people.team_lead)
    for _ in range(3):
        eee = demo.endorse_once(eee)
    p5_visit.case.refresh_from_db()
    assert "post_visit_report" in cs.current_stages(p5_visit.case)  # the CS report is still open
    demo.drive_reports(p5_visit.case)
    p5_visit.case.refresh_from_db()
    assert cs.current_stages(p5_visit.case) == ["post_visit_recommendation"]
    assert services.report_variables(p5_visit.case) == {"reports_total": 2, "reports_open": 0}


def test_stage_13_needs_a_recommendation_per_programme(p5_visit):
    from accreditation import case_demo
    from accreditation import case_services as cs
    from inspection import demo, services

    _complete(p5_visit)
    demo.drive_reports(p5_visit.case)
    case = p5_visit.case
    case.refresh_from_db()
    holder = cs.holders(case, "post_visit_recommendation")[0]
    assert cs.prerequisites(case, "post_visit_recommendation")
    with pytest.raises(PermissionDenied):
        services.recommend(services.reports_of(case)[0], user=p5_visit.people.desk_officer,
                           recommendation="accredit")
    demo.recommend_all(case)
    assert not cs.prerequisites(case, "post_visit_recommendation")
    cs.act(case, user=holder, stage_code="post_visit_recommendation", action="approve", remark="Recommended")
    case.refresh_from_db()
    assert case_demo.stage_no(case) == 14


# ---------------------------------------------------------------------------
# Override Post-Visit Personnel → compensating ServiceRecord rows
# ---------------------------------------------------------------------------
def test_corrected_attendance_writes_a_compensating_row(p5_visit):
    from inspection.models import PostVisitPersonnel, ServiceRecord
    from qa_core import actions

    insp = _complete(p5_visit)
    rp = p5_visit.rps["rp_sci"]
    pvp = PostVisitPersonnel.unscoped.get(inspection=insp, resource_person=rp)
    original = ServiceRecord.unscoped.get(inspection=insp, resource_person=rp)
    payload = {"changes": [{"pvp_id": pvp.pk, "attended": False, "reason": "Fell ill on the first day"}],
               "reason": "Fell ill on the first day"}
    with pytest.raises(PermissionDenied):  # only the Team Lead starts it
        actions.initiate("inspection.override_post_visit_personnel", actor=p5_visit.people.desk_officer,
                         target=insp, payload=payload, reason="x")
    pa = actions.initiate("inspection.override_post_visit_personnel", actor=p5_visit.people.team_lead,
                          target=insp, payload=payload, reason="Fell ill on the first day")
    assert pa.status == "pending"
    pvp.refresh_from_db()
    assert pvp.attended  # nothing changes until a second officer confirms
    actions.confirm(pa, p5_visit.people.director, remark="Checked with the institution")
    pvp.refresh_from_db()
    assert not pvp.attended and pvp.overridden_via_id == pa.pk
    rows = list(ServiceRecord.unscoped.filter(inspection=insp, resource_person=rp).order_by("pk"))
    assert len(rows) == 2 and rows[0].pk == original.pk and rows[0].visit_count == 1  # never edited
    assert rows[1].voids_id == original.pk and rows[1].visit_count == -1
    assert sum(r.visit_count for r in rows) == 0


def test_override_can_add_an_unplanned_attendee(p5_visit):
    from inspection.models import PostVisitPersonnel, ServiceRecord
    from qa_core import actions

    insp = _complete(p5_visit)
    extra = p5_visit.people.busy_officer.staff_profile
    pa = actions.initiate("inspection.override_post_visit_personnel", actor=p5_visit.people.team_lead,
                          target=insp, payload={"changes": [{"staff_id": extra.pk, "attended": True,
                                                             "reason": "Joined to drive"}]},
                          reason="Joined to drive")
    actions.confirm(pa, p5_visit.people.director)
    assert PostVisitPersonnel.unscoped.filter(inspection=insp, staff=extra, planned=False, attended=True).exists()
    assert ServiceRecord.unscoped.filter(inspection=insp, staff=extra, visit_count=1).exists()


def test_ssq_review_completion_writes_a_service_row(p5):
    from accreditation.models import SSQ
    from inspection import services
    from inspection.models import ServiceRecord, SSQReviewAssignment

    ssq = SSQ.unscoped.filter(qa_request_programme=p5.eee).first()
    reviewer = p5.people.desk_officer
    SSQReviewAssignment.unscoped.create(ssq=ssq, reviewer=reviewer)
    services.record_ssq_review(ssq, reviewer=reviewer, outcome="accepted")
    row = ServiceRecord.unscoped.filter(staff__user=reviewer, duty="ssq_review").first()
    assert row is not None and row.ssq_count == 1 and row.visit_count == 0


# ---------------------------------------------------------------------------
# Screens: the Team Lead submits, the Deputy returns with an inline error
# ---------------------------------------------------------------------------
def test_report_screens_submit_endorse_and_return(p5_visit):
    from inspection import services
    from qa_core.tests.phase4_fixtures import staff_client

    _complete(p5_visit)
    people = p5_visit.people
    report = _eee_report(p5_visit)
    lead = staff_client(people.team_lead)
    url = f"/qa/inspection/reports/{report.pk}/"
    assert lead.get(url).status_code == 200
    r = lead.post(url + "save/", {"op": "submit", "summary": "short", "recommendation": ""},
                  HTTP_HX_REQUEST="true")
    assert r.status_code == 422 and "at least 20 characters" in r.content.decode()
    data = {"op": "submit", "f_facilities": "Seen", "summary": "All facilities in the SSQ were seen and verified.",
            "recommendation": "accredit"}
    r = lead.post(url + "save/", data, HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    report.refresh_from_db()
    assert report.status == "submitted"
    r = staff_client(people.division_head).post(url + "endorse/", {"action": "endorse", "content": ""},
                                                HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    deputy = staff_client(people.deputy_director)
    r = deputy.post(url + "endorse/", {"action": "return", "content": ""}, HTTP_HX_REQUEST="true")
    assert r.status_code == 422 and "A return needs a remark" in r.content.decode()
    r = deputy.post(url + "endorse/", {"action": "return", "content": "Quantify the deficiencies."},
                    HTTP_HX_REQUEST="true")
    report.refresh_from_db()
    assert report.status == "returned"
    assert "Quantify the deficiencies" in lead.get(url).content.decode()  # pinned remark shown
    assert services.can_write(report, people.team_lead)[0]


def test_list_screens_have_search_filters_and_pagination(p5_visit):
    from qa_core.tests.phase4_fixtures import staff_client

    _complete(p5_visit)
    c = staff_client(p5_visit.people.director)
    for url in ("/qa/inspection/resource-persons/", "/qa/inspection/inspections/", "/qa/inspection/endorsements/",
                "/qa/inspection/service-history/"):
        body = c.get(url).content.decode()
        assert 'type="search"' in body, url
        assert body.count("<select") + body.count('type="date"') >= 3, url
    r = c.get("/qa/inspection/service-history/?q=Yakubu&show=all", HTTP_HX_REQUEST="true",
              HTTP_HX_TARGET="qa-rows")
    assert r.status_code == 200 and "Yakubu" in r.content.decode()


def test_ict_monitor_is_read_only(p5_visit):
    from qa_core.tests.phase4_fixtures import staff_client

    _complete(p5_visit)
    report = _eee_report(p5_visit)
    c = staff_client(p5_visit.people.ict_monitor)
    assert c.post(f"/qa/inspection/reports/{report.pk}/save/", {"op": "save"}).status_code == 403
    assert c.post(f"/qa/inspection/reports/{report.pk}/endorse/", {"action": "endorse"}).status_code == 403
    assert c.post(f"/qa/inspection/inspections/{p5_visit.inspection.pk}/attendance/", {}).status_code == 403


# ---------------------------------------------------------------------------
# Portal: the resource person's findings feed the team's report
# ---------------------------------------------------------------------------
def test_resource_person_portal_findings_feed_the_report(p5_visit, portal_host):
    from django.test import Client

    from inspection import services

    _complete(p5_visit)
    rp = p5_visit.rps["rp_eng"]
    rp.user.set_password("Test-pass-2026!")  # as after the set-password link
    rp.user.must_change_password = False
    rp.user.save()
    c = Client(HTTP_HOST=portal_host)
    c.force_login(rp.user)
    assert c.get("/engagements/").status_code == 200
    mapping = next(m for m in services.rp_engagements(rp))
    r = c.post("/engagements/", {"mapping": mapping.pk, "findings": "too short"})
    assert r.status_code == 422 and "at least 20 characters" in r.content.decode()
    r = c.post("/engagements/", {"mapping": mapping.pk,
                                 "findings": "The electrical machines laboratory matches the SSQ."})
    assert r.status_code == 200
    report = _eee_report(p5_visit)
    assert any("electrical machines" in m.findings for m in services.rp_findings_for(report))
    assert c.get("/service-history/").status_code == 200 and c.get("/profile/").status_code == 200
    # Institution accounts see their inspections read-only; resource persons do not.
    assert c.get("/inspections/").status_code == 404
    inst = Client(HTTP_HOST=portal_host)
    inst.force_login(p5_visit.people.institution_admin)
    body = inst.get("/inspections/").content.decode()
    assert p5_visit.inspection.reference in body


def test_stage_13_return_sends_every_report_back_to_its_writer(p5_visit):
    from accreditation import case_services as cs
    from inspection import demo, services

    _complete(p5_visit)
    demo.drive_reports(p5_visit.case)
    case = p5_visit.case
    case.refresh_from_db()
    director = cs.holders(case, "post_visit_recommendation")[0]
    with pytest.raises(ValidationError):
        cs.act(case, user=director, stage_code="post_visit_recommendation", action="return", remark="")
    cs.act(case, user=director, stage_code="post_visit_recommendation", action="return",
           remark="Recheck the staffing figures in both reports.")
    case.refresh_from_db()
    assert cs.current_stages(case) == ["post_visit_report"]
    for report in services.reports_of(case):
        assert report.status == "returned"
        assert services.report_holders(report)[0] == [p5_visit.people.team_lead]
    demo.drive_reports(case)
    case.refresh_from_db()
    assert cs.current_stages(case) == ["post_visit_recommendation"]
