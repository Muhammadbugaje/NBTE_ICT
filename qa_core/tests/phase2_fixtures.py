"""Shared fixtures for the Phase 2 tests (institutions, accreditation,
payments). Import with ``from qa_core.tests.phase2_fixtures import *`` from a
conftest; they build on the Phase 1 fixtures in qa_core/tests/conftest.py."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def pdf_upload(name="doc.pdf", extra=b""):
    return SimpleUploadedFile(name, PDF + extra, content_type="application/pdf")


@pytest.fixture(autouse=True)
def _fast_password_hashing(settings):
    """Phase 2 tests create a dozen users each; a fast hasher keeps them quick
    (password strength is not what these tests check)."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def p2(qa_seed, make_user):
    """Users for every Phase 2 role, with departments resolved the way
    seed_startup resolves them (aliases: legal, finance, aspd, ppd …)."""
    from accounts.models import Role, User
    from qa_core.departments import qa_department

    def mk(email, role, dept=None, **kw):
        user = make_user(email, role=role, **kw)
        if dept:
            user.department = qa_department(dept)
            user.save(update_fields=["department"])
        return User.objects.get(pk=user.pk)

    class NS:
        pass

    ns = NS()
    ns.officer = mk("p2.officer@t.local", "officer", "dpap")
    ns.officer2 = mk("p2.officer2@t.local", "officer", "dpap")
    ns.director = mk("p2.director@t.local", "director", "dpap")
    ns.ntird_officer = mk("p2.ntird.officer@t.local", "officer", "ntird")
    ns.ntird_director = mk("p2.ntird.director@t.local", "director", "ntird")
    ns.legal = mk("p2.legal@t.local", "officer", "legal")
    ns.finance_clr = mk("p2.finclr@t.local", "officer", "finance")
    ns.aspd = mk("p2.aspd@t.local", "officer", "academic-planning")
    ns.ppd = mk("p2.ppd@t.local", "officer", "physical-planning")
    ns.fin_officer = mk("p2.fin.officer@t.local", "qa-finance-officer", "finance")
    ns.fin_director = mk("p2.fin.director@t.local", "qa-finance-director", "finance")
    ns.dpap_monitor = mk("p2.dpap.plain@t.local", "officer", "dpap")
    # Finance has set the account printed on slips (otherwise bills wait in
    # "issued" — see test_bills_wait_for_a_real_bank_account).
    from payments.models import BankAccount

    ns.bank = BankAccount.objects.create(bank_name="Central Bank of Nigeria — TSA", account_name="NBTE",
                                         account_number="1234567890", is_active=True, is_default=True)
    assert Role.objects.filter(code="qa-finance-director").exists()
    return ns


@pytest.fixture
def lookups(qa_seed):
    from institutions.models import InstitutionCategory, InstitutionOwnership, InstitutionType
    from staff.models import StateOfOrigin

    # --skip-base-seeders leaves the state list empty; add the few we use.
    for name, code, zone in (("KADUNA", "kd", "NW"), ("KANO", "kn", "NW"), ("LAGOS", "la", "SW"),
                             ("BAUCHI", "ba", "NE"), ("NIGER", "ni", "NC")):
        StateOfOrigin.objects.get_or_create(name=name, defaults={"code": code, "zone": zone})

    class L:
        private = InstitutionOwnership.objects.get(code="private")
        federal = InstitutionOwnership.objects.get(code="federal")
        state_own = InstitutionOwnership.objects.get(code="state")
        poly = InstitutionType.objects.get(code="polytechnic")
        poly_cat = InstitutionCategory.objects.get(code="polytechnic")
        kaduna = StateOfOrigin.objects.get(name="KADUNA")

    return L


@pytest.fixture
def add_tvet_payload(lookups):
    from institutions import services

    def _payload(name="Zaria Institute of Technology", email="registrar@zit.edu.ng", ownership=None, **extra):
        data = {
            "name": name, "code": "", "ownership_id": (ownership or lookups.private).pk,
            "institution_type_id": lookups.poly.pk, "category_id": lookups.poly_cat.pk,
            "state_id": lookups.kaduna.pk, "lga": services.lgas_for(lookups.kaduna)[0],
            "address_line1": "Km 5 Sokoto Road", "address_town": "Zaria", "official_email": "info@zit.edu.ng",
            "phone": "08031234567", "head_name": "Dr. Hauwa Bello", "admin_first_name": "Musa",
            "admin_last_name": "Danjuma", "admin_email": email, "admin_phone": "", "admin_job_title": "Registrar",
            "year_established": 2024, "cac_rc_number": "RC1", "website": "",
        }
        data.update(extra)
        return data

    return _payload


@pytest.fixture
def proposed_inst(p2, add_tvet_payload):
    """A proposed private polytechnic created through Add TVET (two officers),
    with an active primary portal user."""
    from institutions.models import Institution
    from qa_core import actions

    pa = actions.initiate("institutions.add_tvet", actor=p2.officer, payload=add_tvet_payload(), reason="new")
    actions.confirm(pa, p2.director)
    inst = Institution.unscoped.get(name="Zaria Institute of Technology")
    user = inst.primary_user()
    user.is_active = True
    user.must_change_password = False
    user.save()
    return inst


@pytest.fixture
def inst_user(proposed_inst):
    from accounts.models import User

    return User.objects.get(pk=proposed_inst.primary_user().pk)


def upload_all_documents(inst, user, application=None):
    from institutions import services

    for row in services.checklist(inst):
        services.upload_document(inst, row["kind"], pdf_upload(f"{row['kind'].code}.pdf"), user=user,
                                 application=application)


@pytest.fixture
def submitted_app(proposed_inst, inst_user):
    from accreditation import services

    app = services.start_application(proposed_inst, user=inst_user)
    upload_all_documents(proposed_inst, inst_user, app)
    services.submit(app, user=inst_user, declaration=True)
    app.refresh_from_db()
    return app


def clear_all(app, p2, *, skip=()):
    from accreditation import services

    users = {"legal": p2.legal, "finance": p2.finance_clr, "academic": p2.aspd, "physical": p2.ppd}
    for clearance in services.clearances_for_round(app):
        if clearance.status != "pending":
            continue
        key = next(k for k in users if k in clearance.branch_code)
        if key in skip:
            continue
        services.decide(clearance, user=users[key], decision="clear", remark="ok")
    app.refresh_from_db()
    return app


@pytest.fixture
def fee_obligation(submitted_app, p2):
    """The institutional fee issued when all four departments clear."""
    from django.contrib.contenttypes.models import ContentType

    from accreditation.models import Application
    from payments.models import PaymentObligation

    clear_all(submitted_app, p2)
    ct = ContentType.objects.get_for_model(Application)
    return PaymentObligation.unscoped.get(source_type=ct, source_id=str(submitted_app.pk))


def call(*args, **kwargs):
    from django.core.management import call_command

    out = io.StringIO()
    call_command(*args, stdout=out, **kwargs)
    return out.getvalue()


@pytest.fixture
def bill(p2, proposed_inst):
    """An institutional-processing bill published to a proposed institution
    (without going through clearance)."""
    from payments import services

    return services.issue_obligation(proposed_inst, "institutional_processing", actor=p2.fin_officer,
                                     description="Institutional processing fee (test)")
