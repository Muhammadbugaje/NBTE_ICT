"""v2.1 Phase 0 (Stabilise), QA portal items:

* 0.5  O10 / T-SEC-7 — only the clearance department's director, a ClearanceDelegate, the ES or a superuser
       decides a departmental clearance;
* 0.8  O8 / O9 (first slice of X17) — re-upload of a returned document during review, routed back;
* 0.9  Q1 / Q2 — submit programmes; NTIRD programme clearance; programme fee;
* 0.11 X35 slice — the institution never sees required quantities or "Short" flags;
* 0.13 X12 slice — only the Chief Executive submits.
"""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from qa_core.tests.phase2_fixtures import clear_all, pdf_upload


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _legal(app):
    from accreditation import services

    return next(c for c in services.clearances_for_round(app) if "legal" in c.branch_code)


def _confirm_fee(app, p2, user, capture):
    """Clear all four departments and have Finance confirm the institutional fee."""
    from django.contrib.contenttypes.models import ContentType

    from accreditation.models import Application
    from payments import services as pay
    from payments.models import PaymentObligation, PaymentVerification

    with capture(execute=True):
        clear_all(app, p2)
    ob = PaymentObligation.unscoped.get(source_type=ContentType.objects.get_for_model(Application),
                                        source_id=str(app.pk))
    with capture(execute=True):
        pay.submit_proof(ob, user=user, upload=pdf_upload("teller.pdf"), amount_claimed=ob.amount,
                         bank_name="CBN", teller_number="T-1", paid_on=None)
    item = PaymentVerification.unscoped.get(obligation=ob, is_open=True)
    with capture(execute=True):
        pay.final_decision(item, user=p2.fin_director, decision="confirmed")
    app.refresh_from_db()
    assert app.status == "programme_selection"
    return app


@pytest.fixture
def selecting_app(submitted_app, p2, inst_user, django_capture_on_commit_callbacks):
    """An onboarding application at programme selection with two programmes."""
    from institutions import services as inst_services
    from institutions.models import ProgrammeLevel

    app = _confirm_fee(submitted_app, p2, inst_user, django_capture_on_commit_callbacks)
    nd = ProgrammeLevel.objects.get(code="nd")
    for name, streams in (("Computer Science", 2), ("Statistics", 1)):
        inst_services.add_proposed_programme(app.institution, name, nd, application=app, user=inst_user,
                                             streams=streams)
    return app


@pytest.fixture
def ict_head(make_user, proposed_inst):
    return make_user("ict.head@zit.edu.ng", role="inst-admin", user_type="institution", institution=proposed_inst)


@pytest.fixture
def legal_director(make_user):
    from qa_core.departments import qa_department

    user = make_user("legal.director@t.local", role="director")
    user.department = qa_department("legal")
    user.save(update_fields=["department"])
    from accounts.models import User

    return User.objects.get(pk=user.pk)


# ===========================================================================
# 0.5 — O10 / T-SEC-7
# ===========================================================================
def test_o10_officer_role_no_longer_holds_clearance_permission(qa_seed):
    from accounts.models import Role

    officer = Role.objects.get(code="officer")
    assert not officer.permissions.filter(codename="decide_departmental_clearance").exists()
    es = Role.objects.get(code="executive-secretary")
    assert es.permissions.filter(codename="decide_departmental_clearance").exists()


def test_o10_officer_cannot_decide_clearance(submitted_app, make_user):
    from accounts.models import User
    from accreditation import services
    from qa_core.departments import qa_department

    officer = make_user("plain.legal@t.local", role="officer")
    officer.department = qa_department("legal")
    officer.save(update_fields=["department"])
    officer = User.objects.get(pk=officer.pk)
    ok, why = services.can_decide(_legal(submitted_app), officer)
    assert not ok and "director" in why
    with pytest.raises(PermissionDenied):
        services.decide(_legal(submitted_app), user=officer, decision="clear", remark="ok")


def test_o10_director_can_decide(submitted_app, legal_director):
    from accreditation import services

    clearance = services.decide(_legal(submitted_app), user=legal_director, decision="clear", remark="ok")
    assert clearance.status == "cleared"


