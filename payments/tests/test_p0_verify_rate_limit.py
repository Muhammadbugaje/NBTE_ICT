"""Phase 0 (X8 slice, S-18): the public payment verify page is rate-limited
per IP (qa.verify.rate_limit_per_hour, default 60) and never indexed."""
import pytest


def _token(bill):
    """A pre-v2.1 signed slip token: the old /verify/<token>/ page keeps serving
    slips printed before the registry (new slips carry /v/<token>/, Phase F)."""
    from payments import services
    from payments.models import PaymentSlip

    slip = PaymentSlip.unscoped.get(obligation=bill, is_current=True)
    return services.verify_token("slip", bill.reference, slip.version)


def test_verify_rate_limited(bill, client, portal_host):
    token = _token(bill)
    ok = client.get(f"/verify/{token}/", HTTP_HOST=portal_host, REMOTE_ADDR="198.51.100.7")
    assert ok.status_code == 200
    assert "noindex" in ok["X-Robots-Tag"]
    assert b'name="robots" content="noindex' in ok.content
    assert ok["Referrer-Policy"] == "no-referrer"
    # 59 more (some with bad tokens: guessing counts too) use up the hour.
    for i in range(59):
        path = f"/verify/{token}/" if i % 2 else f"/verify/{token[:-3]}xyz/"
        assert client.get(path, HTTP_HOST=portal_host, REMOTE_ADDR="198.51.100.7").status_code in (200, 404)
    limited = client.get(f"/verify/{token}/", HTTP_HOST=portal_host, REMOTE_ADDR="198.51.100.7")
    assert limited.status_code == 429
    assert "noindex" in limited["X-Robots-Tag"] and limited["Retry-After"] == "3600"
    assert bill.reference.encode() not in limited.content
    # Another address is not affected.
    assert client.get(f"/verify/{token}/", HTTP_HOST=portal_host, REMOTE_ADDR="198.51.100.8").status_code == 200


def test_verify_rate_limit_is_a_setting(bill, client, portal_host):
    from django.core.cache import cache

    from config.models import ConfigEntry

    assert ConfigEntry.objects.filter(key="qa.verify.rate_limit_per_hour", value="60").exists()
    ConfigEntry.objects.filter(key="qa.verify.rate_limit_per_hour").update(value="2")
    cache.clear()  # config values are cached
    token = _token(bill)
    for _ in range(2):
        assert client.get(f"/verify/{token}/", HTTP_HOST=portal_host).status_code == 200
    assert client.get(f"/verify/{token}/", HTTP_HOST=portal_host).status_code == 429
