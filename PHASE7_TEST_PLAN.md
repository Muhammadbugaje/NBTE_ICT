# Phase 7 test plan

Run: `python -m pytest -q complaints/tests qa_core/tests/test_security_phase8.py`

| PHASES.md requirement | Test |
|---|---|
| Routing rule order and fallback | `complaints/tests/test_engine.py::test_routing_rule_order_and_fallback` |
| Business-hour SLAs across a weekend and a `portal.Holiday` | `test_business_hour_sla_across_a_weekend_and_a_holiday` |
| SLA pause while awaiting the raiser | `test_sla_pauses_while_awaiting_the_raiser` |
| Escalation level progression | `test_payment_complaint_routes_to_finance_breaches_escalates_and_is_resolved`, `test_critical_breach_reaches_the_es` |
| Internal messages never on the portal | `test_views.py::test_portal_raise_list_detail_and_reply`, `test_withdraw_and_messages` |
| Cross-institution isolation | `test_cross_institution_isolation`, `test_views.py::test_portal_isolation_and_about_prefill`, `test_internal_routes_answer_404_across_departments`, `qa_core/tests/test_scoping_enforcement.py` (layers 1–3) |
| Holds impossible while `enabled_for` is empty | `test_holds_are_impossible_while_enabled_for_is_empty` |
| Reopen window | `test_reopen_window_and_auto_close` |
| DoD: payment complaint → Finance → breach → Director of Finance → resolved → timeline | `test_payment_complaint_routes_to_finance_breaches_escalates_and_is_resolved` |
| DoD: admin adds a layer with its own two-stage flow, no code | `test_admin_adds_a_layer_with_its_own_two_stage_flow`, `test_views.py::test_settings_complaints_crud` |
| DoD: §20 contract on every new page | `test_views.py` (full page + partials, URL filters, 25/page + Show all, 422 validation in place, toasts) |
| Leadership dashboards / staff home | `test_leadership_dashboards_show_the_qa_overview` |
| Phase 8: fresh-DB seed, idempotent | `test_seed_phase8.py` |
| Phase 8: guard review | `qa_core/tests/test_security_phase8.py` |

Full suite: two halves (A = qa_core, accreditation, inspection tests; B = the rest). Known failures: the 15
`review_checks` tests (they document findings in the original system and failed before Phase 7).
