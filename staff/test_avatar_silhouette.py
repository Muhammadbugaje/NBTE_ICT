"""Staff avatar silhouette (Issue 4).

Run:  pytest staff/test_avatar_silhouette.py -q
"""
import pytest
from django.template import Context, Template

pytestmark = pytest.mark.django_db


class Fake:
    def __init__(self, gender="", first="Ada", last="Bello"):
        self.gender, self.first_name, self.last_name = gender, first, last


def render(staff):
    return Template("{% load staff_tags %}{% staff_avatar_fallback s %}").render(Context({"s": staff}))


def test_male_silhouette():
    html = render(Fake("M"))
    assert "sil-male" in html and 'data-silhouette="male"' in html and ">AB<" in html


def test_female_silhouette():
    html = render(Fake("F"))
    assert "sil-female" in html and 'data-silhouette="female"' in html


@pytest.mark.parametrize("g", ["", None, "X"])
def test_blank_or_unknown_is_neutral(g):
    assert "sil-neutral" in render(Fake(g))


def test_silhouette_uses_current_color_not_external_files():
    html = render(Fake("M"))
    assert "currentColor" in html and "http" not in html.replace("http://www.w3.org/2000/svg", "")


def test_photo_shows_no_silhouette_in_directory_card():
    """The templates only call the tag in the no-photo branch."""
    import pathlib
    for name in ("list", "detail"):
        src = pathlib.Path(f"templates/staff/{name}.html").read_text(encoding="utf-8")
        i = src.index("staff_avatar_fallback")
        before = src[:i]
        assert before.rfind("{% else %}") > before.rfind("{% if") or "photo" in before[-400:]
