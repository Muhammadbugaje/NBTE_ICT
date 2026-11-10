"""v2.1 Phase F onboarding (F.13–F.16): two accounts, profile gate, pay to
submit with auto-submit on the Director of Finance's confirmation, per-document
review with single-document return and re-upload routing, X18 threshold."""
import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied

from qa_core.tests.phase2_fixtures import pdf_upload, upload_all_documents


@pytest.fixture
def v2(qa_seed):
    from accreditation import onboarding_v2
    from accreditation.seed_sets import install_notifications

    onboarding_v2.install_flow_v2()
    install_notifications()


@pytest.fixture
def v3(qa_seed):
    from accreditation import onboarding_v2, onboarding_v3
    from accreditation.seed_sets import install_notifications

    onboarding_v2.install_flow_v2()
    onboarding_v3.install_flow_v3()
    install_notifications()


def _complete_profile(inst, ce_user):
    """CE + Head of ICT accounts and every mandatory management role."""
    from accounts.models import Role, User
    from institutions import profile
    from institutions.models import InstitutionUser, ManagementRole

    InstitutionUser.unscoped.filter(user=ce_user).update(account_kind="chief_executive")
    ict = User.objects.create(email=f"ict.{inst.code.lower()}@t.local", first_name="Ict", last_name="Head",
                              user_type="institution", institution=inst, role=Role.objects.get(code="inst-admin"),
                              is_active=True)
    InstitutionUser.unscoped.create(institution=inst, user=ict, account_kind="ict_head")
    for role in ManagementRole.objects.filter(is_mandatory=True, is_active=True):
        profile.set_holder(inst, role, {"full_name": f"{role.name} Person", "email": f"{role.code}@t.local",
                                        "phone": "0803"}, actor=ce_user)
    assert profile.profile_complete(inst), profile.setup_checklist(inst)
    return User.objects.get(pk=ict.pk)


def _directors(make_user, depts):
    from staff.models import Department

    out = {}
    for dept in depts:
        d = Department.objects.get(pk=dept.pk)
        if d.director_id is None:
            u = make_user(f"dir.{d.code}@t.local", role="director")
            u.department = d
            u.save(update_fields=["department"])
            Department.objects.filter(pk=d.pk).update(director=u)
        out[d.pk] = Department.objects.get(pk=d.pk).director
    return out


def test_x13_gate_redirects_and_unlocks(v2, proposed_inst, inst_user, client_for, portal_host):
    c = client_for(inst_user, host=portal_host)
    r = c.get("/application/")
    assert r.status_code == 302 and r["Location"] == "/setup/"
    r = c.get("/documents/", HTTP_HX_REQUEST="true")
    assert r["HX-Redirect"] == "/setup/"
    page = c.get("/setup/").content.decode()
    assert "Your profile checklist" in page and "Management team" in page
    _complete_profile(proposed_inst, inst_user)
    assert c.get("/application/").status_code == 200


