# NBTE QA System — Phase 5 test plan and results

```bash
python -m pytest -q                              # everything (Phases 1–5)
python -m pytest -q inspection/tests             # Phase 5 only
```

## A. Automated tests (Phase 5)

### Eligibility — `inspection/tests/test_eligibility.py` (18 tests)

| Rule (§9 v1.1) | Setting | At nomination / registration | At assignment (mapping) |
|---|---|---|---|
| 1 Minimum seniority | `qa.resource_person.min_rank` | `test_rule1_min_rank_blocks_staff_below_the_role`, `test_rule1_refused_at_qa_personnel_registration`, `test_rule1_refused_at_nomination_of_an_nbte_staff_resource_person` | `test_rule1_and_rule2_refused_at_assignment_for_qa_personnel` |
| 2 Allowed posts / ranks | `qa.resource_person.required_designations` | `test_rule2_external_rank_must_be_listed_at_nomination`, `test_rule2_staff_designation_must_be_listed` | `test_falling_below_later_blocks_new_work_and_shows_the_badge`, `test_rule1_and_rule2_refused_at_assignment_for_qa_personnel` |
| 3 Concurrent load (R-11) | `qa.resource_person.max_active_engagements` | — (a load, not a qualification) | `test_rule3_concurrent_load` |
| 4 Open SSQ reviews (R-01) | `qa.resource_person.max_open_ssq_per_officer` | — | `test_rule4_open_ssq_reviews` |
| 5 Annual visits | `qa.resource_person.max_visits_per_year` | — | `test_rule5_annual_cap` |
| 6 Institutional Admin's department | `qa.institution.admin_required_department` | `test_rule6_institutional_admin_department` (portal invite), `test_rule6_at_add_tvet` | — (institution accounts are not mapped) |
| 7 Chief Executive is the head | `qa.institution.chief_executive_required_position` | `test_rule7_chief_executive_must_be_the_head` | — |
| No override | — | `test_no_override_permission_exists` | `test_no_override_even_for_a_superuser` |
| Vetting | — | `test_vetting_flow` (the nominator cannot vet; a rejection needs a remark), `test_vetting_needs_a_cv` | — |
| Falling below later | — | `test_nightly_reconciliation_lists_people_no_longer_eligible` | `test_falling_below_later_blocks_new_work_and_shows_the_badge` |

### Availability Finder and double booking — `inspection/tests/test_availability.py` (14 tests)

| What | Test |
|---|---|
| Only eligible people listed; *show all* gives the reasons | `test_finder_lists_only_eligible_people`, `test_finder_screen_lists_eligible_people_with_filters` |
| Status | `test_filter_status` |
| Discipline + `allow_secondary_discipline` | `test_filter_discipline_and_secondary_setting` |
| Double booking with `travel_buffer_days` | `test_filter_double_booking_with_the_travel_buffer` |
| R-11 one programme at a time | `test_filter_one_programme_at_a_time_r11` |
| Conflict of interest | `test_filter_conflict_of_interest` |
| Cooldown at the same institution | `test_filter_cooldown` |
| Annual cap | `test_filter_annual_cap` |
| R-02 reviewer is not the visitor | `test_filter_reviewer_is_not_a_visitor_r02` |
| Ranking (fewest visits first) | `test_ranking_fewest_visits_first` |
| Two officers with the same view: the second save is refused | `test_two_officers_with_the_same_view_only_one_wins` |
| **Two threads booking at the same moment: exactly one wins** | `test_concurrent_double_booking_threads_only_one_wins` (real threads, transactional database) |
| Confirmation re-checks under the lock | `test_confirmation_rechecks_under_the_lock` |

### Visit, reports, endorsement, service history — `inspection/tests/test_reports.py` (16 tests)

| What | Test |
|---|---|
| ES visit approval creates the inspection with the mapped team; stage 12 opens one draft report per programme for the Team Lead | `test_es_visit_approval_creates_the_inspection_with_the_mapped_team` |
| Only the Team Lead records the visit; not in the future | `test_only_the_team_lead_records_the_visit` |
| Recording writes service rows for attendees only; append-only | `test_recording_the_visit_writes_the_service_history_for_attendees_only` |
| **B17** only attendees write and submit (resource persons included) | `test_b17_only_attendees_write_and_submit` |
| Submit needs a summary and a recommendation | `test_submit_needs_a_summary_and_a_recommendation` |
| **Endorsement chain**: holders per layer, add content, return without a remark refused, return with a remark goes to the submitter (pinned), resubmit → version 2 → three endorsements → director-endorsed; full trail | `test_endorsement_chain_return_with_mandatory_remark_and_resubmit` |
| Stage 12 → 13 only when every report is director-endorsed | `test_stage_12_advances_only_when_every_report_is_director_endorsed` |
| Stage 13 recommendation per programme, then approve → stage 14 | `test_stage_13_needs_a_recommendation_per_programme` |
| Stage 13 return sends every report back to its writer | `test_stage_13_return_sends_every_report_back_to_its_writer` |
| **Corrected attendance writes a compensating row** (two officers; the original is never edited) | `test_corrected_attendance_writes_a_compensating_row` |
| Override adds an unplanned attendee | `test_override_can_add_an_unplanned_attendee` |
| SSQ review completion writes a service row | `test_ssq_review_completion_writes_a_service_row` |
| Screens: inline errors (422), submit, endorse, return with an inline error, pinned remark shown | `test_report_screens_submit_endorse_and_return` |
| Lists: search + ≥ 3 filters; HTMX partial; Show all | `test_list_screens_have_search_filters_and_pagination` |
| ICT monitor read-only (R-13) | `test_ict_monitor_is_read_only` |
| Resource-person portal: findings (inline error, saved, shown on the report), history, profile; institution sees its inspections read-only | `test_resource_person_portal_findings_feed_the_report` |

