"""Phase 8 (Master Plan Phase 6) — decisions at stages 14–16, the Chairman's
batch approval (B23), stage 17 (programme update, expiry, letters) and the
portal views of the result. These tests walk the demo SOP-03 case, so they
are slow (a few minutes each)."""
import datetime
import re
import zlib

import pytest
from django.core.exceptions import ValidationError

from qa_core.tests.phase3_fixtures import set_config
from qa_core.tests.phase4_fixtures import advance_to, holder, staff_client


def _pdf_text(raw):
    """Every content stream of a PDF, inflated (ReportLab compresses pages and
    ASCII85-encodes images)."""
    import base64

    out = [raw]
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        data = match.group(1).strip()
        for decode in (lambda d: d, lambda d: base64.a85decode(d[:-2] if d.endswith(b"~>") else d)):
            try:
                out.append(zlib.decompress(decode(data)))
                break
            except (zlib.error, ValueError):
                continue
    return b"\n".join(out)


def _rows(case):
    from accreditation import decisions as dec

    return {r.programme.name: r for r in dec.rows(case)}


def _cqac_vote_data(rows, outcomes, approval_type):
    data = {}
    for row in rows:
        outcome = outcomes[row.pk]
        prefix = f"d{row.pk}-"
        data[prefix + "outcome"] = outcome
        data[prefix + "approval_type"] = approval_type.pk if outcome == "approve" else ""
        data[prefix + "approved_streams"] = "1" if outcome == "approve" else ""
        data[prefix + "commencement_date"] = "2026-10-15" if outcome == "approve" else ""
        data[prefix + "weaknesses"] = "Needs further review." if outcome == "deny" else ""
        data[prefix + "note"] = "Panel requests return." if outcome == "return" else ""
    return data


@pytest.fixture
def individual_cqac_mode(qa_seed):
    from config.models import ConfigEntry
    from qa_core.tests.phase3_fixtures import set_config

    setting = ConfigEntry.objects.get(key="qa.cqac.recommendation_mode", scope="global")
    previous = setting.value
    set_config("qa.cqac.recommendation_mode", "individual")
    yield
    set_config("qa.cqac.recommendation_mode", previous)


