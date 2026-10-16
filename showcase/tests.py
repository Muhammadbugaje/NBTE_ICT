"""Tests for the showcase app (UI overhaul Phase 4)."""
from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import User
from showcase.models import ShowcaseImage
from showcase.services import active_images

pytestmark = pytest.mark.django_db


def _png():
    buf = BytesIO()
    Image.new("RGB", (40, 20), (31, 107, 62)).save(buf, "PNG")
    return SimpleUploadedFile("pic.png", buf.getvalue(), content_type="image/png")


def make(placements=("hero",), **kw):
    img = ShowcaseImage(alt_text="Alt", **kw)
    img.set_placements(placements)
    img.image = _png()
    img.save()
    return img


def test_image_is_stored_outside_public_media(settings):
    img = make()
    assert (settings.PROTECTED_MEDIA_ROOT / img.image.name).exists()
    assert not (settings.MEDIA_ROOT / img.image.name).exists()
    assert img.image.name.startswith("showcase/")


def test_placements_round_trip_and_display():
    img = make(("hero", "signin_background", "nonsense"))
    assert img.placement_list == ["signin_background", "hero"]
    assert "Landing page hero" in img.get_placements_display()


def test_active_images_filters_placement_activity_and_window():
    now = timezone.now()
    shown = make(("hero",), order=2)
    first = make(("hero", "signin_background"), order=1)
    make(("signin_background",))                                   # other placement
    make(("hero",), is_active=False)                               # inactive
    make(("hero",), start_at=now + timedelta(days=1))              # not started
    make(("hero",), end_at=now - timedelta(minutes=1))             # finished
    assert list(active_images("hero")) == [first, shown]
    assert list(active_images("unknown")) == []


def test_template_tag_returns_list():
    make(("signin_background",))
    out = Template('{% load showcase_tags %}{% showcase_images "signin_background" as imgs %}{{ imgs|length }}').render(Context())
    assert out == "1"


def test_image_view_is_public_for_live_images(client):
    img = make()
    resp = client.get(img.get_image_url())
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
    assert resp["X-Content-Type-Options"] == "nosniff"


def test_image_view_hides_inactive_images_except_from_staff(client):
    img = make(is_active=False)
    assert client.get(reverse("showcase:image", args=[img.pk])).status_code == 404
    staff = User.objects.create_user("st@x.org", "Pass-word-123", is_staff=True, must_change_password=False)
    client.force_login(staff)
    assert client.get(reverse("showcase:image", args=[img.pk])).status_code == 200


def test_seed_command_creates_placeholders():
    call_command("showcase_seed", "--count", "2", "--width", "320", "--height", "180")
    assert ShowcaseImage.objects.count() == 2
    assert active_images("signin_background").count() == 2
    assert active_images("dashboard_banner").count() == 1
    call_command("showcase_seed", "--count", "1", "--width", "320", "--height", "180")
    assert ShowcaseImage.objects.count() == 1               # earlier placeholders replaced


def test_signin_page_renders_carousel_or_fallback(client):
    resp = client.get(reverse("accounts:login"))
    assert b"carousel-fallback" in resp.content            # no images yet: brand gradient
    make(("signin_background",))
    make(("signin_background",))
    resp = client.get(reverse("accounts:login"))
    html = resp.content.decode()
    assert html.count("carousel-slide") == 2 and "carousel-dots" in html
    assert "carousel-fallback" not in html


def test_landing_hero_uses_hero_images(client):
    assert b"has-carousel" not in client.get("/").content
    make(("hero",))
    assert b"has-carousel" in client.get("/").content
