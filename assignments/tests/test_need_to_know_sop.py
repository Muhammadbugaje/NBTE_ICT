"""Need-to-know across SOP-01 → SOP-03 (v2.1 X1–X5, X16, X27, X47).

One small scenario built from scratch (no ``qa_seed``): institution ALP is
handled by DPAP. DPAP is involved; VTSD is not. Both directors and officers
hold the same QA permissions, so the only difference the tests see is
need-to-know (``qa.privacy.mode = assignment``, the default).
"""
import pytest
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db
API = "/api/v1/qa/"


class NS:
    pass


def _dept(code, name):
    from staff.models import Department
    return Department.objects.get_or_create(code=code, defaults={"name": name})[0]


def _role(code, name, rank):
    from accounts.models import Role
    role, _ = Role.objects.get_or_create(code=code, defaults={"name": name, "rank": rank})
    return role


def _user(email, *, role, dept, superuser=False):
    from accounts.models import User
    u = User.objects.create(email=email, first_name=email.split("@")[0].title(), last_name="Test", user_type="staff",
                            role=role, department=dept, is_superuser=superuser, is_staff=superuser,
                            must_change_password=False)
    u.set_password("Test-pass-2026!")
    u.save()
    return u


@pytest.fixture
def w(db):
    from accreditation.models import QARequest, QARequestProgramme, QAType, SSQ, SSQTemplate
    from config.models import ConfigEntry
    from institutions import tvet_access
    from institutions.models import DocumentKind, DocumentReview, Institution, InstitutionDocument, Programme

    assert ConfigEntry.objects.get(key="qa.privacy.mode").value == "assignment"  # the new-install default
    ns = NS()
    ns.dpap, ns.vtsd, ns.legal, ns.ict = (_dept("dpap", "DPAP"), _dept("vtsd", "VTSD"), _dept("legal", "Legal"),
                                          _dept("ict", "ICT"))
    director, officer = _role("director", "Director", 100), _role("officer", "Officer", 20)
    es, sa = _role("executive-secretary", "Executive Secretary", 999), _role("super-admin", "Super Admin", 0)
    viewers, _ = Group.objects.get_or_create(name="NTK test viewers")
    viewers.permissions.add(*Permission.objects.filter(
        content_type__app_label="accreditation", codename__in=("review_ssq", "view_qa_case")))
    director.groups.add(viewers)
    officer.groups.add(viewers)
    tvet_access.enforce()  # the seed: ES / super admin keep Add TVET, other roles do not

    ns.dpap_dir = _user("dpapdir@t.local", role=director, dept=ns.dpap)
    ns.dpap_off = _user("dpapoff@t.local", role=officer, dept=ns.dpap)
    ns.vtsd_dir = _user("vtsddir@t.local", role=director, dept=ns.vtsd)
    ns.vtsd_off = _user("vtsdoff@t.local", role=officer, dept=ns.vtsd)
    ns.legal_off = _user("legaloff@t.local", role=officer, dept=ns.legal)
    ns.es = _user("es@t.local", role=es, dept=_dept("es-office", "ES Office"))
    ns.root = _user("root@t.local", role=sa, dept=ns.ict, superuser=True)
    for dept, user in ((ns.dpap, ns.dpap_dir), (ns.vtsd, ns.vtsd_dir)):
        type(dept).objects.filter(pk=dept.pk).update(director=user)

    ns.inst = Institution.unscoped.create(code="ALP", name="Alpha Polytechnic", handling_department=ns.dpap,
                                          status="approved")
    qa_type = QAType.objects.create(code="ntk-test", name="Accreditation")
    template = SSQTemplate.objects.create(qa_type=qa_type, name="SSQ")
    req = QARequest.unscoped.create(reference="T/QAR/1", institution=ns.inst, qa_type=qa_type, status="in_workflow")
    prog = Programme.unscoped.create(institution=ns.inst, name="Computer Science")
    row = QARequestProgramme.unscoped.create(qa_request=req, programme=prog)
    ns.ssq = SSQ.unscoped.create(qa_request_programme=row, template=template, status="submitted")
    kind = DocumentKind.objects.create(code="ntk-deed", name="Deed")
    ns.doc = InstitutionDocument(institution=ns.inst, kind=kind, status="submitted")
    ns.doc.file = ContentFile(b"%PDF-1.4\n%%EOF\n", name="deed.pdf")
    ns.doc.save()
    ns.review = DocumentReview.unscoped.create(document=ns.doc, department=ns.dpap, status=DocumentReview.AWAITING)
    return ns


