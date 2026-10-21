# Phase 7 — definition of done

| Item | Status |
|---|---|
| Part 1: bell (mobile), Settings hub, self-registration control + banner, news grid with placeholders, crest watermark and motion | Done, checked on desktop and a 390 px phone (no horizontal scroll) |
| Part 2: ES/Director QA overview (Chart.js, count-up), staff home "Your desk" with glowing urgent items | Done |
| Part 3: Complaints exactly per Master Plan §5.6/§11 — every model, portal and internal pages, knowledge base, SLA escalation chain | Done |
| A demo payment complaint routes to Finance, breaches in a time-travel test, escalates to the Director of Finance, is resolved and is on the institution timeline | `complaints/tests/test_engine.py` |
| An admin adds a layer with its own two-stage flow without code | `test_admin_adds_a_layer_with_its_own_two_stage_flow`, `test_settings_complaints_crud` |
| §20 contract on every new page | `complaints/tests/test_views.py` |
| `seed_phase7_demo`: three open complaints in different SLA states | Done (also run by `seed_startup --with-demo`) |
| Phase 8: fresh DB gets all roles, groups and demo data from `seed_startup --with-demo`; guard security review | `test_seed_phase8.py`, `test_security_phase8.py`, `SECURITY_REVIEW_PHASE8.md` |
| Part 4: `NBTE_QA_Master_Handover_Guide.docx` (setup, every demo account, screenshots, architecture) | Delivered with the release |
| Phases 1–6 unbroken | Full suite run before and after; only the 15 known `review_checks` failures remain |

Not done / deferred: SMS for critical escalations (`qa.complaints.sms_for_critical` exists, no SMS provider is configured in this project);
lifting a hold from the complaint page (holds stay off by default — Requirements 15–17).
