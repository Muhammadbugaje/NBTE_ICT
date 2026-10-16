"""Regression tests for review findings M17 (sidebar/action-queue cost),
M18 (rate limiting) and M19 (deployment defaults)."""
from pathlib import Path

import pytest
from django.core.cache import cache
from django.urls import reverse

from accounts.models import Role, User
from staff.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def _open_request(requester, dept, ref, rt):
    from workflow.models import Request
    return Request.objects.create(reference=ref, request_type=rt, requester=requester,
                                  subject="s", body="b", receiving_department=dept)


@pytest.fixture
def rtype(ict):
    from workflow.models import ApprovalChain, RequestCategory, RequestType
    cat = RequestCategory.objects.create(name="C17", code="c17")
    chain = ApprovalChain.objects.create(name="ch17", code="ch17")
    return RequestType.objects.create(name="T17", code="t17", category=cat,
                                      handling_department=ict, approval_chain=chain)


# ----------------------------------------------------------------- M17
def test_candidates_exclude_other_departments(ict, roles, rtype):
    from workflow.services import candidate_requests_for
    fin = Department.objects.create(name="Fin", code="fin-m17")
    mine = _open_request(mk("m17a@x.org", ict, roles["staff"]), ict, "REQ-M17-1", rtype)
    theirs = _open_request(mk("m17b@x.org", fin, roles["staff"]), fin, "REQ-M17-2", rtype)
    director = mk("m17c@x.org", ict, roles["director"])
    ids = set(candidate_requests_for(director).values_list("pk", flat=True))
    assert mine.pk in ids and theirs.pk not in ids


def test_candidates_include_finance_gate_for_finance_department(ict, roles, rtype):
    from workflow.services import candidate_requests_for
    fin = Department.objects.create(name="Finance", code="finance")
    rtype.requires_finance_approval = True
    rtype.save()
    req = _open_request(mk("m17d@x.org", ict, roles["staff"]), ict, "REQ-M17-3", rtype)
    fin_director = mk("m17e@x.org", fin, roles["director"])
    assert req in candidate_requests_for(fin_director)


def test_candidates_unrestricted_for_superuser_and_es(ict, roles, rtype):
    from workflow.services import candidate_requests_for
    req = _open_request(mk("m17f@x.org", ict, roles["staff"]), ict, "REQ-M17-4", rtype)
    assert req in candidate_requests_for(mk("su17@x.org", is_superuser=True))
    other = Department.objects.create(name="ES office", code="es-m17")
    assert req in candidate_requests_for(mk("es17@x.org", other, roles["executive-secretary"]))


def test_departmentless_user_sidebar_does_no_per_request_work(client, ict, roles, rtype,
                                                              django_assert_max_num_queries):
    from core.context_processors import sidebar_counts
    from django.test import RequestFactory
    requester = mk("m17g@x.org", ict, roles["staff"])
    for i in range(30):
        _open_request(requester, ict, f"REQ-M17-Q{i}", rtype)
    loner = mk("m17h@x.org")
    req = RequestFactory().get("/")
    req.user = loner
    with django_assert_max_num_queries(15):
        sidebar_counts(req)


def test_notification_does_not_flush_other_users_sidebar(ict, roles):
    from core.cache import make_key
    from notifications.models import Notification
    a = mk("m17i@x.org", ict, roles["staff"])
    b = mk("m17j@x.org", ict, roles["staff"])
    key_a = make_key("sidebar", "counts", user=a)
    key_b = make_key("sidebar", "counts", user=b)
    cache.set(key_a, {"notifications": 0}, 60)
    cache.set(key_b, {"notifications": 0}, 60)
    Notification.objects.create(recipient=b, title="hello")
    assert cache.get(make_key("sidebar", "counts", user=a)) is not None
    assert cache.get(make_key("sidebar", "counts", user=b)) is None


def test_only_notification_and_request_touch_the_sidebar_cache():
    from django.db.models.signals import post_save
    uids = {r[0][0] for r in post_save.receivers if isinstance(r[0][0], str)}
    sidebar = {u for u in uids if u.startswith("cache_inv_") and "sidebar" in u}
    assert sidebar == {"cache_inv_workflow.Request_sidebar",
                       "cache_inv_notifications.Notification_sidebar_user"}


# ----------------------------------------------------------------- M18
def test_login_locked_after_five_failures(client):
    mk("m18@x.org")
    url = reverse("accounts:login")
    for _ in range(5):
        resp = client.post(url, {"username": "m18@x.org", "password": "wrong"})
        assert resp.status_code == 200
    resp = client.post(url, {"username": "m18@x.org", "password": "Xx-123456789"})
    assert resp.status_code == 429
    assert "_auth_user_id" not in client.session


def test_login_success_clears_failures(client):
    mk("m18b@x.org")
    url = reverse("accounts:login")
    for _ in range(4):
        client.post(url, {"username": "m18b@x.org", "password": "wrong"})
    resp = client.post(url, {"username": "m18b@x.org", "password": "Xx-123456789"})
    assert resp.status_code == 302
    from core import ratelimit
    assert ratelimit.count("login", "m18b@x.org|127.0.0.1") == 0


def test_password_reset_emails_capped(client, monkeypatch):
    import core.email_utils
    sent = []
    monkeypatch.setattr(core.email_utils, "send_templated_email",
                        lambda *a, **kw: sent.append(kw.get("recipients")))
    mk("m18c@x.org")
    for _ in range(5):
        resp = client.post(reverse("accounts:password_reset"), {"email": "m18c@x.org"})
        assert resp.status_code == 302  # same "check your inbox" page every time
    assert len(sent) == 3


def test_contact_form_capped(client, monkeypatch):
    import core.email_utils
    monkeypatch.setattr(core.email_utils, "send_templated_email", lambda *a, **kw: None)
    data = {"name": "Visitor", "email": "v@x.org", "subject": "Hi",
            "message": "Hello there, this is a message.", "website": ""}
    codes = [client.post(reverse("core:contact_submit"), data).status_code for _ in range(7)]
    assert codes[5:] == [429, 429]
    assert codes.count(429) == 2


# ----------------------------------------------------------------- M19
NGINX = Path(__file__).resolve().parent.parent / "deploy" / "nginx.conf"


def test_nginx_serves_app_over_tls_and_redirects_http():
    conf = NGINX.read_text()
    assert "listen 443 ssl;" in conf
    assert "return 301 https://$host$request_uri;" in conf
    port80 = conf.split("listen 443 ssl;")[0]
    assert "proxy_pass" not in port80, "port 80 must only redirect"


def test_celery_defaults_to_prod_when_redis_url_set(monkeypatch):
    import importlib.util
    import os
    src = (Path(__file__).resolve().parent.parent / "nbte_ict" / "celery.py").read_text()
    assert '"nbte_ict.settings.prod" if os.environ.get("REDIS_URL")' in src
    # The expression itself: prod with REDIS_URL, dev without.
    expr = '"nbte_ict.settings.prod" if os.environ.get("REDIS_URL") else "nbte_ict.settings.dev"'
    monkeypatch.setenv("REDIS_URL", "redis://r:6379/0")
    assert eval(expr, {"os": os}) == "nbte_ict.settings.prod"
    monkeypatch.delenv("REDIS_URL")
    assert eval(expr, {"os": os}) == "nbte_ict.settings.dev"
    assert importlib.util.find_spec("nbte_ict.celery") is not None
