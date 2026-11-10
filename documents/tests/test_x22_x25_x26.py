"""X22 (slips/receipts in the registry, legacy tokens), X25 (provisional +
official copy), X26 (Scan, /v/<token>/, public fields, rate limit,
Permissions-Policy)."""
import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from documents import issue as issuing
from documents.models import DocumentOpen, IssuedDocument, OfficialCopy, VerificationLog

from .conftest import pdf_text, portal_client, staff_client

pytestmark = pytest.mark.django_db
PORTAL = settings.QA_PORTAL_HOST.split(":")[0]


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _letter(light, **kw):
    return issuing.approval_recorded("institution.approved", light.inst, actor=light.director,
                                     institution=light.inst, context={"department": "Polytechnic Programmes"}, **kw)


# --------------------------------------------------------------------------- X22
def test_x22_slip_and_receipt_qr_registry(claimed, p2):
    """New slips carry /v/<token>/ + short code; the Director of Finance's
    confirmation issues the signed receipt (it replaces the advice PDF), and
    both verify. Slips printed before v2.1 (signed tokens) still verify."""
    from documents import payments as doc_payments
    from payments import services
    from payments.models import PaymentAdvice, PaymentSlip
    from payments.tests.test_payments import _open_item

    slip = PaymentSlip.unscoped.get(obligation=claimed, is_current=True)
    slip_doc = doc_payments.slip_document(claimed, slip.version)
    assert slip_doc is not None and slip_doc.kind == "payment_slip"
    assert slip.qr_payload == issuing.verify_url(slip_doc.token)
    assert slip_doc.file.name == slip.file.name and slip_doc.sha256 == slip.sha256
    with slip.file.open("rb") as fh:
        assert slip_doc.short_code in pdf_text(fh.read())

    item = _open_item(claimed)
    services.final_decision(item, user=p2.fin_director, decision="confirmed")
    advice = PaymentAdvice.unscoped.get(obligation=claimed)
    receipt = doc_payments.receipt_for(advice)
    assert receipt.kind == "receipt" and receipt.reference == advice.reference and not receipt.provisional
    assert receipt.signatories[0]["user_id"] == p2.fin_director.pk and receipt.signatories[0]["signature_id"]
    assert advice.qr_payload == issuing.verify_url(receipt.token)
    assert receipt.legacy_token  # the advice's original signed token still resolves
    with advice.file.open("rb") as fh:
        text = pdf_text(fh.read())
    assert "OFFICIAL RECEIPT" in text.upper() and receipt.short_code in text
    assert doc_payments.issue_receipt(claimed) == receipt  # idempotent, takes the obligation too

    c = Client(HTTP_HOST=PORTAL)
    for raw in (slip.qr_payload, receipt.short_code, f"https://{PORTAL}/verify/{receipt.legacy_token}/"):
        r = c.post("/scan/lookup/", {"raw": raw, "via": "qr_camera"})
        assert r.status_code == 200 and b"VALID" in r.content, raw

    # A slip printed before v2.1: its signed token is unknown to the registry ->
    # Scan falls back to the payment records; the old page still works.
    legacy = services.verify_token("slip", claimed.reference, slip.version)
    r = c.post("/scan/lookup/", {"raw": f"https://{PORTAL}/verify/{legacy}/", "via": "image_upload"})
    assert r.status_code == 200 and b"VALID" in r.content and claimed.reference.encode() in r.content
    assert c.get(f"/verify/{legacy}/").status_code == 200

    # Back-fill (migration 0003) registers old slips under their legacy token.
    import importlib

    from django.apps import apps

    IssuedDocument.unscoped.filter(kind="payment_slip").delete()
    slip.qr_payload = f"https://{PORTAL}/verify/{legacy}/"
    slip.save(update_fields=["qr_payload"])
    importlib.import_module("documents.migrations.0003_seed_rules_and_backfill").forwards(apps, None)
    back = IssuedDocument.unscoped.get(legacy_token=legacy)
    assert back.kind == "payment_slip" and back.institution_id == claimed.institution_id
    r = c.post("/scan/lookup/", {"raw": f"https://{PORTAL}/verify/{legacy}/", "via": "qr_camera"})
    assert back.short_code.encode() in r.content