def test_o10_delegate_can_decide_until_revoked(submitted_app, legal_director, make_user):
    from accounts.models import User
    from accreditation import clearance_access, services
    from qa_core.departments import qa_department

    legal = qa_department("legal")
    officer = make_user("delegate.legal@t.local", role="officer")
    officer.department = legal
    officer.save(update_fields=["department"])
    officer = User.objects.get(pk=officer.pk)
    assert not services.can_decide(_legal(submitted_app), officer)[0]
    # Only the director (or ES) can delegate.
    with pytest.raises(PermissionDenied):
        clearance_access.add_delegate(legal, officer, granted_by=officer)
    delegate = clearance_access.add_delegate(legal, officer, granted_by=legal_director)
    officer = User.objects.get(pk=officer.pk)
    assert officer.has_perm("accreditation.decide_departmental_clearance")
    assert services.can_decide(_legal(submitted_app), officer)[0]
    clearance_access.revoke_delegate(delegate, by=legal_director)
    officer = User.objects.get(pk=officer.pk)
    assert not officer.has_perm("accreditation.decide_departmental_clearance")
    assert not services.can_decide(_legal(submitted_app), officer)[0]
    clearance_access.add_delegate(legal, officer, granted_by=legal_director)
    officer = User.objects.get(pk=officer.pk)
    assert services.decide(_legal(submitted_app), user=officer, decision="clear", remark="ok").status == "cleared"


def test_o10_expired_delegation_refused(submitted_app, p2):
    import datetime

    from django.utils import timezone

    from accounts.models import User
    from accreditation import services
    from accreditation.models import ClearanceDelegate

    ClearanceDelegate.objects.filter(user=p2.legal).update(expires_at=timezone.now() - datetime.timedelta(hours=1))
    assert not services.can_decide(_legal(submitted_app), User.objects.get(pk=p2.legal.pk))[0]


def test_o10_es_can_decide_any_department(submitted_app, make_user):
    from accreditation import services
    from qa_core.departments import qa_department

    es = make_user("es@t.local", role="executive-secretary")
    es.department = qa_department("es-office") or es.department
    es.save()
    from accounts.models import User

    es = User.objects.get(pk=es.pk)
    assert services.decide(_legal(submitted_app), user=es, decision="clear", remark="ok").status == "cleared"


def test_o10_dry_run_report_writes_counts_only(qa_seed, tmp_path):
    from qa_core.tests.phase2_fixtures import call

    out = tmp_path / "report.md"
    text = call("plan_p0_access", output=str(out))
    assert out.exists() and "O10" in text and "qa.ssq.submit_roles" in text


# ===========================================================================
# 0.8 — O8 / O9: re-upload a returned document during review
# ===========================================================================
def _return_cac(app, p2):
    from institutions import services as inst_services
    from institutions.models import InstitutionDocument

    doc = InstitutionDocument.unscoped.filter(institution=app.institution, kind__code="cac").order_by(
        "-version").first()
    inst_services.review_document(doc, "return", user=p2.legal, remark="Certified copy please")
    return doc


def test_o8_returned_document_upload_allowed_under_review(submitted_app, p2, inst_user, client_for, portal_host):
    from institutions.models import DocumentKind, InstitutionDocument

    _return_cac(submitted_app, p2)
    kind = DocumentKind.objects.get(code="cac")
    c = client_for(inst_user, portal_host)
    page = c.get("/documents/")
    assert b"Re-upload" in page.content
    response = c.post(f"/documents/upload/{kind.pk}/", {"file": pdf_upload("cac2.pdf", b"v2")})
    assert response.status_code == 200
    assert b"You do not need to resubmit your application" in response.content
    latest = InstitutionDocument.unscoped.filter(institution=submitted_app.institution, kind=kind).order_by(
        "-version").first()
    assert latest.version == 2 and latest.status == "submitted" and latest.application_id == submitted_app.pk


