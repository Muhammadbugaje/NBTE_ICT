"""Regression (Phase 8 integration): the live fee estimates sit inside forms
whose hx-target is the page (#qa-main) or the programmes card. Without their
own hx-target they inherited it and the estimate replaced the whole page."""
import re
from pathlib import Path

from django.conf import settings

TEMPLATES = ["qa_portal/qa_requests/_new_programmes.html", "qa_portal/application/_programmes.html"]


def test_fee_estimates_swap_only_themselves():
    for name in TEMPLATES:
        text = (Path(settings.BASE_DIR) / "templates" / name).read_text()
        div = re.search(r'<div id="qa-(?:fee|prog)-estimate"[^>]*>', text).group(0)
        assert 'hx-target="this"' in div, name
        assert 'hx-swap="innerHTML"' in div, name
