"""Rotation = a queue of REAL work waiting in the director's department.

Only the director (own department) and the ES / super admin (all departments)
open it; you can only assign a job that exists, is still unassigned and sits
in your department, to active staff of that department.
"""
import pytest
from django.core.files.base import ContentFile
from django.test import Client

from assignments import rotation_queue as queue
from assignments.models import WorkAssignment
from assignments.models_rotation import RotationMember, RotationPool

URL = "/qa/department/rotation/"


def web(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


def _review(inst, dept, code):
    from institutions.models import DocumentKind, DocumentReview, InstitutionDocument
    kind = DocumentKind.objects.create(code=f"rq-{code}", name=f"Deed {code}")
    doc = InstitutionDocument(institution=inst, kind=kind, status="submitted")
    doc.file = ContentFile(b"%PDF-1.4\n%%EOF\n", name=f"{code}.pdf")
    doc.save()
    return DocumentReview.unscoped.create(document=doc, department=dept, status=DocumentReview.AWAITING)


def _ssq(inst, ref):
    from accreditation.models import QARequest, QARequestProgramme, QAType, SSQ, SSQTemplate
    from institutions.models import Programme
    qa_type, _ = QAType.objects.get_or_create(code="rq-acc", defaults={"name": "Accreditation"})
    template, _ = SSQTemplate.objects.get_or_create(qa_type=qa_type, name="SSQ")
    req = QARequest.unscoped.create(reference=ref, institution=inst, qa_type=qa_type, status="in_workflow")
    prog = Programme.unscoped.create(institution=inst, name=f"Computer Science {ref}")
    row = QARequestProgramme.unscoped.create(qa_request=req, programme=prog)
    return SSQ.unscoped.create(qa_request_programme=row, template=template, status="submitted")


@pytest.fixture
def q(people, depts):
    from institutions.models import Institution
    ict, hr = depts["ict"], depts["hr"]
    inst = Institution.unscoped.create(code="RQA", name="Queue Poly", status="approved", handling_department=ict)
    inst_hr = Institution.unscoped.create(code="RQB", name="HR Poly", status="approved", handling_department=hr)
    new_inst = Institution.unscoped.create(code="RQN", name="Brand New Poly", status="proposed",
                                           handling_department=ict)
    pool = RotationPool.objects.create(code="document_analysis:ict", kind="document_analysis", department=ict,
                                       name="Document analysis · ICT")
    for u in (people["officer"], people["officer2"], people["staff"]):
        RotationMember.objects.create(pool=pool, user=u)
    return {**people, "ict": ict, "hr": hr, "inst": inst, "new_inst": new_inst,
            "review": _review(inst, ict, "a"), "review_hr": _review(inst_hr, hr, "b"),
            "ssq": _ssq(inst, "T/RQ/1"), "ssq_hr": _ssq(inst_hr, "T/RQ/2")}


def _keys(ctx):
    return {j.key for g in ctx["groups"] for j in g.jobs}


def test_only_director_and_es_open_rotation(q):
    for who in ("officer", "staff", "deputy", "head", "hr_officer"):
        assert not queue.can_open(q[who]), who
        assert web(q[who]).get(URL).status_code == 403, who
        r = web(q[who]).post(URL + "assign/confirm/", {"job": f"document_analysis:{q['review'].pk}",
                                                        "chosen": q["officer"].pk})
        assert r.status_code == 403, who
    assert queue.can_open(q["director"]) and queue.can_open(q["es"])
    assert not WorkAssignment.objects.filter(kind="document_analysis", subject_id=str(q["review"].document_id)).exists()


def test_sidebar_link_only_for_directors_and_es(q):
    from qa_core.context_processors import qa_nav
    from django.test import RequestFactory
    for who, expected in (("director", True), ("es", True), ("officer", False), ("deputy", False)):
        req = RequestFactory().get("/qa/")
        req.user = q[who]
        nav = qa_nav(req).get("qa_nav", {})
        assert bool(nav.get("rotation")) is expected, who


def test_director_sees_only_own_department_work_by_category(q):
    r = web(q["director"]).get(URL)
    assert r.status_code == 200
    ctx = r.context
    keys = _keys(ctx)
    assert f"document_analysis:{q['review'].pk}" in keys
    assert f"ssq_review:{q['ssq'].pk}" in keys
    assert f"oversight_ict:{q['new_inst'].pk}" in keys, "ICT oversight lands in the ICT department"
    assert f"document_analysis:{q['review_hr'].pk}" not in keys and f"ssq_review:{q['ssq_hr'].pk}" not in keys
    assert [g.meta["code"] for g in ctx["groups"]] == ["document_analysis", "ssq_review", "oversight_ict"]
    doc_group = ctx["groups"][0]
    assert doc_group.head_suggested["user"] in (q["officer"], q["officer2"], q["staff"])
    # asking for another department is ignored
    r = web(q["director"]).get(URL + "?dept=hr")
    assert r.context["dept"] == q["ict"] and f"document_analysis:{q['review_hr'].pk}" not in _keys(r.context)
    # the HR director sees HR's work only (no ICT oversight)
    r = web(q["hr_dir"]).get(URL)
    keys = _keys(r.context)
    assert keys == {f"document_analysis:{q['review_hr'].pk}", f"ssq_review:{q['ssq_hr'].pk}"}


def test_es_sees_every_department(q):
    c = web(q["es"])
    r = c.get(URL + "?dept=hr")
    assert r.status_code == 200 and r.context["dept"] == q["hr"]
    assert f"document_analysis:{q['review_hr'].pk}" in _keys(r.context)
    r = c.get(URL + "?dept=ict")
    assert f"document_analysis:{q['review'].pk}" in _keys(r.context)
    assert len(r.context["depts"]) >= 2


def test_empty_state_when_nothing_waits(q, depts):
    from staff.models import Department
    from accounts.models import User
    other = Department.objects.create(name="Legal (t)", code="legal-t")
    boss = User.objects.create_user("legaldir@t.local", "x-Pass-2026!", first_name="Lea", last_name="T",
                                    department=other, must_change_password=False)
    Department.objects.filter(pk=other.pk).update(director=boss)
    r = web(boss).get(URL)
    assert r.status_code == 200 and r.context["groups"] == []
    assert b"No work waiting for assignment in Legal (t)" in r.content


def test_assign_document_next_in_rotation(q):
    from institutions.models import DocumentReview
    c = web(q["director"])
    key = f"document_analysis:{q['review'].pk}"
    r = c.get(URL + f"assign/?job={key}")
    assert r.status_code == 200
    job = queue.get_job(q["director"], key)
    _rows, suggested = queue.candidates(job)
    r = c.post(URL + "assign/confirm/", {"job": key, "chosen": suggested["user_id"]}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"is assigned" in r.content
    review = DocumentReview.unscoped.get(pk=q["review"].pk)
    assert review.status == DocumentReview.ASSIGNED and review.analyst_id == suggested["user_id"]
    assert key not in _keys(c.get(URL).context), "assigned work leaves the queue"
    # already assigned: refused, nothing new created
    n = WorkAssignment.objects.count()
    r = c.post(URL + "assign/confirm/", {"job": key, "chosen": q["officer2"].pk, "reason": "second try"},
               HTTP_HX_REQUEST="true")
    assert r.status_code == 422 and b"no longer waiting" in r.content
    assert WorkAssignment.objects.count() == n


def test_choosing_someone_else_needs_a_reason(q):
    c = web(q["director"])
    key = f"document_analysis:{q['review'].pk}"
    job = queue.get_job(q["director"], key)
    rows, suggested = queue.candidates(job)
    other = next(r for r in rows if r["eligible"] and r["user_id"] != suggested["user_id"])
    r = c.post(URL + "assign/confirm/", {"job": key, "chosen": other["user_id"], "reason": ""},
               HTTP_HX_REQUEST="true")
    assert r.status_code == 422 and b"Say why" in r.content
    r = c.post(URL + "assign/confirm/", {"job": key, "chosen": other["user_id"], "reason": "Knows the file"},
               HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    assert WorkAssignment.objects.filter(kind="document_analysis", assignee_id=other["user_id"]).exists()


@pytest.mark.parametrize("forged", ["document_analysis:999999", "ssq_review:999999", "oversight_ict:999999",
                                    "visit_panel:1", "nonsense", ""])
def test_cannot_assign_a_job_that_does_not_exist(q, forged):
    n = WorkAssignment.objects.count()
    r = web(q["director"]).post(URL + "assign/confirm/", {"job": forged, "chosen": q["officer"].pk},
                                HTTP_HX_REQUEST="true")
    assert r.status_code == 422
    assert WorkAssignment.objects.count() == n
    assert web(q["director"]).get(URL + f"assign/?job={forged}").status_code == 404


def test_cannot_assign_another_departments_job(q):
    from institutions.models import DocumentReview
    key = f"document_analysis:{q['review_hr'].pk}"
    r = web(q["director"]).post(URL + "assign/confirm/", {"job": key, "chosen": q["officer"].pk},
                                HTTP_HX_REQUEST="true")
    assert r.status_code == 422
    assert DocumentReview.unscoped.get(pk=q["review_hr"].pk).status == DocumentReview.AWAITING
    assert web(q["director"]).get(URL + f"assign/?job={key}").status_code == 404
    r = web(q["director"]).post(URL + "assign/confirm/", {"job": f"ssq_review:{q['ssq_hr'].pk}",
                                                          "chosen": q["officer"].pk}, HTTP_HX_REQUEST="true")
    assert r.status_code == 422
    assert not WorkAssignment.objects.filter(kind="ssq_review").exists()


def test_cannot_assign_to_staff_outside_the_department(q):
    key = f"document_analysis:{q['review'].pk}"
    for outsider in ("hr_officer", "director", "es"):
        r = web(q["director"]).post(URL + "assign/confirm/", {"job": key, "chosen": q[outsider].pk,
                                                              "reason": "forged choice"}, HTTP_HX_REQUEST="true")
        assert r.status_code == 422, outsider
    assert not WorkAssignment.objects.filter(kind="document_analysis", subject_id=str(q["review"].document_id)).exists()


def test_assign_ssq_and_notify(q, django_capture_on_commit_callbacks):
    from notifications.models import Notification
    key = f"ssq_review:{q['ssq'].pk}"
    job = queue.get_job(q["director"], key)
    _rows, suggested = queue.candidates(job)
    with django_capture_on_commit_callbacks(execute=True):
        r = web(q["director"]).post(URL + "assign/confirm/", {"job": key, "chosen": suggested["user_id"]},
                                    HTTP_HX_REQUEST="true")
    assert r.status_code == 200, r.content
    wa = WorkAssignment.objects.get(kind="ssq_review")
    assert wa.assignee_id == suggested["user_id"] and wa.subject_id == str(q["ssq"].pk)
    assert Notification.objects.filter(recipient_id=suggested["user_id"]).exists()
    assert queue.get_job(q["director"], key) is None


def test_assign_ict_oversight(q):
    key = f"oversight_ict:{q['new_inst'].pk}"
    job = queue.get_job(q["director"], key)
    _rows, suggested = queue.candidates(job)
    r = web(q["director"]).post(URL + "assign/confirm/", {"job": key, "chosen": suggested["user_id"]},
                                HTTP_HX_REQUEST="true")
    assert r.status_code == 200, r.content
    assert WorkAssignment.objects.filter(kind="oversight_ict", institution=q["new_inst"],
                                         assignee_id=suggested["user_id"]).exists()
    assert queue.get_job(q["director"], key) is None
    assert queue.get_job(q["hr_dir"], key) is None, "oversight is the ICT department's work"


def test_es_assigns_in_any_department(q):
    key = f"document_analysis:{q['review_hr'].pk}"
    r = web(q["es"]).post(URL + "assign/confirm/", {"job": key, "chosen": q["hr_officer"].pk},
                          HTTP_HX_REQUEST="true")
    assert r.status_code == 200, r.content
    assert WorkAssignment.objects.filter(kind="document_analysis", assignee=q["hr_officer"]).exists()


def test_no_js_post_redirects_with_message(q):
    key = f"document_analysis:{q['review'].pk}"
    r = web(q["director"]).post(URL + "assign/confirm/", {"job": key, "chosen": q["hr_officer"].pk})
    assert r.status_code == 302 and r["Location"].startswith(URL)


# ---------------------------------------------------------------------------
# v2.3 speed pass: the page batches per-person numbers. Batched numbers must
# be the same answers the per-person queries give (and use fewer queries).
# ---------------------------------------------------------------------------
def _suggest_kwargs(review):
    return dict(subject=review.document, institution=review.document.institution,
                department=review.department, record=False)


def _snapshot(s):
    """Ranking, scores, loads, recency and every reason — the whole output."""
    eligible = [((c.user.pk), (c.last.isoformat() if c.last else None), c.open, c.recent,
                 round(c.score, 6), list(c.reasons), list(c.notes), list(c.warnings))
                for c in s.eligible]
    blocked = [((c.user.pk), (c.last.isoformat() if c.last else None), c.open, c.recent,
                list(c.reasons)) for c in s.blocked]
    return eligible + blocked


def test_batched_suggestion_equals_and_uses_fewer_queries(q):
    import datetime

    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from django.utils import timezone

    from assignments import rotation
    from assignments import rotation_load as load
    from assignments.models import WorkAssignment

    review = q["review"]
    pool = rotation.get_pool("document_analysis", q["ict"])
    members = [m.user for m in pool.active_members()]
    Wa = WorkAssignment
    Wa.objects.create(kind="document_analysis", subject=review.document, assignee=members[0],
                      institution=q["inst"], department=q["ict"], status=Wa.IN_PROGRESS,
                      assigned_at=timezone.now() - datetime.timedelta(days=20))
    with CaptureQueriesContext(connection) as per_person:
        base = rotation.suggest("document_analysis", **_suggest_kwargs(review))
        base_open = load.open_load_all(members[0])
        base_other = load.open_load_all(q["staff"])
    covered = [*members, q["director"], q["deputy"], q["head"], q["staff"]]
    with CaptureQueriesContext(connection) as ctx:
        with load.batch(covered, ("document_analysis", "ssq_review", "oversight_ict", "onboarding_ssq")):
            with_b = rotation.suggest("document_analysis", **_suggest_kwargs(review))
            batched_open = load.open_load_all(members[0])
            batched_other = load.open_load_all(q["staff"])
    assert _snapshot(base) == _snapshot(with_b)
    assert batched_open == base_open
    assert batched_other == base_other
    assert len(ctx) < len(per_person), (len(per_person), len(ctx))


def test_batched_board_rows_equal_the_per_person_rows(q):
    """The board's per-person "n open" grouped in one query — same numbers."""
    from assignments import rotation_load as load

    staff_pool = [q["officer"], q["officer2"], q["staff"], q["deputy"]]
    with load.batch([*staff_pool, q["director"]],
                    ("document_analysis", "ssq_review", "oversight_ict", "onboarding_ssq")):
        batched = [load.open_load_all(u) for u in staff_pool]
    per = [load.open_load_all(u) for u in staff_pool]
    assert batched == per


def test_uncovered_person_falls_back_to_the_per_person_query(q):
    """Anything the batch never grouped still answers from its own queries."""
    from assignments import rotation_load as load

    with load.batch([q["officer"]], ("document_analysis",)):
        assert load.open_load(q["officer"], "document_analysis") == 0
        assert not load.active().covers(q["staff"])          # never grouped
        assert load.open_load(q["staff"], "document_analysis") == 0   # fallback query, still right
        assert load.open_load_all(q["staff"]) == 0


def test_batched_suggest_does_not_scale_with_pool_members(q):
    """The point of the batcher: ranking a pool of 17 must not cost more
    queries than ranking a pool of 3 (the per-person path grew ~1 query per
    member per question)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.models import User as U_
    from assignments import rotation
    from assignments import rotation_load as load
    from assignments.models_rotation import RotationMember

    review = q["review"]
    pool = rotation.get_pool("document_analysis", q["ict"])
    kwargs = dict(subject=review.document, institution=review.document.institution,
                  department=review.department, record=False)
    with load.batch([m.user for m in pool.active_members()], ("document_analysis",)):
        with CaptureQueriesContext(connection) as small:
            rotation.suggest("document_analysis", **kwargs)
    for i in range(14):
        u = U_.objects.create_user(f"extra{i}@t.local", "pw-t-tests", first_name=f"Extra{i}", last_name="T",
                                   role_id=pool.active_members()[0].user.role_id, department=q["ict"],
                                   must_change_password=False)
        RotationMember.objects.create(pool=pool, user=u)
    with load.batch([m.user for m in pool.active_members()], ("document_analysis",)):
        with CaptureQueriesContext(connection) as large:
            rotation.suggest("document_analysis", **kwargs)
    assert len(large) <= len(small) + 2, (len(small), len(large))
