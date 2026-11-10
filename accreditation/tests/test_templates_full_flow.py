"""v2.4 — complete SSQ and visit-report templates, end to end.

1. Every template in accreditation/template_library.py is valid, publishes as
   the next version of its QA type (earlier versions retired, SSQs already
   opened keep theirs) and the seed is idempotent.
2. The facilities register drives the SSQ catalogue (level-aware) and the
   shortfall score; with no register the hand-made catalogue still works.
3. Formal flow: an ACCREDITATION request → SSQ filled on the new template →
   submitted → shortfall → assigned, assessed, accepted → QA case.
4. SOP-03: the case reaches stage 12, the visit is recorded, the panel fills
   the NBTE/P/4A programme evaluation (score → Full / Interim / Denied written
   onto the post-visit report) and the ICT verification (VVT/001: points /400,
   mandatory items, evidence register); the reports go up the endorsement
   chain to the stage-13 recommendation.
"""
import datetime
import copy
from io import BytesIO
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from qa_core.tests.phase3_fixtures import set_config, waive_fee

pytestmark = pytest.mark.django_db


def _install():
    from accreditation import template_library

    out = template_library.install()
    assert not out["errors"], out["errors"]
    return out


def _upload_required_files(ssq, user):
    from accreditation import qa_services as svc

    for field in ssq.template.field_map().values():
        if field["type"] == "file" and field.get("required"):
            svc.attach_upload(ssq, field["key"], SimpleUploadedFile(
                f"{field['key']}.pdf", b"%PDF-1.4\n% evidence\n%%EOF\n", content_type="application/pdf"), user=user)


def _photo_upload(name="lab.png"):
    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (2, 2), color=(30, 80, 120)).save(image, format="PNG")
    return SimpleUploadedFile(name, image.getvalue(), content_type="image/png")


def _fill(ssq, user, *, quantities=1.0):
    from accreditation import qa_services as svc
    from accreditation import template_library

    programme = ssq.qa_request_programme.programme
    values = template_library.sample_answers(ssq.template, programme=programme, quantities=quantities)
    errors = svc.save_fields(ssq, values, user=user)
    assert not errors, errors
    _upload_required_files(ssq, user)
    ssq.refresh_from_db()
    assert not svc.submission_problems(ssq), svc.submission_problems(ssq)
    return ssq


def _register(name="Computer Science", levels="ND, HND", items=(("Desktop computer", "40", "60", True),
                                                               ("Laser printer", "2", "2", False),
                                                               ("Network analyser", "-", "1", False))):
    """A small register list for ``name`` (as the Excel import would create)."""
    from facilities.models import FacilityItem, FacilityProgramme, FacilitySection

    prog = FacilityProgramme.objects.create(name=name, levels=levels)
    lab = FacilitySection.objects.create(programme=prog, code="A", name="Computer Laboratory", sort_order=1)
    FacilitySection.objects.create(programme=prog, code="B", name="Hardware Workshop", sort_order=2)
    for order, (item, nd, hnd, critical) in enumerate(items, 1):
        FacilityItem.objects.create(programme=prog, section=lab, name=item, nd_qty=nd, hnd_qty=hnd,
                                    is_critical=critical, sort_order=order)
    return prog


# ---------------------------------------------------------------------------
# 1. Templates
# ---------------------------------------------------------------------------
def test_every_qa_type_gets_a_valid_published_template_and_the_seed_is_idempotent(qa_seed):
    from accreditation import ssq as engine
    from accreditation import template_library
    from accreditation.models import QAType, SSQTemplate

    before = {t.qa_type.code: t for t in SSQTemplate.objects.filter(is_current=True).select_related("qa_type")}
    out = _install()
    assert set(out["created"]) == {code for code, _n, _f in template_library.TEMPLATES}
    for code, name, _factory in template_library.TEMPLATES:
        qa_type = QAType.objects.get(code=code)
        current = qa_type.ssq_template
        assert current.name == name and current.status == SSQTemplate.PUBLISHED and current.is_current
        assert not engine.validate_schema(current.schema)
        if code in before:  # the seeded v1 is retired, never edited
            old = SSQTemplate.objects.get(pk=before[code].pk)
            assert old.status == SSQTemplate.RETIRED and current.version == old.version + 1
    for code in template_library.REPORT_HOLDER_TYPES:
        assert not QAType.objects.get(code=code).is_active  # never requestable
    assert QAType.objects.get(code="accreditation").is_available
    sizes = {code: len(QAType.objects.get(code=code).ssq_template.field_map()) for code in
             ("accreditation", "reaccreditation", "resource_inspection", "increase_capacity",
              "institutional_administration")}
    assert all(n >= 50 for n in sizes.values()), sizes
    # idempotent: nothing new on a second run
    again = template_library.install()
    assert not again["created"] and len(again["unchanged"]) == len(template_library.TEMPLATES)


