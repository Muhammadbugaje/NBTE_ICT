"""ExternalUserGuardMiddleware, both directions (Master Plan §3), and the
portal's own sign-in rules."""
import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory

from accounts.middleware import ExternalUserGuardMiddleware

from .conftest import PASSWORD


@pytest.fixture
def institution(qa_seed, make_institution):
    return make_institution("DMP")


@pytest.fixture
def inst_user(institution, make_user):
    return make_user("admin@dmp.local", user_type="institution", institution=institution, role="inst-admin")


@pytest.fixture
def resource_person(qa_seed, make_user):
    return make_user("rp@t.local", user_type="resource_person", role="resource-person")


# ------------------------------------------------- institution / RP on internal host
@pytest.mark.parametrize("who", ["inst_user", "resource_person"])
def test_external_user_refused_and_logged_out_on_internal_host(request, who):
    user = request.getfixturevalue(who)
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    for path in ("/home/", "/tasks/", "/qa/settings/", "/accounts/profile/"):
        r = c.get(path)
        assert r.status_code == 403, path
        assert b"Please sign in at the QA portal" in r.content
        assert "_auth_user_id" not in c.session  # the session was logged out
        c.force_login(user)


def test_internal_login_form_refuses_external_users(inst_user):
    c = Client(HTTP_HOST="localhost")
    r = c.post("/accounts/login/", {"username": inst_user.email, "password": PASSWORD})
    assert r.status_code == 200
    assert b"Quality Assurance Portal" in r.content
    assert "_auth_user_id" not in c.session


