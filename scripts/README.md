# Scripts: tests, demo seeds and browser walkthroughs

Everything used to build and check Phase 4 (and the earlier phases) so you can run it yourself.
Run every command from the **project folder** (the one with `manage.py`), with your virtual environment active.

| Script | What it does |
|---|---|
| `run_tests.sh` / `run_tests.bat` | Runs the test suites. `quick` = Phase 4 only (≈15 min), `phases` = Phases 1–4 (≈1 h), `all` = the whole project (≈70 min). |
| `seed_demo.sh` / `seed_demo.bat` | Migrates the database and seeds everything for the demo: settings and flows (`seed_startup`), then the Phase 4 case (`seed_phase4_demo`). Prints every demo account and password. |
| `set_demo_passwords.py` | Gives every demo account (`@demo.nbte.local`) the same password — handy before a walkthrough. `python manage.py shell < scripts/set_demo_passwords.py` |
| `walkthroughs/phase4_walk.py` | Playwright walkthrough of SOP-03: Case Board → cost sheet → Audit task → *Open in QA* → audit → ES return / approve → tasks close and open. Saves screenshots. |
| `walkthroughs/phase3_walk_a.py`, `_b.py`, `_c.py` | The Phase 3 (SOP-02 / SSQ) walkthroughs. They expect the Phase 3 demo state (`qa_phase3_demo.sqlite3`). |
| `walkthroughs/lib.py` | Shared helpers (sign-in, screenshots). |
| `make_share_zip.sh` | Builds a shareable zip from `git ls-files` only (no databases, `media/`, `protected_media/`, `.env`). `--with-db` adds a migrated `db.sqlite3` (demo data only); `-o FILE` sets the output. See SETUP.md → Sharing a copy. |

## 1. Seed a demo database

```bash
bash scripts/seed_demo.sh            # Windows: scripts\seed_demo.bat
```

Useful options of the Phase 4 seed (run it directly):

```bash
python manage.py seed_phase4_demo                 # case at stage 2 (cost sheet) — the default
python manage.py seed_phase4_demo --stage 5       # drive the demo case on to stage 5 (Audit)
python manage.py seed_phase4_demo --stage 18      # all the way to the end (closed)
python manage.py seed_phase4_demo --reset-passwords   # new passwords for existing demo accounts
python manage.py seed_startup --reset-demo        # remove every demo row again
```

The command is idempotent: run it again and it reuses the institution, the accounts and the case.

**Phase 5 demo** (inspection, resource persons, post-visit reports):

```bash
python manage.py seed_phase5_demo                    # case at stage 12: visit recorded, reports in draft
python manage.py seed_phase5_demo --leave-visit-open # stage 12, the Team Lead records the visit himself
python manage.py seed_phase5_demo --reset-passwords
```

It adds the Team Lead, Division Head, Deputy Director and a second QA officer, eight resource persons (each illustrates a §9 rule, two with portal accounts) and drives the Phase 4 demo case to stage 12.

## 2. Run the tests

```bash
bash scripts/run_tests.sh quick      # Phase 5 only (inspection/tests)
bash scripts/run_tests.sh phases     # Phases 1–5
bash scripts/run_tests.sh all        # everything
```

Tests use their own throw-away database; your `db.sqlite3` is never touched.
The 15 tests in `review_checks/test_review_findings.py` already failed on the code as received (before Phase 1); they are not caused by the QA work.

## 3. Run a browser walkthrough (optional)

Needs Playwright: `pip install playwright` then `python -m playwright install chromium`.

```bash
python manage.py shell < scripts/set_demo_passwords.py      # every demo account → Walk-Through#2026
python manage.py runserver 0.0.0.0:8000                      # in another terminal
python scripts/walkthroughs/phase4_walk.py                   # screenshots → ./walkthrough_shots/phase4/
python scripts/walkthroughs/phase5_walk.py                   # screenshots → ./walkthrough_shots/phase5/
python scripts/build_phase5_doc.py --shots walkthrough_shots/phase5   # → PHASE5_WALKTHROUGH.docx
```

Settings (environment variables): `WALK_OUT` (screenshot folder), `WALK_INTERNAL` (default `http://localhost:8000`),
`WALK_PORTAL` (default `http://qa.localhost:8000`), `WALK_PASSWORD` (default `Walk-Through#2026`).
The Phase 4 walkthrough expects the demo case at stage 2 (a fresh `seed_phase4_demo`); the Phase 5 walkthrough expects a fresh `seed_phase5_demo` (stage 12, reports in draft). For the portal pages also give `admin@p4demo.local` the walkthrough password.