def web(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


def api(user):
    from api.tokens import refresh_for
    c = APIClient(HTTP_HOST="localhost")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_for(user).access_token}")
    return c


def _fresh(user):
    from accounts.models import User
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# Rule 2: an uninvolved department does not see that the institution exists
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("who", ["vtsd_dir", "vtsd_off"])
def test_uninvolved_department_cannot_list_open_or_count(w, who):
    from assignments import access
    from institutions.models import Institution

    user = getattr(w, who)
    assert w.inst.pk not in access.visible_institution_ids(user)
    page = web(user).get(reverse("qa_inst:list"))
    assert page.status_code == 200 and b"Alpha Polytechnic" not in page.content
    assert page.context["counts"]["total"] == 0
    assert web(user).get(reverse("qa_inst:detail", args=["ALP"])).status_code == 404
    assert Institution.objects.for_user(user).count() == 0
    listed = api(user).get(API + "institutions/")
    assert listed.status_code == 200 and listed.json()["count"] == 0
    assert api(user).get(API + "institutions/ALP/").status_code == 404
    assert b"Alpha Polytechnic" not in web(user).get(reverse("core:search"), {"q": "Alpha"}).content


def test_involved_director_sees_it_and_their_staff_do_not(w):
    """The handling department's director sees it; DPAP staff only once assigned."""
    from assignments import access

    assert w.inst.pk in access.visible_institution_ids(w.dpap_dir)
    assert web(w.dpap_dir).get(reverse("qa_inst:detail", args=["ALP"])).status_code == 200
    assert api(w.dpap_dir).get(API + "institutions/ALP/").status_code == 200
    assert w.inst.pk not in access.visible_institution_ids(w.dpap_off)
    assert api(w.dpap_off).get(API + "institutions/").json()["count"] == 0


def test_closed_review_step_does_not_keep_a_department_involved(w):
    from accreditation.models import Application, DepartmentalClearance
    from assignments import access

    app = Application.unscoped.create(reference="T/APP/1", institution=w.inst, status="clearance", round=1)
    cl = DepartmentalClearance.unscoped.create(application=app, department=w.legal, branch_code="legal_review",
                                               round=1)
    assert w.inst.pk in access.department_part_institution_ids(w.legal)
    cl.status = DepartmentalClearance.CLEARED
    cl.save()
    assert w.inst.pk not in access.department_part_institution_ids(w.legal)


# ---------------------------------------------------------------------------
# Rule 3: SSQ and documents only after assignment, and only while it is open
# ---------------------------------------------------------------------------
def test_ssq_hidden_until_assigned_and_after_the_work_closes(w):
    from assignments import work
    from assignments.models import WorkAssignment

    url = reverse("qa_req:ssq", args=[w.ssq.pk])
    assert web(w.dpap_off).get(url).status_code == 404  # 404, not 403: existence is not revealed
    assert api(w.dpap_off).get(API + f"requests/ssq/{w.ssq.pk}/").status_code == 404
    wa = work.assign(w.inst, WorkAssignment.SSQ_REVIEW, w.dpap_off, subject=w.ssq, department=w.dpap,
                     assigned_by=w.dpap_dir, accept_now=True)
    grant = wa.grants.filter(revoked_at__isnull=True).first() if hasattr(wa, "grants") else None
    from assignments.models import AccessGrant
    grant = grant or AccessGrant.objects.filter(grantee=w.dpap_off, revoked_at__isnull=True).first()
    assert grant is not None and grant.expires_at is not None  # time-boxed
    assert web(_fresh(w.dpap_off)).get(url).status_code == 200
    assert api(_fresh(w.dpap_off)).get(API + f"requests/ssq/{w.ssq.pk}/").status_code == 200
    work.complete(wa, w.dpap_dir)
    assert web(_fresh(w.dpap_off)).get(url).status_code == 404
    assert api(_fresh(w.dpap_off)).get(API + f"requests/ssq/{w.ssq.pk}/").status_code == 404
    assert web(w.vtsd_dir).get(url).status_code == 404


