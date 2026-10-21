"""Settings → QA write path (Master Plan §14): direct keys save at once,
dual-control keys go to a second officer, locked keys never change."""
import json

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory
from django.urls import reverse

from config.models import LOCKED_PERMISSION, ConfigEntry
from qa_core import settings_service
from qa_core.models import PendingAction

from .conftest import hx

LOCKED = "qa.payment.provider_confirmation_is_evidence_only"


def entry(key):
    return ConfigEntry.objects.get(key=key, scope="global")


# --------------------------------------------------------------------- new value types
@pytest.mark.django_db
def test_new_value_types_validate(qa_seed):
    ok = ConfigEntry(key="qa.test.decimal", value="1250000.50", value_type="decimal")
    ok.save()
    assert str(ok.parsed()) == "1250000.50"
    with pytest.raises(ValidationError):
        ConfigEntry(key="qa.test.bad_decimal", value="abc", value_type="decimal").save()
    with pytest.raises(ValidationError):
        ConfigEntry(key="qa.test.nan", value="NaN", value_type="decimal").save()
    ConfigEntry(key="qa.test.date", value="2026-10-16", value_type="date").save()
    with pytest.raises(ValidationError):
        ConfigEntry(key="qa.test.bad_date", value="16/10/2026", value_type="date").save()
    ConfigEntry(key="qa.test.formula", value=json.dumps({"expr": "a * 2", "vars": ["a"]}),
                value_type="formula").save()
    with pytest.raises(ValidationError, match="Formula rejected"):
        ConfigEntry(key="qa.test.evil", value=json.dumps({"expr": "().__class__", "vars": []}),
                    value_type="formula").save()
    with pytest.raises(ValidationError, match="Formula rejected"):
        ConfigEntry(key="qa.test.undeclared", value=json.dumps({"expr": "a + b", "vars": ["a"]}),
                    value_type="formula").save()


@pytest.mark.django_db
def test_secret_ban_still_applies_but_credential_policy_keys_are_allowed(qa_seed):
    assert entry("qa.credentials.link_valid_hours").parsed() == 72
    with pytest.raises(ValidationError):
        ConfigEntry(key="qa.credentials.api_token", value="x", value_type="str").save()


# --------------------------------------------------------------------- direct vs dual
def test_direct_key_saves_immediately(officer):
    outcome, obj = settings_service.save_setting(officer, entry("qa.stage.default_sla_hours"), "48")
    assert outcome == "saved"
    assert entry("qa.stage.default_sla_hours").value == "48"


def test_dual_key_creates_pending_action_and_value_waits(officer, director):
    fee = entry("qa.fee.programme_processing")
    old = fee.value
    new = json.dumps({"expr": "programmes * (2500000 if ownership == 'private' else 1250000)",
                      "vars": ["programmes", "ownership"], "returns": "decimal"})
    outcome, pa = settings_service.save_setting(officer, fee, new, reason="Annex A revision")
    assert outcome == "pending" and isinstance(pa, PendingAction)
    assert entry("qa.fee.programme_processing").value == old
    from qa_core import actions
    pa = actions.confirm(pa, director, remark="Approved")
    assert pa.status == "applied"
    assert entry("qa.fee.programme_processing").value == new


def test_dual_key_rejects_invalid_value_at_initiation(officer):
    with pytest.raises(ValidationError):
        settings_service.save_setting(officer, entry("qa.impersonation.max_minutes"), "thirty")
    with pytest.raises(ValidationError):
        settings_service.save_setting(officer, entry("qa.fee.programme_processing"),
                                      json.dumps({"expr": "__import__('os')", "vars": []}))
    assert not PendingAction.objects.exists()


def test_user_without_edit_permission_cannot_edit(qa_seed, make_user):
    plain = make_user("plain@t.local", role="officer", dept="dpap")
    allowed, why = settings_service.can_edit(plain, entry("qa.stage.default_sla_hours"))
    assert not allowed and "qa_core.edit_qa_settings" in why
    with pytest.raises(PermissionDenied):
        settings_service.save_setting(plain, entry("qa.stage.default_sla_hours"), "10")