def test_o8_non_returned_documents_stay_locked(submitted_app, p2, inst_user, client_for, portal_host):
    from institutions.models import DocumentKind, InstitutionDocument

    _return_cac(submitted_app, p2)
    other = DocumentKind.objects.get(code="c-of-o")
    c = client_for(inst_user, portal_host)
    response = c.post(f"/documents/upload/{other.pk}/", {"file": pdf_upload("x.pdf", b"v2")})
    assert b"locked" in response.content
    assert InstitutionDocument.unscoped.filter(institution=submitted_app.institution, kind=other).count() == 1


def test_o9_reupload_routes_to_reviewing_department_and_notifies(submitted_app, p2, inst_user, legal_director,
                                                                 django_capture_on_commit_callbacks):
    from accreditation import services
    from accreditation.models import DepartmentalClearance
    from institutions import services as inst_services
    from institutions.models import DocumentKind
    from notifications.models import Notification

    with django_capture_on_commit_callbacks(execute=True):
        _return_cac(submitted_app, p2)
    before = {c.pk: (c.status, c.resubmitted_at) for c in services.clearances_for_round(submitted_app)}
    with django_capture_on_commit_callbacks(execute=True):
        doc = inst_services.upload_document(submitted_app.institution, DocumentKind.objects.get(code="cac"),
                                            pdf_upload("cac2.pdf", b"v2"), user=inst_user, application=submitted_app)
    assert doc.routed_department.code == "legal"
    submitted_app.refresh_from_db()
    assert submitted_app.status == "clearance" and submitted_app.round == 1  # no resubmission
    after = {c.pk: c for c in services.clearances_for_round(submitted_app)}
    legal = _legal(submitted_app)
    assert after[legal.pk].resubmitted_at is not None and after[legal.pk].resubmissions == 1
    assert after[legal.pk].status == DepartmentalClearance.PENDING
    for pk, (status, resub) in before.items():
        if pk != legal.pk:
            assert after[pk].status == status and after[pk].resubmitted_at == resub  # others untouched
    staff_titles = set(Notification.objects.filter(recipient__in=[legal_director, p2.legal])
                       .values_list("recipient_id", flat=True))
    assert {legal_director.pk, p2.legal.pk} <= staff_titles
    assert not Notification.objects.filter(recipient=p2.finance_clr, title__startswith="Re-uploaded").exists()
    assert Notification.objects.filter(recipient=inst_user, title__icontains="sent back to NBTE").exists()
    # The legal queue lists it first, marked re-uploaded; the director can now clear.
    rows = services.clearance_rows(legal_director)
    assert rows[0].pk == legal.pk and rows[0].resubmitted_at
    assert services.decide(legal, user=legal_director, decision="clear", remark="ok").status == "cleared"


# ===========================================================================
# 0.9 — Q1 / Q2: submit programmes, NTIRD clearance, programme fee
# ===========================================================================
def test_q1_submit_programmes_moves_flow_and_locks(selecting_app, inst_user, p2, client_for, portal_host,
                                                   django_capture_on_commit_callbacks):
    from accreditation.models import OnboardingProgrammeReview
    from notifications.models import Notification

    c = client_for(inst_user, portal_host)
    page = c.get("/application/")
    assert b"Submit programmes to NBTE" in page.content and b"data-qa-confirm" in page.content
    with django_capture_on_commit_callbacks(execute=True):
        response = c.post("/application/programmes/submit/")
    assert response.status_code == 200
    selecting_app.refresh_from_db()
    assert selecting_app.status == "programme_clearance" and selecting_app.programme_round == 1
    reviews = OnboardingProgrammeReview.unscoped.filter(application=selecting_app)
    assert reviews.count() == 2 and {r.department.code for r in reviews} == {"ntird"}
    assert b"Waiting for" in c.get("/application/").content
    # Locked: no more edits while NBTE reviews.
    prog = reviews.first().programme
    assert c.post(f"/application/programmes/{prog.pk}/remove/").status_code == 404
    assert Notification.objects.filter(recipient=p2.ntird_director, title__startswith="Programmes to clear").exists()
    assert Notification.objects.filter(recipient=inst_user, title="Programmes submitted to NBTE").exists()


