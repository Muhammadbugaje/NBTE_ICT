"""v2.3 redesign: the case page's seven sections, folds on the overview,
the portal journey wording and the R2 storage switch."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.urls import reverse

from qa_core.tests.phase4_fixtures import advance_to, staff_client


def test_every_case_view_sits_in_exactly_one_section():
    from accreditation.case_views import TAB_GROUPS, TABS

    keys = [k for _g, _l, ks in TAB_GROUPS for k in ks]
    assert sorted(keys) == sorted(k for k, _ in TABS)
    assert len(keys) == len(set(keys))
    assert len(TAB_GROUPS) == 7


def test_tab_groups_mark_the_active_section_and_the_users_step():
    from accreditation.case_views import tab_groups

    groups = {g["key"]: g for g in tab_groups("funds", {"cost"})}
    assert groups["money"]["active"] and not groups["overview"]["active"]
    assert groups["money"]["dot"]
    subs = {s["key"]: s for s in groups["money"]["subs"]}
    assert subs["funds"]["active"] and subs["cost"]["dot"]
    # the overview never shows a "your step" dot (the step is on the page itself)
    assert not tab_groups("overview", {"overview"})[0]["dot"]


@pytest.mark.django_db
def test_case_page_shows_sections_subviews_and_folds(p4):
    case = advance_to(p4, 3)
    client = staff_client(p4.people.director)
    url = reverse("qa_case:detail", args=[case.pk])

    html = client.get(url + "?tab=overview").content.decode()
    assert 'class="c3-tabs v3-sections"' in html
    for label in ("Overview", "Money", "Team &amp; tasks", "Visit", "Reports", "Decision", "History"):
        assert f">{label}" in html
    assert "v3-subtabs" not in html  # the overview has one view only

    html = client.get(url + "?tab=funds").content.decode()
    assert 'class="v3-subtabs"' in html and "Cost sheet" in html and "Fund release" in html

    html = client.get(url + "?tab=capacity").content.decode()
    assert 'aria-label="Reports views"' in html


@pytest.mark.django_db
def test_journey_folds_the_finished_steps(p4):
    case = advance_to(p4, 8)
    html = staff_client(p4.people.director).get(
        reverse("qa_case:detail", args=[case.pk]) + "?tab=overview").content.decode()
    assert 'class="v3-journey-done"' in html
    assert "Stages 1–" in html


def test_portal_journey_says_cleared_for_qa():
    from qa_core.portal_journey import STEP_DEFS

    labels = {code: (todo, done) for code, todo, done in STEP_DEFS}
    assert labels["approval"][1] == "Cleared for QA"
    assert "approves your institution" not in labels["approval"][0]


ROOT = Path(__file__).resolve().parents[2]


def _settings_probe(env):
    code = ("import django; django.setup(); from django.core.files.storage import storages; "
            "from django.conf import settings; "
            "print(type(storages['default']).__name__, type(storages['restricted']).__name__, "
            "settings.USE_X_ACCEL_REDIRECT)")
    full = {**os.environ, "DJANGO_SETTINGS_MODULE": "nbte_ict.settings.dev", **env}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full, capture_output=True, text=True,
                          timeout=120)


def test_r2_storage_switch_uses_two_buckets_and_never_x_accel():
    pytest.importorskip("storages")
    out = _settings_probe({"FILE_STORAGE": "r2", "R2_ACCOUNT_ID": "acc", "R2_ACCESS_KEY_ID": "k",
                           "R2_SECRET_ACCESS_KEY": "s", "R2_PUBLIC_BUCKET": "pub", "R2_PRIVATE_BUCKET": "priv",
                           "USE_X_ACCEL_REDIRECT": "True"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["R2PublicStorage", "R2RestrictedStorage", "False"]


def test_r2_storage_switch_refuses_missing_settings():
    out = _settings_probe({"FILE_STORAGE": "r2", "R2_ACCOUNT_ID": "", "R2_PUBLIC_BUCKET": ""})
    assert out.returncode != 0
    assert "FILE_STORAGE=r2 needs" in out.stderr


def test_restricted_r2_storage_has_no_public_url():
    pytest.importorskip("storages")
    from core.storage import R2RestrictedStorage

    store = R2RestrictedStorage(bucket_name="x", access_key="a", secret_key="b",
                                endpoint_url="https://example.r2.cloudflarestorage.com")
    with pytest.raises(ValueError):
        store.url("qa/file.pdf")


@pytest.mark.django_db
def test_team_workload_counts_open_tasks_and_flags_duty_conflicts(p4):
    from accreditation.models import QACase
    from qa_core import home

    case = advance_to(p4, 2)
    department = case.owning_department
    data = home.team_workload(department)
    people = {r["user"].pk for r in data["rows"]}
    assert case.desk_officer_id in people
    assert data["open"] == sum(r["open"] for r in data["rows"])
    assert data["conflicts"] == []

    QACase.unscoped.filter(pk=case.pk).update(supervising_audit_officer=case.desk_officer)
    data = home.team_workload(department)
    assert [c["roles"] for c in data["conflicts"]] == [("desk officer", "Audit officer")]

    widget = home.w_team_load(p4.people.director, home.profile(p4.people.director))
    assert widget is not None and widget.actionable == 1
    assert "segregation-of-duties" in widget.action