def test_seed_set_is_registered():
    import accreditation.seed_sets as sets

    assert hasattr(sets, "seed_templates_v24")


def test_seeded_report_template_publishes_next_version_when_its_schema_changes(qa_seed, monkeypatch):
    from accreditation import template_library
    from accreditation.models import QAType, SSQTemplate

    _install()
    qa_type = QAType.objects.get(code="report_ict_verification")
    current = qa_type.ssq_template
    schema = copy.deepcopy(current.schema)
    schema["intro"] += " Updated template."
    monkeypatch.setattr(template_library, "TEMPLATES", [
        ("report_ict_verification", current.name, lambda: schema),
    ])

    result = template_library.install()
    assert result["created"] == ["report_ict_verification"]
    qa_type.refresh_from_db()
    assert qa_type.ssq_template.version == current.version + 1
    assert qa_type.ssq_template.schema == schema
    assert not template_library.install()["created"]


# ---------------------------------------------------------------------------
# 2. The facilities register drives the catalogue
# ---------------------------------------------------------------------------
def test_register_drives_the_catalogue_by_level_and_updates_when_it_changes(p3):
    from accreditation import register_link
    from accreditation import ssq as engine

    _install()
    from accreditation.models import QAType

    template = QAType.objects.get(code="accreditation").ssq_template
    equipment = template.field_map()["equipment"]
    facilities = template.field_map()["facilities"]
    # Not imported yet: the hand-made starter catalogue (seeded for Computer Science) is used.
    before = {i.name for i in engine.catalogue_items(equipment, p3.cs)}
    assert "Desktop computer" in before and register_link.register_for(p3.cs) is None
    prog = _register()
    rows = {i.name: i for i in engine.catalogue_items(equipment, p3.cs)}  # ND programme
    assert rows["Desktop computer"].required_quantity == 40 and rows["Desktop computer"].is_critical
    assert "Network analyser" not in rows  # HND-only item
    assert {i.name for i in engine.catalogue_items(facilities, p3.cs)} >= {"Computer Laboratory",
                                                                            "Hardware Workshop"}
    # HND programme of the same curriculum gets the HND quantities
    from institutions.models import Programme, ProgrammeLevel

    hnd = Programme.unscoped.create(institution=p3.inst, name="Computer Science",
                                    level=ProgrammeLevel.objects.get(code="hnd"), category=p3.cs.category,
                                    status="interim", approved_streams=1)
    # each level keeps its own quantities through its master-list row (as the import creates them)
    from institutions.models import ProgrammeMaster

    master = ProgrammeMaster.objects.create(name="Computer Science", level=hnd.level)
    Programme.unscoped.filter(pk=hnd.pk).update(master=master)
    hnd.refresh_from_db()
    hnd_rows = {i.name: i for i in engine.catalogue_items(equipment, hnd)}
    assert hnd_rows["Desktop computer"].required_quantity == 60 and "Network analyser" in hnd_rows
    # the register changes → the catalogue follows (ids stay stable)
    item = prog.items.get(name="Laser printer")
    item_id = rows["Laser printer"].pk
    item.nd_qty = "5"
    item.save()
    rows = {i.name: i for i in engine.catalogue_items(equipment, p3.cs)}
    assert rows["Laser printer"].required_quantity == 5 and rows["Laser printer"].pk == item_id


