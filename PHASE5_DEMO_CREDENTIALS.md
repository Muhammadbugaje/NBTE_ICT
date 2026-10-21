# NBTE QA System — Phase 5 demo credentials

These accounts are in **your `db.sqlite3`**. On 19 Oct 2026 the database was backed up to `db.sqlite3.before-phase5-20261019-121257`, migrated, and seeded with `python manage.py seed_phase5_demo`. Sign in with the **e-mail address** at `http://localhost:8000/` (internal system). Resource persons and the institution administrator sign in on the portal, `http://qa.localhost:8000/`.

DEBUG is on, so these demo accounts are not forced to change their password. They are demo-tagged: `python manage.py seed_startup --reset-demo` removes them, along with the demo institution, case, resource persons and tasks.

**Demo case:** `NBTE/QAC/2026/DPAP/0001` — Kaduna Demo Polytechnic (Phase 4), 2 programmes.
**Now at:** stage 12 *Post-visit report*.

- **Inspection:** `NBTE/INS/2026/DPAP/0001`, visit recorded (closed).
- **Reports:** both programmes' reports are in **draft**, each a task for `qa.team_lead@demo.nbte.local`.
- **Findings:** the engineering resource person has already submitted findings on the portal.

The Phase 4 accounts keep the passwords in PHASE4_DEMO_CREDENTIALS.md. The Phase 5 accounts (the last six rows) were created by this seed.

| Who | E-mail | Password | Role · department | In the Phase 5 walkthrough |
|---|---|---|---|---|
| team_lead | qa.team_lead@demo.nbte.local | `zj48-gGByvPY` | director · DPAP | Team Lead: records the visit, writes and submits the reports, corrects attendance |
| division_head | qa.division_head@demo.nbte.local | `F-W41FAKH3cg` | division-head · DPAP | First endorsement layer |
| deputy_director | qa.deputy_director@demo.nbte.local | `O-G2MljFFUB4` | deputy-director · DPAP | Second layer (returns with a remark in the walkthrough) |
| director | qa.director@demo.nbte.local | `QXIp7P4z22no` | director · DPAP | Third layer; stage 13 recommendation; vets nominations; confirms mappings |
| desk_officer | qa.officer@demo.nbte.local | `DwRu56jE6Plc` | officer · DPAP | Nominates resource persons, proposes mappings |
| qa_officer2 | qa.verifier@demo.nbte.local | `TgptssFSFlmb` | director · DPAP | Registered QA personnel (verification officer) |
| es | qa.es@demo.nbte.local | `ugERTWmjuFeo` | executive-secretary · ES Office | Stage 10 visit approval (creates the inspection) |
| pay_officer | qa.pay_officer@demo.nbte.local | `fKEL50cyyjBl` | pay-officer · Finance | Stage 11 fund release |
| ict_monitor | qa.ict_monitor@demo.nbte.local | `jsI0s5tlisqB` | ict-qa-monitor · ICT | Sees every Phase 5 screen, acts on nothing (R-13) |
| busy_officer | qa.busy_officer@demo.nbte.local | `YhlIiJih5fVe` | officer · DPAP | Not on the visit: refused by B17 |
| institution_admin | admin@p4demo.local | `ARRegC36FULs` | inst-admin · Kaduna Demo Polytechnic (Phase 4) | Portal: read-only Inspections list |
| rp_eng | rp.yakubu@demo.nbte.local | `xEYxjpDlYd1k` | resource-person · QA portal | Portal: My engagements, findings, service history, Profile & CV |
| rp_sci | rp.zainab@demo.nbte.local | `SEGUsC5IrQtl` | resource-person · QA portal | Portal (computing assessor) |

The other Phase 4 accounts (Audit, Finance, ICT, CQAC) are unchanged and listed in PHASE4_DEMO_CREDENTIALS.md.

## The demo resource persons

Each one illustrates a §9 rule in the Availability Finder. Choose *Also show who is not eligible, and why* to see the reasons.

| Key | Name | Status | Illustrates |
|---|---|---|---|
| rp_eng | Engr. Prof. Yakubu Danjuma | Active | Eligible: engineering, mapped to EEE, portal account |
| rp_sci | Dr. Zainab Abdullahi | Active | Eligible: computing, mapped to Computer Science, portal account |
| rp_sci2 | Dr. Chukwuma Obi | Active | Eligible: computing, ranked by fewest visits |
| rp_nominated | Mrs. Halima Sule | Nominated | Waits for a vetter (the nominator cannot vet) |
| rp_coi | Mr. Emeka Nwosu | Active | Conflict of interest: works at the demo institution |
| rp_lowrank | Mr. Garba Lawal | Active | Rank no longer in `required_designations`, so he carries the **No longer eligible** badge |
| rp_health | Dr. Ifeoma Eze | Active | Discipline: health only |
| rp_capped | Prof. Bashir Musa | Active | Annual cap reached (earlier visits this year were backfilled) |

## Lost a password?

```bash
python manage.py seed_phase5_demo --reset-passwords      # new passwords for every demo account (printed)
python manage.py shell < scripts/set_demo_passwords.py   # or: every @demo.nbte.local account → Walk-Through#2026
```

## The seed output (as printed)

```
NBTE QA — Phase 5 demo ready
  Case NBTE/QAC/2026/DPAP/0001 for Kaduna Demo Polytechnic (Phase 4) — existing.
  Now at stage 12: Post-visit report
  Inspection NBTE/INS/2026/DPAP/0001: closed, team lead qa.team_lead@demo.nbte.local
    Report — ND Computer Science: draft → qa.team_lead@demo.nbte.local
    Report — ND Electrical/Electronic Engineering Technology: draft → qa.team_lead@demo.nbte.local
```

Running it a second time changed nothing: the same case, inspection and reports, and no new accounts.
