"""v2.1 N1 (Part 2.3): the QA home by role and office, under need-to-know.

Cast and records come from the Phase P fixture (assignments.tests.pp_fixtures):
institution A (Alpha, DPAP) has the whole journey; institution B (Bravo,
VTSD) belongs to VTSD only.
"""
import pytest
from django.core.files.base import ContentFile
from django.test import RequestFactory

from assignments.tests.pp_fixtures import assign_doc, assignment_mode, pp  # noqa: F401


def _widgets(user):
    from qa_core import home

    return home.build(user)["by_key"]


def _review(pp, inst, dept_code, status="awaiting_assignment"):
    from institutions.models import DocumentKind, DocumentReview, InstitutionDocument

    doc = InstitutionDocument(institution=inst, kind=DocumentKind.objects.first(), status="submitted")
    doc.file = ContentFile(b"%PDF-1.4\n%%EOF\n", name=f"{inst.code}.pdf")
    doc.save()
    return DocumentReview.unscoped.create(document=doc, department=pp.D[dept_code], status=status)


@pytest.fixture
def cast(pp):  # noqa: F811
    from qa_core.departments import qa_department

    assignment_mode()
    _review(pp, pp.a, "dpap")                       # DPAP must assign an officer (Alpha)
    _review(pp, pp.a, "dpap", status="recommended")  # ... and decide on one recommendation
    _review(pp, pp.b, "vtsd")                       # VTSD's own (Bravo)
    pp.fin_officer = _finance_officer(pp)
    pp.fin_dept = qa_department("finance")
    return pp


def _finance_officer(pp):
    from accounts.models import Role, User
    from qa_core.departments import qa_department

    user = User(email="fin.officer@t.local", first_name="Fin", last_name="Officer", user_type="staff",
                role=Role.objects.get(code="qa-finance-officer"), department=qa_department("finance"),
                must_change_password=False)
    user.set_password("Test-pass-2026!")
    user.save()
    return User.objects.get(pk=user.pk)


def test_n1_director_sees_department_decisions(cast):
    w = _widgets(cast.dpap_dir)
    docs = w["document_reviews"]
    stats = dict(docs.stats)
    assert stats["Need an officer"] == 1 and stats["Awaiting your decision"] == 1
    assert docs.actionable == 2 and "Oversight" not in [x.title for x in w.values()]
    assert "oversight" not in w
    # The SSQ of Alpha (DPAP handles it) is in the director's SSQ count.
    if cast.ssq is not None and "ssq" in w:
        assert w["ssq"].count == 1


def test_n1_uninvolved_director_counts_exclude_other_departments(cast, client_for):
    w = _widgets(cast.vtsd_dir)
    stats = dict(w["document_reviews"].stats)
    assert stats["Need an officer"] == 1 and stats["Awaiting your decision"] == 0  # Bravo only
    if "ssq" in w:
        assert w["ssq"].count == 0  # Alpha's SSQ is not in VTSD's part
    for key in ("clearances", "complaints", "cases"):
        if key in w:
            assert w[key].count == 0, key
    assert "oversight" not in w and "supervised" not in w
    html = client_for(cast.vtsd_dir).get("/qa/").content.decode()
    assert "Alpha Polytechnic" not in html
    # The legal director has no documents at all.
    assert dict(_widgets(cast.legal_dir)["document_reviews"].stats)["Need an officer"] == 0


def test_n1_officer_sees_own_assigned_work(cast, client_for):
    assign_doc(cast)
    w = _widgets(cast.dpap_officer)
    assert w["my_work"].count == 1 and w["my_work"].items[0].sub == "Alpha Polytechnic"
    assert "oversight" not in w and "document_reviews" not in w and "supervised" not in w
    page = client_for(cast.dpap_officer).get("/qa/")
    assert page.status_code == 200
    html = page.content.decode()
    assert 'data-widget="my_work"' in html and "Alpha Polytechnic" in html
    assert 'data-qh-today="1"' in html
    # A colleague in the same department with no work sees none of it.
    idle = client_for(cast.dpap_idle).get("/qa/").content.decode()
    assert _widgets(cast.dpap_idle)["my_work"].count == 0 and "Alpha Polytechnic" not in idle


def test_n1_finance_officer_sees_payment_queue(cast, client_for):
    from payments import services as pay

    w = _widgets(cast.fin_officer)
    assert "finance" in w and w["finance"].count == len(pay.queue_items(cast.fin_officer))
    assert "oversight" not in w and "document_reviews" not in w
    assert 'data-widget="finance"' in client_for(cast.fin_officer).get("/qa/").content.decode()


def test_n1_es_sees_oversight(cast, client_for):
    w = _widgets(cast.es)
    assert "oversight" in w
    assert dict(w["document_reviews"].stats)["Open, NBTE-wide"] == 3
    html = client_for(cast.es).get("/qa/").content.decode()
    assert 'data-widget="oversight"' in html
    assert "oversight" not in _widgets(cast.dpap_dir)


def test_n1_switch_to_qa_lands_on_home_and_externals_refused(cast, client_for):
    c = client_for(cast.dpap_dir)
    r = c.post("/qa/workspace/", {"workspace": "qa", "here": "/home/", "go": "1"})
    assert r.status_code == 302 and r["Location"] == "/qa/"
    assert client_for(cast.inst_user).get("/qa/").status_code == 403


def test_n1_mobile_summary_shares_my_work_counts(cast):
    from api.services.qa_staff import summary

    assign_doc(cast)
    request = RequestFactory().get("/")
    request.user = cast.dpap_officer
    counts = summary(cast.dpap_officer, request)["counts"]
    if counts["my_work"] is not None:  # only when the officer can open a QA section
        assert counts["my_work"] == 1 and counts["my_work_overdue"] == 0
