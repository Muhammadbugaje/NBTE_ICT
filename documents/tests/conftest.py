"""Phase F document tests reuse the QA fixtures (heavy ones only where a real
payment flow is needed)."""
from qa_core.tests.conftest import *  # noqa: F401,F403
from qa_core.tests.phase2_fixtures import *  # noqa: F401,F403

import pytest  # noqa: E402

from payments.tests.test_payments import claimed  # noqa: E402,F401  (a confirmed-flow-ready bill)

PW = "Docs-test-2026!"


@pytest.fixture
def light(db, settings):
    """A department, an institution with one portal user, a Director (with a
    signature), a Director without one, an officer and a register keeper -
    without running the QA seed."""
    from django.contrib.auth.models import Permission

    from accounts.models import Role, User
    from documents.testing import give_signature
    from institutions.models import Institution
    from staff.models import Department

    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    dept, _ = Department.objects.get_or_create(code="dpap", defaults={"name": "Polytechnic Programmes"})
    director_role, _ = Role.objects.get_or_create(code="director", defaults={"name": "Director", "rank": 100})
    if director_role.rank < 100:
        director_role.rank = 100
        director_role.save(update_fields=["rank"])
    officer_role, _ = Role.objects.get_or_create(code="officer", defaults={"name": "Officer", "rank": 10})
    inst = Institution.unscoped.create(code="DOCP", name="Demo Docs Polytechnic", handling_department=dept,
                                       status="approved")

    def user(email, role=None, user_type="staff", institution=None, perms=()):
        u = User(email=email, first_name=email.split("@")[0].split(".")[0].title(), last_name="Demo",
                 user_type=user_type, role=role, institution=institution, must_change_password=False)
        u.set_password(PW)
        u.save()
        for codename in perms:
            u.user_permissions.add(Permission.objects.get(codename=codename, content_type__app_label="documents"))
        return User.objects.get(pk=u.pk)

    class NS:
        pass

    ns = NS()
    ns.dept, ns.inst = dept, inst
    ns.director = user("doc.director@demo.nbte.local", director_role)
    ns.director_sig = give_signature(ns.director)
    ns.unsigned = user("doc.unsigned@demo.nbte.local", director_role)
    ns.officer = user("doc.officer@demo.nbte.local", officer_role)
    ns.keeper = user("doc.keeper@demo.nbte.local", officer_role,
                     perms=("view_issued_documents", "upload_official_copy", "revoke_document",
                            "view_verification_log"))
    ns.inst_user = user("rector@demo-poly.local", None, user_type="institution", institution=inst)
    return ns


def staff_client(user):
    from django.test import Client

    c = Client()
    c.force_login(user)
    return c


def portal_client(user):
    from django.conf import settings
    from django.test import Client

    c = Client(HTTP_HOST=settings.QA_PORTAL_HOST.split(":")[0])
    c.force_login(user)
    return c


def pdf_text(pdf):
    import io

    from pypdf import PdfReader

    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def pdf_images(pdf):
    import io

    from pypdf import PdfReader

    return sum(len(p.images) for p in PdfReader(io.BytesIO(pdf)).pages)
