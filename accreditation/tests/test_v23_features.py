"""v2.3 Phases 2–4: the owner decisions turned into behaviour.

Frozen versions (3), Cleared for QA (1), the HND warning (5), the minimum
running period (6), the Division Director (2), devices declared / found not
working, one-page letters with the crest, ZIP media backups, and the task
page's Accept & proceed / Return / Decline / Reject."""
import datetime
import zipfile

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from qa_core.tests.phase3_fixtures import set_config
from qa_core.tests.phase4_fixtures import advance_to

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Owner decision 3: frozen versions with dated notes
# ---------------------------------------------------------------------------
def test_frozen_version_never_changes_but_takes_dated_notes(p4):
    from accreditation import versions
    from accreditation.models import ArtifactAddendum

    case = advance_to(p4, 2)
    frozen = versions.freeze(case, case, "case", title="Case record", actor=p4.people.director,
                             reason="Rolled back to stage 2")
    assert frozen.version == 1 and frozen.data["reference"] == case.reference

    frozen.title = "Changed"
    with pytest.raises(Exception):
        frozen.save()

    again = versions.freeze(case, case, "case", title="Case record")
    assert again.version == 2
    assert [v.version for v in versions.versions_for(case, kind="case")] == [2, 1]

    with pytest.raises(ValidationError):
        versions.append_note(frozen, p4.people.director, "no")
    note = versions.append_note(frozen, p4.people.director, "Checked against the signed copy.")
    assert ArtifactAddendum.unscoped.filter(version=frozen).get() == note
    assert any(r["label"] == "Reference" for r in versions.readable(frozen))


def test_version_notes_are_staff_only(p4, make_user):
    from accreditation import versions

    case = advance_to(p4, 2)
    frozen = versions.freeze(case, case, "case", title="Case record")
    stranger = make_user("stranger@test.local")
    with pytest.raises(PermissionDenied):
        versions.append_note(frozen, stranger, "Looks fine to me.")


# ---------------------------------------------------------------------------
# Owner decision 1: SOP-01 ends with "Cleared for QA", not "Approved"
# ---------------------------------------------------------------------------
def test_cleared_for_qa_label_until_the_first_case_approves(p4):
    from django.utils import timezone

    from institutions.models import Institution

    inst = p4.inst
    Institution.unscoped.filter(pk=inst.pk).update(status=Institution.UNDER_REVIEW,
                                                   cleared_for_qa_at=timezone.now())
    inst.refresh_from_db()
    assert inst.is_cleared_for_qa and inst.status_label == "Cleared for QA"
    Institution.unscoped.filter(pk=inst.pk).update(status=Institution.APPROVED)
    inst.refresh_from_db()
    assert not inst.is_cleared_for_qa and inst.status_label != "Cleared for QA"


# ---------------------------------------------------------------------------
# Owner decisions 5 and 6: HND without an accredited ND, minimum running period
# ---------------------------------------------------------------------------
def _programme(inst, name, level_code, status="proposed", **extra):
    from institutions.models import Programme, ProgrammeLevel

    level = (ProgrammeLevel.objects.filter(code__iexact=level_code).first()
             or ProgrammeLevel.objects.create(code=level_code.upper(), name=level_code.upper()))
    return Programme.unscoped.create(institution=inst, name=name, level=level, status=status, **extra)


def test_hnd_without_accredited_nd_warns_but_never_blocks(p4):
    from accreditation import qa_services as svc

    hnd = _programme(p4.inst, "Marine Engineering Technology", "hnd")
    assert "no accredited ND" in svc.nd_prerequisite_warning(hnd)
    _programme(p4.inst, "Marine Engineering", "nd", status="accredited")
    assert svc.nd_prerequisite_warning(hnd) == ""
    nd = _programme(p4.inst, "Welding", "nd")
    assert svc.nd_prerequisite_warning(nd) == ""  # only HND programmes are checked


def test_new_programme_waits_the_minimum_running_period(p4):
    from accreditation import qa_services as svc
    from accreditation.models import QAType

    qa_type = QAType.objects.get(code="accreditation")
    set_config("qa.programme.min_running_months_before_accreditation", 24)
    start = datetime.date(2025, 9, 1)
    prog = _programme(p4.inst, "Glass Technology", "nd", status="approval_to_commence", commencement_date=start)
    assert svc.accreditation_opens(prog) == datetime.date(2027, 9, 1)
    if "approval_to_commence" in (qa_type.eligible_programme_statuses or []):
        ok, why = svc.programme_eligibility(prog, qa_type, today=datetime.date(2026, 11, 1))
        assert not ok and "Too early" in why
        ok, _ = svc.programme_eligibility(prog, qa_type, today=datetime.date(2027, 9, 2))
        assert ok
    set_config("qa.programme.min_running_months_before_accreditation", 0)
    assert svc.accreditation_opens(prog) is None