def test_q1_submit_needs_a_programme(submitted_app, p2, inst_user, django_capture_on_commit_callbacks):
    from accreditation import onboarding_programmes as op

    app = _confirm_fee(submitted_app, p2, inst_user, django_capture_on_commit_callbacks)
    with pytest.raises(ValidationError, match="at least one programme"):
        op.submit_programmes(app, user=inst_user)


def test_q2_ntird_clear_and_return_per_programme(selecting_app, inst_user, p2, client_for,
                                                 django_capture_on_commit_callbacks):
    from accreditation import onboarding_programmes as op
    from accreditation.models import OnboardingProgrammeReview
    from notifications.models import Notification

    op.submit_programmes(selecting_app, user=inst_user)
    # Queue: NTIRD director sees it; DPAP director does not; an officer cannot decide.
    page = client_for(p2.ntird_director).get("/qa/applications/programmes/")
    assert page.status_code == 200 and selecting_app.reference.encode() in page.content
    assert selecting_app.reference.encode() not in client_for(p2.director).get("/qa/applications/programmes/").content
    first, second = OnboardingProgrammeReview.unscoped.filter(application=selecting_app).order_by("programme__name")
    with pytest.raises(PermissionDenied):
        op.decide(first, user=p2.ntird_officer, decision="clear")
    with pytest.raises(ValidationError):
        op.decide(first, user=p2.ntird_director, decision="return", remark="")
    c = client_for(p2.ntird_director)
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post(f"/qa/applications/programmes/reviews/{first.pk}/decide/", {"decision": "clear"})
    assert r.status_code == 200
    selecting_app.refresh_from_db()
    assert selecting_app.status == "programme_clearance"  # one still waiting
    assert Notification.objects.filter(recipient=inst_user, title__contains="cleared").exists()
    with django_capture_on_commit_callbacks(execute=True):
        op.decide(second, user=p2.ntird_director, decision="return", remark="Add the workshop list")
    selecting_app.refresh_from_db()
    assert selecting_app.status == "programme_selection"
    # The returned programme reopens; the cleared one stays locked.
    assert op.is_locked(selecting_app, first.programme) and not op.is_locked(selecting_app, second.programme)
    cp = client_for(inst_user, "qa.localhost")
    assert cp.post(f"/application/programmes/{first.programme.pk}/remove/").status_code == 404
    assert b"Add the workshop list" in cp.get("/application/").content
    # Resubmission sends only the returned programme.
    op.submit_programmes(selecting_app, user=inst_user)
    current = OnboardingProgrammeReview.unscoped.filter(application=selecting_app, round=2)
    assert [r.programme_id for r in current] == [second.programme_id]


def test_q2_all_cleared_issues_programme_fee(selecting_app, inst_user, p2, django_capture_on_commit_callbacks):
    from django.contrib.contenttypes.models import ContentType

    from accreditation import onboarding_programmes as op
    from accreditation.models import Application, OnboardingProgrammeReview
    from payments.models import PaymentObligation

    op.submit_programmes(selecting_app, user=inst_user)
    with django_capture_on_commit_callbacks(execute=True):
        for review in OnboardingProgrammeReview.unscoped.filter(application=selecting_app):
            op.decide(review, user=p2.ntird_director, decision="clear")
    selecting_app.refresh_from_db()
    assert selecting_app.status == "programme_fee_due"
    bills = PaymentObligation.unscoped.filter(source_type=ContentType.objects.get_for_model(Application),
                                              source_id=str(selecting_app.pk), fee_schedule__code="programme_processing")
    assert bills.count() == 1 and bills.first().amount > 0


def test_flows_have_no_dead_end_stages_q1_q2(qa_seed):
    """Q1/Q2 give programme_selection and programme_clearance an outbound
    transition fired by code (T-CONS-8)."""
    import inspect

    from accreditation import onboarding_programmes as op

    src = inspect.getsource(op)
    for action in ("submit_programmes", "clear_programmes", '"return"'):
        assert action in src
    from qa_core.tests import dead_end_allowlist

    for stage in ("programme_selection", "programme_clearance"):
        assert stage not in dead_end_allowlist.DEAD_END_ALLOWLIST.get("onboarding", {})


