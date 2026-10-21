# NBTE QA System — Phase 5 changelog

**Phase 5 — Inspection and resource persons** (PHASES.md Phase 5; Master Plan §5.5, §9; SOP-03 stages 12–13). Built 19 Oct 2026.

What you can do now: keep a vetted register of resource persons and NBTE QA personnel; find who is eligible and free for a visit with the **Availability Finder** (every §9 rule enforced, no override); map them to a case's programmes (a second officer confirms); see visits on the **Visit Calendar**. When the ES approves a visit the **inspection** is created; after the visit the Team Lead records who went, which writes the **service history**. Stage 12 is now a real **post-visit report** per programme, written only by people who attended (B17) and endorsed Division Head → Deputy Director → Director, with *endorse*, *add content* and *return with remark*. Stage 13 records the Directorate's recommendation per programme. Resource persons have their own portal pages.

Out of scope, as agreed: decisions, letters, the capacity engine and falsification handling (Phase 6). The `raise_falsification` permission exists but nothing uses it yet.

---

## 1. Registers

| Screen (internal, `/qa/inspection/…`) | What it does |
|---|---|
| **Resource persons** | Search + filters (status, source, discipline, eligibility), 25 per page or *Show all*. A red **No longer eligible** badge when an active person's rank / designation no longer meets the rules: their confirmed work continues, new work is refused |
| **Nominations** | Nominate (inline field errors; rank and designation checked at once), upload the CV, a **vetter** approves → *active* or rejects with a remark. The nominator cannot vet; vetting needs a CV |
| **Resource person detail** | Record, disciplines, files, engagements, service history; vet, suspend / retire / reactivate, invite to the portal |
| **QA personnel** | NBTE staff registered for QA duty through *Edit QA Personnel* (two officers). Eligibility (rank, designation) is checked when it is started and when it is confirmed |

## 2. §9 eligibility rules — enforced server-side, no override

| # | Rule | Setting | Checked at |
|---|---|---|---|
| 1 | Minimum seniority | `qa.resource_person.min_rank` | nomination, QA personnel registration, every mapping |
| 2 | Allowed posts / ranks | `qa.resource_person.required_designations` | same |
| 3 | Concurrent load (R-11: one programme at a time) | `qa.resource_person.max_active_engagements` | every mapping |
| 4 | Open SSQ reviews (R-01) | `qa.resource_person.max_open_ssq_per_officer` | SSQ-review mappings (counts `SSQReviewAssignment`) |
| 5 | Annual visits | `qa.resource_person.max_visits_per_year` | visit mappings (sums the service history) |
| 6 | Institutional Admin's department | `qa.institution.admin_required_department` | inviting / registering an Institutional Admin, *Add TVET* |
| 7 | Chief Executive is the actual head | `qa.institution.chief_executive_required_position` | inviting the Chief Executive |

There is no override path and no override permission — not even for a superuser. The only lever is the setting.

## 3. Availability Finder and the double-booking guard

* Lists **only eligible people** for a case, programme, duty and dates (switch to *also show who is not eligible, and why*). Every reason names the rule and the setting.
* Filters: status, rank / designation, discipline (`qa.matching.allow_secondary_discipline`), **double booking** with `qa.matching.travel_buffer_days`, **R-11**, **conflict of interest** (home institution), **cooldown** at the same institution (`qa.matching.same_institution_cooldown_months`), **annual cap**, **R-02** (an SSQ reviewer of the programme is not its visitor; `qa.workload.reviewer_cannot_verify`), **R-01**.
* Ranked by `qa.matching.rank_score` (fewest visits this year first, then rating).
* **Map** books the person under a lock: `select_for_update` on PostgreSQL plus a `booking_version` compare-and-swap that every database honours. Two officers who loaded the same finder: the first wins, the second sees "someone else booked this person meanwhile". Confirmation re-checks every rule under the lock.
* *Edit QA Mapping* (two officers) makes the mapping active; QA mappings list with filters.

## 4. Inspection

* The ES's visit approval (stage 10) creates the **Inspection** (`NBTE/INS/{YEAR}/{DEPT}/{SEQ}`) from the visit details, the personnel list and the active visit mappings.
* **Visit Calendar** (month grid; a list on phones). **Inspections** list with search and filters.
* **Inspection detail**: planned team; **Mark the visit completed** (Team Lead only; actual dates not in the future; tick anyone who did not go) → post-visit personnel → inspection closed → service records for attendees, visit mappings released.
* **Override Post-Visit Personnel** (Team Lead starts, a second officer confirms): switches attendance or adds an unplanned attendee. The plan never changes; the service history gets **compensating rows**.

