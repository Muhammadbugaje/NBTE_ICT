# NBTE QA System — Phase 7 demo credentials

Every demo account in your `db.sqlite3` now uses one password: **`NBTE-Demo#2026`**
(set with `python manage.py set_demo_passwords`; the full list is in `PHASE7_DEMO_ACCOUNTS.csv`
and in section 3 of `NBTE_QA_Master_Handover_Guide.docx`). Your superuser account is unchanged.

Internal system: `http://localhost:8000/` · QA portal: `http://qa.localhost:8000/`

| Try this | Sign in as | Where |
|---|---|---|
| Complaint queue, a breached complaint escalated three levels | qa.director@demo.nbte.local | Internal → Complaints |
| Handle the payment complaint (Finance) | qa.finance@demo.nbte.local or qa.finance_officer@demo.nbte.local | Internal → Complaints |
| SLA dashboard, ES QA overview | qa.es@demo.nbte.local | Dashboards → ES; Complaints → SLA dashboard |
| Confirm a payment (Director of Finance) | finance.director@demo.nbte.local | Finance → Verification queue |
| Raise / follow a complaint, help articles | admin@p4demo.local | Portal → Complaints |
| Settings → Complaints (layers, routing, escalation, KB) | qa.officer@demo.nbte.local (QA Settings Admin) | QA settings → Complaints |

Demo complaints: NBTE-CMP-2026-000001 (Payment issues, on track), -000002 (SSQ submission issues, past half-way),
-000003 (Workflow delays, Critical, breached and escalated to the Director, Deputy Director and ES).
