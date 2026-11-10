"""X23 / S-13 / S-07: staff signatures on My profile (ported from EE Hub)."""
import base64
import io
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone
from PIL import Image, ImageDraw

from documents import signature as SIG
from documents.models import StaffSignature

pytestmark = pytest.mark.django_db
PW = "Sig-test-2026!"
URL = "/accounts/profile/signature/"


def _user(email, rank=0, code=None):
    from accounts.models import Role, User

    role = None
    if code:
        role, _ = Role.objects.get_or_create(code=code, defaults={"name": code.title(), "rank": rank})
    u = User(email=email, first_name="Amina", last_name="Demo", user_type="staff", role=role, must_change_password=False)
    u.set_password(PW)
    u.save()
    return u


def _drawing():
    img = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.line([(40, 140), (120, 60), (200, 150), (300, 70), (420, 140), (540, 90)], fill=(20, 33, 74, 255), width=5)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _reauth(c):
    r = c.post(URL, {"section": "reauth", "password": PW})
    assert r.status_code == 302


def test_x23_signature_photo_and_draw():
    user = _user("sig.photo@demo.nbte.local")
    c = _client(user)
    html = c.get(URL).content.decode()
    assert "Confirm it’s you" in html and "data-sig-pad" not in html      # gate first (S-07)
    # changing without the password is refused
    r = c.post(URL, {"section": "sig_draw", "ink": "navy", "sig_data": _drawing()})
    assert not StaffSignature.objects.filter(user=user).exists()
    _reauth(c)
    html = c.get(URL).content.decode()
    assert "data-sig-pad" in html and "Upload a photo" in html
    # photo: upload -> check -> adjust -> accept
    c.post(URL, {"section": "sig_upload", "sensitivity": "50", "ink": "navy",
                 "sig_photo": SimpleUploadedFile("sig.jpg", SIG.demo_photo(), "image/jpeg")})
    pending = StaffSignature.objects.get(user=user)
    assert not pending.is_active and pending.source
    html = c.get(URL).content.decode()
    assert "Check your signature" in html and "data:image/png;base64," in html
    c.post(URL, {"section": "sig_adjust", "sensitivity": "70", "ink": "blue"})
    pending.refresh_from_db()
    assert pending.sensitivity == 70 and pending.ink == "blue"
    c.post(URL, {"section": "sig_accept"})
    pending.refresh_from_db()
    assert pending.is_active and pending.accepted_at and not pending.source and len(pending.sha256) == 64
    assert SIG.png_for(user)[:8] == b"\x89PNG\r\n\x1a\n"
    # draw replaces it; the old one is kept (retired)
    c.post(URL, {"section": "sig_draw", "ink": "black", "sig_data": _drawing()})
    assert StaffSignature.objects.filter(user=user, is_active=True).count() == 1
    pending.refresh_from_db()
    assert not pending.is_active and pending.retired_at
    drawn = SIG.active_for(user)
    img = Image.open(drawn.image.open("rb"))
    assert img.mode == "RGBA" and img.getpixel((0, 0))[3] == 0 and drawn.ink == "black"
    # remove
    c.post(URL, {"section": "sig_remove"})
    assert SIG.active_for(user) is None


def test_x23_change_emails_owner():
    user = _user("sig.mail@demo.nbte.local")
    c = _client(user)
    _reauth(c)
    c.post(URL, {"section": "sig_draw", "ink": "navy", "sig_data": _drawing()})
    from notifications.models import EmailLog

    assert EmailLog.objects.filter(subject__icontains="signature", recipient_text=user.email).exists()


def test_x23_required_from_director_rank(settings):
    staff = _user("officer.sig@demo.nbte.local", rank=20, code="officer")
    director = _user("director.sig@demo.nbte.local", rank=100, code="director")
    es = _user("es.sig@demo.nbte.local", rank=999, code="executive-secretary")
    assert not SIG.signature_required(staff)
    assert SIG.signature_required(director) and SIG.signature_required(es)
    SIG.require_signature_for_approval(staff)                       # no block below Director
    with pytest.raises(ValidationError) as exc:
        SIG.require_signature_for_approval(director)
    assert "My profile → Signature" in " ".join(exc.value.messages)
    SIG.save_demo(director)
    SIG.require_signature_for_approval(director)                    # unblocked once on file
    # banner: required (persistent) for the director without; reminder for others
    html = _client(es).get("/accounts/profile/").content.decode()
    assert "Directors and above must have a signature" in html
    html = _client(staff).get("/accounts/profile/").content.decode()
    assert "Add your signature" in html and "data-pf-dismissible" in html


def test_x23_signature_at_issue_time():
    user = _user("sig.time@demo.nbte.local", rank=100, code="director")
    first = SIG.save_demo(user, seed="one")
    first.accepted_at = timezone.now() - timedelta(days=2)
    first.save()
    issued_yesterday = timezone.now() - timedelta(days=1)
    second = SIG.save_drawn(user, _drawing(), user)
    assert second.is_active
    with first.image.open("rb") as fh:
        first_png = fh.read()
    assert SIG.png_for(user, at=issued_yesterday) == first_png       # yesterday's document keeps the old one
    assert SIG.png_for(user) != first_png
    assert SIG.data_uri_for(user).startswith("data:image/png;base64,")


def test_signature_images_not_routable():
    """S-13: no URL pattern serves signatures; the protected download view refuses them."""
    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                yield prefix + str(p.pattern), p
    for route, pattern in walk(get_resolver().url_patterns):
        name = (pattern.name or "").lower()
        assert not ("signature" in name and ("image" in name or "file" in name or "png" in name)), route
    user = _user("sig.route@demo.nbte.local", rank=100, code="director")
    sig = SIG.save_demo(user)
    admin = _user("root.sig@demo.nbte.local")
    admin.is_superuser = admin.is_staff = True
    admin.save()
    r = _client(admin).get(reverse("core:protected_download", args=["documents.staffsignature", sig.pk]))
    assert r.status_code == 404
    with pytest.raises(ValueError):
        sig.image.url                                                  # restricted storage has no URL


def test_x23_owner_only_and_impersonation_hides_image():
    user = _user("sig.owner@demo.nbte.local", rank=100, code="director")
    SIG.save_demo(user)
    admin = _user("root.imp@demo.nbte.local")
    admin.is_superuser = admin.is_staff = True
    admin.save()
    c = _client(admin)
    session = c.session
    from core.impersonation import IMPERSONATE_SESSION_KEY

    session[IMPERSONATE_SESSION_KEY] = user.pk
    session.save()
    html = c.get(URL).content.decode()
    assert "Only the account owner can see or change this signature" in html
    assert "data:image/png;base64," not in html
    r = c.post(URL, {"section": "sig_remove"})
    assert r.status_code in (302, 403)
    assert SIG.active_for(user) is not None