# --------------------------------------------------------------------------- X25
def test_x25_provisional_and_official_copy(light, django_capture_on_commit_callbacks):
    from notifications.models import Notification

    with django_capture_on_commit_callbacks(execute=True):
        doc = _letter(light)
    assert doc.provisional
    with doc.file.open("rb") as fh:
        assert "PROVISIONAL" in pdf_text(fh.read())
    # the institution is told (in-app) with the pop-up text
    assert Notification.objects.filter(recipient=light.inst_user, title__startswith="Provisional document").exists()

    c = portal_client(light.inst_user)
    letters = c.get("/letters/")
    assert letters.status_code == 200 and doc.reference.encode() in letters.content
    first = c.get(f"/letters/documents/{doc.pk}/")
    assert first.status_code == 200 and b"data-provisional-popup" in first.content
    assert issuing.popup_text().encode() in first.content
    assert c.get(f"/letters/documents/{doc.pk}/").content.count(b"<dialog class=\"sv-dialog\"") == 1  # until acknowledged
    assert c.post(f"/letters/documents/{doc.pk}/understood/").status_code == 302
    assert DocumentOpen.objects.get(document=doc, user=light.inst_user).acknowledged_at
    assert b"data-provisional-popup" not in c.get(f"/letters/documents/{doc.pk}/").content
    # another institution cannot open it
    from accounts.models import User
    from institutions.models import Institution

    other = Institution.unscoped.create(code="OTHR", name="Other Poly", handling_department=light.dept,
                                        status="approved")
    stranger = User.objects.create(email="other@demo-poly.local", user_type="institution", institution=other,
                                   must_change_password=False)
    assert portal_client(stranger).get(f"/letters/documents/{doc.pk}/").status_code == 404

    # staff upload the signed official copy -> superseding the provisional status
    s = staff_client(light.keeper)
    bad = s.post(f"/qa/documents/{doc.pk}/official/", {"file": SimpleUploadedFile("x.pdf", b"not a pdf")})
    assert bad.status_code == 302 and not OfficialCopy.unscoped.filter(document=doc).exists()
    pdf = SimpleUploadedFile("signed.pdf", b"%PDF-1.4\n%signed\n", content_type="application/pdf")
    with django_capture_on_commit_callbacks(execute=True):
        assert s.post(f"/qa/documents/{doc.pk}/official/", {"file": pdf, "note": "Signed by ES"}).status_code == 302
    copy = OfficialCopy.unscoped.get(document=doc)
    assert Notification.objects.filter(recipient=light.inst_user, title__startswith="Your official").exists()
    page = c.get(f"/letters/documents/{doc.pk}/").content
    assert b"Official copy issued" in page and copy.portal_download_url().encode() in page
    assert c.get(copy.portal_download_url()).status_code == 200
    r = Client(HTTP_HOST=PORTAL).post("/scan/lookup/", {"raw": doc.short_code})
    assert b"Official copy issued" in r.content and b"Provisional" not in r.content
    # an officer without the permission cannot upload
    assert staff_client(light.officer).post(f"/qa/documents/{doc.pk}/official/", {"file": pdf}).status_code == 403


# --------------------------------------------------------------------------- X26
def test_x26_scan_pages_nav_and_permissions_policy(light):
    s = staff_client(light.keeper)
    page = s.get("/scan/")
    assert page.status_code == 200 and b"data-scan" in page.content and b"jsQR-1.4.0.js" in page.content
    assert "camera=(self)" in page["Permissions-Policy"]
    assert "camera=()" in s.get("/home/")["Permissions-Policy"]
    register = s.get("/qa/documents/")
    assert register.status_code == 200 and b"Issued documents" in register.content
    assert staff_client(light.officer).get("/qa/documents/").status_code == 403
    # visitors on the staff host are sent to the public Scan page (portal host)
    anon = Client().get("/scan/")
    assert anon.status_code == 302 and anon["Location"].endswith("/scan/")
    public = Client(HTTP_HOST=PORTAL).get("/scan/")
    assert public.status_code == 200 and b"Scan and verify" in public.content
    assert "camera=(self)" in public["Permissions-Policy"] and "noindex" in public["X-Robots-Tag"]
    portal = portal_client(light.inst_user).get("/scan/")
    assert portal.status_code == 200 and b'href="/scan/"' in portal.content  # nav item