# ---------------------------------------------------------------------------
# 3. Formal QA request → SSQ → review → QA case
# ---------------------------------------------------------------------------
def test_accreditation_request_ssq_review_and_case(p3, django_capture_on_commit_callbacks, settings):
    import org
    from accreditation import qa_services as svc
    from accreditation import shortfall, ssq_assign, ssq_routing
    from accreditation.models import QACase, QAType
    from assignments import work
    from institutions.models import Programme

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _install()
    _register()
    Programme.unscoped.filter(pk=p3.cs.pk).update(status="interim")
    p3.cs.refresh_from_db()
    qa_type = QAType.objects.get(code="accreditation")
    req = svc.create_request(p3.inst, qa_type, [p3.cs], user=p3.admin)
    svc.submit(req, user=p3.ceo, declaration=True)
    with django_capture_on_commit_callbacks(execute=True):
        waive_fee(p3, req)
    req.refresh_from_db()
    assert req.status == "ssq_in_progress"
    ssq = svc.request_ssqs(req)[0]
    assert ssq.template == qa_type.ssq_template and ssq.template.name.startswith("Accreditation SSQ")
    _fill(ssq, p3.admin, quantities=0.5)  # half of NBTE's minimum list
    with django_capture_on_commit_callbacks(execute=True):
        svc.submit_ssq(ssq, user=p3.ceo, declaration=True)
    ssq.refresh_from_db()
    assert ssq.status == "submitted"
    analysis = shortfall.latest_for(ssq)
    # half of each item (facilities 1 × 0.5 rounds to none) → a major shortfall
    assert analysis is not None and analysis.band == "major" and Decimal("10") <= analysis.score < Decimal("60")
    assert any(i["name"] == "Desktop computer" and i["critical"] for i in analysis.items)
    # the institution's copy never shows NBTE's required quantities
    from accreditation import ssq as engine
    from accreditation.models import Submission

    sub = Submission.unscoped.filter(ssq=ssq).first()
    inst_view = engine.for_audience(sub.rendered_snapshot, "institution")
    equipment = next(f for s in inst_view for f in s["fields"] if f["key"] == "equipment")
    assert equipment["rows"] and "required" not in equipment["rows"][0]
    # the QA case opened when the (only) SSQ was submitted
    req.refresh_from_db()
    case = QACase.unscoped.get(qa_request=req)
    assert case.programme_count == 1
    # assign → assess → accept (a major shortfall needs a remark)
    director = org.director_of(p3.inst.handling_department)
    officer = p3.p2.officer2
    wa = ssq_assign.assign(ssq, by=director, officer=officer)
    work.accept(wa, officer)
    with django_capture_on_commit_callbacks(execute=True):
        ssq_routing.submit_assessment(ssq, officer=officer, recommendation="accept",
                                      remark="Complete; equipment at half of the minimum list.")
    ssq.refresh_from_db()
    with pytest.raises(ValidationError):
        svc.accept_ssq(ssq, user=director)
    svc.accept_ssq(ssq, user=director, remark="Accepted for the visit to verify the equipment shortfall.")
    ssq.refresh_from_db()
    assert ssq.status == "accepted"


# ---------------------------------------------------------------------------
# 4. SOP-03 stage 12: visit → structured reports → recommendation
# ---------------------------------------------------------------------------
@pytest.fixture
def accreditation_visit(qa_seed, settings):
    """The Phase 5 demo cast on an ACCREDITATION case (programmes on interim
    accreditation), SSQs filled on the v2.4 template, driven to stage 12 and
    the visit recorded."""
    from types import SimpleNamespace

    from accreditation import case_demo
    from accreditation import case_services as cs
    from accreditation import qa_services as svc
    from accreditation.models import QACase, QARequestProgramme, QAType
    from inspection import demo, services
    from institutions.models import Programme

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _install()
    demo.ensure_designations()
    set_config("qa.resource_person.min_rank", "deputy-director")
    inst = case_demo.ensure_institution(tag=False)
    people, _creds = case_demo.ensure_accounts(force_change=False, tag=False)
    _register("Electrical/Electronic Engineering Technology", items=(("Digital oscilloscope", "10", "12", True),
                                                                     ("Digital multimeter", "20", "20", False)))
    Programme.unscoped.filter(institution=inst).update(status="interim")
    for key, value in (("qa.fee.qa_request.base", "50000"), ("qa.fee.qa_request.per_programme", "150000"),
                       ("qa.fee.qa_request_processing.confirmed", "true")):
        set_config(key, value)
    from payments.models import BankAccount

    if not BankAccount.objects.filter(is_active=True, is_placeholder=False).exists():
        BankAccount.objects.create(bank_name="Test Bank", account_name="NBTE", account_number="0123456789",
                                   is_active=True, is_default=True)
    admin = people.institution_admin
    req = svc.create_request(inst, QAType.objects.get(code="accreditation"),
                             list(Programme.unscoped.filter(institution=inst)), user=admin)
    ce = case_demo._chief_executive(inst, tag=False)
    svc.submit(req, user=ce, declaration=True)
    req.refresh_from_db()
    case_demo._waive(req, people)
    req.refresh_from_db()
    with cs.pinned_actors(case_demo.demo_pins(people)):
        for ssq in svc.request_ssqs(req):
            _fill(ssq, admin, quantities=1)
            svc.submit_ssq(ssq, user=ce, declaration=True)
    case = cs.start_case(QACase.unscoped.get(qa_request=req))
    rps = demo.ensure_resource_persons(people, inst, tag=False)
    demo.ensure_qa_personnel(people)
    with cs.pinned_actors(case_demo.demo_pins(people)):
        case = case_demo.advance(case, people, 9)
        demo.map_demo_people(case, people, rps)
        case = case_demo.advance(case, people, 12)
    inspection = services.live_inspection(case)
    from accreditation import visit_report as vr

    services.complete(inspection, actor=inspection.team_lead)
    inspection.refresh_from_db()
    for person in vr.panel_people(inspection):
        vr.declare(inspection, user=people.team_lead, staff=person.staff, resource_person=person.resource_person)
    rows = {r.programme.name: r for r in QARequestProgramme.unscoped.filter(qa_request=req, status="active")}
    return SimpleNamespace(case=case, people=people, inst=inst, inspection=inspection, req=req, rows=rows)


