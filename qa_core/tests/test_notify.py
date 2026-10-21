"""Notification templates render in a sandbox (Master Plan §5.1)."""
import pytest

from qa_core import notify


def test_context_is_reduced_to_plain_values():
    class Secretive:
        password = "hunter2"

        def __str__(self):
            return "Officer"

    ctx = notify.safe_context({"user": Secretive(), "n": 3, "items": [1, {"a": "b"}], "_private": "x"})
    assert ctx == {"user": "Officer", "n": 3, "items": [1, {"a": "b"}]}
    assert notify.render_string("{{ user.password }}|{{ user }}", {"user": Secretive()}) == "|Officer"


@pytest.mark.parametrize("source", [
    "{% include 'base.html' %}",
    "{% extends 'base.html' %}",
    "{% load static %}{% static 'x' %}",
    "{% if %}",
])
def test_file_access_and_extra_libraries_are_refused(source):
    with pytest.raises(notify.TemplateRenderError):
        notify.render_string(source, {})


def test_values_are_autoescaped():
    assert notify.render_string("{{ v }}", {"v": "<b>"}) == "&lt;b&gt;"


@pytest.mark.django_db
def test_dispatch_never_raises_and_respects_inactive_templates(officer, director):
    from notifications.models import Notification
    from qa_core.models import NotificationTemplate

    sent = notify.dispatch("pending_action.decided", {"reference": "PA-1", "action": "x", "target": "y",
                                                      "status": "Applied"}, {"initiator": [officer]})
    assert sent == 1 and Notification.objects.filter(recipient=officer, category="qa").count() == 1
    NotificationTemplate.objects.filter(code="pending_action.decided").update(is_active=False)
    assert notify.dispatch("pending_action.decided", {}, {"initiator": [officer]}) == 0
    assert notify.dispatch("no.such.event", {}, {"initiator": [officer]}) == 0
    assert notify.dispatch("pending_action.decided", {}, None) == 0  # bad input is swallowed and logged
