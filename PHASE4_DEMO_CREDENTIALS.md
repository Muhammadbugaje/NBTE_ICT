# NBTE QA System — Phase 4 demo credentials

These accounts were created in **your `db.sqlite3`** by `python manage.py seed_phase4_demo` on 19 Oct 2026 (after the database was backed up and migrated). Sign in with the **e-mail address** at `http://localhost:8000/` (internal system) — the institution administrator signs in at `http://qa.localhost:8000/` (portal).

DEBUG is on, so these demo accounts are not forced to change their password. They are demo-tagged: `python manage.py seed_startup --reset-demo` removes them together with the demo institution, case and tasks.

**Demo case:** `NBTE/QAC/2026/DPAP/0001` — Kaduna Demo Polytechnic (Phase 4), code `KDP`, 2 programmes (Computer Science; Electrical/Electronic Engineering Technology).
**Now at:** stage 2 *Generate the cost sheet* → task for `qa.officer@demo.nbte.local`. Stage 1 is done (desk officer, Audit, Finance and ICT supervising officers chosen through real tasks).

| Who | E-mail | Password | Role · department | Acts at |
|---|---|---|---|---|
| desk_officer | qa.officer@demo.nbte.local | `DwRu56jE6Plc` | officer · DPAP | 2 cost sheet, 3 personnel, 8 visit, 12 post-visit report |
| director | qa.director@demo.nbte.local | `QXIp7P4z22no` | director · DPAP | 0 open the case, 4 confirm personnel, 9 confirm visit, 13 recommendation |
| audit_director | qa.audit_director@demo.nbte.local | `ebOgphk3yCnd` | director · Int. Aud. Dept | 1 assign Audit officer, 6 confirm audited cost |
| audit_officer | qa.audit_officer@demo.nbte.local | `Btbdxq04qYIW` | officer · Int. Aud. Dept | 5 audit the personnel cost |
| finance_director | qa.finance_director@demo.nbte.local | `aspoOx1gWEbD` | director · Finance | 1 assign Finance officer |
| finance_officer | qa.finance_officer@demo.nbte.local | `K6iBF_MPnUiQ` | officer · Finance | supervising Finance officer |
| ict_director | qa.ict_director@demo.nbte.local | `P_-zUXQK8bTa` | director · ICT | 1 assign ICT officer(s) |
| ict_officer | qa.ict_officer@demo.nbte.local | `g6xEsHcHF60Y` | officer · ICT | tracking ICT officer |
| ict_officer2 | qa.ict_officer2@demo.nbte.local | `qKXw-6RFCLWb` | officer · ICT | second ICT officer (big cases, R-04) |
| es | qa.es@demo.nbte.local | `ugERTWmjuFeo` | executive-secretary · ES Office | 7 approve cost, 10 approve visit, 16 final approval |
| pay_officer | qa.pay_officer@demo.nbte.local | `fKEL50cyyjBl` | pay-officer · Finance | 11 release funds |
| cqac_member | qa.cqac@demo.nbte.local | `Cz8igEfjcv77` | cqac-member · DPAP | 14 CQAC recommendation |
| cqac_chair | qa.cqac_chair@demo.nbte.local | `CUuFAwVryhrz` | cqac-chairman · DPAP | 15 Chairman's approval |
| ict_monitor | qa.ict_monitor@demo.nbte.local | `jsI0s5tlisqB` | ict-qa-monitor · ICT | sees everything, acts on nothing (R-13) |
| busy_officer | qa.busy_officer@demo.nbte.local | `YhlIiJih5fVe` | officer · DPAP | for trying a reassignment the workload rule refuses (R-01) |
| institution_admin | admin@p4demo.local | `ARRegC36FULs` | inst-admin · Kaduna Demo Polytechnic (Phase 4) | portal |

Your database already had a Director of Finance, a Director of ICT and an ES of its own. The demo case is **pinned** to the demo people (`QACase.stage_actors`), so its tasks go to the accounts above, not to your real ones.

## Lost a password?

```bash
python manage.py seed_phase4_demo --reset-passwords      # new passwords for every demo account (printed)
python manage.py shell < scripts/set_demo_passwords.py   # or: all demo staff → Walk-Through#2026
```

## The seed output (as printed)

```
NBTE QA — Phase 4 demo ready
  Case NBTE/QAC/2026/DPAP/0001 for Kaduna Demo Polytechnic (Phase 4) (2 programmes) — created.
  Now at stage 2: Generate the cost sheet → task for qa.officer@demo.nbte.local
  Internal system: http://localhost:8000/qa/cases/   (Tasks: http://localhost:8000/tasks/assigned/)
  DEBUG is on, so these demo accounts are not forced to change their password.
  Remove the demo with: python manage.py seed_startup --reset-demo
```
