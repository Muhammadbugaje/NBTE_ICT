"""Phase 0 (T-CONS-8): no flow stage is a dead end.

Every non-terminal stage of every seeded flow must (1) be reachable from the
start stage through transitions some code path can fire and (2) have an
outgoing transition that some code path can fire. "Can fire" is decided from
the source: the action code appears as a string literal in a non-test,
non-seed module that also names the flow (``"onboarding"`` …), or it is an
engine move (``auto*`` actions, parallel gates, joins). Known gaps live in
``dead_end_allowlist.py``.
"""
import re
from functools import lru_cache
from pathlib import Path

import pytest
from django.conf import settings

from qa_core.tests.dead_end_allowlist import DEAD_END_ALLOWLIST

SKIP_DIRS = ("docs/", "media/", "protected_media/", "static/vendor", "staticfiles/", "node_modules/")
LITERAL = re.compile(r"""["']([a-z0-9_\-]+)["']""")
IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
# Helpers that fire a flow without naming it in the calling module.
FLOW_HELPERS = {"institution_lifecycle": {"lifecycle_move"}}


@lru_cache(maxsize=1)
def _source_files():
    root = Path(settings.BASE_DIR)
    out = []
    for pattern in ("*.py", "*.html", "*.js"):
        for p in root.rglob(pattern):
            rel = str(p.relative_to(root))
            if rel.startswith(SKIP_DIRS) or "/migrations/" in rel or "/tests/" in rel \
                    or p.name.startswith(("test_", "conftest")) or "seed_data" in p.name \
                    or "/management/commands/" in rel or rel.startswith(("scripts/", "review_checks/")):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                out.append((set(LITERAL.findall(text)), set(IDENT.findall(text))))
            except OSError:
                continue
    return out


def _fired_actions(flow_code):
    """Action literals of modules that also name the flow."""
    fired = set()
    helpers = FLOW_HELPERS.get(flow_code, set())
    for lits, idents in _source_files():
        if flow_code in lits or helpers & idents:
            fired |= lits
    return fired


def dead_end_report():
    """{flow_code: {stage_code: why}} for every dead or unreachable stage."""
    from qa_core.models import FlowDefinition, FlowStage

    report = {}
    for flow in FlowDefinition.objects.filter(is_active=True).order_by("code"):
        fired = _fired_actions(flow.code)
        stages = {s.code: s for s in flow.stages.all()}
        transitions = list(flow.transitions.select_related("from_stage", "to_stage"))

        def can_fire(t):
            return (t.action_code in fired or t.action_code.startswith("auto")
                    or t.from_stage.kind in (FlowStage.PARALLEL_GATE, FlowStage.JOIN))

        live = [t for t in transitions if can_fire(t)]
        reach = {c for c, s in stages.items() if s.kind == FlowStage.START}
        frontier = list(reach)
        while frontier:
            code = frontier.pop()
            for t in live:
                if t.from_stage.code == code and t.to_stage.code not in reach:
                    reach.add(t.to_stage.code)
                    frontier.append(t.to_stage.code)
        for code, st in stages.items():
            if st.is_terminal_success or st.is_terminal_failure or st.kind == FlowStage.END:
                continue
            if st.kind == FlowStage.PARALLEL_GATE:
                continue  # the engine never rests on a gate; it opens the branches
            outs = [t for t in transitions if t.from_stage_id == st.pk]
            if not outs:
                report.setdefault(flow.code, {})[code] = "no outgoing transition"
            elif not any(can_fire(t) for t in outs):
                report.setdefault(flow.code, {})[code] = "no code fires " + ", ".join(t.action_code for t in outs)
            elif code not in reach:
                report.setdefault(flow.code, {})[code] = "unreachable from the start stage"
    return report


def _flatten(d):
    return {(f, s) for f, stages in d.items() for s in stages}


@pytest.mark.django_db
def test_flows_have_no_dead_end_stages(qa_seed):
    from qa_core.models import FlowDefinition

    assert FlowDefinition.objects.filter(is_active=True).count() >= 5
    found = dead_end_report()
    unexpected = _flatten(found) - _flatten(DEAD_END_ALLOWLIST)
    assert not unexpected, "dead-end flow stages: " + "; ".join(
        f"{f}:{s} ({found[f][s]})" for f, s in sorted(unexpected))


@pytest.mark.django_db
def test_dead_end_allowlist_is_current(qa_seed):
    """An allow-listed stage that now works must leave the allow-list."""
    fixed = _flatten(DEAD_END_ALLOWLIST) - _flatten(dead_end_report())
    assert not fixed, f"remove from qa_core/tests/dead_end_allowlist.py: {sorted(fixed)}"
