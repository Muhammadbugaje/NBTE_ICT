# NBTE QA System — Phase 4 test plan

Two parts: the automated tests (`accreditation/tests/test_sop03.py`, 53 tests) and the interactive verification asked for in the Phase 4 prompt, which was done in a real browser (Playwright, Chromium) against a copy of your migrated, seeded `db.sqlite3`.

## How to run

```bash
bash scripts/run_tests.sh quick     # Phase 4 (≈15 min)       Windows: scripts\run_tests.bat quick
bash scripts/run_tests.sh phases    # Phases 1–4 (≈1 h)
bash scripts/run_tests.sh all       # the whole project (≈70 min)
```

## A. Automated tests — `accreditation/tests/test_sop03.py`

The fixture builds the case exactly like `seed_phase4_demo`: real QA request → fee waived → SSQs submitted → QA case → SOP-03 → tasks.

| Area | Tests | What they prove |
|---|---|---|
| Flow on the engine | `test_qa_case_flow_is_seeded_on_the_engine` | `qa_case` is a published FlowDefinition: stages numbered 1–17, stage 1 a parallel gate with Audit/Finance/ICT branches, every linear stage has a return that requires a remark, 17 is a system stage |
| Start + stage 1 | `test_case_starts_with_a_task_for_the_owning_director`, `test_stage_1_opens_three_parallel_branches_with_a_task_each` | Task created on entry by the system user, category `qa-sop03`, due date; three branch tasks for the three Directors; the join waits for all three; stage 2 task for the desk officer |
| 5+ programme branch (R-04) | `test_big_cases_need_two_ict_officers_r04`, `test_small_cases_need_one_ict_officer` | With the threshold at 2, one ICT officer is refused (choice and approve), two pass |
| **17-stage walk** | `test_seventeen_stage_walk_with_approvals`, `test_seventeen_stage_walk_through_the_screens` | Every stage reached in order with the right holder; each task closes *approved* and completes; cost sheet ES-approved, personnel approved, visit ES-approved, fund release released for the approved total; 19 links, none open; the second test does it all through the case screen's POSTs |
| **Returns** | `test_return_needs_a_remark`, `test_return_from_stage_5_goes_to_stage_4_with_the_remark_pinned`, `test_every_stage_returns_to_the_exact_submitter`, `test_return_from_stage_2_goes_back_to_the_director_and_resubmits`, `test_branch_return_goes_back_to_the_department` | No remark → refused; stage 5 → 4 to the Director who confirmed, remark pinned, priority high; **every** stage 3–16 returned once to the exact person who submitted the previous stage, remark pinned, then re-approved |
| **Task Bounce** | `test_task_closes_when_the_qa_action_is_taken`, `test_linked_task_cannot_be_completed_or_cancelled_from_tasks` | QA action closes the task and opens the next; *mark done* / *cancel* refused from Tasks; task screen shows **Open in QA** (with the right link) and no *Mark done*; inbox card shows **Open in QA** |
| **Reassignment** | `test_reassign_to_an_overworked_officer_is_rejected_r01`, `…_grade_range_is_rejected_r20`, `…_visit_team_is_rejected_r02`, `test_valid_reassign_moves_the_task_and_the_case_field`, `test_who_may_reassign`, `test_assignee_returning_the_task_flags_the_case` | R-01 refused both from QA and through Tasks' own `reassign` (signal re-validates and **rolls back**: old assignee kept); R-20 and R-02 refused; a valid one moves the task, updates the case field and writes a *reassigned* event; ICT monitor / assignee / other departments may not; *Return to assigner* flags the case |
| **ICT monitor** | `test_ict_monitor_cannot_write` ×13 (parametrised over **every POST URL** of the case screens), `test_ict_monitor_can_view_every_tab_and_the_drift_report`, `test_monitor_cannot_act_through_the_service_either` | 403 on every POST and nothing changes; every tab, the board and drift report open read-only |
| Scope / rules | `test_out_of_scope_staff_get_404`, `test_only_the_task_holder_can_act`, `test_cost_sheet_editor_rules`, `test_team_lead_must_hold_a_team_lead_role`, `test_only_the_pay_officer_releases_funds` | 404 out of scope; only the holder acts; department edits at 2, Audit at 5 with a note, ES cannot edit; team-lead roles; release needs a reference and `release_funds` |
| **SLA reminders** | `test_sla_reminders_at_the_configured_percentages`, `test_reminder_percentages_follow_the_setting`, `test_reminder_command_runs` | 50 % and 90 % once each, then one breach notice; follows `qa.stage.reminder_at_percent` |
| **Reconciliation** | `test_reconciliation_repairs_an_orphaned_task`, `test_reconcile_command` | Drift (instance moved without the bridge): dry run reports 1 orphan + 1 missing and changes nothing; the real run closes the orphan (*reconciled*) and opens the missing task; a third run is clean |
| Timeline | `test_timeline_matches_the_task_history` | Every approve/return event ↔ one closed task link (same stages); the remark shows on the Timeline tab |
| Send Back To Audit | `test_send_back_to_audit_service`, `test_send_back_to_audit_action_handler_is_live` | Refused before stage 7; from stage 8 back to 5 with a new task for the Audit officer; the §6 handler is available, validates and applies |
| Screens / settings / seed | `test_case_board_and_queues`, `test_rate_table_settings`, `test_seed_phase4_demo_is_idempotent` | Board (full and HTMX partial), ES and Pay Officer queues; rate edits by the Finance Director only; the seed run twice gives one case and unchanged accounts |
| Pinned actors | `test_stages_go_to_the_pinned_people_even_with_other_directors`, `test_reassigning_a_director_stage_pins_the_new_holder` | Another ES / Director in the database doesn't steal the case's stages; reassigning a Director stage re-pins |