# ---------------------------------------------------------------------------
# Owner decision 2: the Division Director (advisory; the Director still acts)
# ---------------------------------------------------------------------------
def test_only_the_owning_director_chooses_the_division_director(p4):
    from accreditation import case_services as cs

    case = advance_to(p4, 2)
    director = p4.people.director
    assert cs.can_set_division_director(case, director)
    assert not cs.can_set_division_director(case, p4.people.desk_officer)
    with pytest.raises(PermissionDenied):
        cs.set_division_director(case, user=p4.people.desk_officer, director=director)
    candidates = cs.division_director_candidates(case)
    if candidates:
        cs.set_division_director(case, user=director, director=candidates[0])
        case.refresh_from_db()
        assert cs.division_director_of(case) == candidates[0]
    cs.set_division_director(case, user=director, director=None)
    case.refresh_from_db()
    assert case.division_director_id is None


# ---------------------------------------------------------------------------
# Devices declared / found not working
# ---------------------------------------------------------------------------
def test_device_outcome_separates_declared_defects_from_shortfalls():
    from accreditation.models import VisitFinding

    def finding(**kw):
        row = VisitFinding(**kw)
        return row.device_outcome

    assert finding(declared_quantity=4, found_quantity=4) == VisitFinding.CONSISTENT
    assert finding(declared_quantity=4, found_quantity=4, declared_not_working=1,
                   found_not_working=1) == VisitFinding.DECLARED_DEFECTIVE
    assert finding(declared_quantity=4, found_quantity=4, declared_not_working=0,
                   found_not_working=2) == VisitFinding.DECLINED


# ---------------------------------------------------------------------------
# Letters: one page, crest behind the text
# ---------------------------------------------------------------------------
def test_letters_fit_one_page_and_carry_the_crest():
    from documents import render

    # Five long paragraphs: two pages at full size, one page once fitted.
    long_body = ["The Board has considered the report of the visit and the recommendation of its committee. " * 4] * 5
    spec = {"reference": "NBTE/TEST/1", "kind_label": "Letter", "date": "11 November 2026",
            "addressee": "The Rector", "subject": "Outcome of accreditation", "paragraphs": long_body,
            "qr_url": "https://example.test/v/ABC", "short_code": "ABC"}
    assert render.page_count(render.render(spec)) == 2
    pdf = render.render({**spec, "fit_one_page": True})
    assert pdf.startswith(b"%PDF") and render.page_count(pdf) == 1
    assert render.crest_setting()["enabled"] is True


# ---------------------------------------------------------------------------
# Backups: media as ZIP with paths kept, restore never escapes the root
# ---------------------------------------------------------------------------
def test_media_zip_keeps_paths_and_restores_inside_the_root(tmp_path):
    from backups import engine

    src = tmp_path / "media"
    (src / "qa" / "letters").mkdir(parents=True)
    (src / "qa" / "letters" / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (src / "top.txt").write_text("top")
    archive = tmp_path / "media.zip"
    assert engine.zip_folder(src, archive, prefix="media") == 2
    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == ["media/qa/letters/a.pdf", "media/top.txt"]
        zf.close()
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("media/../../evil.txt", "nope")

    out = tmp_path / "restore"
    out.mkdir()
    engine.extract_archive(archive, out, prefix="media")
    assert (out / "qa" / "letters" / "a.pdf").read_bytes() == b"%PDF-1.4 a"
    assert not (tmp_path / "evil.txt").exists()


# ---------------------------------------------------------------------------
# Tasks: Accept & proceed, and Return / Decline / Reject with a reason
# ---------------------------------------------------------------------------
def test_qa_task_accept_and_proceed_and_respond(p4):
    from accreditation import case_services as cs
    from tasks import services as ts

    case = advance_to(p4, 1)
    link = cs.open_link(case, cs.current_stages(case)[0])
    holder = cs.holders(case, link.stage_code)[0]
    url = ts.accept_and_proceed(link.task, holder)
    assert url.startswith("/qa/")

    with pytest.raises(ValidationError):
        ts.respond_to_qa_task(link.task, holder, "shrug", "because")
    with pytest.raises(ValidationError):
        ts.respond_to_qa_task(link.task, holder, "decline", "no")
    assert set(ts.QA_RESPONSES) == {"return", "decline", "reject"}


def test_portal_request_refuses_an_hnd_without_its_nd(p3):
    """Owner rule (hard, portal path): an HND needs the ND of the same
    programme running at the institution or on the same request; a
    justification no longer gets it through. System callers (no form) are
    never blocked."""
    from accreditation import qa_services as svc
    from accreditation.models import QAType

    if not svc.hnd_block_reason(p3.eee):
        pytest.skip("fixture HND has an ND")
    qa_type = QAType.objects.get(code="reaccreditation")
    with pytest.raises(ValidationError, match="HND"):
        svc.create_request(p3.inst, qa_type, [p3.eee], user=p3.admin,
                           justifications={p3.eee.pk: "Our ND is under accreditation this cycle."})
    assert svc.create_request(p3.inst, qa_type, [p3.eee], user=p3.admin) is not None  # system caller
