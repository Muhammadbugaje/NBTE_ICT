"""seed_startup (Master Plan §15): idempotent, demo tagged, reset exact."""
import io
import re

import pytest
from django.core.management import CommandError, call_command

from accounts.models import Role, User
from config.models import ConfigEntry
from qa_core.models import ActionPolicy, DemoTag, FlowDefinition, FlowTransition, NotificationTemplate, ReferenceFormat
from qa_core.settings_catalogue import SETTING_KEYS

DEMO_EMAILS = {
    "qa.officer@demo.nbte.local": ("staff", "officer", "dpap"),
    "qa.director@demo.nbte.local": ("staff", "director", "dpap"),
    "qa.finance@demo.nbte.local": ("staff", "qa-finance-officer", "finance"),
    "admin@demo-poly.local": ("institution", "inst-admin", None),
    "resource@demo.nbte.local": ("resource_person", "resource-person", None),
}


def run(*args):
    out = io.StringIO()
    call_command("seed_startup", "--skip-base-seeders", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def base(db):
    call_command("seed_roles", "--sync", stdout=io.StringIO())


def test_first_run_creates_everything(base):
    out = run()
    assert "seed_startup complete" in out
    assert set(SETTING_KEYS) <= set(ConfigEntry.objects.values_list("key", flat=True))
    for key in ("qa.settings.pending_action_expiry_hours", "qa.credentials.link_valid_hours",
                "qa.impersonation.max_minutes", "qa.portal.self_registration_enabled",
                "qa.stage.default_sla_hours", "qa.stage.reminder_at_percent",
                "qa.institution.admin_required_department", "qa.resource_person.max_visits_per_year",
                "qa.ui.max_show_all_rows", "qa.ui.request_timeout_seconds",
                "qa.payment.finance_department", "qa.payment.receipt_required_for_provider_payments",
                "qa.payment.provider_confirmation_is_evidence_only", "qa.fee.programme_processing"):
        assert ConfigEntry.objects.filter(key=key).exists(), key
    assert ConfigEntry.objects.get(key="qa.fee.programme_processing").dual_control
    from accounts.models import Role as _R
    assert not _R.objects.filter(code="qa-finance-director").exists()  # Phase B (B2): no "director of X" roles
    for code in ("qa-finance-officer", "ict-qa-monitor", "qa-settings-admin",
                 "inst-chief-executive", "inst-admin", "inst-academic-dept", "resource-person"):
        assert Role.objects.filter(code=code).exists(), code
    assert ReferenceFormat.objects.count() == 10  # Phase 3 adds "query" (was 9)
    # Phase 2 adds the institution lifecycle, SOP-01 onboarding and refunds;
    # Phase 3 adds SOP-02 (qa_request) and the SSQ; Phase 4 adds SOP-03 (qa_case);
    # Phase 5 adds the inspection and the post-visit report (stage 12's sub-flow).
    assert set(FlowDefinition.objects.values_list("code", flat=True)) == {
        "payment", "pending_action", "institution_lifecycle", "onboarding", "refund", "qa_request", "ssq",
        "qa_case", "inspection", "post_visit_report",
        "complaint"}  # Phase 7: the default complaint flow
    assert ActionPolicy.objects.filter(action_code="config.update_setting").exists()
    assert NotificationTemplate.objects.count() >= 4
    assert "UNCONFIRMED" in out and "Fixed by policy" in out
    director = Role.objects.get(code="director")
    assert director.permissions.filter(codename="edit_qa_dual_control_settings").exists()


def test_second_run_changes_nothing(base):
    run("--with-demo")
    counts = {m.__name__: m.objects.count() for m in (ConfigEntry, Role, FlowDefinition, ReferenceFormat,
                                                     NotificationTemplate, ActionPolicy, DemoTag)}
    counts["users"] = User.objects.count()
    out = run("--with-demo")
    assert "Changes this run: 0" in out
    assert "passwords are never shown again" in out
    after = {m.__name__: m.objects.count() for m in (ConfigEntry, Role, FlowDefinition, ReferenceFormat,
                                                    NotificationTemplate, ActionPolicy, DemoTag)}
    after["users"] = User.objects.count()
    assert after == counts


def test_admin_edits_are_never_overwritten(base):
    run()
    ConfigEntry.objects.filter(key="qa.stage.default_sla_hours").update(value="12")
    run()
    assert ConfigEntry.objects.get(key="qa.stage.default_sla_hours").value == "12"


def test_startup_repairs_missing_direct_report_submission_routes(base):
    run()
    flow = FlowDefinition.objects.get(code="post_visit_report")
    FlowTransition.objects.filter(flow=flow, from_stage__code="draft",
                                  action_code="submit_to_director").delete()
    FlowTransition.objects.filter(flow=flow, from_stage__code="returned",
                                  action_code="resubmit_to_director").delete()

    run()

    assert FlowTransition.objects.filter(
        flow=flow, from_stage__code="draft", action_code="submit_to_director",
        to_stage__code="deputy_verified").exists()
    assert FlowTransition.objects.filter(
        flow=flow, from_stage__code="returned", action_code="resubmit_to_director",
        to_stage__code="deputy_verified").exists()


def test_dry_run_rolls_back(base):
    out = run("--dry-run")
    assert "DRY RUN" in out
    assert not ConfigEntry.objects.filter(key__startswith="qa.").exists()


def test_with_demo_creates_the_five_accounts_and_prints_passwords_once(base):
    out = run("--with-demo")
    for email, (user_type, role, dept) in DEMO_EMAILS.items():
        u = User.objects.get(email=email)
        assert u.user_type == user_type and u.role.code == role
        assert (u.department.code if u.department else None) == dept
        assert u.must_change_password
        assert DemoTag.is_tagged(u)
        line = next(line for line in out.splitlines() if email in line)
        password = re.split(r"\s+", line.strip())[1]
        assert u.check_password(password)
    inst_user = User.objects.get(email="admin@demo-poly.local")
    assert inst_user.institution.code == "DMP" and inst_user.institution.name == "Demo Polytechnic, Kaduna"
    assert DemoTag.is_tagged(inst_user.institution)


def test_reset_demo_removes_exactly_the_tagged_rows(base, make_user, settings):
    settings.DEBUG = True  # development, where --reset-demo needs no extra flag
    run("--with-demo")
    keeper = make_user("real.officer@nbte.gov.ng", role="officer", dept="dpap")
    from qa_core import settings_service
    officer = User.objects.get(email="qa.officer@demo.nbte.local")
    settings_service.save_setting(officer, ConfigEntry.objects.get(key="qa.impersonation.max_minutes"), "20")
    users_before = User.objects.count()
    settings_before = ConfigEntry.objects.count()
    out = io.StringIO()
    call_command("seed_startup", "--reset-demo", stdout=out)
    assert "tagged row(s) removed" in out.getvalue()
    assert not DemoTag.objects.exists()
    # Phase 1's 5 demo users + Phase 2's 6 (Director of Finance, four clearance
    # officers, the second demo institution's administrator).
    demo_users = 12  # Phase 3 adds Demo Polytechnic's academic-department account (was 11)
    assert User.objects.count() == users_before - demo_users
    assert not User.objects.filter(email__in=DEMO_EMAILS).exists()
    assert User.objects.filter(pk=keeper.pk).exists()
    assert ConfigEntry.objects.count() == settings_before
    from institutions.models import Institution
    assert not Institution.unscoped.filter(code="DMP").exists()
    # And the demo can be recreated afterwards.
    assert f"+{demo_users} new" in run("--with-demo")


def test_reset_demo_refused_without_debug(base, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="refused"):
        call_command("seed_startup", "--reset-demo", stdout=io.StringIO())
    call_command("seed_startup", "--reset-demo", "--i-understand-production", stdout=io.StringIO())