Phase 1–3 suites are run unchanged except for the updates listed in PHASE4_DONE.md.

## B. Interactive verification (the prompt's six steps)

Done on a copy of your migrated, seeded `db.sqlite3` (so your own database stays at stage 2 for you to try). Screenshots in `PHASE4_UI_SCREENSHOTS/`. Script: `scripts/walkthroughs/phase4_walk.py`.

| Step | What was done | Screenshots |
|---|---|---|
| 1 | `migrate` on `db.sqlite3` (backed up first) | — (see PHASE4_MIGRATIONS.md) |
| 2 | `seed_phase4_demo` → case at stage 2, credentials printed | PHASE4_DEMO_CREDENTIALS.md |
| 3 | Desk officer → Case Board → demo case → cost sheet: three lines added (₦370,000 provisional, *rates not confirmed* banner) → submitted; personnel list (Director as team lead) → submitted | `01`–`05` |
| — | Director: *My stage work* → confirms the personnel | `06` |
| 4 | Audit officer → **Tasks inbox** → card with **Open in QA** → task screen (no *Mark done*; **Open in QA** panel) → Open in QA → cost tab in **Audit mode**: member days 3 → 2 with the note "Two nights are enough…" → submits | `07`, `08`, `09` |
| 5 | Director of Audit confirms → **ES** opens the cost queue, reviews, **returns** with a remark → Director of Audit sees the **pinned remark**, re-confirms → ES **approves** | `10`, `11`, `12` |
| 6 | Tasks tab: every earlier task *Completed* (approved / returned), the new *Add the visit details* task open; the desk officer's inbox shows the new task | `13`, `14`, `15` |
| + | ICT monitor read-only; task-link drift; Case Board and case on a phone | `16`–`19` |

No browser console errors and no server errors during the walk.

## C. Try it yourself

```bash
python manage.py runserver 0.0.0.0:8000
```

1. Sign in at `http://localhost:8000/` as `qa.officer@demo.nbte.local` (password in PHASE4_DEMO_CREDENTIALS.md) → sidebar **QA cases (SOP-03) → Case Board** → the KDP case → **Cost sheet** tab → add lines → **Submit cost sheet**.
2. Continue with the people in the "Acts at" column of the credentials file: each one finds the case in **Tasks → Assigned to me** (button **Open in QA**) or **QA cases → My stage work**.
3. Try a return: write a remark and press **Return with remark** — the previous person gets the task back with your remark pinned.
4. Try `qa.ict_monitor@demo.nbte.local`: everything is visible, nothing can be changed.
