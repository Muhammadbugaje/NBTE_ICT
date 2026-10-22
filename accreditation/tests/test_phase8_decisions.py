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
    # The capacity tab: prefilled from the SSQ, run by an officer, shown to the CQAC.
    r = client.get(url + "?tab=capacity", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200 and "Capacity and resources" in r.content.decode()
    officer = staff_client(p4.people.director)
    r = officer.post(url + f"capacity/{cs_row.pk}/classroom/",
                     {"tab": "capacity", "approved_streams": "1", "requested_streams": "2", "contact_hours_week": "20",
                      "other_load_week": "0", "rooms-0-label": "LT", "rooms-0-seats": "40", "rooms-0-count": "2"},
                     HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200, r.content.decode()[:500]
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