def test_x12_register_creates_two_accounts_and_resend_is_rate_limited(v2, lookups, client_for, portal_host,
                                                                      django_capture_on_commit_callbacks, monkeypatch):
    from django.test import Client

    from institutions import services
    from institutions.models import Institution, InstitutionUser

    def fail_email_delivery(*args, **kwargs):
        raise RuntimeError("email service unavailable")

    monkeypatch.setattr("qa_core.mail.send", fail_email_delivery)
    c = Client(HTTP_HOST=portal_host)
    data = {"name": "Kafanchan Polytechnic", "ownership": lookups.private.pk, "institution_type": lookups.poly.pk,
            "category": lookups.poly_cat.pk, "state": lookups.kaduna.pk, "lga": services.lgas_for(lookups.kaduna)[0],
            "address_line1": "1 Road", "official_email": "info@kp.edu.ng", "phone": "0803", "year_established": 2024,
            "cac_rc_number": "RC9", "ce_title": "Dr", "ce_full_name": "Amina Yusuf", "ce_email": "ce@kp.edu.ng",
            "ce_phone": "0803 1", "admin_first_name": "Ibrahim", "admin_last_name": "Musa",
            "admin_email": "ict@kp.edu.ng", "admin_phone": "0803 2", "admin_job_title": "Head of ICT",
            "password1": "Very-long-pass-2026!", "password2": "Very-long-pass-2026!", "declaration": "on"}
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post("/register/", data)
    assert r.status_code == 302 and r["Location"].endswith("/setup/"), r.content[:2000]
    inst = Institution.unscoped.get(name="Kafanchan Polytechnic")
    kinds = dict(InstitutionUser.unscoped.filter(institution=inst).values_list("account_kind", "user__email"))
    assert kinds == {"chief_executive": "ce@kp.edu.ng", "ict_head": "ict@kp.edu.ng"}
    # v2.2: registration succeeds even if e-mail fails, but the CE set-up link is
    # never handed to the Head of ICT. NBTE staff resend or reveal it instead.
    assert InstitutionUser.unscoped.get(institution=inst, account_kind="chief_executive").invited_at
    assert "qa_ce_activation_link_once" not in c.session
    setup_page = c.get("/setup/")
    assert setup_page.status_code == 200 and "One-time Chief Executive setup link" not in setup_page.content.decode()
    monkeypatch.undo()
    from accounts.models import User
    nbte = User.objects.create_superuser(email="nbte-ict@x.org", password="x-Very-long-2026!")
    activation = services.nbte_send_ce_link(inst, actor=nbte, reveal=True)
    assert activation["url"] and activation["email"] == "ce@kp.edu.ng"
    ict = InstitutionUser.unscoped.get(institution=inst, account_kind="ict_head").user
    ce = InstitutionUser.unscoped.get(institution=inst, account_kind="chief_executive").user
    assert services.is_chief_executive(ce) and not services.is_chief_executive(ict)
    assert not ce.has_usable_password()
    from django.test import Client
    from urllib.parse import urlsplit
    ce_client = Client(HTTP_HOST=portal_host)
    activation_path = urlsplit(activation["url"]).path
    set_password_page = ce_client.get(activation_path, follow=True)
    assert set_password_page.status_code == 200
    assert set_password_page.redirect_chain
    set_password_response = ce_client.post(
        set_password_page.redirect_chain[-1][0], {"new_password1": "New-CE-password-2026!",
                                                   "new_password2": "New-CE-password-2026!"})
    assert set_password_response.status_code == 302
    ce.refresh_from_db()
    assert ce.check_password("New-CE-password-2026!")
    from qa_core.portal_tokens import portal_token_generator
    activation_token = activation["url"].rstrip("/").rsplit("/", 1)[-1]
    assert not portal_token_generator.check_token(ce, activation_token)
    with pytest.raises(PermissionDenied, match="Head of ICT"):
        services.resend_ce_invitation(inst, actor=ce)
    services.resend_ce_invitation(inst, actor=ict)
    with pytest.raises(Exception, match="less than"):
        services.resend_ce_invitation(inst, actor=ict)
    ce_client.force_login(ce)
    ce_session = ce_client.session
    ce_session["qa_ce_activation_link_once"] = activation
    ce_session.save()
    ce_setup_page = ce_client.get("/setup/", HTTP_HOST=portal_host)
    assert "One-time Chief Executive setup link" not in ce_setup_page.content.decode()


