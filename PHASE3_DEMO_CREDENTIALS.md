# NBTE QA System — Phase 3 demo credentials

> **Change these passwords immediately, or delete the demo accounts.** Every demo account has `must_change_password=True` (the first sign-in forces a new password). Remove all demo data with `python manage.py seed_startup --reset-demo`.

## Which database these belong to

The **ready-to-run demo database shipped in this folder: `qa_phase3_demo.sqlite3`**, built on 18 Oct 2026 by upgrading the Phase 2 demo database exactly as you would upgrade a real one:

```bash
cp qa_phase2_demo.sqlite3 qa_phase3_demo.sqlite3
DATABASE_URL=sqlite:///qa_phase3_demo.sqlite3 python manage.py migrate          # accreditation.0002
DATABASE_URL=sqlite:///qa_phase3_demo.sqlite3 python manage.py seed_startup --with-demo
```

So every Phase 1/2 account keeps its password, and `seed_startup` printed a password once for the one new Phase 3 account. All passwords below were checked against `qa_phase3_demo.sqlite3` before packaging. Your own `db.sqlite3` was **not touched**. The Phase 3 demo documents are in `protected_media/qa/institution_documents/` (shipped).

| Account | Password | Type / role | Where | Use it for in Phase 3 |
|---|---|---|---|---|
| `qa.officer@demo.nbte.local` | `U6GBfL0msrZyvsg0` | staff · `officer` (DPAP) | **Portal and** internal | Staff Portal Dashboard on the portal; SSQ review, raise queries |
| `qa.director@demo.nbte.local` | `yOrbfVvyWDcBOyz4` | staff · `director` (DPAP) | Internal | Publish SSQ templates, catalogue, confirm *Add Programme to QA* / *Cancel Request* |
| `qa.finance@demo.nbte.local` | `AEaWXqmtQROo0WwN` | staff · `qa-finance-officer` | Internal | Propose the QA-fee waiver |
| `finance.director@demo.nbte.local` | `CrZjKvXdIpNxxQI2` | staff · `qa-finance-director` | Internal | Confirm the waiver (or a payment) |
| `admin@demo-poly.local` | `E0RtxUEmKr8_KBBJ` | institution · `inst-admin` (DMP) | Portal | QA requests, SSQ workspace, queries |
| **`academic@demo-poly.local`** (new) | **`4G3x9E9NOisCeQA5`** | institution · `inst-academic-dept` (DMP) | Portal | The department return path (completes the sections it is sent) |
| `admin@demo-health.local` | `zwMxH9aTvZQfaSP2` | institution · `inst-admin` (DCH) | Portal | A second institution (isolation checks) |
| `legal.officer@…`, `finance.clearance@…`, `planning.academic@…`, `planning.physical@…`, `resource@…` | as in PHASE2_DEMO_CREDENTIALS.md | | | Unchanged |

What `--with-demo` added for Phase 3 (Demo Polytechnic, `DMP`): ND Computer Science and HND Electrical/Electronic Engineering Technology now expire 4 and 5 months from the seeding date (inside the six-month reassessment window); an **Institutional Administration** programme; accepted *Organogram*, *CAC certificate* and *Certificate of Occupancy* (they prefill the SSQs) and a *Physical Master Plan* still awaiting review (it must not prefill); the QA fee set to **₦50,000 + ₦150,000 per programme, confirmed** (demo only — set the real amounts before go-live).

## Try it

```bash
DATABASE_URL=sqlite:///qa_phase3_demo.sqlite3 python manage.py runserver
```
Internal system: http://localhost:8000/ · QA portal: http://qa.localhost:8000/ (e-mails are written to the console and the QA e-mail outbox, never sent).

## The Phase 3 walkthrough in one page

1. **Portal, as `qa.officer`** → the **Staff Portal Dashboard** with **Go to Internal System** (no institution data on the portal).
2. **Portal, as `admin@demo-poly.local`** → *QA requests* → **New QA request** → *Reaccreditation* → tick ND Computer Science (and HND EEE) → *Create draft request*. Optionally *Drop…* one programme, then tick the declaration → **Submit request** → *QA processing fee due*.
3. **Internal, `qa.finance`** → Finance (QA) → Bills → the QA bill → reason → **Propose waiver**; **`finance.director`** → Pending confirmations → *Confirm and apply*. The request moves to **SSQ in progress** (or pay it through the simulator; the Director of Finance confirms).
4. **Portal, admin** → the request → *Continue* on the SSQ → fill a section → watch *All changes saved* → **reload the page** (answers are there) → fill the remaining sections (tables: *Add row*; catalogue: quantities only) → *Previous visit* → **Upload** a file → declaration → **Submit SSQ**.
   *Department path:* before submitting, *Send sections to a department* → `academic@demo-poly.local` completes them on the portal and *Sends to my institution*.
5. **Internal, `qa.officer`** → QA requests (SOP-02) → **SSQ review queue** → open the submission (immutable snapshot, checksum *Verified*, catalogue shortfalls highlighted) → **Raise query** (or a query on a single file). *Accept* or *Return to institution* (returns start version 2; version 1 stays on record).
6. **Portal, admin** → the dashboard shows the query → *Respond* (text + attachment). The officer is notified and can *Close* it.
7. **Internal, `qa.director`** → QA settings → **SSQ templates** (*New version* → builder → *Publish*) and **Curriculum catalogue**.
8. Also try the *Institutional Administration* QA type (governance answers prefilled from the record) — it can run alongside the Reaccreditation request.

## Removing the demo

```bash
DATABASE_URL=sqlite:///qa_phase3_demo.sqlite3 python manage.py seed_startup --reset-demo
```
Deletes exactly the tagged demo rows (now 12 users) and everything hanging off them, including QA requests, SSQs, submissions, evidence, queries and QA cases.