def test_x26_scan_verify_rate_limited(light):
    from config.models import ConfigEntry

    doc = _letter(light)
    c = Client(HTTP_HOST=PORTAL, REMOTE_ADDR="198.51.100.20")
    # the QR link
    r = c.get(f"/v/{doc.token}/")
    assert r.status_code == 200 and b"VALID" in r.content and doc.reference.encode() in r.content
    assert r["Referrer-Policy"] == "no-referrer"
    # typed code, any spacing/case
    r = c.post("/scan/lookup/", {"raw": doc.short_code.lower().replace("-", " "), "via": "code"},
               HTTP_HX_REQUEST="true")
    assert b"VALID" in r.content
    # uniform not-found (no enumeration hints), logged hashed
    r = c.post("/scan/lookup/", {"raw": "NBTE-AAAA-AAAA"}, HTTP_HX_REQUEST="true")
    assert b"NOT FOUND" in r.content
    log = VerificationLog.objects.filter(result="not_found").latest("at")
    assert log.token_or_code.startswith("sha256:") and "AAAA" not in log.token_or_code
    assert VerificationLog.objects.filter(document=doc, via="link").exists()
    doc.refresh_from_db()
    assert doc.verify_count == 2
    # the limit is a setting; then 429 and nothing about the document
    ConfigEntry.objects.update_or_create(key="qa.verify.rate_limit_per_hour", scope="global",
                                         defaults={"value": "4", "value_type": "int"})
    cache.clear()
    for _ in range(4):
        assert c.post("/scan/lookup/", {"raw": doc.short_code}).status_code == 200
    limited = c.post("/scan/lookup/", {"raw": doc.short_code})
    assert limited.status_code == 429 and doc.reference.encode() not in limited.content
    assert limited["Retry-After"] == "3600"
    assert c.get(f"/v/{doc.token}/").status_code == 429
    assert Client(HTTP_HOST=PORTAL, REMOTE_ADDR="198.51.100.21").get(f"/v/{doc.token}/").status_code == 200
    assert VerificationLog.objects.filter(result="rate_limited").exists()


def test_x26_public_fields_only_and_revoked(light):
    from notifications.models import Notification

    doc = _letter(light)
    anon = Client(HTTP_HOST=PORTAL).post("/scan/lookup/", {"raw": doc.short_code}).content.decode()
    assert light.director.get_full_name() in anon            # signatory name + designation are public
    assert light.director.email not in anon and "data:image/png" not in anon  # no e-mail, no signature image
    assert "Signature on file" in anon
    staff = staff_client(light.keeper).post("/scan/lookup/", {"raw": doc.short_code}).content.decode()
    assert "data:image/png" in staff and "Open in the register" in staff  # staff see thumbnails (inline only)
    # revoke with a reason (permission + reason required)
    s = staff_client(light.keeper)
    assert staff_client(light.officer).post(f"/qa/documents/{doc.pk}/revoke/", {"reason": "Issued in error"}
                                            ).status_code == 403
    s.post(f"/qa/documents/{doc.pk}/revoke/", {"reason": "no"})
    doc.refresh_from_db()
    assert doc.status == "valid"
    s.post(f"/qa/documents/{doc.pk}/revoke/", {"reason": "Issued to the wrong institution"})
    doc.refresh_from_db()
    assert doc.status == "revoked" and doc.revoked_by_id == light.keeper.pk
    r = Client(HTTP_HOST=PORTAL).get(f"/v/{doc.token}/")
    assert b"REVOKED" in r.content and b"wrong institution" not in r.content  # reason is internal
    # scanning a revoked document alerts the holders of the verification log (ICT)
    assert Notification.objects.filter(recipient=light.keeper, title__startswith="Revoked document scanned").exists()
    detail = s.get(f"/qa/documents/{doc.pk}/").content.decode()
    assert "Verification history" in detail and "wrong institution" in detail


def test_x26_extract_accepts_urls_codes_and_tokens():
    from documents import verify

    assert verify.extract("https://qa.nbte.gov.ng/v/abcdefghijklmnopqrstuvwx/") == ("token", "abcdefghijklmnopqrstuvwx")
    assert verify.extract(" nbte 7k2p 9qxm ") == ("code", "NBTE-7K2P-9QXM")
    assert verify.extract("https://qa.localhost:8000/verify/eyJrIjoic2xpcCJ9:1abc:xyz/")[0] == "legacy"
    assert verify.extract("NBTE-0000-1111")[0] != "code"  # 0 and 1 are not in the alphabet
    assert verify.extract("")[0] == "empty"
