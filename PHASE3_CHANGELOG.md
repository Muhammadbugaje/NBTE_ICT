# NBTE QA System — Phase 3 changelog

**Scope:** PHASES.md "Phase 3 — QA requests (SOP-02) and the dynamic SSQ"; MASTER_PLAN §5.4 and §5.7; plus the new requirement from the Phase 3 prompt — **NBTE staff may sign in to the portal and get a third dashboard, the Staff Portal Dashboard**.
**Not in scope (untouched):** SOP-03 workflows, inspections, complaints (Phase 4+), the internal `workflow` engine, the backups app.
**No new dependency.** No existing file was renamed or moved. Secrets stay in the environment.

---

## 1. The Staff Portal Dashboard (changed Phase 1 rule)

| Before (Phase 1) | Now (Phase 3) |
|---|---|
| A staff account on the portal host was redirected to the internal system, and the portal sign-in form refused staff. | Staff sign in on the portal and land on the **Staff Portal Dashboard**. Any other portal page sends them back to it (HTMX requests get `HX-Redirect`). |

* **`accounts/middleware.py` — `ExternalUserGuardMiddleware`.** For a staff user on the portal host: if `qa.portal.staff_sign_in_enabled` is true (default), only the staff-safe routes are allowed (dashboard, sign-in/out, account, password, verify, webhooks, simulator, impersonation); everything else redirects to `/`. With the setting false, the Phase 1 behaviour (redirect to the internal system) returns unchanged. Institution and resource-person rules are unchanged.
* **`qa_core/portal_forms.py`** — the sign-in form refuses staff only when the setting is off.
* **`qa_core/portal_views.py` — `dashboard`** now has three branches: institution, resource person, **staff** (`templates/qa_portal/staff_dashboard.html`). The staff view shows the officer's own work list — programme clearances, fees that could not be issued, SSQs waiting for review, answered queries — **by reference and action only**, each linking to the internal system, and a prominent **Go to Internal System** button. No institution name or record is shown on the portal to staff; staff notifications (which name institutions) are not shown in the portal bell either.
* Institution users still get the institution dashboard (now with SOP-02 action items: open queries, SSQs to complete or revise, draft requests); resource persons keep theirs.

## 2. Models (`accreditation`, migration `0002_qa_requests_ssq`)

`QAType`, `QARequest`, `QARequestProgramme`, `SSQTemplate`, `SSQ`, `Submission`, `Evidence`, `Query`, `Response`, `CatalogueItem`, and a minimal `QACase` (see decisions). `ApplicationCycle.qa_types` added. Details and reversal: **PHASE3_MIGRATIONS.md**.

New permissions: `accreditation.view_qa_case`, `raise_query`, `review_ssq`, `clear_qa_programmes`, `manage_ssq_templates`, `publish_ssq_template`, `manage_catalogue`.
`Evidence` and `Response` files use restricted storage, are registered for file cleanup (`core/files_register.py`) and downloadable on the portal (`PORTAL_FILE_MODELS`) with per-object `can_download`.

## 3. SOP-02 on the Phase 1 FlowDefinition engine

Two seeded flows (data, editable/versionable in Settings → Workflows); no engine change.

**`qa_request`**: `draft → submitted → [programme_clearance (NTIRD, new programmes only)] → fee_due → payment_pending_confirmation → fee_confirmed → ssq_in_progress → ssq_submitted → in_workflow → decided`; `cancelled`.
* `cancel`, `drop_programme`, `replace_programme` transitions exist **only** from `draft`, `submitted`, `fee_due`, `payment_pending_confirmation` (B6/R-25). The services ask the flow (`flow.can`) — the rule lives in the data, not in code.
* `add_programme` exists from `draft`, `submitted`, `fee_due` (not once a payment is claimed).
* Effects: `qa_request_submitted`, `qa_request_clearance_opened`, `qa_request_returned`, `issue_qa_request_fee`, `qa_request_cancelled`, `open_ssqs`, `spawn_qa_case`.
* Payments → request: the existing `payment_claimed / payment_rejected / payment_settled` signals now also move QA requests (`accreditation/effects.py`).

**`ssq`**: `draft ⇄ returned_to_department → submitted_by_department → submitted → accepted | returned`; `accepted → analysed` (Phase 4). Effects: `ssq_submitted` (takes the snapshot), `ssq_accepted`, `ssq_returned`.