def _rate_all(form, value="very_good"):
    out = {}
    for field in form.template.field_map().values():
        if (field.get("scoring") or {}).get("points") and field["type"] == "choice":
            values = [v for v, _l in __import__("accreditation.ssq", fromlist=["x"]).option_pairs(field)]
            out[field["key"]] = value if value in values else values[0]
    return out


def test_visit_reports_compute_the_recommendation_and_feed_the_endorsement_chain(accreditation_visit):
    from accreditation import case_services as cs
    from accreditation import report_forms
    from accreditation.models import QAType
    from inspection import demo, services

    _install()
    v = accreditation_visit
    lead = v.people.team_lead
    assert v.case.qa_request.qa_type.code == "accreditation"
    assert cs.current_stages(v.case) == ["post_visit_report"]
    reports = services.reports_of(v.case)
    eee = next(r for r in reports if r.qa_request_programme.programme.name.startswith("Electrical"))
    evaluation, ict = report_forms.ensure_forms(eee)
    assert evaluation.template == QAType.objects.get(code="report_programme_evaluation").ssq_template
    # prefilled from the record
    assert evaluation.data["programme_title"].endswith("Electrical/Electronic Engineering Technology")
    assert "items in" in evaluation.data["register_items"]
    assert ict.data["batch_reference"] == v.case.reference and int(ict.data["c_programmes"]) == len(reports)
    # declared vs found on the VVT D rows now starts from the SSQ's declared quantities
    from accreditation import visit_report as vr

    d_rows = vr.catalogue_rows(eee)
    assert d_rows and any(str(r["declared"]) not in ("", "0") for r in d_rows)

    # --- programme evaluation: all Very good → Full accreditation ---------------
    values = _rate_all(evaluation, "very_good")
    values.update({"verified_lecturers": "6", "verified_technical": "3", "verified_enrolment": "70",
                   "verified_volumes": "9000", "strengths": "Well-equipped laboratories and a stable staff.",
                   "panel_recommendation": "computed",
                   "team": [{"name": "Engr. A. Lead", "role": "Chairman", "organisation": "NBTE"}]})
    assert not report_forms.save(evaluation, values, user=lead)
    evaluation.refresh_from_db()
    assert evaluation.score["score"] == "100.0" and evaluation.score["outcome"] == "full"
    assert evaluation.score["report_recommendation"] == "accredit"
    # one deferring observation (laboratories Poor) → interim, even with a high score
    report_forms.save(evaluation, {"laboratories": "poor"}, user=lead)
    evaluation.refresh_from_db()
    assert evaluation.score["outcome"] == "interim" and float(evaluation.score["score"]) > 90
    with pytest.raises(ValidationError) as exc:  # interim needs the conditions
        report_forms.submit(evaluation, user=lead)
    assert "conditions" in exc.value.message_dict
    # no curriculum → denied
    report_forms.save(evaluation, {"curriculum": "absent"}, user=lead)
    evaluation.refresh_from_db()
    assert evaluation.score["outcome"] == "denied" and evaluation.score["report_recommendation"] == "deny"
    # back to a realistic report: Interim with conditions, submitted onto the post-visit report
    report_forms.save(evaluation, {"curriculum": "good", "conditions": "Replace the two faulty oscilloscopes "
                                   "and recruit a Principal Lecturer within 12 months.",
                                   "laboratories_comment": "Two of ten oscilloscopes faulty."}, user=lead)
    form = report_forms.submit(evaluation, user=lead)
    assert form.status == "submitted" and form.rendered_snapshot
    eee.refresh_from_db()
    assert eee.recommendation == "interim"
    assert "NBTE/P/4A" in eee.summary and "Two of ten oscilloscopes" in eee.findings["facilities"]

    # --- ICT verification (VVT/001) ---------------------------------------------
    ict_values = {f["key"]: "3" for f in ict.template.field_map().values()
                  if (f.get("scoring") or {}).get("points")}
    ict_values.update({f"{k}_evidence": "E01" for k in ict_values})
    ict_values.update({f["key"]: "met" for f in ict.template.field_map().values()
                       if (f.get("scoring") or {}).get("mandatory")})
    ict_values.update({
        "visit_date": timezone.localdate().isoformat(), "arrival_departure": "09:10 – 16:40",
        "gps_arrival": "10.5105, 7.4165", "ict_officer": "ICT Officer, DPA", "coi_declared": "yes",
        "coi_date": timezone.localdate().isoformat(), "c_capacity": "120", "f_counted": "40", "f_working": "30",
        "g_readings": [{"location": "Teaching laboratory", "time": "10:00", "download": "12", "upload": "5",
                        "latency": "40", "result": "pass"}] * 3,
        "g_tool": "Speedtest (offline-capable)", "h_operators": "2", "h_domain": "yes",
        "h_observed_action": "The ICT officer uploaded a staff list unaided.",
        "j_samples": [{"asset": "Lab 1 PC 3", "model": "HP 290", "serial": "SN123", "working": "yes",
                       "photo": "E01"}], "j_method": "Every fourth bench",
        "k_register": [{"ref": "E01", "shows": "Laboratory 1", "location": "Block C", "time": "10:05",
                        "geotag": "10.51, 7.41"}],
        "l_register": [{"item": "Workstations", "declared": "40", "found": "30 working", "severity": "Major",
                        "action": "Repair or replace 10 units", "period_days": "90"}],
        "n1_record": "SSQ declared 40 workstations and 20 Mbps.", "n2_established": "Only 30 of 40 workstations "
        "worked; bandwidth measured 12 Mbps.", "n3_recommendation": "computed", "o_rep_name": "Registrar",
        "o_signed": "yes", "p_uploaded": timezone.localdate().isoformat(),
    })
    assert "j_photographs" in ict.template.field_map()
    assert not report_forms.save(ict, ict_values, user=lead)
    ict.refresh_from_db()
    s = ict.score
    assert s["points"] == "300" and s["max_points"] == "400" and s["score"] == "75.0"
    assert s["students_per_workstation"] == "4.0" and s["deficiencies"]["Major"] == 1
    assert s["computed"] == "adequate_subject" and s["outcome"] == "adequate_subject"
    # an evidence reference that is not in the register blocks submission
    report_forms.save(ict, {"e_power_evidence": "E09"}, user=lead)
    ict.refresh_from_db()
    with pytest.raises(ValidationError) as exc:
        report_forms.submit(ict, user=lead)
    assert "e_power_evidence" in exc.value.message_dict
    report_forms.save(ict, {"e_power_evidence": "E01"}, user=lead)
    assert report_forms.submit(ict, user=lead).status == "submitted"
    # a mandatory item not met caps the recommendation; a critical deficiency makes it "not adequate"
    report_forms.save(ict, {"l_register": ict_values["l_register"] + [
        {"item": "Backup", "declared": "daily", "found": "none", "severity": "Critical"}]}, user=lead)
    ict.refresh_from_db()
    assert ict.score["computed"] == "not_adequate" and ict.status == "draft"

    # --- the screens -------------------------------------------------------------
    c = Client(HTTP_HOST="localhost")
    c.force_login(lead)
    url = f"/qa/cases/{v.case.pk}/reports/{eee.pk}/form/programme_evaluation/"
    page = c.get(url)
    assert page.status_code == 200 and b"NBTE/P/4A" in page.content and b"Interim" in page.content
    assert c.get(f"/qa/inspection/reports/{eee.pk}/").status_code == 200
    pdf = c.get(url + "pdf/")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    ict_url = f"/qa/cases/{v.case.pk}/reports/{eee.pk}/form/ict_verification/"
    ict_page = c.get(ict_url)
    assert ict_page.status_code == 200 and b"+ Add another row" in ict_page.content
    assert b'type="file"' in ict_page.content and b"Upload visit photographs" in ict_page.content
    post = c.post(ict_url, {"f__n2_established": "Only 30 of 40 workstations worked; 12 Mbps measured.",
                           "f__j_photographs": _photo_upload(), "op": "save"})
    assert post.status_code == 302
    from accreditation.models import VisitReportAttachment

    attachment = VisitReportAttachment.unscoped.get(form=ict, field_key="j_photographs")
    assert attachment.original_name == "lab.png" and attachment.can_download(lead)
    assert c.get(ict_url).status_code == 200

    # Other case viewers can see who owns the ICT report, but never its data or PDF.
    outsider = Client(HTTP_HOST="localhost")
    outsider.force_login(v.people.desk_officer)
    ict.data = {**ict.data, "n2_established": "ICT-only-private-test-value"}
    ict.save(update_fields=["data", "updated_at"])
    locked = outsider.get(f"/qa/cases/{v.case.pk}/reports/{eee.pk}/form/ict_verification/")
    assert locked.status_code == 200 and b"ICT verification is locked" in locked.content
    assert b"ICT-only-private-test-value" not in locked.content
    assert outsider.get(
        f"/qa/cases/{v.case.pk}/reports/{eee.pk}/form/ict_verification/pdf/").status_code == 404

    # --- endorsement chain → stage 13 recommendation from the computed outcome ----
    demo.prepare_attendee_reports(eee)
    eee = services.submit_report(eee, user=lead)
    while eee.status != "director_endorsed":
        eee = demo.endorse_once(eee)
    assert eee.status == "director_endorsed"
    demo.drive_reports(v.case)
    v.case.refresh_from_db()
    assert cs.current_stages(v.case) == ["post_visit_recommendation"]
    holder = cs.holders(v.case, "post_visit_recommendation")[0]
    for report in services.reports_of(v.case):
        services.recommend(report, user=holder, recommendation=report.recommendation or "accredit",
                           note="As the panel computed.")
    assert services.reports_of(v.case)[0].recommended_by_id
    eee.refresh_from_db()
    assert eee.recommendation == "interim"


