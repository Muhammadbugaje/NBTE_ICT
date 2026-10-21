# NBTE QA System — Phase 2 migrations

Four new migrations. All are additive: no existing table loses a column, and no existing data is rewritten. No migration contains data: lookups, document rules, fee schedules, flows and action policies are created by `python manage.py seed_startup`, never by a migration.

Apply them in the usual way (Django orders them by their dependencies):

```bash
python manage.py migrate
python manage.py seed_startup          # adds the Phase 2 settings, lookups, flows and policies
```

| # | App | Migration | Forward | Reverse | Depends on |
|---|---|---|---|---|---|
| 1 | `qa_core` | `0002_outbound_email` | Creates `qa_core_outboundemail` (the console e-mail log shown at **QA e-mail outbox**: subject, recipients, text and HTML body, category, created time) | Drops the table | `qa_core.0001_initial` |
| 2 | `accreditation` | `0001_onboarding` | Creates `applicationcycle`, `application` (SOP-01 onboarding application: reference, institution, cycle, status, round, submitted by/at, routed department, last return remark and department, declaration accepted) and `departmentalclearance` (one row per branch per round: department, branch code, status, remark, decided by/at). Adds the `accreditation.*` permissions on `Application.Meta` | Drops the three tables. Reverse `institutions.0002` first (it depends on this migration) | `institutions.0001_initial`, `staff.0011`, `accounts` user model |
| 3 | `institutions` | `0002_full_institution_record` | Adds to `institution`: `ownership`, `institution_type`, `category`, `state`, `lga`, `zone`, `year_established`, `cac_rc_number`, `website`, `origin`, `portal_enabled` (default true), `approved_at`, `delisted_at`, `delist_reason`, `former_names`, `desk_officer`. Creates the lookups (`institutionownership`, `institutiontype`, `institutioncategory`, `discipline`, `programmelevel`, `programmecategory`), `documentkind`, `documentrequirement`, `institutioncontact`, `institutionaddress`, `institutionuser`, `programme`, `institutiondocument`, `institutionrequesttype`, `institutionrequest`, `zonalassignment`, `impersonationsession`, `institutionimportbatch`. Adds every `institutions.*` permission on `Institution.Meta` | Drops the new tables and columns. The Phase 1 `institution` table (code, name, status, handling department) is kept | `institutions.0001`, `accounts.0014`, `accreditation.0001`, `qa_core.0002`, `staff.0011`, `contenttypes` |
| 4 | `payments` | `0001_payments` | Creates `bankaccount`, `feeschedule`, `paymentobligation`, `paymentslip`, `paymentattempt`, `proofofpayment`, `paymentverification`, `paymentadvice`, `paymentcredit`, `refund`, `bankstatementimport`, `bankstatementline`, `webhooklog`. Adds the `payments.*` permissions | Drops the 13 tables | `institutions.0002`, `contenttypes`, `accounts` user model |

## Existing data

* Existing institutions (the Phase 1 stub rows, e.g. the demo `DMP`) keep their code, name, status and department. The new columns are blank/null until edited, except `portal_enabled` (true) and `origin` (`nbte`).
* `seed_startup` adopts any existing institution into the `institution_lifecycle` flow without changing its status.
* Uploaded files (documents, proofs, slips, advices, statements, import sheets) are written under `PROTECTED_MEDIA_ROOT/qa/…` and served only through the protected download views.

## Full rollback of Phase 2 (if ever needed)

Run this on a copy first. It removes all Phase 2 data (institution records beyond the stub, applications, bills, payments).

```bash
python manage.py migrate payments zero
python manage.py migrate institutions 0001
python manage.py migrate accreditation zero
python manage.py migrate qa_core 0001
```

Then remove the Phase 2 rows that `seed_startup` created in Phase 1 tables (they are harmless if left): the `qa.payment.*`, `qa.ownership_change.*`, `qa.institution.*` and `qa.documents.*` settings, the `institution_lifecycle`, `onboarding` and `refund` flows, and the Phase 2 action policies. The rollback was exercised on a scratch database on 18 Oct 2026 (forward → zero → forward).