# ===========================================================================
# 0.13 — X12: only the Chief Executive submits (web; API in api/tests)
# ===========================================================================
def test_x12_ict_head_cannot_submit(proposed_inst, inst_user, ict_head, client_for, portal_host, p2,
                                   django_capture_on_commit_callbacks):
    from accreditation import onboarding_programmes as op
    from accreditation import services
    from qa_core.tests.phase2_fixtures import upload_all_documents

    app = services.start_application(proposed_inst, user=inst_user)
    upload_all_documents(proposed_inst, inst_user, app)
    # Application: service 403, button disabled with the reason.
    with pytest.raises(PermissionDenied, match="Chief Executive"):
        services.submit(app, user=ict_head, declaration=True)
    c = client_for(ict_head, portal_host)
    page = c.get("/application/")
    assert b"Only the Chief Executive can submit the application" in page.content
    r = c.post("/application/submit/", {"declaration": "on"})
    assert r.status_code == 422
    app.refresh_from_db()
    assert app.status == "draft"
    app = services.submit(app, user=inst_user, declaration=True)
    # Programmes
    app = _confirm_fee(app, p2, inst_user, django_capture_on_commit_callbacks)
    from institutions import services as inst_services
    from institutions.models import ProgrammeLevel

    inst_services.add_proposed_programme(proposed_inst, "Computer Science", ProgrammeLevel.objects.get(code="nd"),
                                         application=app, user=ict_head)
    with pytest.raises(PermissionDenied):
        op.submit_programmes(app, user=ict_head)
    page = c.get("/application/")
    assert b"Only the Chief Executive can submit the list" in page.content
    assert c.post("/application/programmes/submit/").status_code == 422
    app.refresh_from_db()
    assert app.status == "programme_selection"


def test_x12_ict_head_cannot_submit_qa_request(p3, client_for, portal_host):
    from accreditation import qa_services as svc
    from qa_core.tests.phase3_fixtures import make_request

    req = make_request(p3)
    with pytest.raises(PermissionDenied, match="Chief Executive"):
        svc.submit(req, user=p3.admin, declaration=True)
    c = client_for(p3.admin, portal_host)
    page = c.get(f"/qa-requests/{req.pk}/")
    assert b"Only the Chief Executive can submit QA requests" in page.content
    c.post(f"/qa-requests/{req.pk}/submit/", {"declaration": "on"})
    req.refresh_from_db()
    assert req.status == "draft"
    assert svc.submit(req, user=p3.ceo, declaration=True).status != "draft"


def test_x12_ict_head_cannot_submit_ssq(p3, paid_request):
    from accreditation import qa_services as svc
    from qa_core.tests.phase3_fixtures import complete_ssq

    ssq = complete_ssq(svc.request_ssqs(paid_request)[0], p3.admin)
    assert svc.submit_roles() == ["inst-chief-executive"]
    with pytest.raises(PermissionDenied):
        svc.submit_ssq(ssq, user=p3.admin, declaration=True)
    assert svc.submit_ssq(ssq, user=p3.ceo, declaration=True).status == "submitted"


def test_x12_registrant_can_register_as_chief_executive(qa_seed, lookups, portal_host):
    from django.test import Client

    from accounts.models import User
    from institutions import services

    c = Client(HTTP_HOST=portal_host)
    data = {"name": "Kafanchan Tech", "ownership": lookups.private.pk, "institution_type": lookups.poly.pk,
            "category": lookups.poly_cat.pk, "state": lookups.kaduna.pk, "lga": services.lgas_for(lookups.kaduna)[0],
            "address_line1": "Road 1", "official_email": "info@kt.edu.ng", "phone": "0803", "head_name": "Dr K",
            "admin_first_name": "Kabir", "admin_last_name": "K", "admin_email": "rector@kt.edu.ng",
            "admin_job_title": "Rector", "admin_is_chief_executive": "on", "password1": "Very-long-pass-2026!",
            "password2": "Very-long-pass-2026!", "declaration": "on"}
    r = c.post("/register/", data)
    assert r.status_code == 302, r.content[:2000]
    assert User.objects.get(email="rector@kt.edu.ng").role.code == "inst-chief-executive"