Services: **`accreditation/qa_services.py`** (system code): eligibility (Requirement 4 window, QA-type rules, one open request per programme, cycles), create / add / drop / replace / submit / cancel, fee issue and retry, programme clearance and return, SSQ open (with prefill), autosave, uploads, re-use of accepted documents, department return path, submit, accept / return, revise (new version), QA case, queries.

## 4. The dynamic SSQ

**`accreditation/ssq.py`** — the engine:
* **Schema** (JSON sections → fields). Field types: text, textarea, email, phone, url, number, integer, date, choice, multi_choice, yes_no, file, catalogue, table. `validate_schema()` returns plain-language problems (keys, duplicates, types, options, catalogue binding, table columns, prefill sources, unknown keys).
* **Prefill** from the institution record (name, ownership, address, contacts …), the programme (level, dates, streams) and **documents — ACCEPTED only**; drafts, submitted-but-unreviewed, returned and superseded documents are never used. Prefilled document fields become `Evidence` rows *re-using* the accepted document (no copy). Prefilled answers carry a "Prefilled from your record" badge until changed ("Changed from the record").
* **Cleaning** per field type; autosave applies draft rules (`required` is checked on submission). A bad answer never overwrites a good one.
* **Catalogue binding (R-22):** catalogue fields list the catalogue items for the programme (by programme name, else by programme category), and accept **quantities only**, keyed by item id. Names, unknown ids and another programme's items are ignored.
* **Snapshot rendering:** `render_snapshot()` freezes labels, display values, catalogue names, required quantities and shortfalls into the `Submission`.

**`Submission`** is immutable: `save()` of an existing row and `delete()` raise `PermissionError`; a SHA-256 checksum over data, rendered sections and evidence is stored and verified on the review screen.

**Versioning (Requirement 16):** a returned SSQ is never edited. *Start version n+1* copies answers, meta and evidence into a new SSQ that supersedes it; the returned version and its submission stay unchanged. Templates and catalogue lines are versioned the same way (published templates are never edited; a catalogue change writes a new line and retires the old).

**Department return path:** the institution administrator sends chosen sections to an academic-department sub-account (`inst-academic-dept`); while it is with the department only those sections are editable, and only by that account; the department sends it back; the administrator submits. Submission is limited to `qa.ssq.submit_roles` (default chief executive and institution administrator).

## 5. Screens and routes

### Portal (`qa_core/portal_urls.py`, views in `accreditation/qa_portal_views.py`)
| Route | Screen |
|---|---|
| `/qa-requests/` | QA requests list (filters: open / closed / all, QA type) |
| `/qa-requests/new/` | New request: QA type cards (unavailable ones say why) → eligible programmes (ineligible ones say why, e.g. "Too early: reassessment opens on …") |
| `/qa-requests/<id>/` (+ `submit/`, `cancel/`, `add/`, `programmes/<row>/drop/`, `…/replace/`) | Request detail: progress, SSQs with progress bars, programmes (drop / replace / add while R-25 allows), submit with declaration, fee, queries, cancel (R-25) |
| `/qa-requests/<id>/ssq/<ssq>/` (+ `save/`, `upload/<field>/`, `reuse/<field>/`, `evidence/<ev>/remove/`, `department/`, `recall/`, `department-done/`, `submit/`, `revise/`) | **SSQ workspace**: section navigation with per-section progress; one section at a time swapped by HTMX (no full reload, URL pushed); autosave; prefill badges; file upload or re-use of accepted documents; table rows; catalogue quantity grid; submit panel listing missing answers; department panel; versions; read-only submitted view |
| `/queries/`, `/queries/<id>/` | Queries list and detail with response form (text + optional attachment) |

Navigation: *QA requests* and *Queries* appear for approved institutions (Institution and Queries move into the user menu so the top bar keeps five links).

**Autosave** (`static/js/qa-ssq.js`, loaded by the portal shell): saves the whole section after a pause in typing (`qa.ssq.autosave_delay_ms`, default 900 ms), at once on choices/checkboxes, before moving to another section and before submitting; the status reads *Unsaved changes… → Saving… → All changes saved · hh:mm:ss* (or *Saved — n answers need fixing* / *Not saved* with automatic retry); out-of-band swaps update the field errors, the section's progress mark and the submit panel; leaving with unsaved answers warns.