def test_x14_x15_pay_to_submit_and_auto_submit_on_dof_confirmation(
        v2, p2, proposed_inst, inst_user, client_for, portal_host, django_capture_on_commit_callbacks):
    from accreditation import onboarding_v2, services
    from institutions.models import DocumentReview
    from payments.signals import obligation_confirmed

    ict = _complete_profile(proposed_inst, inst_user)
    app = services.start_application(proposed_inst, user=inst_user)
    assert onboarding_v2.is_v2(app)
    assert onboarding_v2.pay_blockers(app)  # documents missing
    upload_all_documents(proposed_inst, ict, app)
    assert onboarding_v2.pay_blockers(app) == []
    page = client_for(inst_user, host=portal_host).get("/application/").content.decode()
    assert "Pay to submit" in page and "Declare and pay" in page and "Submit application" not in page
    with pytest.raises(PermissionDenied):
        onboarding_v2.declare_and_pay(app, user=ict, declaration=True)  # Head of ICT cannot declare
    app = onboarding_v2.declare_and_pay(app, user=inst_user, declaration=True)
    assert app.status == "fee_due" and app.declared_by == inst_user and app.fee_obligation_id
    assert not app.submitted_at
    with django_capture_on_commit_callbacks(execute=True):
        obligation_confirmed.send(sender=type(app.fee_obligation), obligation=app.fee_obligation,
                                  verification=None, actor=p2.fin_director)
    app.refresh_from_db()
    assert app.status == "document_review" and app.auto_submitted and app.submitted_by == inst_user
    reviews = DocumentReview.unscoped.filter(application=app)
    assert reviews.exists() and set(reviews.values_list("status", flat=True)) == {"awaiting_assignment"}
    # A second confirmation is harmless (idempotent).
    with django_capture_on_commit_callbacks(execute=True):
        obligation_confirmed.send(sender=type(app.fee_obligation), obligation=app.fee_obligation)
    app.refresh_from_db()
    assert app.status == "document_review"


def test_v3_finance_confirmation_unlocks_documents_then_ce_submits(
        v3, p2, proposed_inst, inst_user, django_capture_on_commit_callbacks):
    from accreditation import onboarding_v2, onboarding_v3, services
    from institutions import services as inst_services
    from institutions.models import DocumentReview, InstitutionType
    from payments.models import FeeComponent, FeeSchedule
    from config.models import ConfigEntry
    from django.core.cache import cache
    from django.core.exceptions import ValidationError

    ict = _complete_profile(proposed_inst, inst_user)
    schedule = FeeSchedule.objects.get(code="institutional_processing_v3")
    itype = InstitutionType.objects.get(code="polytechnic")
    for code, label, kind, setting, amount, order in (
            ("base-polytechnic", "Institution fee", "base",
             "qa.fee.institutional_processing_v3.base.polytechnic", "2000000", 10),
            ("processing-polytechnic", "Processing fee", "processing",
             "qa.fee.institutional_processing_v3.processing.polytechnic", "1000000", 20)):
        FeeComponent.objects.update_or_create(
            schedule=schedule, code=code,
            defaults={"label": label, "kind": kind, "amount_setting": setting,
                      "institution_type": itype, "order": order, "to_confirm": False})
        ConfigEntry.objects.update_or_create(key=setting, scope="global",
                                             defaults={"value": amount, "value_type": "decimal"})
    cache.clear()

    app = services.start_application(proposed_inst, user=inst_user)
    assert onboarding_v3.is_v3(app)
    assert onboarding_v2.pay_blockers(app) == []  # documents are not required before payment in v3
    app = onboarding_v2.declare_and_pay(app, user=inst_user, declaration=True)
    assert app.status == "fee_due" and app.fee_obligation.amount == 3000000
    assert onboarding_v3.document_upload_blocked(app)
    kind = inst_services.checklist(proposed_inst)[0]["kind"]
    with pytest.raises(ValidationError, match="unlock after"):
        inst_services.upload_document(proposed_inst, kind, pdf_upload("before-payment.pdf"),
                                      user=ict, application=app)

    with django_capture_on_commit_callbacks(execute=True):
        services.payment_settled(app.fee_obligation)
    app.refresh_from_db()
    assert app.status == "document_upload" and not onboarding_v3.document_upload_blocked(app)
    upload_all_documents(proposed_inst, ict, app)
    with pytest.raises(PermissionDenied):
        onboarding_v3.submit_documents(app, user=ict)
    app = onboarding_v3.submit_documents(app, user=inst_user)
    assert app.status == "document_review" and app.submitted_by == inst_user and not app.auto_submitted
    assert DocumentReview.unscoped.filter(application=app).exists()


def test_x14_v1_instances_stay_on_v1(qa_seed, proposed_inst, inst_user):
    from accreditation import onboarding_v2, services

    app = services.start_application(proposed_inst, user=inst_user)  # before v2 is published
    onboarding_v2.install_flow_v2()
    assert services.instance_of(app).flow_version.version == 1 and not onboarding_v2.is_v2(app)


