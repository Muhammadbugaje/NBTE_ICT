"""Two-step confirmation hard rules (Master Plan §7)."""
import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from config.models import ConfigEntry
from qa_core import actions
from qa_core.models import ActionPolicy, PendingAction


def _setting(key="qa.fee.programme_processing"):
    return ConfigEntry.objects.get(key=key, scope="global")


def _start(user, key="qa.impersonation.max_minutes", value="20"):
    return actions.initiate("config.update_setting", actor=user, target=_setting(key),
                            payload={"value": value}, reason="test")


@pytest.fixture
def superuser(qa_seed, make_user):
    return make_user("root@t.local", superuser=True, dept="dpap", role="director")


def test_initiate_creates_pending_with_snapshot(officer):
    pa = _start(officer)
    assert pa.status == PendingAction.PENDING
    assert pa.reference.startswith("PA-")
    assert pa.before_snapshot == {"value": "30", "value_type": "int"}
    assert pa.target_version
    assert pa.scope_department.code == "dpap"
    assert pa.expires_at > timezone.now() + datetime.timedelta(hours=71)
    assert _setting("qa.impersonation.max_minutes").value == "30"  # nothing applied yet


def test_initiator_cannot_confirm(officer):
    pa = _start(officer)
    with pytest.raises(PermissionDenied, match="different officer"):
        actions.confirm(pa, officer)


def test_superuser_cannot_confirm_own_initiation(superuser):
    pa = _start(superuser)
    ok, reason = actions.can_decide(pa, superuser)
    assert not ok and "different officer" in reason
    with pytest.raises(PermissionDenied):
        actions.confirm(pa, superuser)


def test_second_officer_confirms_and_applies(officer, director):
    pa = _start(officer)
    pa = actions.confirm(pa, director, remark="ok")
    assert pa.status == PendingAction.APPLIED
    assert pa.decided_by == director
    assert _setting("qa.impersonation.max_minutes").value == "20"


def test_confirmer_needs_permission_and_rank(officer, make_user):
    peer = make_user("peer@t.local", role="officer", dept="dpap", groups=["QA Settings Admin"])
    pa = _start(officer)
    ok, reason = actions.can_decide(pa, peer)
    assert not ok and "permission" in reason


def test_other_department_cannot_confirm_unless_any_department(officer, make_user):
    vtsd_director = make_user("vd@t.local", role="director", dept="vtsd")
    pa = _start(officer)
    ok, reason = actions.can_decide(pa, vtsd_director)
    assert not ok and "another department" in reason
    # Q-14: holders of qa_core.confirm_any_department (here via the seeded
    # "QA Senior Confirmers" group) may confirm across departments.
    cross = make_user("vd2@t.local", role="director", dept="vtsd", groups=["QA Senior Confirmers"])
    assert actions.can_decide(pa, cross)[0]
    # The ES has confirm_any_department, but still needs the action's own
    # confirm permission: the department rule is lifted, nothing else.
    es = make_user("es@t.local", role="executive-secretary", dept="es-office")
    ok, reason = actions.can_decide(pa, es)
    assert not ok and "permission" in reason


def test_impersonating_confirmer_refused(officer, director):
    pa = _start(officer)
    with pytest.raises(PermissionDenied, match="impersonating"):
        actions.confirm(pa, director, impersonating=True)
    with pytest.raises(PermissionDenied):
        _ = actions.initiate("config.update_setting", actor=officer, target=_setting(),
                             payload={"value": "{}"}, impersonating=True)


def test_external_users_can_never_decide(officer, make_user, make_institution):
    inst = make_institution("ABC")
    inst_user = make_user("i@abc.local", user_type="institution", institution=inst, role="inst-admin")
    pa = _start(officer)
    assert not actions.can_decide(pa, inst_user)[0]
    assert not actions.visible_to(inst_user).exists()


def test_expiry(officer, director):
    pa = _start(officer)
    PendingAction.objects.filter(pk=pa.pk).update(expires_at=timezone.now() - datetime.timedelta(minutes=1))
    pa.refresh_from_db()
    ok, reason = actions.can_decide(pa, director)
    assert not ok and "expired" in reason
    assert actions.expire_overdue() == 1
    pa.refresh_from_db()
    assert pa.status == PendingAction.EXPIRED
    with pytest.raises(PermissionDenied):
        actions.confirm(pa, director)
    assert actions.expire_overdue() == 0


def test_expiry_hours_come_from_setting(officer):
    entry = _setting("qa.settings.pending_action_expiry_hours")
    ConfigEntry.objects.filter(pk=entry.pk).update(value="1")  # system write for the test
    from django.core.cache import cache
    cache.clear()
    pa = _start(officer)
    assert pa.expires_at < timezone.now() + datetime.timedelta(hours=1, minutes=1)


def test_stale_target_fails_and_is_never_applied(officer, director):
    pa = _start(officer, value="20")
    entry = _setting("qa.impersonation.max_minutes")
    # Someone changes the target after initiation (a newer, confirmed change).
    entry.value = "25"
    entry.save()
    pa = actions.confirm(pa, director)
    assert pa.status == PendingAction.FAILED
    assert "Stale" in pa.failure_detail
    assert _setting("qa.impersonation.max_minutes").value == "25"


def test_one_open_action_per_target(officer):
    _start(officer, value="20")
    with pytest.raises(ValidationError, match="already waiting"):
        _start(officer, value="21")


def test_reject_needs_remark_and_withdraw_only_by_initiator(officer, director):
    pa = _start(officer)
    with pytest.raises(ValidationError):
        actions.reject(pa, director, remark="")
    with pytest.raises(PermissionDenied):
        actions.withdraw(pa, director)
    pa = actions.withdraw(pa, officer)
    assert pa.status == PendingAction.WITHDRAWN
    pa2 = _start(officer)
    pa2 = actions.reject(pa2, director, remark="Not approved by memo")
    assert pa2.status == PendingAction.REJECTED
    assert _setting("qa.impersonation.max_minutes").value == "30"


def test_disabled_policy_cannot_start(officer):
    ActionPolicy.objects.filter(action_code="config.update_setting").update(is_enabled=False)
    with pytest.raises(ValidationError, match="not enabled"):
        _start(officer)


def test_decisions_are_audited(officer, director):
    from core.models import AuditLogEntry

    pa = _start(officer)
    actions.confirm(pa, director)
    notes = list(AuditLogEntry.objects.filter(model_label="qa_core.PendingAction", object_pk=str(pa.pk))
                 .values_list("note", flat=True))
    assert any("initiated" in n for n in notes)
    assert "confirmed" in notes and "applied" in notes


def test_confirmers_are_notified(officer, director):
    from notifications.models import Notification

    _start(officer)
    assert Notification.objects.filter(recipient=director, category="qa").exists()
    assert not Notification.objects.filter(recipient=officer, category="qa").exists()
