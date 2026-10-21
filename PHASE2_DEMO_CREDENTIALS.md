# NBTE QA System — Phase 2 demo credentials

> **Change these passwords immediately, or delete the demo accounts.** Every demo account has `must_change_password=True`, so the first sign-in forces a new password. Remove all demo data at any time with `python manage.py seed_startup --reset-demo`.

## Which database these belong to

These passwords belong to the **ready-to-run demo database shipped in this folder: `qa_phase2_demo.sqlite3`**, built on 18 Oct 2026 by upgrading the Phase 1 demo database exactly as you would upgrade a real one:

```bash
cp qa_phase1_demo.sqlite3 qa_phase2_demo.sqlite3
DATABASE_URL=sqlite:///qa_phase2_demo.sqlite3 python manage.py migrate
DATABASE_URL=sqlite:///qa_phase2_demo.sqlite3 python manage.py seed_startup --with-demo
```

So the five Phase 1 demo accounts keep their Phase 1 passwords, and `seed_startup` printed new passwords once for the six Phase 2 accounts. Every password below was checked against that database before packaging. The demo slips, advice and proof it generated are in `protected_media/qa/` (shipped alongside it).

Your own `db.sqlite3` was **not touched**. Running `seed_startup --with-demo` on any other database prints a new set of passwords once, and only those work there.

| Account | Password | Type | Role | Department / institution | Signs in at | Use it for |
|---|---|---|---|---|---|---|
| `qa.officer@demo.nbte.local` | `U6GBfL0msrZyvsg0` | staff | `officer` (+ *QA Settings Admin*, *QA Demo Walkthrough*) | DPAP | Internal | Add TVET, bulk import, any §6 action (first officer) |
| `qa.director@demo.nbte.local` | `yOrbfVvyWDcBOyz4` | staff | `director` (+ *QA Demo Walkthrough*) | DPAP | Internal | Confirming §6 actions (second officer) |
| `qa.finance@demo.nbte.local` | `AEaWXqmtQROo0WwN` | staff | `qa-finance-officer` | Finance | Internal | Finance queue first pass, statements, matching |
| `finance.director@demo.nbte.local` | `CrZjKvXdIpNxxQI2` | staff | `qa-finance-director` | Finance | Internal | **The only role that can confirm a payment** |
| `legal.officer@demo.nbte.local` | `h1sRgzaE6G93obOL` | staff | `officer` | Legal Unit | Internal | Legal clearance (SOP-01) |
| `finance.clearance@demo.nbte.local` | `ycMChlO-zTf0IZMm` | staff | `officer` | Finance | Internal | Finance clearance (SOP-01) |
| `planning.academic@demo.nbte.local` | `-FL3RMybHUQjdgh3` | staff | `officer` | ASPD (Academic Planning) | Internal | Academic Planning clearance |
| `planning.physical@demo.nbte.local` | `OtXRNs2CcFh7apYV` | staff | `officer` | PPD (Physical Planning) | Internal | Physical Planning clearance |
| `admin@demo-poly.local` | `E0RtxUEmKr8_KBBJ` | institution | `inst-admin` | Demo Polytechnic, Kaduna (`DMP`) | QA portal | An approved institution with one confirmed and one pending bill |
| `admin@demo-health.local` | `zwMxH9aTvZQfaSP2` | institution | `inst-admin` | Demo College of Health Technology (`DCH`) | QA portal | A second institution (host isolation checks) |
| `resource@demo.nbte.local` | `1_34JlIcHjFrgSKL` | resource_person | `resource-person` | — | QA portal | Resource-person dashboard (Phase 4 content) |

*QA Demo Walkthrough* is a demo-only group that lets the two DPAP demo accounts see every institution and confirm in any department, so one officer pair can drive the whole walkthrough, including the proposed institutions handled by NTIRD. Real staff never get it.

## Try it

```bash
pip install -r requirements-dev.txt
DATABASE_URL=sqlite:///qa_phase2_demo.sqlite3 python manage.py runserver
```

- **Public site:** http://localhost:8000/ — the gold **Quality Assurance Portal** button in the hero, and the link in the footer and mobile menu.
- **Internal system:** http://localhost:8000/accounts/login/ — sidebar groups **New Institution**, **TVET Support**, **Applications (SOP-01)**, **Finance (QA)** and **Quality Assurance**, depending on the role.
- **QA portal:** http://qa.localhost:8000/login/ — Dashboard, Application, Documents, Payments, Institution. New institutions can self-register at http://qa.localhost:8000/register/.
- **E-mails** are logged, not sent (`QA_EMAIL_DELIVERY=console`): read them in the server console or at **Quality Assurance → QA e-mail outbox** (superusers, the oversight board and QA settings administrators such as `qa.officer`). The set-password link for a newly added institution is there.
- **Online payments** run against the built-in simulator while no Remita/Paystack keys are set (`QA_PAYMENTS_SIMULATE=True` in dev only). The simulator sends a correctly signed webhook; the payment still waits for the Director of Finance.

## The walkthrough in one page

1. `qa.officer` → New Institution → **Add TVET** → fill the form → *Send for confirmation*.
2. `qa.director` → Quality Assurance → Pending confirmations → *Confirm* → the institution exists as **proposed** (handled by NTIRD).
3. `qa.officer` → the institution → Actions → **Send Login Credentials** → read the set-password link in the outbox.
4. The institution admin sets a password at the link, signs in, uploads the documents on **Documents**, then **Application → Start → Submit**.
5. `legal.officer`, `finance.clearance`, `planning.academic`, `planning.physical` → Applications → Departmental clearance → *Clear* (any one can *Return to institution* instead).
6. When the fourth clears, the institutional fee is issued with its slip. The institution pays (Paystack/Remita simulator, or bank transfer + *I have paid*), and uploads the provider receipt for online payments.
7. `qa.finance` → Finance (QA) → Verification queue → *Save first pass*; `finance.director` → *Confirm payment*. The Payment Advice is generated and the application moves to **Programme selection**.
8. `qa.officer` → **Bulk Import (Excel)** → download the template → upload three rows → *Send for confirmation*; `qa.director` confirms.

## Removing the demo

```bash
DATABASE_URL=sqlite:///qa_phase2_demo.sqlite3 python manage.py seed_startup --reset-demo
```

This deletes exactly the rows tagged as demo (the 11 users, the DMP and DCH institutions and everything hanging off them: applications, bills, slips, documents, pending confirmations) and nothing else. It is refused when `DEBUG` is false unless you add `--i-understand-production`.

The memo-routing practice accounts from Phase 1 (`*@memo-scenarios.test`, password `Memo-Scenario-2026!`) are still in this database; see PHASE1_DEMO_CREDENTIALS.md.
