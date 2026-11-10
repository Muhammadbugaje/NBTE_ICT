"""Contracts for the live-refresh kit (v2.8).

These are deliberately database-free: they prove the routes, the template
hooks and the assets stay wired together without paying for a test database.
"""
from pathlib import Path

from django.conf import settings
from django.urls import reverse


ROOT = Path(settings.BASE_DIR)


def test_sop03_live_routes_reverse():
    assert reverse("qa_case:live", args=[1]) == "/qa/cases/1/live/"
    assert reverse("qa_insp:inspection_live", args=[1]) == "/qa/inspection/inspections/1/live/"
    assert reverse("qa_insp:report_live", args=[1]) == "/qa/inspection/reports/1/live/"
    assert reverse("qa_insp:inspections_live") == "/qa/inspection/inspections/live/"
    assert reverse("qa_insp:calendar_live") == "/qa/inspection/calendar/live/"
    assert reverse("qa_insp:endorsements_live") == "/qa/inspection/endorsements/live/"


def test_sop03_live_panels_are_stamped():
    for template, route in (
        ("templates/accreditation/case/_detail_panel.html", "qa_case:live"),
        ("templates/inspection/_inspection_panel.html", "qa_insp:inspection_live"),
        ("templates/inspection/_report_panel.html", "qa_insp:report_live"),
        ("templates/inspection/_inspections_panel.html", "qa_insp:inspections_live"),
        ("templates/inspection/_calendar_panel.html", "qa_insp:calendar_live"),
        ("templates/inspection/_endorsements_panel.html", "qa_insp:endorsements_live"),
    ):
        markup = (ROOT / template).read_text()
        assert "data-qa-live-url" in markup, template
        assert route in markup, template
        assert 'data-qa-live-target="#qa-panel"' in markup, template


def test_qa_live_assets_are_loaded_by_both_shells():
    for template in ("templates/qa_core/_shell.html", "templates/qa_portal/base_portal.html"):
        markup = (ROOT / template).read_text()
        assert "css/qa-live.css" in markup, template
        assert "js/qa-live.js" in markup, template
    base = (ROOT / "templates" / "base.html").read_text()
    assert "extra_css_last" in base
