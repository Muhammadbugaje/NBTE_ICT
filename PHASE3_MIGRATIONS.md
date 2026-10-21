# NBTE QA System — Phase 3 migrations

Phase 3 adds **one migration**: `accreditation/migrations/0002_qa_requests_ssq.py`.
No migration of any other app was added or changed, and no existing column was altered or dropped.

```bash
python manage.py migrate            # applies accreditation.0002_qa_requests_ssq
python manage.py seed_startup       # adds the Phase 3 data (idempotent)
python manage.py makemigrations --check --dry-run   # → No changes detected
```

## What `0002_qa_requests_ssq` creates

Depends on `accreditation.0001_onboarding`, `institutions.0002_full_institution_record`, `payments.0001_payments`, `staff.0011…`, `contenttypes` and the user model.

| Model (table) | Purpose | Notes |
|---|---|---|
| `QAType` | The 7 QA types (D1) | Code, eligible programme statuses, reassessment-window flag, admin-programme flag, fee schedule, current SSQ template |
| `SSQTemplate` | Versioned SSQ schema (JSON) | `draft / published / retired`, `is_current`; unique `(qa_type, version)`; permissions `manage_ssq_templates`, `publish_ssq_template` |
| `CatalogueItem` | Curriculum catalogue (B3/D3, R-22) | Programme name and/or category, facility/equipment, required quantity, unit, version, effective date; permission `manage_catalogue` |
| `QARequest` | SOP-02 request (institution-scoped) | Unique reference, QA type, cycle, status mirrored from the `qa_request` flow, link to the generic `InstitutionRequest`; permissions `view_qa_case`, `raise_query`, `review_ssq`, `clear_qa_programmes` |
| `QARequestProgramme` | Programme rows of a request | `active / dropped / replaced`, `replaced_by`, programme-clearance fields |
| `SSQ` | One questionnaire per programme row and version | `data` / `meta` JSON, status mirrored from the `ssq` flow, `version`, `supersedes`, `is_current`, department assignment; unique `(qa_request_programme, version)` |
| `Submission` | Immutable snapshot taken on every SSQ submission | Raw data, rendered sections (catalogue names and quantities frozen), evidence list, SHA-256 checksum; `save()` on an existing row and `delete()` raise; unique `(ssq, version)`; `ssq` is `PROTECT` |
| `Evidence` | A file behind an SSQ field | Uploaded (restricted storage) **or** `reused_from` an accepted `InstitutionDocument` (`PROTECT`) |
| `Query` / `Response` | NBTE question → institution answer | Generic subject (request, SSQ or evidence), due date, `open / answered / closed`; response may carry one restricted file |
| `QACase` | The SOP-03 case shell | Created **empty** (`awaiting_sop03`) when every SSQ of a request is submitted; its stages arrive in Phase 4 |

Also: `ApplicationCycle.qa_types` (new many-to-many, so a cycle can open or close QA types).

Every institution-bearing model uses `InstitutionScopedManager` (`objects`) + `UnscopedInstitutionManager` (`unscoped`) with `INSTITUTION_PATH`, exactly like Phase 2; the Phase 1 enforcement test (layer 1) covers them automatically.

## Reversing

```bash
python manage.py migrate accreditation 0001
```

drops the Phase 3 tables (and the `ApplicationCycle.qa_types` link table). Phase 2 data is untouched. Tested forward → backward → forward on a copy of the Phase 3 demo database on 18 Oct 2026.

Seeded *data* (flows, QA types, templates, catalogue, settings, notification templates) is not removed by reversing the migration; remove it by hand if you really roll back (the flows `qa_request` and `ssq` are harmless without their tables). `seed_startup --reset-demo` removes all demo rows, including Phase 3 ones (submissions are deleted in bulk because a snapshot refuses `delete()` one by one).

## Data changes made by `seed_startup` (not migrations)

All additive and idempotent; nothing existing is overwritten except the two flagged data fixes:

* 7 QA types; 2 SSQ templates published as version 1 (Institutional Administration, Reaccreditation) and linked to their QA types; 16 starter catalogue lines (ND Computer Science, HND Electrical/Electronic Engineering Technology).
* Flows `qa_request` (SOP-02) and `ssq` — only when missing (published flows are never re-seeded).
* 7 settings (`qa.programme.reassessment_window_months`, `qa.programme.expiry_warning_days`, `qa.portal.staff_sign_in_enabled`, `qa.ssq.submit_roles`, `qa.ssq.autosave_delay_ms`, `qa.query.default_due_days`, `qa.qa_request.max_open_per_type`). `qa.documents.max_upload_mb` and `qa.fee.qa_request.*` already existed (Phase 1/2) and are now used.
* Reference format `query` (`NBTE-QRY-{YEAR}-{SEQ:5}`).
* 15 notification templates and 16 rules (QA request, SSQ and query events).
* Role permissions (additive) and `QA Settings Admin` group permissions.
* **Data fix 1:** reference format `qa_request` is switched from `NBTE/QA/{YEAR}/{INST}/{SEQ:5}` (the same pattern as onboarding applications, so the first request and the first application of an institution printed the same number) to `NBTE/QAR/…` — **only if it has never issued a number**.
* **Data fix 2:** the Phase 2 action policy `institutions.add_programme_to_qa` (seeded disabled, "Needs Phase 3–5 records") is enabled — only while it still carries the Phase 2 placeholder description.
* `--with-demo` only: an academic-department account for Demo Polytechnic, DMP programme expiries moved inside the reassessment window (4 and 5 months), an Institutional Administration programme, three accepted documents and one awaiting review, and the demo QA fee (₦50,000 + ₦150,000 per programme, confirmed). **Set the real amounts before go-live.**