### Internal (`/qa/requests/`, namespace `qa_req`, views in `accreditation/qa_views.py`)
| Route | Screen |
|---|---|
| `/qa/requests/` | QA requests list (search, stage, QA type, "fee not issued"; counts) |
| `/qa/requests/<id>/` (+ `clearance/`, `retry-fee/`) | Request detail: programmes, programme clearance (clear / return with remark, NTIRD), fee and retry, SSQs, queries, history, QA case |
| `/qa/requests/ssq/` | **SSQ review queue** (tabs: waiting, accepted, returned, still with the institution, all; search; QA type; waiting days) |
| `/qa/requests/ssq/<id>/` (+ `decide/`) | SSQ review: the immutable snapshot (checksum verified), catalogue shortfalls highlighted, evidence links, accept / return (remark required), versions, history |
| `/qa/requests/query/<request|ssq|evidence>/<id>/` | **Raise query** form (inline, HTMX) on a request, an SSQ or a single file |
| `/qa/requests/queries/`, `…/<id>/`, `…/<id>/close/` | Queries list (open/answered/closed, mine) and detail with close |

Sidebar: new group **QA requests (SOP-02)** — QA requests, SSQ review queue (badge), Queries.

### Settings (`/qa/settings/`, namespace `qa_set`, views in `accreditation/settings_views.py`, services in `accreditation/builder.py`)
| Route | Screen |
|---|---|
| `/qa/settings/ssq-templates/` | **SSQ templates**: per QA type, current version, draft, all versions; *New version* |
| `/qa/settings/ssq-templates/<id>/` (+ `section/`, `field/`, `json/`, `publish/`, `discard/`, `preview/`) | **Builder**: sections (add, edit, move, remove), fields (add by type, edit, move, remove) with a type-aware field editor (options, min/max, document kind, catalogue kind, table columns, prefill source picker), live validation, preview as institutions see it, advanced JSON, publish (needs `publish_ssq_template`), discard draft |
| `/qa/settings/catalogue/` (+ `<id>/`) | **Curriculum catalogue**: filters (programme, kind, search, include retired); add; change (new version) and retire |

Two tabs added to the settings tab bar: *SSQ templates*, *Curriculum catalogue*. Holders of `manage_ssq_templates`, `publish_ssq_template` or `manage_catalogue` can open QA settings.

All new screens follow the §20 contract: HTMX partials for every list/panel (`render_qa`), URL pushed, filters on lists, loading states (busy buttons, region skeletons, determinate upload progress), 422 with the form for validation errors, toasts announced to screen readers, confirmation dialogs for irreversible steps (publish, submit SSQ, cancel request, return), focus moved to the new heading after a swap.

## 6. Phase 2 actions that go live

* **Add Programme to QA** (`institutions.add_programme_to_qa`) — real handler (choose request and programme; preview shows the programme list before/after; refused past `fee_due`); the ActionPolicy is enabled by `seed_startup`.
* **Cancel Request** — for a QA request container it runs the SOP-02 cancel, so R-25 applies (refused with a clear message after the fee is confirmed).
* **Wipe Institution Requests** — QA drafts are cancelled through SOP-02 (unpaid bills cancelled with them).
* **Drop Programmes** — a dropped programme leaves every open QA request still at an R-25 stage (the last programme cancels the request); later stages keep the row and log it for the Phase 4 case.

## 7. Settings (Settings → QA → *QA requests and SSQs*)

New: `qa.programme.reassessment_window_months` (6), `qa.programme.expiry_warning_days` ([180, 90, 30]), `qa.portal.staff_sign_in_enabled` (true), `qa.ssq.submit_roles`, `qa.ssq.autosave_delay_ms` (900), `qa.query.default_due_days` (14), `qa.qa_request.max_open_per_type` (1). Now used: `qa.documents.max_upload_mb`, `qa.fee.qa_request_processing` + `.confirmed`, `qa.fee.qa_request.base`, `qa.fee.qa_request.per_programme`.

## 8. Seed (`seed_startup`)

`qa_core/seed_data_phase3.py` + new steps in `seed_startup`: 7 QA types; the **Institutional Administration** and **Reaccreditation** SSQ templates published as v1; a starter curriculum catalogue (16 lines); the two flows; permissions; notification templates/rules; the `query` reference format; the two data fixes (QA-request reference pattern, Add Programme to QA policy); `--with-demo` additions; `--reset-demo` covers every Phase 3 model. Output gains a line: *QA types · SSQ templates · Catalogue items*.