def test_cqac_decision_evidence_links_are_role_and_case_scoped(p4, make_user):
    from accreditation import decisions as dec
    from accreditation.models import Evidence, VisitReportAttachment
    from accreditation import report_forms, visit_report
    from accreditation import template_library
    from accreditation.models import Submission
    from inspection import services as insp
    from qa_core.tests.phase4_fixtures import staff_client
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.urls import reverse

    installed = template_library.install()
    assert not installed["errors"], installed["errors"]
    case = advance_to(p4, 14)
    member = p4.people.cqac_member
    chair = p4.people.cqac_chair
    row = dec.rows(case)[0]
    ssq, _submission = visit_report.submitted_ssq(row)
    member_client = staff_client(member)
    decision_url = f"/qa/cases/{case.pk}/?tab=decisions"
    panel = member_client.get(decision_url).content.decode()
    assert "Review the complete case record" in panel
    assert "Open original QA request and fee payments" in panel
    assert "Open visit fund release" in panel
    assert "Open all reports" in panel
    assert "Institution SSQ and attachments" in panel
    assert "attendee/member reports" in panel
    assert "Shared panel recommendation" in panel
    assert "Assigned CQAC panelists" in panel
    assert member_client.get(f"/qa/cases/{case.pk}/ssq/{ssq.pk}/").status_code == 200
    source_pdf = member_client.get(reverse("qa_case:source_ssq_pdf", args=[case.pk, ssq.pk]))
    assert source_pdf.status_code == 200 and source_pdf["Content-Type"] == "application/pdf"
    assert "inline" in source_pdf["Content-Disposition"]
    source_download = member_client.get(reverse("qa_case:source_ssq_pdf", args=[case.pk, ssq.pk]) + "?download=1")
    assert "attachment" in source_download["Content-Disposition"]
    report = dec.board(case, "cqac_recommendation", member)[0]["report"]
    assert member_client.get(reverse("qa_insp:report", args=[report.pk])).status_code == 200
    evaluation, ict = report_forms.ensure_forms(report)
    assert ict is not None
    ict_url = f"/qa/cases/{case.pk}/reports/{report.pk}/form/ict_verification/"
    ict_page = member_client.get(ict_url)
    assert ict_page.status_code == 200 and "ICT verification is locked" not in ict_page.content.decode()
    ict_pdf_url = reverse("qa_case:report_form_pdf", args=[case.pk, report.pk, "ict_verification"])
    assert member_client.get(ict_pdf_url).status_code == 200
    assert "attachment" in member_client.get(ict_pdf_url + "?download=1")["Content-Disposition"]
    attachment = VisitReportAttachment(form=ict, field_key="j_photographs")
    assert attachment.can_download(member)
    assert attachment.can_download(p4.people.es)
    assert insp.can_review_cqac_evidence(case, member)
    assert not insp.can_review_cqac_evidence(case, chair)

    submission = Submission.unscoped.filter(ssq=ssq).order_by("-version").first()
    file_key = next(field["key"] for section in submission.rendered_snapshot
                    for field in section["fields"] if field.get("type") == "file")
    evidence = Evidence.unscoped.create(
        ssq=ssq, field_key=file_key, uploaded_by=p4.people.institution_admin,
        file=SimpleUploadedFile("cqac-evidence.pdf", b"evidence", content_type="application/pdf"))
    source_page = member_client.get(f"/qa/cases/{case.pk}/ssq/{ssq.pk}/").content.decode()
    assert "cqac-evidence.pdf" in source_page
    assert member_client.get(evidence.download_url()).status_code == 200

    assert "Review the complete case record" not in staff_client(chair).get(
        decision_url).content.decode()
    case = advance_to(p4, 15, case)
    chair_client = staff_client(chair)
    chair_panel = chair_client.get(decision_url).content.decode()
    assert "Review the complete case record" in chair_panel
    assert "Open original QA request and fee payments" in chair_panel
    assert "Open visit fund release" in chair_panel
    assert "Institution SSQ and attachments" in chair_panel
    assert "attendee/member reports" in chair_panel
    assert chair_client.get(f"/qa/cases/{case.pk}/ssq/{ssq.pk}/").status_code == 200
    assert chair_client.get(ict_url).status_code == 200
    assert chair_client.get(evidence.download_url()).status_code == 200
    assert insp.can_review_cqac_evidence(case, chair)
    assert not insp.can_review_cqac_evidence(case, member)
    case = advance_to(p4, 16, case)
    es_client = staff_client(p4.people.es)
    es_panel = es_client.get(decision_url).content.decode()
    assert "Review the complete case record" in es_panel
    assert es_client.get(f"/qa/cases/{case.pk}/ssq/{ssq.pk}/").status_code == 200
    assert es_client.get(ict_url).status_code == 200
    assert es_client.get(evidence.download_url()).status_code == 200
    assert es_client.get(ict_pdf_url).status_code == 200
    assert insp.can_review_cqac_evidence(case, p4.people.es)
    assert not insp.can_review_cqac_evidence(case, chair)
    assert es_client.get(reverse("qa_case:summary_pdf", args=[case.pk])).status_code == 200
    compiled_pdf_url = reverse("qa_insp:report_pdf", args=[report.pk])
    assert es_client.get(compiled_pdf_url).status_code == 200
    assert "attachment" in es_client.get(compiled_pdf_url + "?download=1")["Content-Disposition"]

    outsider = make_user("unrelated.cqac.evidence@vtsd.local", role="officer", dept="vtsd")
    outsider_client = staff_client(outsider)
    assert outsider_client.get(decision_url).status_code == 404
    assert outsider_client.get(f"/qa/cases/{case.pk}/ssq/{ssq.pk}/").status_code == 404
    assert outsider_client.get(ict_url).status_code == 404
    assert outsider_client.get(evidence.download_url()).status_code == 403


