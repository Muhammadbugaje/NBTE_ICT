"""v2.2 hardening: trusted client address, safe return-to links, /healthz,
the resilient Redis cache and the per-request director-seat memo."""
import pytest
from django.test import RequestFactory, override_settings

rf = RequestFactory()


# --------------------------------------------------------------- client address
@override_settings(CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP")
def test_client_ip_ignores_forged_headers_behind_cloudflare():
    from core.ratelimit import client_ip

    req = rf.get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_REAL_IP="1.1.1.1",
                 HTTP_X_FORWARDED_FOR="2.2.2.2", HTTP_CF_CONNECTING_IP="41.58.1.9")
    assert client_ip(req) == "41.58.1.9"
    # A header that is not an address falls back to the socket address.
    assert client_ip(rf.get("/", REMOTE_ADDR="10.0.0.5", HTTP_CF_CONNECTING_IP="x' or 1=1")) == "10.0.0.5"


@override_settings(CLIENT_IP_HEADER="")
def test_client_ip_socket_only_when_no_proxy():
    from core.ratelimit import client_ip

    assert client_ip(rf.get("/", REMOTE_ADDR="10.0.0.7", HTTP_X_REAL_IP="1.1.1.1")) == "10.0.0.7"


@override_settings(CLIENT_IP_HEADER="HTTP_X_REAL_IP")
def test_client_ip_nginx_default():
    from core.ratelimit import client_ip

    assert client_ip(rf.get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="105.112.4.4")) == "105.112.4.4"


# --------------------------------------------------------------- return-to links
@pytest.mark.parametrize("value,ok", [
    ("/qa/institutions/", True),
    ("/qa/?tab=1", True),
    ("//evil.example/", False),
    ("/\\evil.example/", False),
    ("https://evil.example/", False),
    ("javascript:alert(1)", False),
    ("", False),
])
def test_safe_next(value, ok):
    from core.urlsafe import safe_next

    req = rf.post("/", HTTP_HOST="localhost")
    assert safe_next(req, value, "/fallback/") == (value if ok else "/fallback/")


# --------------------------------------------------------------- /healthz
@pytest.mark.django_db
def test_healthz_answers_any_host_without_database(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        r = client.get("/healthz", HTTP_HOST="10.1.2.3:10000")
    assert r.status_code == 200 and r.content == b"ok" and r["Cache-Control"] == "no-store"


# --------------------------------------------------------------- resilient cache
def test_resilient_redis_cache_degrades_when_redis_is_down():
    from core.cache_backends import ResilientRedisCache

    cache = ResilientRedisCache("redis://127.0.0.1:1/0", {"OPTIONS": {"socket_connect_timeout": 0.2,
                                                                       "socket_timeout": 0.2}})
    assert cache.get("k", "miss") == "miss"
    assert cache.set("k", 1) is None
    assert cache.add("lock", 1) is False          # a lock is never "taken" while Redis is down
    assert cache.get_many(["a", "b"]) == {}
    assert cache.get_or_set("k", lambda: 5) == 5
    assert cache.incr("n") == 1
    assert cache.delete("k") is False


# --------------------------------------------------------------- director seats
@pytest.mark.django_db
def test_director_seat_memo_follows_saves():
    import org
    from accounts.models import User
    from qa_core.departments import clear_cache
    from staff.models import Department

    d = Department.objects.create(name="Seat test (v22)", code="seat-v22")
    u = User.objects.create_user(email="seat.v22@x.org", password="x-Very-long-2026!")
    assert not org.is_director(u, "seat-v22")
    d.director = u
    d.save()                                     # post_save clears the memo
    assert org.is_director(u, "seat-v22") and org.is_director(u, d) and org.is_director(u, d.pk)
    assert org.is_director(u)
    Department.objects.filter(pk=d.pk).update(director=None)
    assert not org.is_director(u, "seat-v22")    # outside a request: always read fresh
    from org.services import _SEATS, seat_request_finished, seat_request_started

    seat_request_started()                       # inside a request: one read, then remembered
    try:
        org.is_director(u, "seat-v22")
        assert getattr(_SEATS, "map", None) is not None
        clear_cache()
        assert getattr(_SEATS, "map", None) is None
    finally:
        seat_request_finished()