## 9. Decisions taken (the prompt said "Go"; documented here)

1. **QACase in Phase 3.** PHASES.md's DoD says a QA case is created automatically when the SSQs are in. A minimal `QACase` (reference `NBTE/QAC/{YEAR}/{DEPT}/{SEQ}`, owning department, programme count, status `awaiting_sop03`) is created then — **empty**; its SOP-03 stages, tasks and screens are Phase 4.
2. **Two extra permissions:** `review_ssq` (accept/return SSQs) and `clear_qa_programmes` (programme clearance), so review and clearance can be granted separately from `view_qa_case`.
3. **The QA fee in the demo.** `qa.fee.qa_request_processing` stays *unconfirmed* with zero amounts in a real installation (Phase 2 decision). When a fee cannot be issued, the request waits at *fee due* with a note ("will be issued once NBTE confirms the amount") and staff get **Issue the fee now** — the submission never fails. `--with-demo` sets ₦50,000 + ₦150,000 per programme and confirms it; the walkthrough then waives the bill through the existing two-step waiver (Finance officer proposes, Director of Finance confirms).
4. **Programme changes and the bill.** At *fee due* an unpaid bill is cancelled and re-issued for the new count (§10.6 cancel-and-reissue). At *payment pending confirmation* Finance is alerted to check the amount (drop/replace allowed by R-25; add is not).
5. **Generic request container.** Each QA request creates an `InstitutionRequest` of type `qa`, so the Phase 2 *Cancel Request* and *Wipe* actions see it; both route through the SOP-02 cancel.
6. **Reference format.** QA requests use `NBTE/QAR/{YEAR}/{INST}/{SEQ:5}` (the Phase 1 default repeated the onboarding pattern). The switch is made only on a format that never issued a number.
7. **Template wording.** The two SSQ templates are built from the field lists in the project documents; DPAP and the Curriculum Office refine them in the builder (as version 2).
8. **SOP-01 onboarding** still ends at programme selection (its programme clearance/fee is not Phase 3 scope).
9. **A returned SSQ after the case opened.** The request stays *in the QA workflow*; the revised SSQ is reviewed again (the case is Phase 4's to act on).
10. **Staff portal privacy.** "Staff cannot see institution data on the portal" is applied strictly: the staff work list shows references and actions only; links open the internal system, where scoping applies.

## 10. Fixes found during the walkthrough / tests

* Template-builder edits made through a stale object could lose a just-added section — builder edits now reload the draft first.
* `department_submit`, `accept_ssq`, `return_ssq` now reload the SSQ under a row lock before checking its state.
* A partial autosave post no longer blanks fields of the section that were not sent (table and multi-choice inputs carry a *present* marker so "no rows"/"nothing ticked" still saves).
* SSQ workspace: the submit panel lists the first six missing answers in questionnaire order with links to their sections.

## 11. Tests

New: `accreditation/tests/test_qa_requests.py`, `accreditation/tests/test_ssq.py`, fixtures `qa_core/tests/phase3_fixtures.py` (see PHASE3_TEST_PLAN.md).

**Changed Phase 1 tests (listed as required):**
| File | Change | Why |
|---|---|---|
| `qa_core/tests/test_external_guard.py` | `test_staff_redirected_off_the_portal` → `test_staff_land_on_the_staff_portal_dashboard` + `test_staff_redirected_off_the_portal_when_switched_off`; `test_portal_login_form_refuses_staff` → `test_portal_login_form_accepts_staff` + `test_portal_login_form_refuses_staff_when_switched_off` | The Phase 3 prompt changes the Phase 1 rule; the old behaviour is still tested behind `qa.portal.staff_sign_in_enabled = false` |
| `qa_core/tests/test_host_routing.py` | The pinned list of portal routes gains the 20 Phase 3 routes (QA requests, SSQ workspace, queries) | New portal pages; the disjointness checks are unchanged and pass |
| `qa_core/tests/test_seed_startup.py` | Reference formats **9 → 10** (`query`); flows set gains `qa_request`, `ssq`; demo users **11 → 12** (academic-department account) | New seeded rows |
| `qa_core/tests/test_scoping_enforcement.py` | `AUDITED_FILES` adds `qa_views.py`, `qa_portal_views.py` | The layer-2 source audit now covers the new view modules (they pass) |

No Phase 2 test needed changing.
