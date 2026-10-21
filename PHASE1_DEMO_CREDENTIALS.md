# NBTE QA System — Phase 1 demo credentials

> **Change these passwords immediately, or delete the demo accounts.** Every demo account has `must_change_password=True`, so the first sign-in forces a new password. Remove all demo data at any time with `python manage.py seed_startup --reset-demo`.

## Which database these belong to

These passwords were generated on 18 Oct 2026 for the **ready-to-run demo database shipped in this folder: `qa_phase1_demo.sqlite3`**. It was built from the merged code (your 18 Oct internal-system update + QA Phase 1 + the memo routing fix) with `migrate` → `seed_roles --sync` → `seed_nbte` → `seed_startup --with-demo` → `seed_memo_routes` → `seed_memo_scenarios`. All of them were checked against that database before packaging.

Your own `db.sqlite3` was **not touched**: it is exactly as you sent it, not migrated and with no demo accounts. When you run `seed_startup --with-demo` on it (or on any other database), the command prints a **new** set of passwords once, and only those work there.

| Account | Password | Type | Role | Department / institution | Signs in at |
|---|---|---|---|---|---|
| `qa.officer@demo.nbte.local` | `U6GBfL0msrZyvsg0` | staff | `officer` (+ group *QA Settings Admin*) | DPAP | Internal host |
| `qa.director@demo.nbte.local` | `yOrbfVvyWDcBOyz4` | staff | `director` | DPAP | Internal host |
| `qa.finance@demo.nbte.local` | `AEaWXqmtQROo0WwN` | staff | `qa-finance-officer` | Finance | Internal host |
| `admin@demo-poly.local` | `E0RtxUEmKr8_KBBJ` | institution | `inst-admin` | Demo Polytechnic, Kaduna (`DMP`) | QA portal |
| `resource@demo.nbte.local` | `1_34JlIcHjFrgSKL` | resource_person | `resource-person` | — | QA portal |

## Try it

```bash
pip install -r requirements-dev.txt
DATABASE_URL=sqlite:///qa_phase1_demo.sqlite3 python manage.py runserver
```

- **Internal system:** http://localhost:8000/accounts/login/ (sign in as `qa.officer` or `qa.director`; the sidebar shows **Quality Assurance → Pending confirmations / QA settings**).
- **QA portal:** http://qa.localhost:8000/login/ (sign in as `admin@demo-poly.local` or `resource@demo.nbte.local`). Chrome, Edge and Firefox resolve `*.localhost` to your own machine automatically. In production, set `QA_PORTAL_HOST=qa.nbte.gov.ng` and point that DNS name at the same server.

## Removing the demo

```bash
DATABASE_URL=sqlite:///qa_phase1_demo.sqlite3 python manage.py seed_startup --reset-demo
```

This deletes exactly the rows tagged as demo (the 5 users, the DMP institution, and any pending confirmation a demo user started) and nothing else. It is refused when `DEBUG` is false unless you add `--i-understand-production`.

## Memo routing practice accounts (same demo database)

`seed_memo_scenarios` added 14 practice accounts in four practice directorates, each with the password **`Memo-Scenario-2026!`**, for example:
- `alpha.officer@memo-scenarios.test`
- `alpha.linehead@memo-scenarios.test`
- `alpha.deputy@memo-scenarios.test`
- `alpha.director@memo-scenarios.test`
- `bravo.director@memo-scenarios.test`
- `delta.director@memo-scenarios.test`

It also sent 16 practice memos, one per routing case. 11 of them are waiting part-way, so you can sign in as the person each waits on (internal host → Messages → Memos → Awaiting) and approve it. Run `python manage.py seed_memo_scenarios` again at any time to print where each memo is now. MEMO_ROUTING.md has the full table.

Remove them with:

```bash
DATABASE_URL=sqlite:///qa_phase1_demo.sqlite3 python manage.py seed_memo_scenarios --remove
```