# --------------------------------------------------------------------- through the pages
def test_officer_edits_fee_via_page_and_director_confirms_via_page(officer, director, client_for):
    """Interactive check 6, automated: qa.officer edits qa.fee.programme_processing
    -> PendingAction; qa.director confirms -> applied."""
    fee = entry("qa.fee.programme_processing")
    c = client_for(officer)
    r = c.post(reverse("qa:formula_save", args=[fee.pk]),
               {"expr": "programmes * (2200000 if ownership == 'private' else 1100000)", "reason": "memo"},
               **hx("formula-x"))
    assert r.status_code == 200
    assert "Sent for confirmation" in r["HX-Trigger"]
    pa = PendingAction.objects.get(status="pending")
    assert "2200000" not in entry("qa.fee.programme_processing").value
    # The initiator sees it but cannot decide it.
    r = c.post(reverse("qa:pending_confirm", args=[pa.pk]), **hx("qa-panel"))
    assert "different officer" in r.content.decode()
    pa.refresh_from_db()
    assert pa.status == "pending"
    # The director confirms.
    d = client_for(director)
    assert pa.reference in d.get(reverse("qa:pending_list")).content.decode()
    r = d.post(reverse("qa:pending_confirm", args=[pa.pk]), {"remark": "ok"}, **hx("qa-panel"))
    assert r.status_code == 200 and "confirmed and applied" in r["HX-Trigger"]
    assert "2200000" in entry("qa.fee.programme_processing").value


def test_generic_settings_page_cannot_bypass_qa_rules(officer, qa_seed, make_user):
    admin = make_user("cfg@t.local", superuser=True)
    c = Client(HTTP_HOST="localhost")
    c.force_login(admin)
    for key in ("qa.fee.programme_processing", "qa.stage.default_sla_hours", LOCKED):
        e = entry(key)
        before = e.value
        c.post(reverse("config:save", args=[e.pk]), {"value": "999"})
        assert entry(key).value == before, key
    page = c.get(reverse("config:settings")).content.decode()
    assert "is managed on Settings → QA" in page  # the refusal is explained
    for key in ("qa.fee.programme_processing", "qa.stage.default_sla_hours", LOCKED):
        assert reverse("config:save", args=[entry(key).pk]) not in page  # no edit form here
    assert "Open QA settings" in page


# --------------------------------------------------------------------- the locked key
@pytest.mark.django_db
def test_locked_key_is_seeded_locked(qa_seed):
    e = entry(LOCKED)
    assert e.value == "true" and e.value_type == "bool"
    assert e.editable_by == LOCKED_PERMISSION and e.is_locked and not e.dual_control


@pytest.mark.django_db
def test_locked_key_cannot_change_through_save(qa_seed):
    e = entry(LOCKED)
    for field, value in (("value", "false"), ("value_type", "str"), ("editable_by", ""),
                         ("dual_control", True), ("key", "qa.payment.other")):
        e = entry(LOCKED)
        setattr(e, field, value)
        with pytest.raises(ValidationError, match="fixed by policy"):
            e.save()
    with pytest.raises(ValidationError):
        entry(LOCKED).delete()
    with pytest.raises(ValidationError):
        ConfigEntry(key=LOCKED, scope="department:dpap", value="false", value_type="bool").save()
    assert entry(LOCKED).value == "true"


def test_locked_key_cannot_change_through_settings_page(officer, qa_seed, make_user, client_for):
    e = entry(LOCKED)
    root = make_user("root@t.local", superuser=True, dept="dpap")
    for user in (officer, root):
        c = client_for(user)
        assert c.get(reverse("qa:setting_edit", args=[e.pk])).status_code == 403
        r = c.post(reverse("qa:setting_save", args=[e.pk]), {"value": "false"}, **hx("x"))
        assert "Fixed by policy" in r.content.decode()
        with pytest.raises(PermissionDenied):
            settings_service.save_setting(user, e, "false")
    rows = client_for(officer).get(reverse("qa:settings_rules") + "?control=locked").content.decode()
    assert "Fixed by policy" in rows and f'hx-get="{reverse("qa:setting_edit", args=[e.pk])}"' not in rows
    assert entry(LOCKED).value == "true"


@pytest.mark.django_db
def test_locked_key_cannot_change_through_admin(qa_seed, make_user):
    root = make_user("root@t.local", superuser=True)
    model_admin = site._registry[ConfigEntry]
    request = RequestFactory().get("/admin/")
    request.user = root
    e = entry(LOCKED)
    readonly = set(model_admin.get_readonly_fields(request, e))
    assert {"value", "value_type", "editable_by", "dual_control", "key"} <= readonly
    assert not model_admin.has_delete_permission(request, e)
    c = Client(HTTP_HOST="localhost")
    c.force_login(root)
    c.post(f"/admin/config/configentry/{e.pk}/change/",
           {"key": LOCKED, "scope": "global", "value": "false", "value_type": "bool", "group": "x"})
    c.post(f"/admin/config/configentry/{e.pk}/delete/", {"post": "yes"})
    assert entry(LOCKED).value == "true"