def test_x16_x17_review_chain_return_reupload_and_clear(
        v2, p2, make_user, proposed_inst, inst_user, django_capture_on_commit_callbacks):
    from accreditation import onboarding_v2, services
    from institutions import document_review as dr
    from institutions import services as inst_services
    from institutions.models import DocumentReview
    from payments.signals import obligation_confirmed

    ict = _complete_profile(proposed_inst, inst_user)
    app = services.start_application(proposed_inst, user=inst_user)
    upload_all_documents(proposed_inst, ict, app)
    app = onboarding_v2.declare_and_pay(app, user=inst_user, declaration=True)
    with django_capture_on_commit_callbacks(execute=True):
        obligation_confirmed.send(sender=type(app.fee_obligation), obligation=app.fee_obligation)
    reviews = list(DocumentReview.unscoped.filter(application=app).select_related("department", "document__kind"))
    directors = _directors(make_user, {r.department for r in reviews})
    first = reviews[0]
    director = directors[first.department_id]
    officer = make_user(f"an.{first.department.code}@t.local", role="officer")
    officer.department = first.department
    officer.save(update_fields=["department"])
    # Only the department's director assigns; another department's director cannot.
    other = next((d for pk, d in directors.items() if pk != first.department_id), None)
    if other is not None:
        with pytest.raises(PermissionDenied):
            dr.assign(first, director=other, officer=officer, reason="x")
        assert not dr.visible_reviews(other).filter(pk=first.pk).exists()  # director sees only his department
    with django_capture_on_commit_callbacks(execute=True):
        dr.assign(first, director=director, officer=officer, reason="test choice")
        with pytest.raises(PermissionDenied):
            dr.recommend(first, analyst=director, recommendation="accept")
        dr.recommend(first, analyst=officer, recommendation="return", remark="Not signed.")
        dr.decide(first, director=director, decision="send_back", remark="Check page 2 too.")
        dr.recommend(first, analyst=officer, recommendation="return", remark="Not signed; page 2 blank.")
        dr.decide(first, director=director, decision="return_to_institution", remark="Upload the signed copy.")
    first.refresh_from_db()
    assert first.status == "returned_to_institution" and first.document.status == "returned"
    app.refresh_from_db()
    assert app.status == "document_review"  # the application is never sent back
    # Re-upload just that document: same department, director and analyst.
    with django_capture_on_commit_callbacks(execute=True):
        new_doc = inst_services.upload_document(proposed_inst, first.document.kind, pdf_upload("v2.pdf"), user=ict)
    nxt = DocumentReview.unscoped.get(document=new_doc)
    assert (nxt.department_id, nxt.analyst_id, nxt.round, nxt.status) == (first.department_id, officer.pk, 2,
                                                                          "assigned")
    assert DocumentReview.unscoped.get(pk=first.pk).status == "superseded"
    with django_capture_on_commit_callbacks(execute=True):
        dr.recommend(nxt, analyst=officer, recommendation="accept")
        dr.decide(nxt, director=director, decision="approve")
        for r in DocumentReview.unscoped.filter(application=app, status="awaiting_assignment"):
            d = directors[r.department_id]
            o = make_user(f"an{r.pk}@t.local", role="officer")
            o.department = r.department
            o.save(update_fields=["department"])
            dr.assign(r, director=d, officer=o, reason="test choice")
            dr.recommend(r, analyst=o, recommendation="accept")
            dr.decide(r, director=d, decision="approve")
    app.refresh_from_db()
    assert app.status == "programme_selection"


@pytest.mark.parametrize("n,comparator,expected", [(5, "gt", False), (6, "gt", True), (5, "gte", True),
                                                   (4, "gte", False)])
def test_x18_threshold_comparator(db, n, comparator, expected):
    from accreditation import onboarding_v2
    from config.models import ConfigEntry

    ConfigEntry.objects.update_or_create(key="qa.onboarding.physical_verification_comparator", scope="global",
                                         defaults={"value": comparator, "value_type": "str"})
    from django.core.cache import cache

    cache.clear()
    assert onboarding_v2.needs_physical_verification(n) is expected