## 5. Post-visit report (stage 12) and recommendation (stage 13)

* One report per programme, opened when the case enters stage 12; the Team Lead's task. Sections: facilities, staffing, curriculum, governance; summary (≥ 20 characters) and the team's recommendation.
* **B17:** only people who attended write and submit (a resource person with a portal account included) — and only once the visit is recorded.
* Resource persons' findings submitted on the portal appear on the report page for the team.
* **Endorsement chain (B14):** Division Head → Deputy Director → Director, each with a task: **Endorse**, **Add content** (stays at the layer) or **Return with remark** (remark required, back to the person who submitted, pinned on their task). Resubmission increments the version and starts again at the Division Head. Every step is an append-only `ReportEndorsement`.
* **Endorsement queue** with search and filters (where, recommendation, *waiting on me*).
* The case moves to stage 13 on its own when the Director endorses the **last** report (stage 12 has no case task any more).
* **Stage 13:** the case's **Reports** tab lets the Director confirm a recommendation per programme; *Confirm recommendation* is refused until every programme has one. A stage-13 return sends every report back to its writer.

## 6. Service history (§9)

* `ServiceRecord`, **append-only**: written when an inspection closes (attendees only) and when an SSQ review completes (accept / return of an SSQ). Corrections are compensating rows (`voids`), never edits or deletes.
* **Service history** screen: filter by person, institution, programme, discipline, duty, person type and date range; totals per person; **Export (Excel)**.
* Historical backfill: the demo shows a person whose earlier visits this year were backfilled.

## 7. Portal

* **Resource persons** (user type `resource_person`): *My engagements* (with the findings form), *Service history*, *Profile & CV* (upload a new CV or promotion letter; the eligibility badge shows here too).
* **Institutions:** a read-only *Inspections* list (scheduled and recorded visits).

## 8. Two-officer actions now live

*Edit QA Personnel*, *Edit QA Mapping* and *Override Post-Visit Personnel* (Phase 1 placeholders) are enabled; their menu entries open the inspection screens.

## 9. UI

* Every internal list (Resource persons, Nominations, QA personnel, Finder, Mappings, Inspections, Endorsements, Service history) has a search bar and at least three facet filters (the portal lists have a search and their own filters), 25-row pages with *Show all* (up to `qa.ui.max_show_all_rows`), filters and page in the URL, HTMX for every filter, save and page click, loading skeletons.
* **Fix (all QA screens):** the shared `static/js/app.js` dropped every 4xx response, so the 422 forms with inline errors never appeared; a 422 now swaps in place (§20). Script version bumped to `?v=21`.
* New stylesheet `static/css/qa-p5.css` (tokens only, dark mode, no inline styles); the sidebar has an **Inspection (§9)** group with a badge for reports waiting on you.

## 10. Tests

`inspection/tests/test_eligibility.py`, `test_availability.py`, `test_reports.py` — see PHASE5_TEST_PLAN.md. Phase 4's SOP-03 walk tests were updated for the new stage 12 (no case task; the report sub-flow), and the portal URL list in `test_host_routing.py` includes the four new portal pages.

## 11. Files

New: `inspection/{models,rules,services,handlers,effects,forms,views,urls,portal_views,demo}.py`, `inspection/migrations/0001_inspection.py`, `institutions/migrations/0003_admin_department.py`, `qa_core/seed_data_phase5.py`, `qa_core/management/commands/seed_phase5_demo.py`, `templates/inspection/*`, `templates/qa_portal/resource_person/*`, `templates/qa_portal/inspections/*`, `templates/accreditation/case/_tab_reports.html`, `static/css/qa-p5.css`, `scripts/walkthroughs/phase5_walk.py`, `scripts/build_phase5_doc.py`, `docs/phase5/*`.

Changed (additive): `accreditation/case_services.py` (inspection on ES approval, report variables, stage 12–13 prerequisites), `accreditation/case_views.py` + `case_urls.py` (Reports tab, recommendation POST), `accreditation/qa_services.py` (SSQ review → service history), `accreditation/case_demo.py` (Phase 5 accounts, pins, stage 12–13), `institutions/{services,forms,handlers,portal_views}.py` (rules 6–7), `qa_core/{settings_catalogue,task_bridge,context_processors,portal_views,portal_urls}.py`, `qa_core/seed_data_phase4.py` + `seed_startup.py` (stage 12 patch, Phase 5 data), the sidebar, the QA shell and portal base templates, `static/js/app.js` (422 swap), `scripts/run_tests.*` and `scripts/README.md`.