def test_capacity_is_assessed_by_desk_officer_and_director_confirmed_before_cqac(p4, make_user):
    from accreditation import capacity, case_services as cs, decisions as dec
    from accreditation.case_demo import advance

    case = advance(p4.case, p4.people, 13)
    rows = dec.rows(case)
    director = cs.holders(case, "post_visit_recommendation")[0]
    assessor = case.desk_officer

    problems = cs.prerequisites(case, "post_visit_recommendation")
    assert any("capacity assessment" in problem.lower() for problem in problems)
    with pytest.raises(ValidationError, match="capacity|confirm"):
        cs.act(case, user=director, stage_code="post_visit_recommendation", action="approve")

    assessor_client = staff_client(assessor)
    url = f"/qa/cases/{case.pk}/"
    page = assessor_client.get(url + "?tab=capacity", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert page.status_code == 200 and "Assigned assessor" in page.content.decode()

    from django.contrib.auth.models import Permission

    other_officer = make_user("capacity.unassigned@t.local", role="officer", dept="dpap")
    other_officer.user_permissions.add(Permission.objects.get(
        content_type__app_label="accreditation", codename="view_qa_case"))
    denied = staff_client(other_officer).post(
        url + f"capacity/{rows[0].pk}/support_staff/",
        {"tab": "capacity", "requested_streams": "2", "students_per_stream": "40", "support_staff": "2"},
        HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel",
    )
    assert denied.status_code == 403
    director_denied = staff_client(director).post(
        url + f"capacity/{rows[0].pk}/support_staff/",
        {"tab": "capacity", "requested_streams": "2", "students_per_stream": "40", "support_staff": "2"},
        HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel",
    )
    assert director_denied.status_code == 403
    saved = assessor_client.post(
        url + f"capacity/{rows[0].pk}/support_staff/",
        {"tab": "capacity", "requested_streams": "2", "students_per_stream": "40", "support_staff": "2"},
        HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel",
    )
    assert saved.status_code == 200

    for row in rows:
        current = capacity.current(row)
        for kind, inputs in capacity.prefill(row).items():
            if current[kind] is None:
                capacity.run(row, kind, inputs, user=assessor)
        with pytest.raises(ValidationError, match="different officer|This stage is with"):
            capacity.confirm_review(case, row, user=assessor)
        capacity.confirm_review(case, row, user=director)

    assert not capacity.missing_assessments(rows)
    assert all(state["is_current"] for state in capacity.review_states(case, rows).values())
    capacity.run(rows[0], "classroom", capacity.prefill(rows[0])["classroom"], user=assessor)
    assert not capacity.review_states(case, rows)[rows[0].pk]["is_current"]
    assert any("Director must confirm" in problem for problem in
               cs.prerequisites(case, "post_visit_recommendation"))
    assert capacity.confirm_review(case, rows[0], user=director).version == 2
    from inspection import demo as inspection_demo
    inspection_demo.recommend_all(case)
    assert not cs.prerequisites(case, "post_visit_recommendation")
    cs.act(case, user=director, stage_code="post_visit_recommendation", action="approve")
    assert cs.current_stages(case) == ["cqac_recommendation"]


def test_cqac_individual_panel_assignments_votes_majority_tie_and_gating(p4, make_user, individual_cqac_mode):
    from django.core.exceptions import PermissionDenied, ValidationError

    from accounts.models import Role
    from accreditation import case_services as cs
    from accreditation import decisions as dec
    from accreditation.models import ApprovalType, CQACVote, Decision
    extra_members = [
        make_user(f"cqac.panel.{index}@t.local", role="cqac-member", dept="dpap")
        for index in range(2, 5)
    ]
    case = advance_to(p4, 14)
    rows = dec.rows(case)
    members = [p4.people.cqac_member, *extra_members]
    assert {user.pk for user in cs.holders(case, "cqac_recommendation")} == {user.pk for user in members}
    assert p4.people.cqac_chair.pk not in {user.pk for user in cs.holders(case, "cqac_recommendation")}
    member_client = staff_client(members[0])
    panel_page = member_client.get(f"/qa/cases/{case.pk}/?tab=decisions").content.decode()
    assert "Individual panel votes" in panel_page and "0 / 4" in panel_page

    full = ApprovalType.objects.get(code="full")
    votes_by_member = [
        {rows[0].pk: "approve", rows[1].pk: "approve"},
        {rows[0].pk: "approve", rows[1].pk: "approve"},
        {rows[0].pk: "approve", rows[1].pk: "deny"},
        {rows[0].pk: "deny", rows[1].pk: "approve"},
    ]
    first_data = _cqac_vote_data(rows, votes_by_member[0], full)
    dec.record_from_post(case, "cqac_recommendation", first_data, user=members[0])
    panel_page = member_client.get(f"/qa/cases/{case.pk}/?tab=decisions").content.decode()
    assert "1 / 4" in panel_page and "Your vote: Approve" in panel_page
    assert not Decision.unscoped.filter(case=case, stage=Decision.CQAC).exists()
    assert "Every assigned CQAC member must vote" in " ".join(dec.problems(case, "cqac_recommendation"))
    with pytest.raises(ValidationError):
        cs.act(case, user=members[0], stage_code="cqac_recommendation", action="approve")

    late_member = make_user("cqac.late@t.local", role="cqac-member", dept="dpap")
    with pytest.raises(PermissionDenied):
        dec.record_from_post(case, "cqac_recommendation", first_data, user=late_member)
    outsider = make_user("cqac.not-a-member@t.local", role="officer", dept="dpap")
    with pytest.raises(PermissionDenied):
        dec.record_from_post(case, "cqac_recommendation", first_data, user=outsider)
    former_member = members[-1]
    former_member.role = Role.objects.get(code="officer")
    former_member.save(update_fields=["role"])
    with pytest.raises(PermissionDenied):
        dec.record_from_post(case, "cqac_recommendation",
                             _cqac_vote_data(rows, votes_by_member[-1], full), user=former_member)
    former_member.role = Role.objects.get(code="cqac-member")
    former_member.save(update_fields=["role"])
    former_member = type(former_member).objects.get(pk=former_member.pk)
    members[-1] = former_member

    for index, (member, outcomes) in enumerate(zip(members[1:], votes_by_member[1:]), start=1):
        data = _cqac_vote_data(rows, outcomes, full)
        if index == 1:
            data[f"d{rows[1].pk}-approved_streams"] = "2"
        if index == 2:
            data[f"d{rows[0].pk}-approved_streams"] = "2"
        if index == 3:
            data[f"d{rows[1].pk}-approved_streams"] = "3"
        dec.record_from_post(case, "cqac_recommendation", data, user=member)

    decisions = dec.decisions_at(case, Decision.CQAC)
    assert decisions[rows[0].pk].outcome == Decision.APPROVE
    assert decisions[rows[0].pk].approved_streams == 1
    assert decisions[rows[1].pk].outcome == Decision.RETURN
    assert "did not reach a majority for approved streams" in decisions[rows[1].pk].note.lower()
    assert CQACVote.unscoped.filter(case=case).count() == len(rows) * len(members)
    assert "marked “return”" in " ".join(dec.problems(case, "cqac_recommendation"))

    CQACVote.unscoped.filter(
        case=case, qa_request_programme=rows[1], user=members[2],
    ).update(outcome=Decision.DENY)
    CQACVote.unscoped.filter(
        case=case, qa_request_programme=rows[1], user=members[3],
    ).update(outcome=Decision.RETURN)
    decision = dec._aggregate_cqac_votes(case, actor=members[0])[1]
    assert decision.outcome == Decision.RETURN
    assert "no cqac outcome received a majority" in decision.note.lower()


def test_cqac_default_shared_mode_records_decisions_immediately(p4):
    from accreditation import decisions as dec
    from accreditation.models import ApprovalType, CQACVote, Decision

    case = advance_to(p4, 14)
    rows = dec.rows(case)
    full = ApprovalType.objects.get(code="full")
    data = _cqac_vote_data(rows, {row.pk: "approve" for row in rows}, full)
    dec.record_from_post(case, "cqac_recommendation", data, user=p4.people.cqac_member)

    assert dec.recommendation_mode() == "shared"
    assert Decision.unscoped.filter(case=case, stage=Decision.CQAC).count() == len(rows)
    assert not CQACVote.unscoped.filter(case=case).exists()


def test_decisions_then_stage_17_updates_programmes_and_issues_letters(p4, client_for, portal_host, make_user,
                                                                       django_capture_on_commit_callbacks):
    from accreditation import case_services as cs
    from accreditation import decisions as dec
    from accreditation.models import ApprovalType, Decision, OutcomeLetter, OutcomeLetterCopy, QARequest
    from institutions.models import Institution
    from institutions.models import Programme
    from notifications.models import Notification
    from qa_core.models import TimelineEvent

    case = advance_to(p4, 14)
    assert case.status == "cqac_recommendation"
    member = holder(case, "cqac_recommendation")
    # Approving needs a decision for every programme.
    assert dec.problems(case, "cqac_recommendation")
    with pytest.raises(ValidationError):
        cs.act(case, user=member, stage_code="cqac_recommendation", action="approve")
    rows = _rows(case)
    cs_row, eee_row = rows["Computer Science"], rows["Electrical/Electronic Engineering Technology"]
    client = staff_client(member)
    url = f"/qa/cases/{case.pk}/"
    # CQAC can inspect the complete capacity record, but cannot alter it after
    # the desk officer has prepared it and the Director has confirmed it.
    r = client.get(url + "?tab=capacity", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200 and "Capacity and resources" in r.content.decode()
    assert "Director confirmed" in r.content.decode()
    from accreditation import capacity
    assert not capacity.missing_assessments(dec.rows(case))
    assert all(state["is_current"] for state in capacity.review_states(case, dec.rows(case)).values())
    director_client = staff_client(p4.people.director)
    r = director_client.post(url + f"capacity/{cs_row.pk}/classroom/",
                             {"tab": "capacity", "requested_streams": "2"},
                             HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 403
    full = ApprovalType.objects.get(code="full")
    data = {"stage": "cqac_recommendation",
            f"d{cs_row.pk}-outcome": "approve", f"d{cs_row.pk}-approval_type": full.pk,
            f"d{cs_row.pk}-approved_streams": "2", f"d{cs_row.pk}-commencement_date": "2026-10-15",
            f"d{cs_row.pk}-weaknesses": "", f"d{cs_row.pk}-note": "",
            f"d{eee_row.pk}-outcome": "deny", f"d{eee_row.pk}-approval_type": "",
            f"d{eee_row.pk}-approved_streams": "", f"d{eee_row.pk}-commencement_date": "",
            f"d{eee_row.pk}-weaknesses": "Workshop equipment below the catalogue\nOnly one technologist",
            f"d{eee_row.pk}-note": ""}
    r = client.post(url + "decisions/", data, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200, r.content.decode()[:800]
    r = client.get(url + "?tab=decisions", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert "streams supportable" in r.content.decode()
    # Someone else (not the stage holder) cannot record decisions.
    r = staff_client(p4.people.es).post(url + "decisions/", data, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 403
    decision = Decision.unscoped.get(case=case, qa_request_programme=cs_row, stage=Decision.CQAC)
    assert decision.expiry_date == datetime.date(2031, 10, 15)  # derived, never typed
    # Stages 15 and 16 start from the previous decision; the ES's decision is applied at stage 17.
    with django_capture_on_commit_callbacks(execute=True):
        case = advance_to(p4, 18, case)
    assert case.status == "closed"
    cs_prog = Programme.unscoped.get(pk=cs_row.programme_id)
    assert (cs_prog.status, cs_prog.approval_type.code, cs_prog.approved_streams) == ("accredited", "full", 2)
    assert cs_prog.commencement_date == datetime.date(2026, 10, 15)
    assert cs_prog.expiry_date == datetime.date(2031, 10, 15)
    assert cs_prog.eligible_for_reassessment_at == datetime.date(2031, 4, 15)
    assert Programme.unscoped.get(pk=eee_row.programme_id).status == "denied"
    assert Decision.unscoped.filter(case=case, stage=Decision.ES, applied_at__isnull=False).count() == 2
    assert QARequest.unscoped.get(pk=case.qa_request_id).status == "decided"
    # Timeline: public for the institution, internal for NBTE.
    events = TimelineEvent.unscoped.filter(institution=case.institution)
    assert events.filter(event_code="programme.decided", visibility="public").count() == 2
    assert events.filter(event_code="programme.decided", public_text__contains="15 Oct 2031").exists()
    assert events.filter(event_code="qa_case.decided", visibility="internal").exists()
    admin = p4.people.institution_admin
    assert Notification.objects.filter(recipient=admin, title__icontains="Decision on").count() >= 2
    # Letters: an original and a watermarked duplicate per programme.
    letters = list(OutcomeLetter.unscoped.filter(case=case))
    assert len(letters) == 2 and all(re.match(r"NBTE/QA/LTR/\d{4}/\d{5}$", l.reference) for l in letters)
    mark = "NBTE DUPLICATE"
    for letter in letters:
        original, duplicate = letter.pdf_original, letter.pdf_duplicate
        assert original and duplicate and original.size_bytes > 1000
        with original.file.open("rb") as fh:
            orig = _pdf_text(fh.read())
        with duplicate.file.open("rb") as fh:
            dup = _pdf_text(fh.read())
        assert mark.encode() in dup and mark.encode() not in orig
    denied = OutcomeLetter.unscoped.get(case=case, programme_id=eee_row.programme_id)
    assert denied.decision.weaknesses == ["Workshop equipment below the catalogue", "Only one technologist"]
    # Portal: the programme board, the letters page and the downloads.
    portal = client_for(admin, host=portal_host)
    board = portal.get("/programmes/").content.decode()
    assert "Accredited — expires 15 Oct 2031" in board
    page = portal.get("/letters/").content.decode()
    assert all(l.reference in page for l in letters)
    copy = OutcomeLetterCopy.unscoped.filter(letter__case=case, kind="duplicate").first()
    r = portal.get(copy.portal_download_url())
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    other_inst = Institution.unscoped.create(code="ZZZ", name="Zulu Poly", status="approved",
                                             handling_department=case.institution.handling_department)
    other = client_for(make_user("admin@zzz.local", role="inst-admin", user_type="institution",
                                 institution=other_inst), host=portal_host)
    assert other.get(copy.portal_download_url()).status_code == 404
    # Internal: the letters tab, internal download and regeneration (ES only).
    es = staff_client(p4.people.es)
    r = es.get(url + "?tab=letters", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200 and letters[0].reference in r.content.decode()
    assert es.get(copy.download_url()).status_code == 200
    target = OutcomeLetter.unscoped.get(case=case, programme_id=cs_row.programme_id)
    r = staff_client(p4.people.cqac_member).post(url + f"letters/{target.pk}/regenerate/",
                                                 {"reason": "Wrong signatory"}, HTTP_HX_REQUEST="true",
                                                 HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 403
    r = es.post(url + f"letters/{target.pk}/regenerate/", {"reason": "Signatory name added"},
                HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200
    target.refresh_from_db()
    fresh = OutcomeLetter.unscoped.get(case=case, programme_id=cs_row.programme_id, is_current=True)
    assert not target.is_current and fresh.version == 2 and fresh.reference != target.reference


def test_chairman_batch_approval_only_within_one_category(p4):
    from accreditation import case_services as cs
    from accreditation import decisions as dec
    from accreditation.models import Decision

    case = advance_to(p4, 15)
    assert case.status == "cqac_chair_approval"
    chair = p4.people.cqac_chair
    assert holder(case, "cqac_chair_approval") == chair
    rows = _rows(case)
    cs_row, eee_row = rows["Computer Science"], rows["Electrical/Electronic Engineering Technology"]
    assert cs_row.programme.category_id != eee_row.programme.category_id
    groups = dec.batch_candidates(chair)
    assert len(groups) == 2 and all(len(g["items"]) == 1 for g in groups)
    with pytest.raises(ValidationError):  # two categories in one batch
        dec.batch_approve(chair, [cs_row.pk, eee_row.pk], category_id=None)
    with pytest.raises(ValidationError):  # the category posted must match the programmes
        dec.batch_approve(chair, [cs_row.pk], category_id=eee_row.programme.category_id)
    set_config("qa.cqac.batch_approval_enabled", "false")
    with pytest.raises(ValidationError):
        dec.batch_approve(chair, [cs_row.pk], category_id=cs_row.programme.category_id)
    set_config("qa.cqac.batch_approval_enabled", "true")
    with pytest.raises(Exception):  # someone without batch_decide
        dec.batch_approve(p4.people.es, [cs_row.pk], category_id=cs_row.programme.category_id)
    key, decided, moved = dec.batch_approve(chair, [cs_row.pk], category_id=cs_row.programme.category_id)
    assert len(decided) == 1 and decided[0].batch_key == key and moved == []
    case.refresh_from_db()
    assert case.status == "cqac_chair_approval"  # EEE is still undecided
    # The batch page lists what is left, then the second category completes the stage.
    r = staff_client(chair).get("/qa/cases/batch/")
    assert r.status_code == 200 and "Engineering Technology" in r.content.decode()
    r = staff_client(chair).post("/qa/cases/batch/", {"rows": [eee_row.pk],
                                                      "category": eee_row.programme.category_id},
                                 HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200, r.content.decode()[:800]
    case.refresh_from_db()
    assert cs.current_stages(case) == ["es_final_approval"]
    chair_decisions = Decision.unscoped.filter(case=case, stage=Decision.CHAIR)
    assert chair_decisions.count() == 2 and chair_decisions.exclude(batch_key="").count() == 2
    assert chair_decisions.values("batch_key").distinct().count() == 2
