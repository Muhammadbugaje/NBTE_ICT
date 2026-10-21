# NBTE QA System — Phase 5 migrations

Phase 5 adds **two migrations**. No other app's migrations were added or changed, and no existing column was altered or dropped.

| Migration | App |
|---|---|
| `inspection/migrations/0001_inspection.py` | Resource persons, QA personnel, QA mappings, inspections, post-visit personnel and reports, endorsements, service history |
| `institutions/migrations/0003_admin_department.py` | `InstitutionUser.institution_department` (§9 rule 6: the Institutional Admin's department) |

```bash
cp db.sqlite3 db.sqlite3.before-phase5-$(date +%Y%m%d-%H%M%S)   # back up first
python manage.py migrate                 # applies both
python manage.py seed_startup            # adds the Phase 5 data (idempotent)
python manage.py seed_phase5_demo        # the demo (see PHASE5_DEMO_CREDENTIALS.md)
python manage.py makemigrations --check --dry-run   # → No changes detected
```

## `institutions.0003_admin_department`

Depends on `institutions.0002_full_institution_record`.

| Change | Purpose |
|---|---|
| `InstitutionUser.institution_department` (char 120, blank) | The department the user works in at the institution. Inviting an Institutional Admin (portal *Users*, *Add TVET*, self-registration) checks it against `qa.institution.admin_required_department` when that setting is not blank. |

## `inspection.0001_inspection`

Depends on `accreditation.0003_sop03_case`, `institutions.0003_admin_department`, `qa_core.0003_task_bridge`, `staff.0011…` and the user model.

**People**

| Model | Scoped? | Purpose |
|---|---|---|
| `ResourcePerson` | No (NBTE-wide register; `home_institution` is declared in `INSTITUTION_FK_ATTRIBUTES`) | External experts and NBTE staff on the register: source, rank code, staff link, home institution (conflict of interest), professional body, status `nominated / vetted / active / suspended / retired / rejected`, nominated / vetted by and when, vetting remark, rating, `booking_version` (the double-booking guard) and the portal account (`user`, user type `resource_person`) |
| `ResourcePersonDiscipline` | — | Primary and secondary disciplines; unique `(person, discipline)` |
| `ResourcePersonFile` | Restricted file | CV, promotion proof, other; downloaded through the protected download view |
| `QAPersonnel` | No | NBTE staff registered for QA duty: disciplines, verification officer, can lead a visit, active, `booking_version` |
| `SSQReviewAssignment` | Yes (`ssq → … → institution`) | An open SSQ review held by an officer: created when an officer accepts the SSQ for review, completed when the review finishes (R-01 counts the open ones) |

**Work**

| Model | Scoped? | Purpose |
|---|---|---|
| `QAMapping` | Yes (`case__institution`) | A person (exactly one of `qa_personnel` / `resource_person`, check constraint) mapped to a programme of a case for a duty (`ssq_review / physical_verification / resource_inspection / tracking`) between two dates (check constraint `from ≤ to`). Status `proposed → active → released`, or `overridden`; `created_via` = the *Edit QA Mapping* pending action |
| `Inspection` | Yes (`case__institution`) | The visit created when the ES approves it (stage 10): reference `NBTE/INS/{YEAR}/{DEPT}/{SEQ}`, team lead, planned and actual dates, completed by, closed at. Runs on the `inspection` flow |
| `InspectionTeamMember` | Yes | The planned team: a QA mapping or a staff member from the case's personnel list |
| `PostVisitPersonnel` | Yes | Who actually went (exactly one of staff / QA personnel / resource person): planned flag, attended, the override reason and the pending action that changed it |
| `PostVisitReport` | Yes | One report per programme: findings (JSON by section), summary, team recommendation, the Directorate's recommendation (stage 13) and who made it, submitted by / at, version. Runs on the `post_visit_report` flow; unique `(inspection, programme)` |
| `ReportEndorsement` | Yes, **append-only** | Every submit / endorse / add content / return at each layer (team, Division Head, Deputy Director, Director) with the content and who did it |
| `ServiceRecord` | Yes (`institution`), **append-only** | One row per person per exercise: person type / id / name, links to the person, case, inspection, institution, programme and discipline, duty, dates, visit and SSQ counts, rating, allowance paid, `voids` (the row a compensating row cancels), reason and source. `delete()` raises; the database never updates a row |

**Permissions** (on `ResourcePerson`): `view_resource_person`, `manage_resource_person`, `vet_resource_person`, `initiate_edit_qa_personnel`, `confirm_edit_qa_personnel`, `initiate_edit_qa_mapping`, `confirm_edit_qa_mapping`, `initiate_override_post_visit_personnel`, `confirm_override_post_visit_personnel`, `submit_post_visit_report`, `endorse_post_visit_report`, `raise_falsification` (created now, used in Phase 6), `view_service_history`, `export_service_history`.

## Data added by `seed_startup` (idempotent)

| Data | Change |
|---|---|
| Flows | `inspection` (scheduled → completed → closed, or cancelled) and `post_visit_report` (draft → submitted → division_endorsed → deputy_verified → director_endorsed; any layer returns to *returned*, which resubmits to *submitted*) |
| `qa_case` flow (patch) | Stage 12 *Post-visit report* no longer opens a case task (the reports carry their own tasks); new transition `reports_endorsed` (condition `reports_total > 0 and reports_open == 0`) moves the case to stage 13 when the Director endorses the last report |
| Settings | `qa.matching.travel_buffer_days` (2), `qa.matching.same_institution_cooldown_months` (24), `qa.matching.allow_secondary_discipline` (false), `qa.matching.rank_score` (the finder's ranking formula) |
| Action policies | *Edit QA Personnel*, *Edit QA Mapping*, *Override Post-Visit Personnel* enabled (confirmer rank 100, 100, 80) |
| Role permissions | The Phase 5 permissions for directors, deputy directors, division heads, officers and the ICT monitor (view only) |
| Notification templates and rules | `rp.vetting_requested`, `rp.vetted` |

## Checks done

| Check | Result |
|---|---|
| `makemigrations --check --dry-run` | No changes detected |
| `migrate` on a copy of the Phase 4 database, then on `db.sqlite3` | Both migrations applied; no other app touched |
| `seed_startup` twice | Second run reports no changes (idempotent) |
| `seed_phase5_demo` twice | Second run creates nothing new |
| Backup | `db.sqlite3.before-phase5-<timestamp>` next to `db.sqlite3` |