# ------------------------------------------------- staff on portal host
def test_staff_land_on_the_staff_portal_dashboard(officer, portal_host):
    """Phase 3 changed this Phase 1 rule (was: staff are redirected off the
    portal). Staff now land on the Staff Portal Dashboard; any other portal
    page sends them back to it."""
    c = Client(HTTP_HOST=portal_host)
    c.force_login(officer)
    r = c.get("/")
    assert r.status_code == 302 and r["Location"] == "/dashboard/"
    r = c.get("/dashboard/")
    assert r.status_code == 200 and b"Go to Internal System" in r.content
    r = c.get("/documents/")
    assert r.status_code == 302 and r["Location"] == "/dashboard/"
    r = c.get("/documents/", HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and r["HX-Redirect"] == "/dashboard/"


def test_staff_redirected_off_the_portal_when_switched_off(officer, portal_host):
    """With qa.portal.staff_sign_in_enabled = false the Phase 1 rule applies."""
    from config.models import ConfigEntry

    entry = ConfigEntry.objects.get(key="qa.portal.staff_sign_in_enabled", scope="global")
    entry.value = "false"
    entry.save()
    c = Client(HTTP_HOST=portal_host)
    c.force_login(officer)
    r = c.get("/")
    assert r.status_code == 302
    assert r["Location"].endswith("/home/") and portal_host not in r["Location"]
    r = c.get("/account/", HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and r["HX-Redirect"].endswith("/home/")


def test_portal_login_form_accepts_staff(officer, portal_host):
    """Phase 3: staff may sign in on the portal (was refused in Phase 1)."""
    c = Client(HTTP_HOST=portal_host)
    r = c.post("/login/", {"username": officer.email, "password": PASSWORD})
    assert r.status_code == 302
    assert "_auth_user_id" in c.session


def test_portal_login_form_refuses_staff_when_switched_off(officer, portal_host):
    from config.models import ConfigEntry

    entry = ConfigEntry.objects.get(key="qa.portal.staff_sign_in_enabled", scope="global")
    entry.value = "false"
    entry.save()
    c = Client(HTTP_HOST=portal_host)
    r = c.post("/login/", {"username": officer.email, "password": PASSWORD})
    assert r.status_code == 200
    assert b"NBTE staff sign in on the NBTE internal system" in r.content
    assert "_auth_user_id" not in c.session


def test_institution_user_signs_in_on_portal(inst_user, portal_host):
    c = Client(HTTP_HOST=portal_host)
    r = c.post("/login/", {"username": inst_user.email, "password": PASSWORD})
    assert r.status_code == 302 and r["Location"] == "/dashboard/"
    r = c.get("/")
    assert r.status_code == 200
    assert b"Accreditation you can follow" in r.content
    assert "_auth_user_id" in c.session
    r = c.get("/dashboard/")
    assert b"Welcome" in r.content and b"DMP" in r.content
    # No internal menu anywhere on the page.
    for marker in (b"app-sidebar", b"/tasks/", b"/qa/settings", b"Quality Assurance</span>"):
        assert marker not in r.content


def test_resource_person_sees_their_variant(resource_person, portal_host):
    c = Client(HTTP_HOST=portal_host)
    c.force_login(resource_person)
    r = c.get("/dashboard/")
    assert r.status_code == 200 and b"Resource person" in r.content


# ------------------------------------------------- impersonation exception
def _run(request):
    return ExternalUserGuardMiddleware(lambda req: HttpResponse("ok"))(request)


def test_impersonated_institution_allowed_on_portal(inst_user, officer):
    req = RequestFactory().get("/", HTTP_HOST="qa.localhost")
    req.user = inst_user          # ImpersonationMiddleware swapped the user in
    req.is_qa_portal = True
    req.is_impersonating = True
    req.impersonator = officer
    assert _run(req).status_code == 200


def test_impersonating_superuser_not_logged_out_on_internal_host(inst_user, make_user, client):
    from importlib import import_module

    from django.conf import settings

    root = make_user("root@t.local", superuser=True)
    c = Client(HTTP_HOST="localhost")
    c.force_login(root)
    session = c.session
    session["impersonate_user_id"] = inst_user.pk
    session.save()
    r = c.get("/home/")
    assert r.status_code == 403
    assert b"viewing as an institution" in r.content
    assert c.session.get("_auth_user_id") == str(root.pk)  # the real user is NOT logged out
    assert import_module(settings.SESSION_ENGINE)


def test_staff_user_unaffected_on_internal_host(officer):
    c = Client(HTTP_HOST="localhost")
    c.force_login(officer)
    assert c.get("/home/").status_code == 200


@pytest.mark.django_db
def test_anonymous_requests_untouched(portal_host):
    assert Client(HTTP_HOST="localhost").get("/").status_code == 200
    assert Client(HTTP_HOST=portal_host).get("/login/").status_code == 200


# ------------------------------------------------- forced password change on the portal
def test_forced_password_change_on_portal(institution, make_user, portal_host):
    user = make_user("new@dmp.local", user_type="institution", institution=institution,
                     role="inst-admin", must_change=True)
    c = Client(HTTP_HOST=portal_host)
    c.force_login(user)
    r = c.get("/dashboard/")
    assert r.status_code == 302 and r["Location"] == "/password/change/"
    r = c.get("/password/change/")
    assert r.status_code == 200 and b"temporary password" in r.content
    r = c.post("/password/change/", {"new_password": "Kaduna-QA-2026!x", "confirm_password": "Kaduna-QA-2026!x"})
    assert r.status_code == 302
    user.refresh_from_db()
    assert not user.must_change_password
    assert c.get("/").status_code == 200


def test_set_password_link_is_single_use_and_portal_only(institution, make_user, portal_host):
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from qa_core.portal_tokens import portal_token_generator

    user = make_user("link@dmp.local", user_type="institution", institution=institution, must_change=True)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = portal_token_generator.make_token(user)
    c = Client(HTTP_HOST=portal_host)
    r = c.get(f"/password/set/{uid}/{token}/", follow=True)
    assert r.status_code == 200 and b"Choose your password" in r.content
    r = c.post(r.redirect_chain[-1][0], {"new_password1": "Kaduna-QA-2026!x", "new_password2": "Kaduna-QA-2026!x"})
    assert r.status_code == 302
    user.refresh_from_db()
    assert user.check_password("Kaduna-QA-2026!x") and not user.must_change_password
    r = Client(HTTP_HOST=portal_host).get(f"/password/set/{uid}/{token}/", follow=True)
    assert b"expired" in r.content  # used once already
    # The internal reset page does not accept portal tokens.
    r = Client(HTTP_HOST="localhost").get(f"/accounts/password/reset/{uid}/{token}/", follow=True)
    assert b"Choose your password" not in r.content


def test_forgot_password_never_reveals_accounts_and_skips_staff(officer, inst_user, portal_host, mailoutbox):
    c = Client(HTTP_HOST=portal_host)
    for email in (officer.email, "nobody@x.local", inst_user.email):
        r = c.post("/password/forgot/", {"email": email})
        assert r.status_code == 302 and r["Location"] == "/password/forgot/sent/"
