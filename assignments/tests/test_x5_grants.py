"""v2.1 X5: the AccessGrant engine (grant, revoke, sweep, can_see, audit)."""
import datetime

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def test_x5_grant_is_idempotent_and_audited(org_c, ticket):
    from assignments import grants
    from assignments.access import can_see
    from core.models import AuditLogEntry

    officer, printer = org_c["officer"], org_c["printer"]
    assert not can_see(officer, printer, "asset")
    g1 = grants.grant(officer, printer, "asset", reason="test", source=ticket)
    g2 = grants.grant(officer, printer, "asset", reason="test", source=ticket)
    assert g1.pk == g2.pk
    assert can_see(officer, printer, "asset")
    grants.revoke(g1, by=org_c["director"], reason="manual")
    assert not can_see(officer, printer, "asset")
    acts = list(AuditLogEntry.objects.filter(model_label="assignments.AccessGrant").values_list("action", flat=True))
    assert acts.count("grant_created") == 1 and acts.count("grant_revoked") == 1


def test_x5_unknown_scope_refused(org_c):
    from assignments import grants
    with pytest.raises(ValueError):
        grants.grant(org_c["officer"], org_c["printer"], "everything", reason="x")


def test_x5_sweep_expires_and_extends(org_c, ticket):
    from assignments import grants
    from assignments.models import AccessGrant
    from tickets.models import Ticket

    now = timezone.now()
    # expired grant without source
    g_exp = grants.grant(org_c["officer2"], org_c["printer"], "asset", reason="old",
                         expires_at=now - datetime.timedelta(minutes=1))
    # grant of open work about to expire: extended
    g_open = grants.grant(org_c["officer"], org_c["printer"], "asset", reason="t", source=ticket,
                          expires_at=now + datetime.timedelta(minutes=5))
    Ticket.objects.filter(pk=ticket.pk).update(resolution_due_at=now + datetime.timedelta(days=2))
    out = grants.sweep(now=now)
    g_exp.refresh_from_db(); g_open.refresh_from_db()
    assert g_exp.revoke_reason == AccessGrant.R_EXPIRED
    assert out["expired"] >= 1 and out["extended"] == 1
    assert g_open.expires_at > now + datetime.timedelta(days=2)
    assert g_open.expires_at <= g_open.granted_at + datetime.timedelta(days=grants.max_days())
    # source closes without the consumer revoking: the sweep closes the grant
    Ticket.objects.filter(pk=ticket.pk).update(state=Ticket.CANCELLED)
    out = grants.sweep(now=now)
    g_open.refresh_from_db()
    assert out["closed"] == 1 and g_open.revoke_reason == AccessGrant.R_COMPLETED


def test_x5_sweep_reports_long_grants(org_c):
    from assignments import grants
    from assignments.models import AccessGrant
    from notifications.models import Notification

    g = grants.grant(org_c["officer"], org_c["printer"], "asset", reason="long",
                     expires_at=timezone.now() + datetime.timedelta(days=60))
    AccessGrant.objects.filter(pk=g.pk).update(granted_at=timezone.now() - datetime.timedelta(days=40))
    out = grants.sweep()
    assert out["reported"] == 1
    assert Notification.objects.filter(recipient=org_c["director"], title__contains="older than").exists()
    assert grants.sweep()["reported"] == 0     # once a week per grant


def test_x5_access_event_append_only(org_c):
    from assignments.access import record_event
    from assignments.models import AccessEvent
    ev = record_event(org_c["officer"], org_c["printer"], AccessEvent.DOWNLOAD)
    with pytest.raises(ValueError):
        ev.save()
    with pytest.raises(ValueError):
        ev.delete()


def test_x5_sweep_command(org_c):
    from io import StringIO
    from django.core.management import call_command
    out = StringIO()
    call_command("sweep_access_grants", "--no-report", stdout=out)
    assert "Access grants:" in out.getvalue()