# ===========================================================================
# 0.11 — X35: the institution never sees required quantities
# ===========================================================================
def test_x35_portal_never_shows_required(p3, paid_request, client_for, portal_host):
    from accreditation import qa_services as svc
    from accreditation import ssq as engine
    from accreditation.models import CatalogueItem, Submission
    from qa_core.tests.phase3_fixtures import complete_ssq

    items = list(CatalogueItem.objects.filter(is_active=True))
    assert items
    for item in items:  # distinctive numbers so a leak is easy to spot
        CatalogueItem.objects.filter(pk=item.pk).update(required_quantity=987 + item.pk)
    ssq = svc.request_ssqs(paid_request)[0]
    c = client_for(p3.ceo, portal_host)
    # 1. The SSQ workspace (every section).
    for section in ssq.template.sections():
        page = c.get(f"/qa-requests/{paid_request.pk}/ssq/{ssq.pk}/?section={section['key']}").content.decode()
        assert ">Required<" not in page and "required quantities" not in page
        for item in items:
            assert f">{987 + item.pk}" not in page
    # 2. The submitted snapshot (the staff copy keeps the numbers; the portal strips them).
    ssq = complete_ssq(ssq, p3.admin)
    svc.submit_ssq(ssq, user=p3.ceo, declaration=True)
    snap = Submission.unscoped.get(ssq=ssq).rendered_snapshot
    rows = [r for s in snap for f in s["fields"] if f["type"] == "catalogue" for r in f["rows"]]
    assert rows and all("required" in r for r in rows)
    portal_rows = [r for s in engine.for_audience(snap, "institution") for f in s["fields"]
                   if f["type"] == "catalogue" for r in f["rows"]]
    assert portal_rows and not any(k in r for r in portal_rows for k in ("required", "short", "weight", "critical"))
    page = c.get(f"/qa-requests/{paid_request.pk}/ssq/{ssq.pk}/").content.decode()
    assert "Short</span>" not in page and ">Required<" not in page
    # 3. render_snapshot with the institution audience.
    live = engine.render_snapshot(ssq.template, ssq.data or {}, programme=ssq.qa_request_programme.programme,
                                  audience="institution")
    assert "required" not in str([r for s in live for f in s["fields"] for r in f.get("rows") or []
                                  if isinstance(r, dict) and "item_id" in r])


def test_o10_director_manages_delegates_on_the_queue(submitted_app, legal_director, make_user, client_for):
    from accounts.models import User
    from accreditation.models import ClearanceDelegate
    from qa_core.departments import qa_department

    officer = make_user("queue.legal@t.local", role="officer")
    officer.department = qa_department("legal")
    officer.save(update_fields=["department"])
    c = client_for(legal_director)
    page = c.get("/qa/applications/clearance/")
    assert b"Clearance delegates" in page.content
    r = c.post("/qa/applications/clearance/delegates/", {"user": officer.pk})
    assert r.status_code == 200
    delegate = ClearanceDelegate.objects.get(user=officer, active=True)
    assert User.objects.get(pk=officer.pk).has_perm("accreditation.decide_departmental_clearance")
    # An officer sees no delegate panel and cannot add delegates.
    oc = client_for(User.objects.get(pk=officer.pk))
    assert b"Clearance delegates" not in oc.get("/qa/applications/clearance/").content
    assert oc.post("/qa/applications/clearance/delegates/", {"user": officer.pk}).status_code == 404
    assert c.post(f"/qa/applications/clearance/delegates/{delegate.pk}/revoke/").status_code == 200
    assert not ClearanceDelegate.objects.filter(user=officer, active=True).exists()
