"""Phase 0 (T-CONS-8): flow stages that are KNOWN dead ends today.

``test_flows_have_no_dead_end_stages`` fails for any non-terminal stage that
no code path can leave (or reach) unless it is listed here. Shrink this list
as the code catches up; never grow it without a ticket in the plan. A listed
stage that is no longer a dead end fails ``test_dead_end_allowlist_is_current``
so the entry gets removed.

Shared with the Phase 0 QA work (0.9 submit_programmes + NTIRD programme
clearance), which removes the onboarding programme stages from this list.
"""

DEAD_END_ALLOWLIST = {
    # flow code: {stage code: reason}
    "institution_lifecycle": {
        "suspended": "no suspend/reinstate action is built; reached only when an existing "
                     "record is adopted into the flow (force_stage)",
    },
    "pending_action": {
        "confirmed": "flow is data only: qa_core.actions keeps PendingAction.status itself "
                     "and never moves a flow instance (consistency, Phase G)",
    },
    "ssq": {
        "accepted": "'analyse' has no code path yet (Phase R scoring)",
    },
}
