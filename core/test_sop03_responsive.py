"""Responsive layout contracts for the SOP-03 QA case and inspection screens."""
from pathlib import Path

from django.conf import settings


ROOT = Path(settings.BASE_DIR)
CASE_CSS = (ROOT / "static" / "css" / "ux-case.css").read_text()


def test_sop03_stage_and_tab_navigation_scrolls_inside_its_panel():
    assert ".c3-steps { display: flex; overflow-x: auto;" in CASE_CSS
    assert "position: sticky; top: 64px; z-index: 6; display: flex; gap: 4px; overflow-x: auto;" in CASE_CSS
    assert ".qa-insp-nav { scroll-snap-type: x proximity; }" in CASE_CSS


def test_sop03_stage_advancement_has_motion_safe_transition_feedback():
    assert ".c3-stage--pending .c3-stage__head::before" in CASE_CSS
    assert "@keyframes c3-stage-arrive" in CASE_CSS
    assert ".c3-decide__btns .btn:focus-visible" in CASE_CSS
    assert "@media (prefers-reduced-motion: reduce)" in CASE_CSS
    assert ".c3-stage--pending { animation: none; }" in CASE_CSS


def test_sop03_tables_use_local_scrolling_and_keep_headers_on_small_screens():
    assert ".qa-page .qa-table-wrap, .c3-page .qa-table-wrap" in CASE_CSS
    assert "overflow-x: auto;" in CASE_CSS
    assert ".qa-page .qa-table-wrap > .qa-table--scroll" in CASE_CSS
    assert ".qa-page .qa-table-wrap > .qa-table--scroll thead { position: static;" in CASE_CSS
    assert ".c3-rf .qa-table-wrap table.qa-table { min-width: 560px; }" in CASE_CSS


def test_sop03_wide_tables_are_keyboard_scrollable_in_reports_and_qa_panels():
    templates = (
        "templates/accreditation/case/_report_form_field.html",
        "templates/accreditation/case/report_form.html",
        "templates/inspection/_mappings_rows.html",
        "templates/inspection/_finder_rows.html",
        "templates/inspection/_personnel_rows.html",
        "templates/inspection/_inspection_panel.html",
        "templates/inspection/_service_rows.html",
        "templates/inspection/_vvt001.html",
    )
    for template in templates:
        markup = (ROOT / template).read_text()
        assert 'role="region"' in markup, template
        assert 'tabindex="0"' in markup, template
        assert "qa-table--scroll" in markup, template
