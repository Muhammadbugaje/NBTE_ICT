"""Phase 8: a fresh database gets everything from one command —
``seed_startup --with-demo`` — and a second run changes nothing."""
import io

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_fresh_database_full_seed_is_complete_and_idempotent(settings):
    from django.contrib.auth.models import Group

    from accounts.models import Role, User
    from complaints.models import Complaint, ComplaintLayer, KnowledgeArticle
    from institutions.models import Institution
    from qa_core.models import DemoTag, FlowDefinition

    settings.DEBUG = True
    out = io.StringIO()
    call_command("seed_startup", "--with-demo", stdout=out, stderr=io.StringIO())
    text = out.getvalue()
    for code in ("executive-secretary", "director", "qa-finance-director", "ict-qa-monitor", "qa-settings-admin",
                 "inst-admin", "resource-person"):
        assert Role.objects.filter(code=code).exists(), code
    for name in ("QA Desk", "Complaints Handlers", "Complaints Admin", "Finance – Approvals"):
        assert Group.objects.filter(name=name).exists(), name
    assert Group.objects.get(name="Complaints Handlers").permissions.filter(codename="handle_complaint").exists()
    assert FlowDefinition.objects.filter(code="complaint", published_at__isnull=False).exists()
    assert ComplaintLayer.objects.count() == 10 and KnowledgeArticle.objects.count() == 5
    assert Institution.unscoped.filter(code__in=("DMP", "DCH")).count() == 2
    assert Complaint.unscoped.count() == 3
    states = set(Complaint.unscoped.values_list("status", flat=True))
    assert states == {"received", "in_progress"}
    assert "Demo complaints: 3 open" in text
    assert User.objects.filter(email="admin@demo-poly.local").exists()
    tagged = DemoTag.objects.count()

    again = io.StringIO()
    call_command("seed_startup", "--with-demo", stdout=again, stderr=io.StringIO())
    assert "already up to date" in again.getvalue()
    assert DemoTag.objects.count() == tagged and Complaint.unscoped.count() == 3

    listing = io.StringIO()
    call_command("set_demo_passwords", "--list-only", stdout=listing)
    assert "admin@demo-poly.local" in listing.getvalue() and "qa.finance@demo.nbte.local" in listing.getvalue()


@pytest.mark.django_db
def test_set_demo_passwords_is_refused_on_a_live_server(settings):
    from django.core.management.base import CommandError

    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("set_demo_passwords", stdout=io.StringIO())


@pytest.mark.django_db
def test_sla_sweep_command_and_celery_task(qa_seed):
    from complaints import tasks

    out = io.StringIO()
    call_command("complaints_sla_sweep", "--at", "2030-01-07 10:00", stdout=out)
    assert "Complaint SLA sweep" in out.getvalue()
    assert tasks.sla_sweep(force=True) == {"reminded": 0, "breached": 0, "escalated": 0, "auto_closed": 0}
    assert tasks.sla_sweep() == {"skipped": True}  # within qa.complaints.sla_poll_minutes