### Earlier phases

* `accreditation/tests/test_sop03.py`: the 17-stage walks were updated for stage 12. It has no case task now: the report sub-flow runs it, and stage 13 needs a recommendation per programme (the screen walk posts it to the new Reports tab). The case's task links are now 18, not 19, and the "return at every stage" walk skips stages 12–13 (their returns are covered above).
* `institutions/tests/test_actions.py`: the three Phase 5 two-officer actions are no longer "later phase". They are live, and they still refuse an invalid request.
* `qa_core/tests/test_host_routing.py`: the portal's URL list includes `engagements/`, `service-history/`, `profile/` and `inspections/`. The internal and portal URL tables are still disjoint.

## B. Results

| Run | Result |
|---|---|
| Phase 5 (`inspection/tests`, 48 tests) | **48 passed** |
| Whole project (`python -m pytest`, Phases 1–5 and the non-QA apps) | **995 passed, 15 failed, 1 skipped, 1 xfailed** (1,012 tests) |

The 15 failures are the same 15 tests in `review_checks/test_review_findings.py` that failed on the code as received and in Phases 1–4 (documented findings in the original codebase, outside the QA system). Nothing else fails.

**How it was run.** The whole suite takes about two hours on this machine, so it ran in two halves at the same time: (a) `qa_core`, `accreditation` and `inspection`; (b) everything else. Four tests needed attention:

* `test_seed_startup.py::test_first_run_creates_everything` pinned the set of seeded flows. Phase 5 adds `inspection` and `post_visit_report`, so the list was updated.
* `test_actions.py::test_phase5_actions_are_live_but_refuse_an_invalid_request` showed that *Override Post-Visit Personnel* started on the wrong kind of record gave a server error instead of a clear refusal. The handler now checks the target first.
* `test_availability.py::test_finder_screen_lists_eligible_people_with_filters` looked for the word "annual". The reason on screen names the setting (`max_visits_per_year`), so the assertion now checks for that.
* Two tests that use pytest's `caplog` fixture errored only because the halves ran with `-p no:logging`.

All four were re-run normally and pass. One test was added after the run started (`test_rule1_and_rule2_refused_at_assignment_for_qa_personnel`); it was run on its own and passes.

## C. Interactive verification (the prompt's seven steps)

Done on a copy of your migrated, seeded `db.sqlite3` (your own database stays at stage 12, reports in draft, for you to try). Screenshots are in `PHASE5_UI_SCREENSHOTS/`; the script is `scripts/walkthroughs/phase5_walk.py`.

| Step | What was done | Screenshots |
|---|---|---|
| 1 | `migrate` on `db.sqlite3` (backed up first to `db.sqlite3.before-phase5-20261019-121257`) | — (see PHASE5_MIGRATIONS.md) |
| 2 | `seed_phase5_demo` → case at stage 12, inspection recorded, two reports in draft, credentials printed | PHASE5_DEMO_CREDENTIALS.md; `01`–`12` (registers, finder, mappings, calendar, inspection) |
| 3 | Team Lead opens the EEE report. Submitting it empty shows the errors inline; after filling it in, it is submitted to the Division Head | `13`, `14`, `15` |
| 4 | Division Head: *Endorsements → Waiting on me* → endorses with a comment | `16`, `17`, `18` |
| 5 | Deputy Director: *Return with remark* with an empty remark is refused inline; with a remark → returned to the Team Lead | `19`, `20` |
| 6 | Team Lead: the task is back in the inbox with the remark pinned; the summary is fixed → re-submitted (v2); the chain completes (Division Head, Deputy Director, Director) | `21`–`24` |
| 7 | Service history: a row per attendee; filter by person; endorsements list; the case's Reports tab | `25`–`28` |
| + | Resource-person portal (engagements with findings, history, profile & CV); the institution's read-only inspections; phone width | `29`–`34` |

There were no server errors during the walk. The only browser console messages were the two intended 422 validation responses (steps 3 and 5).

**Fixed during the walk:** the shared `static/js/app.js` refused to swap any 4xx response. That hid every inline validation error that comes back as 422, in all QA screens and not only Phase 5. A 422 now swaps in place, as §20 requires.

## D. Try it yourself

```bash
python manage.py runserver 0.0.0.0:8000
```

1. Sign in at `http://localhost:8000/` as `qa.team_lead@demo.nbte.local` (password in PHASE5_DEMO_CREDENTIALS.md). In the sidebar, open **Inspection (§9) → Inspections** → the KDP inspection → a report. Write it and press **Submit to the Division Head**.
2. Sign in as `qa.division_head@…` → **Endorsements** → **Endorse**. Then sign in as `qa.deputy_director@…` → **Return with remark**. Then back as the Team Lead: fix the report and **Re-submit**.
3. When both reports are director-endorsed, the case moves to stage 13. Open the case's **Reports** tab as `qa.director@…`, confirm a recommendation per programme, then **Confirm recommendation**.
4. Look at **Service history**, the **Availability finder** (choose the case; *Also show who is not eligible, and why*) and the **Visit calendar**.
5. On the portal (`http://qa.localhost:8000/`), sign in as `rp.yakubu@demo.nbte.local` to see the resource person's pages.
