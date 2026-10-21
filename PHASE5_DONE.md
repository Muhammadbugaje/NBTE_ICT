# NBTE QA System — Phase 5 definition of done

Phase 5: inspection and resource persons. Checked on 19 Oct 2026.

## Checkpoints

| Checkpoint | Status |
|---|---|
| A — Models and migrations: resource persons, QA personnel, mappings, inspections, post-visit personnel and reports, endorsements, service history; the Institutional Admin's department | **PASS** |
| B — §9 rules engine, vetting, Availability Finder, double-booking guard, the two-officer actions live | **PASS** |
| C — Screens: registers, finder, mappings, inspections, Visit Calendar, Inspection Detail, the post-visit report and its endorsement chain, endorsement queue, service history, case Reports tab, portal pages | **PASS** |
| D — Stage 12–13 wired into SOP-03, task bridge for reports, service history writes, `seed_phase5_demo` | **PASS** |
| E — Tests pass (Phases 1–5); real database migrated and seeded; walkthrough, screenshots and Word document done | **PASS** |

## Definition of done

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | ResourcePerson, ResourcePersonDiscipline, QAPersonnel | **PASS** | `inspection.0001_inspection`; PHASE5_MIGRATIONS.md |
| 2 | §9 eligibility enforced at nomination and assignment (min rank, designations, max engagements …); no manual override | **PASS** | `test_eligibility.py` (18 tests); `test_no_override_*` |
| 3 | Vetting: nominate, CV upload, a vetter approves → active | **PASS** | `test_vetting_flow`, `test_vetting_needs_a_cv`; screenshots `03`, `04` |
| 4 | Availability Finder lists only eligible people: no double booking (travel buffer), R-11, conflict of interest / cooldown, annual cap, R-02 | **PASS** | `test_availability.py`; screenshots `06`, `07` |
| 5 | Concurrency: `select_for_update` + `booking_version` compare-and-swap; the second simultaneous booking is rejected | **PASS** | `test_concurrent_double_booking_threads_only_one_wins`, `test_two_officers_with_the_same_view_only_one_wins` |
| 6 | QAMapping, Inspection, InspectionTeamMember, PostVisitPersonnel | **PASS** | migration; `test_es_visit_approval_creates_the_inspection_with_the_mapped_team` |
| 7 | Visit Calendar (monthly) and Inspection Detail | **PASS** | screenshots `09`, `11`, `34` |
| 8 | Override Post-Visit Personnel (two-step) updates `attended` and feeds the service history | **PASS** | `test_corrected_attendance_writes_a_compensating_row`, `test_override_can_add_an_unplanned_attendee`; screenshot `12` |
| 9 | PostVisitReport + ReportEndorsement with real forms for stages 12–13 | **PASS** | screenshots `13`–`24`, `28`; `test_stage_13_needs_a_recommendation_per_programme` |
| 10 | B17: only attendees submit | **PASS** | `test_b17_only_attendees_write_and_submit` |
| 11 | Endorsement chain Division Head → Deputy Director → Director: endorse / add content / return with a mandatory remark | **PASS** | `test_endorsement_chain_return_with_mandatory_remark_and_resubmit`, `test_report_screens_submit_endorse_and_return`; screenshots `17`–`24` |
| 12 | ServiceRecord append-only, written when an inspection closes or an SSQ review completes | **PASS** | `test_recording_the_visit_writes_the_service_history_for_attendees_only`, `test_ssq_review_completion_writes_a_service_row` |
| 13 | Service History UI filterable by person, institution, programme, discipline, duty, date range; export | **PASS** | screenshots `25`, `26`; `test_list_screens_have_search_filters_and_pagination` |
| 14 | "No longer eligible" badge; existing work continues, new work blocked; listed by the nightly reconciliation | **PASS** | `test_falling_below_later_blocks_new_work_and_shows_the_badge`, `test_nightly_reconciliation_lists_people_no_longer_eligible`; screenshots `01`, `02` |
| 15 | Full stage 12–13 flow | **PASS** | `test_stage_12_advances_only_…`, `test_stage_13_*`, Phase 4's 17-stage walks |
| 16 | UI: search + ≥ 3 filters on every list; 25 rows + Show all (≤ 500); URL holds filters and page; HTMX everywhere; skeletons; tokens only, no inline styles; inline field errors | **PASS** | screenshots; `static/css/qa-p5.css`; 422 swap fix in `static/js/app.js` |
| 17 | `seed_phase5_demo`: the demo case advanceable to stage 12 | **PASS** | PHASE5_DEMO_CREDENTIALS.md (run twice: idempotent) |
| 18 | Resource-person portal and the institution's read-only inspections list | **PASS** | `test_resource_person_portal_findings_feed_the_report`; screenshots `29`–`32` |
| 19 | PHASE5_WALKTHROUGH.docx: cover, credentials table, step-by-step, embedded screenshots, embedded endorsement-chain diagram, appendices to the Master Plan | **PASS** | built by `scripts/build_phase5_doc.py` (python-docx `add_picture`); diagrams `docs/phase5/*.dot` → PNG |
| 20 | Phases 1–4 not broken; all earlier tests run | **PASS** | PHASE5_TEST_PLAN.md §B |

## Test results

| Run | Result |
|---|---|
| Phase 5 (`inspection/tests`) | **48 passed** |
| Phase 4 (`accreditation/tests/test_sop03.py`, updated for stage 12) | passed |
| Whole project | **995 passed, 15 failed, 1 skipped, 1 xfailed**. The 15 failures are the same `review_checks` tests that failed on the code as received and in Phases 1–4; nothing else fails (details in PHASE5_TEST_PLAN.md §B) |

## Not in Phase 5 (as agreed)

Decisions, letters, the capacity engine and falsification handling are Phase 6. The `raise_falsification` permission exists but nothing uses it yet.

## Known limits

* The Master Plan's optional PostgreSQL `ExclusionConstraint` on `(person, daterange)` is not added. The lock plus the compare-and-swap version already refuse the second booking on every database, and SQLite (development) has no exclusion constraints.
* The demo records the visit on the two days before today, even though its planned dates are three weeks ahead. This lets you try stage 12 at once; in real use the Team Lead records the visit after it happens.
* The service history has no "zone" filter: institutions have no zone field yet.