def test_resource_inspection_outcomes_map_to_approval_to_commence():
    from accreditation import report_forms

    mapping = report_forms.RECOMMENDATION_BY_TYPE["resource_inspection"]
    assert mapping == {"full": "interim", "interim": "revisit", "denied": "deny"}


def test_scoring_rules_without_a_database():
    from accreditation import report_scoring, template_library

    schema = template_library.programme_evaluation_report()
    rated = {f["key"]: f for s in schema["sections"] for f in s["fields"] if (f.get("scoring") or {}).get("points")}
    assert sum(f["scoring"]["weight"] for f in rated.values()) == 100
    data = {k: "fair" for k in rated}  # 2/4 everywhere = 50 %
    result = report_scoring.evaluate(schema, data)
    assert result["score"] == "50.0" and result["outcome"] == "interim"
    data = {k: "good" for k in rated}
    assert report_scoring.evaluate(schema, data)["outcome"] == "full"  # 75 %
    data["studios"] = "na"  # not applicable is left out of the score
    assert report_scoring.evaluate(schema, data)["score"] == "75.0"
    data = {k: "poor" for k in rated}
    assert report_scoring.evaluate(schema, data)["outcome"] == "denied"  # 25 %
    ict = template_library.ict_verification_report()
    weights = sum(f["scoring"]["weight"] for s in ict["sections"] for f in s["fields"]
                  if (f.get("scoring") or {}).get("points"))
    assert weights == 100
    sugg = report_scoring.suggestions({"entry_compliance_pct": "85", "success_rate_pct": "65",
                                       "hod_rank": "Principal Lecturer", "hod_registered": "yes",
                                       "stream_size": "30", "running_streams": "1"},
                                      {"verified_volumes": "4050", "verified_lecturers": "3",
                                       "verified_enrolment": "60"})
    assert sugg["admission"][0] == "fair" and sugg["success_rate"][0] == "poor"
    assert sugg["library"][0] == "very_good"  # 30 × 45 × 3 = 4050
    assert sugg["core_staff"][0] == "fair"  # 3 of the minimum 4