def test_document_hidden_until_assigned_and_after_approval(w, monkeypatch):
    from institutions import document_review as dr
    from institutions import services as inst_services

    url = reverse("qa_app:docrev_detail", args=[w.review.pk])
    assert web(w.dpap_off).get(url).status_code == 404
    assert web(w.vtsd_dir).get(url).status_code == 404
    dr.assign(w.review, director=w.dpap_dir, officer=w.dpap_off)
    off = _fresh(w.dpap_off)
    assert web(off).get(url).status_code == 200
    from assignments import access
    assert access.can_see(off, w.doc, "documents")
    dr.recommend(w.review, analyst=off, recommendation="accept")
    monkeypatch.setattr(inst_services, "review_document", lambda *a, **k: None)
    dr.decide(w.review, director=w.dpap_dir, decision="approve")
    off = _fresh(w.dpap_off)
    assert not access.can_see(off, w.doc, "documents")
    from assignments.models import AccessGrant
    assert not AccessGrant.objects.live(off).exists()


# ---------------------------------------------------------------------------
# Rule 4: who may assign whom (server-side, forged requests included)
# ---------------------------------------------------------------------------
def test_director_cannot_assign_staff_of_another_department(w):
    from assignments import work
    from assignments.models import WorkAssignment
    from institutions import document_review as dr

    with pytest.raises(PermissionDenied):  # forged: department says DPAP, the person is in Legal
        work.assign(w.inst, WorkAssignment.SSQ_REVIEW, w.legal_off, subject=w.ssq, department=w.dpap,
                    assigned_by=w.dpap_dir)
    with pytest.raises(PermissionDenied):  # Legal's work: not DPAP's director's to give
        work.assign(w.inst, WorkAssignment.SSQ_REVIEW, w.legal_off, subject=w.ssq, department=w.legal,
                    assigned_by=w.dpap_dir)
    with pytest.raises(ValidationError):
        dr.assign(w.review, director=w.dpap_dir, officer=w.legal_off)
    # The same forged POST through the website changes nothing.
    resp = web(w.dpap_dir).post(reverse("qa_app:docrev_assign", args=[w.review.pk]), {"officer": w.legal_off.pk})
    assert resp.status_code in (200, 302, 400, 403, 422)
    w.review.refresh_from_db()
    assert w.review.analyst_id is None
    assert not WorkAssignment.objects.filter(assignee=w.legal_off).exists()


def test_uninvolved_director_and_plain_staff_cannot_assign(w):
    from assignments import work
    from assignments.models import WorkAssignment

    for actor in (w.vtsd_dir, w.dpap_off):
        with pytest.raises(PermissionDenied):
            work.assign(w.inst, WorkAssignment.SSQ_REVIEW, w.dpap_off, subject=w.ssq, department=w.dpap,
                        assigned_by=actor)


@pytest.mark.parametrize("who", ["es", "root"])
def test_es_and_super_admin_assign_anyone(w, who):
    from assignments import work
    from assignments.models import WorkAssignment
    from institutions import document_review as dr

    actor = getattr(w, who)
    wa = work.assign(w.inst, WorkAssignment.SSQ_REVIEW, w.legal_off, subject=w.ssq, department=w.dpap,
                     assigned_by=actor)
    assert wa.assignee_id == w.legal_off.pk
    dr.assign(w.review, director=actor, officer=w.vtsd_off)
    w.review.refresh_from_db()
    assert w.review.analyst_id == w.vtsd_off.pk


# ---------------------------------------------------------------------------
# Rule 5: Add TVET needs the explicit permission
# ---------------------------------------------------------------------------
def _add_tvet_policy():
    from qa_core.models import ActionPolicy
    from qa_core.seed_data_phase2 import ACTION_POLICIES
    row = next(p for p in ACTION_POLICIES if p["action_code"] == "institutions.add_tvet")
    ActionPolicy.objects.update_or_create(action_code=row["action_code"],
                                          defaults={k: v for k, v in row.items() if k != "action_code"})


def test_add_tvet_needs_the_permission(w):
    _add_tvet_policy()
    for user in (w.dpap_dir, w.dpap_off, w.vtsd_dir):
        user = _fresh(user)
        assert not user.has_perm("institutions.initiate_add_tvet")
        assert web(user).get(reverse("qa_inst:add_tvet")).status_code == 403
        assert web(user).post(reverse("qa_inst:add_tvet"), {"name": "X"}).status_code == 403
        listing = web(user).get(reverse("qa_inst:list"))
        assert reverse("qa_inst:add_tvet").encode() not in listing.content
    es = _fresh(w.es)
    assert es.has_perm("institutions.initiate_add_tvet")
    assert reverse("qa_inst:add_tvet").encode() in web(es).get(reverse("qa_inst:list")).content
    assert web(es).get(reverse("qa_inst:add_tvet")).status_code == 200
