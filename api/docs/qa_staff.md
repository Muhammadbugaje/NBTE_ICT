# QA workspace for NBTE staff — mobile API (`qa/…`, staff door)

Base: `https://<internal host>/api/v1/` (dev: `http://<mac-ip>:8000/api/v1/`, no door header).
Every call: `Authorization: Bearer <access>`. All conventions (dates, money, pagination, error
envelope) follow `mobile/API_CONTRACT.md`.

These endpoints are the **everyday, on-the-move** QA actions. Configuration (QA settings, flows,
templates, fee tables), cost-sheet / personnel / visit editing, reassignment, capacity runs,
decision recording, letter regeneration, institution actions (Add TVET, imports, profile
changes), query raising and holds stay **web-only** (see the end of this file). Where a case
stage needs one of those forms, the stage row says `problems` and `web_tab` so the app can say
"finish this on the website".

The server applies exactly the website's rules (same service functions, same scoping). The app
may ask for biometrics before an approve/reject/confirm call; the server does not rely on it.

## Common errors (every endpoint)

| Status | `error.code` | When |
| --- | --- | --- |
| 401 | `not_authenticated` / `token_expired` | no / bad / expired token |
| 403 | `wrong_door` (`correct_door: "portal"`) | institution or resource-person token |
| 403 | `password_change_required` | temporary password still in use |
| 403 | `permission_denied` | the QA section is closed to this user (same rule as the website sidebar), or the website service refused the action — `message` then carries the website's reason, e.g. `"You started this change, so a different officer must confirm it."` |
| 404 | `not_found` | unknown id **or** an object outside the user's scope (never 403 for those) |
| 400 | `validation_error` | request fields (`fields.<name>`), or a business rule from the service (`fields.non_field_errors` + `message`, e.g. `"Choose the desk officer who will build the cost sheet and personnel list."`) |

Sections and who sees them (`qa/summary/ → sections`):

| Section | Endpoints | Website rule |
| --- | --- | --- |
| `cases` | `qa/cases/…` | `accreditation.view_qa_case` or `act_on_qa_stage` |
| `inspections` | `qa/inspections/…` | same as cases |
| `confirmations` | `qa/confirmations/…` | `qa_core.view_pending_actions` / settings / confirm-any-department perms |
| `finance` | `qa/finance/…` | `payments.view_obligation` / `verify_payment` / `final_confirm_payment` / `import_bank_statement` |
| `institutions` | `qa/institutions/…` | staff with a department, or `qa_core.view_all_institutions` |
| `applications` | `qa/applications/…` | `accreditation.view_onboarding_application` / `decide_departmental_clearance` / `view_all_institutions` |
| `requests` | `qa/requests/…` | `view_qa_case` / `review_ssq` / `raise_query` / `clear_qa_programmes` / `view_all_institutions` |
| `complaints` | `qa/complaints/…` | complaint handlers / queue / reports / oversight / escalate / internal-notes perms |

The ICT QA monitor sees everything in cases and can write nothing: every case action answers
403 `"The ICT monitor has read-only access to QA cases (R-13)."`, and `read_only` is `true`.

## Files

### GET `qa/files/<kind>/<id>/`
Every `download_url` in this API points here. Streams the file (`Content-Disposition: attachment`)
after the row's own `can_download(user)` — the same check as the website's protected download.
`kind` is one of `accreditation.evidence`, `accreditation.outcomelettercopy`,
`accreditation.response`, `complaints.complaintattachment`, `institutions.institutiondocument`,
`payments.paymentadvice`, `payments.proofofpayment`, `tasks.taskattachment`.
Unknown kind, missing row, empty file or not allowed → **404**. Send the Bearer header; any
`Accept` is fine.

File object used everywhere:
```json
{"id": 12, "kind": "payments.proofofpayment", "name": "teller.pdf", "size": 48211,
 "download_url": "https://<host>/api/v1/qa/files/payments.proofofpayment/12/"}
```

---

## Summary

### GET `qa/summary/`
```json
{"sections": {"cases": true, "confirmations": true, "finance": false, "institutions": true,
              "applications": true, "requests": true, "complaints": true, "inspections": true},
 "counts": {"my_stages": 1, "my_stages_overdue": 0, "confirmations_waiting": 1,
            "finance_queue": null, "finance_ready_for_final": null, "complaints_assigned": 1,
            "applications_to_clear": 0, "ssq_waiting": 2, "inspections_upcoming": 1}}
```
A count is `null` when its section is closed to the user. Sources: `my_stages` = open QA stage
tasks I hold (sidebar "My stage work"); `confirmations_waiting` = pending confirmations I may
decide (sidebar badge); `finance_queue` = open verification items; `finance_ready_for_final` =
items ready for the Director of Finance (only for the Director, otherwise `null`);
`complaints_assigned` = open complaint stages assigned to me; `applications_to_clear` = pending
clearances of my department; `ssq_waiting` = SSQs waiting for review; `inspections_upcoming` =
visits I lead or am on the team of that have not ended.

---

## Cases (SOP-03)

### GET `qa/cases/my-stages/?kind=`
My open stage tasks, earliest deadline first. `kind`: `""` (all), `cost` (stages 2–7), `visit`
(8–10), `funds` (11). Paginated.
```json
{"results": [{"link_id": 41, "case": {"id": 1, "reference": "NBTE/QAC/2026/DPAP/0001",
              "institution": {"id": 1, "code": "KDP", "name": "Kaduna Demo Polytechnic (Phase 4)"}},
              "stage_code": "case_opened", "stage_label": "Case opened — choose the desk officer",
              "stage_no": 0, "task": {"id": 88, "reference": "TSK-2026-00012"},
              "opened_at": "2026-10-19T04:33:50+01:00", "due_at": "2026-10-22T04:33:50+01:00",
              "overdue": false}],
 "next": null, "previous": null, "count": 1}
```

### GET `qa/cases/?q=&stage=&overdue=&mine=&institution=&department=`
Every case I may open (the Case Board), most recently updated first. Filters: `q` (reference,
institution name, exact code), `stage` = a board group `new`, `setup`, `cost`, `visit`,
`decision`, `closed` **or** an exact stage code, `overdue=1`, `mine=1` (I hold an open stage),
`institution` = code, `department` = owning department code. Paginated.
```json
{"results": [{"id": 1, "reference": "NBTE/QAC/2026/DPAP/0001",
              "institution": {"id": 1, "code": "KDP", "name": "Kaduna Demo Polytechnic (Phase 4)"},
              "owning_department": {"id": 15, "name": "DPAP"}, "status": "post_visit_report",
              "stage_label": "Post-visit report", "stage_no": 12, "holders": [],
              "due_at": null, "overdue": false, "needs_reassignment": false, "programme_count": 2,
              "updated_at": "2026-10-19T12:13:40+01:00"}],
 "next": null, "previous": null, "count": 1}
```

### GET `qa/cases/<id>/`
```json
{"id": 1, "reference": "NBTE/QAC/2026/DPAP/0001", "status": "post_visit_report",
 "stage_label": "Post-visit report", "stage_no": 12, "started": true, "read_only": false,
 "institution": {"id": 1, "code": "KDP", "name": "Kaduna Demo Polytechnic (Phase 4)"},
 "owning_department": {"id": 15, "name": "DPAP"},
 "qa_request": {"id": 1, "reference": "NBTE/QAR/2026/KDP/00001", "qa_type": "Reaccreditation"},
 "programme_count": 2, "needs_reassignment": false,
 "started_at": "2026-10-19T04:33:50+01:00", "completed_at": null,
 "programmes": [{"id": 1, "programme_id": 1, "name": "Computer Science", "level": "National Diploma (ND)",
                 "status": "active", "is_new_programme": false}],
 "progress": [{"no": 0, "label": "Case opened — choose the desk officer", "state": "done", "code": "case_opened"},
              {"no": 1, "label": "Assign supervising staff", "state": "done", "code": "assign_audit_officer"},
              {"no": 12, "label": "Post-visit report", "state": "current", "code": "post_visit_report"},
              {"no": 17, "label": "Programme record update", "state": "todo", "code": "programme_auto_update"}],
 "current_stages": [{"code": "post_visit_report", "label": "Post-visit report", "stage_no": 12,
                     "holders": [{"id": 26, "name": "Sadiq Team Lead (Demo)"}],
                     "due_at": "2026-10-25T12:13:40+01:00", "overdue": false,
                     "can_act": true, "why_not": "",
                     "problems": ["Every post-visit report must be endorsed by the Director first (Reports tab)."],
                     "approve_label": "Submit post-visit report", "can_return": true, "can_attach": true,
                     "web_tab": "reports"}],
 "team": {"desk_officer": {"id": 10, "name": "Amina Officer (Demo)"},
          "supervising_audit_officer": {"id": 13, "name": "Danladi Audit Officer (Demo)"},
          "supervising_finance_officer": {"id": 15, "name": "Gambo Finance Officer (Demo)"},
          "tracking_ict_officers": [{"id": 17, "name": "Ibrahim ICT Officer (Demo)"}],
          "personnel": [{"id": 1, "name": "Sadiq Team Lead (Demo) (USR-00026)", "duty": "team_lead",
                         "duty_label": "Team lead", "status": "approved"}]},
 "visit": {"planned_start": "2026-11-09", "planned_end": "2026-11-11",
           "team_lead": "Sadiq Team Lead (Demo) (USR-00026)", "status": "es_approved"},
 "cost": {"version": 1, "status": "es_approved", "total": "420000.00", "total_display": "₦420,000.00"},
 "decisions": [],
 "documents": [{"id": 7, "kind": "tasks.taskattachment", "name": "minutes.pdf", "size": 48211,
                "download_url": "https://<host>/api/v1/qa/files/tasks.taskattachment/7/",
                "source": "stage_task", "stage_code": "case_opened", "description": "Opening minutes",
                "uploaded_by": {"id": 11, "name": "Bello Director (Demo)"},
                "uploaded_at": "2026-10-19T05:00:00+01:00"}],
 "timeline": [{"at": "2026-10-19T12:13:41+01:00", "kind": "flow", "title": "Approve",
               "detail": "request_fund_release → post_visit_report", "remark": "",
               "actor": {"id": 20, "name": "Lami Pay Officer (Demo)"}}]}
```
Notes
- `progress` is the 17-stage stepper exactly as the website draws it (`state`: `done`, `current`,
  `todo`); stages 1's three parallel assignments are one row.
- `current_stages`: one row per open action stage (several during the parallel stage 1).
  `can_act` = the website's rule (holder of the stage's open QA task with
  `accreditation.act_on_qa_stage`, or a superuser; never the ICT monitor). `why_not` explains a
  `false`. `problems` (only when `can_act`) are what blocks **approve** — fix them on the
  website tab `web_tab`; **return** is still possible. `can_return` is false for `case_opened`
  and `programme_auto_update` (the website shows no return there).
- `documents` = files attached to the case's stage tasks + outcome letter copies, filtered by
  each file's `can_download`. `source`: `stage_task` | `outcome_letter`.
- `decisions[]`: `{id, programme, stage, stage_label, outcome, outcome_label, approval_type,
  approved_streams, commencement_date, expiry_date, note, decided_by, decided_at}`.
- `timeline` = newest first, max 60 (`kind`: `flow` = stage moves with remarks, `timeline` = case events).
- `visit` / `cost` are `null` before they exist.

### POST `qa/cases/<id>/act/` — complete / advance or return a stage
```json
{"stage": "case_opened", "action": "approve", "remark": "Supervising departments, please assign."}
```
`action`: `approve` | `return`. `remark` is required (≥3 chars) for `return`; it is pinned on the
returned task and goes back to the exact person who submitted. Response 200:
```json
{"message": "Done. The case is now at: Assign the supervising Audit officer, Assign the supervising Finance officer, Assign the tracking ICT officer(s).",
 "case": { …same as GET qa/cases/<id>/… }}
```
Errors: 400 `fields.remark` / `fields.action`; 400 `non_field_errors` with the stage
prerequisites (e.g. `"Choose the desk officer who will build the cost sheet and personnel list."`);
403 with the website's reason: `"This stage is with Bello Director (Demo)."` (not the holder),
`"The case is not at this stage."`, `"You do not hold accreditation.act_on_qa_stage."`, or the ICT
monitor message; 404 out of scope.

### POST `qa/cases/<id>/documents/` (multipart) — attach a document to the stage
Fields: `stage` (a current stage code the user may act on), `file`, `description` (optional, ≤255).
Same upload rules as task attachments on the website (allowed types and size from settings).
Response 201: the document row (shape of `documents[]`). Errors: 400 `fields.file` (missing,
bad type, too large); 403 not the stage holder / ICT monitor / stage has no open task; 404.

---

## Confirmations (two-step control, `qa_core` PendingAction)

### GET `qa/confirmations/?tab=&action=&q=`
`tab`: `pending` (default, soonest expiry first), `started_by_me` (mine, any status), `expired`,
`applied`, `rejected`, `failed`, `withdrawn`, `all`. `action` = action code; `q` searches
reference, target, reason, initiator. Visibility = the website's: my own, plus those of my
department I could confirm (or all of my department with `view_pending_actions`), everything for
superusers / cross-department holders. Paginated, plus `counts`.
```json
{"results": [{"id": 3, "reference": "PA-2026-000003",
              "action": {"code": "inspection.edit_qa_mapping", "label": "Edit QA Mapping"},
              "target_label": "Kaduna Demo Polytechnic (Phase 4) (KDP)",
              "reason": "Map Victoria QA Verifier (Demo) (USR-00029) to ND Computer Science (Resource inspection)",
              "status": "pending", "status_label": "Pending",
              "initiated_by": {"id": 10, "name": "Amina Officer (Demo)"},
              "initiated_at": "2026-10-20T00:40:42+01:00", "expires_at": "2026-10-23T00:40:42+01:00",
              "overdue": false, "department": {"id": 15, "name": "DPAP"},
              "decided_by": null, "decided_at": null, "can_decide": true, "decide_reason": ""}],
 "next": null, "previous": null, "count": 1,
 "counts": {"pending": 1, "mine": 0, "overdue": 0}}
```

### GET `qa/confirmations/<id>/`
The row plus the before/after diff exactly as the website's confirm page renders it (for decided
items, "before" is the value it replaced, from the snapshot):
```json
{ …row…,
 "diff": [{"label": "Person", "before": "—", "after": "Victoria QA Verifier (Demo) (USR-00029)", "changed": true},
          {"label": "Programme", "before": "—", "after": "ND Computer Science", "changed": true},
          {"label": "Dates", "before": "—", "after": "09 Nov 2026 – 11 Nov 2026", "changed": true}],
 "stale": false,
 "confirm_options": [],
 "decision_remark": "", "failure_detail": "", "applied_at": null, "can_withdraw": false,
 "history": [{"at": "2026-10-20T00:40:42+01:00", "label": "Created", "actor": {"id": 10, "name": "Amina Officer (Demo)"}},
             {"at": "2026-10-20T00:40:42+01:00", "label": "Initiated inspection.edit_qa_mapping",
              "actor": {"id": 10, "name": "Amina Officer (Demo)"}}]}
```
- `stale: true` → the item changed after initiation; confirming will fail ("Stale — please
  re-initiate"). Show a warning.
- `confirm_options` (some actions, e.g. Add TVET): `[{"name": "send_invites", "label": "E-mail set-password links now", "help": "", "default": true}]` — send the user's choices in `options` on approve.
- `history[].actor` is `null` for the system.

### POST `qa/confirmations/<id>/approve/`
`{"note": "Agreed.", "options": {"send_invites": true}}` (both optional). Confirms and applies
(`qa_core.actions.confirm`). Response 200:
```json
{"message": "PA-2026-000003 confirmed and applied", "confirmation": { …detail… }}
```
If applying fails the status is `failed` and `message` ends with the reason. 403 reasons (from
the server's two-step rules): initiator cannot confirm, missing permission, rank too low, other
department, already decided, expired. 404 out of scope.

### POST `qa/confirmations/<id>/reject/`
`{"note": "Wrong programme."}` — `note` required (≥3 chars) → 400 `fields.note`. Response like approve
(`status: "rejected"`).

### POST `qa/confirmations/<id>/withdraw/`
No body. Only the initiator, only while pending (403 otherwise). Response like approve (`status: "withdrawn"`).

---

## Finance — payment verification queue

### GET `qa/finance/?stage=&source=&age=&q=`
Open items oldest first. `stage`: `""` everything, `first` (needs first pass), `second` (needs
second check), `final` (ready for the Director), `info` (needs information), `overdue`.
`source`: `proof` | `provider` | `statement` | `manual`. `age`: `24h` | `3d` | `7d` (older than).
`q`: bill reference, institution, teller number, provider reference. Paginated + `summary`.
```json
{"results": [{"id": 5, "obligation_id": 9, "reference": "NBTEQAP2026000009",
              "institution": {"id": 3, "code": "DMP", "name": "Demo Polytechnic, Kaduna"},
              "description": "Institutional processing fee", "balance": "250000.00",
              "balance_display": "₦250,000.00", "source": "proof", "source_label": "Uploaded proof",
              "opened_at": "2026-10-20T09:00:00+01:00", "sla_due_at": "2026-10-23T09:00:00+01:00",
              "sla_pct": 40, "overdue": false, "decision": null, "decision_label": null,
              "second_decision": null, "needs_second_check": false,
              "ready_for_final": false, "ready_why": "Waiting for the Finance officer's first pass."}],
 "next": null, "previous": null, "count": 1,
 "summary": {"first_pass": 1, "ready_for_final": 0, "overdue": 0, "role": "officer"}}
```
`summary.role`: `director` (Director of Finance), `officer` (`payments.verify_payment`), `viewer`.

### GET `qa/finance/<id>/`
```json
{"id": 5, "source": "proof", "source_label": "Uploaded proof", "is_open": true,
 "institution": {"id": 3, "code": "DMP", "name": "Demo Polytechnic, Kaduna"},
 "bill": {"id": 9, "reference": "NBTEQAP2026000009", "description": "Institutional processing fee",
          "for_reference": "NBTE/APP/2026/DMP/0001", "status": "awaiting_finance_confirmation",
          "stage_label": "Awaiting Finance confirmation", "amount": "250000.00", "amount_display": "₦250,000.00",
          "balance": "250000.00", "balance_display": "₦250,000.00", "amount_paid": "0.00",
          "amount_paid_display": "₦0.00", "issued_at": "2026-10-18T10:00:00+01:00",
          "due_at": "2026-11-17T10:00:00+01:00", "formula": {"expr": "250000", "result": "250000.00"}},
 "proofs": [{"id": 12, "file": {"id": 12, "kind": "payments.proofofpayment", "name": "teller.pdf", "size": 48211,
                                "download_url": "https://<host>/api/v1/qa/files/payments.proofofpayment/12/"},
             "kind": "teller", "kind_label": "Bank teller / transfer receipt",
             "amount_claimed": "250000.00", "amount_claimed_display": "₦250,000.00", "paid_on": "2026-10-19",
             "bank_name": "CBN", "teller_number": "T-001",
             "uploaded_by": {"id": 34, "name": "Danjuma Admin (Demo Poly)"},
             "uploaded_at": "2026-10-20T09:00:00+01:00", "same_file_as_another_bill": false}],
 "provider_reports": [],
 "statement_lines": [],
 "first_pass": {"decision": null, "decision_label": null, "by": null, "at": null, "reason": "", "amount_confirmed": null},
 "second_check": null,
 "final": {"decision": null, "decision_label": null, "by": null, "at": null, "reason": ""},
 "needs_second_check": false, "dual_verify_threshold_display": "₦5,000,000.00",
 "ready_for_final": false, "ready_why": "Waiting for the Finance officer's first pass.",
 "rights": {"can_first": true, "can_second": false, "can_final": false, "is_director": false, "did_first_pass": false},
 "advices": [],
 "history": [{"at": "2026-10-20T09:00:00+01:00", "action": "submit_proof", "from_stage": "awaiting_payment",
              "to_stage": "awaiting_finance_confirmation", "remark": "", "actor": {"id": 34, "name": "Danjuma Admin (Demo Poly)"}}]}
```
- `proofs[].file` is `null` when the user may not download proofs (website rule: Finance perms,
  Director of Finance, ES, `payments.view_proof`).
- `second_check` is `null` unless the amount is above the dual-verification threshold.
- `provider_reports[]`: `{id, provider, provider_label, provider_reference, status, status_label, amount, amount_display, verified_paid, verified_amount, signature_checked}`.
- `statement_lines[]`: `{id, statement_id, row_number, value_date, amount, amount_display, narration, match_status}`.
- `advices[]`: file object + `reference` (after confirmation).
- Show buttons from `rights`: first pass (`can_first`), second check (`can_second`), Director's
  confirm/reject (`can_final`; confirm also needs `ready_for_final`).

### POST `qa/finance/<id>/first-pass/` (Finance officer)
```json
{"decision": "approved", "amount": "250000.00", "reason": ""}
```
`decision`: `approved` (recommend confirm — `amount` received required), `rejected` (recommend
reject) or `needs_info` (the institution is asked; both need `reason` ≥3). Response 200:
`{"message": "First pass recorded. Only the Director of Finance can now confirm or reject this payment.", "item": { …detail… }}`.
Errors: 400 `fields.amount` / `fields.reason` / `non_field_errors` ("This item is already decided."…); 403 `"You do not hold payments.verify_payment."`; 404.

### POST `qa/finance/<id>/second-check/` (a different officer, large payments)
`{"decision": "approved" | "rejected", "reason": "…"}` (`reason` required for `rejected`).
403 if the same officer did the first pass or lacks `payments.second_verify_payment`.

### POST `qa/finance/<id>/decide/` (Director of Finance — the only path to "confirmed")
`{"decision": "confirmed" | "rejected", "reason": "…"}` (`reason` ≥3 for `rejected`; the institution sees it).
Response 200: `{"message": "NBTEQAP2026000009 confirmed. The Payment Advice was generated and the institution notified.", "item": {…}}`.
403: not the final approver, or did a first pass on this item. 400: not ready
(`"Waiting for the Finance officer's first pass."`, receipt missing, second check missing).

---

## Institutions (read-only)

### GET `qa/institutions/?q=&status=&ownership=&type=&dept=&zone=&sort=`
Institutions in my scope (`Institution.objects.for_user`). `sort`: `name` (default), `code`,
`recent`, `status`. Paginated.
```json
{"results": [{"id": 3, "code": "DMP", "name": "Demo Polytechnic, Kaduna", "status": "approved",
              "status_label": "Approved", "ownership": "Private", "type": "Polytechnic",
              "state": "KADUNA", "lga": "Kaduna North", "zone": "NW",
              "handling_department": {"id": 15, "name": "DPAP"}}],
 "next": null, "previous": null, "count": 1}
```

### GET `qa/institutions/<code>/` (code is case-insensitive)
The row plus:
```json
{ …row…, "former_names": [], "year_established": 2009, "website": null, "portal_enabled": true,
 "category": null, "primary_user": {"id": 34, "name": "Danjuma Admin (Demo Poly)"},
 "contacts": [{"kind": "email", "kind_label": "E-mail", "value": "info@demo-poly.local", "name": "", "is_primary": true}],
 "addresses": [{"kind": "permanent_site", "kind_label": "Permanent site", "address": "…", "town": "Kaduna",
                "lga": "Kaduna North", "state": "KADUNA", "gps": null}],
 "programmes": [{"id": 1, "name": "Computer Science", "level": "National Diploma (ND)",
                 "category": "Computing & Information Technology", "status": "accredited",
                 "status_label": "Accredited", "approved_streams": 1,
                 "commencement_date": "2022-10-19", "expiry_date": "2027-02-16"}],
 "open_cases": [ …case rows as in qa/cases/… ],
 "bills": [{"id": 9, "reference": "NBTEQAP2026000009", "description": "Institutional processing fee",
            "status": "confirmed", "stage_label": "Confirmed", "amount": "250000.00", "amount_display": "₦250,000.00",
            "balance": "0.00", "balance_display": "₦0.00", "due_at": "2026-11-17T10:00:00+01:00",
            "issued_at": "2026-10-18T10:00:00+01:00"}],
 "documents": [{"kind": "Certificate of Occupancy / Land Title", "mandatory": true, "state": "accepted",
                "department": "Legal Unit", "file": { …file… }}],
 "pending_changes": 1,
 "timeline": [{"at": "2026-10-19T12:13:41+01:00", "event": "inspection.completed", "visibility": "public",
               "text": "The QA visit has taken place.", "actor": {"id": 26, "name": "Sadiq Team Lead (Demo)"}}]}
```
- `bills` is `null` without `payments.view_obligation` (website Payments tab rule).
- `documents` = the registration checklist; `state`: `missing`, `submitted`, `accepted`,
  `returned`, `expired`; `file` null when missing or not downloadable.
- `timeline`: latest 30; internal events only for users who may read them (same department,
  `view_timeline_internal`, superuser).
- `pending_changes`: pending two-step changes on this institution visible to me (open them in Confirmations).

---

## Applications — departmental clearance (SOP-01)

### GET `qa/applications/clearance/?status=&dept=&q=&age=`
My department's clearance rows (all departments for `view_all_institutions`, filter `dept`).
`status`: `pending` (default; current round only), `cleared`, `returned`, `closed`, `all`.
`age`: `1d` | `3d` | `7d` (waiting at least). Paginated.
```json
{"results": [{"id": 4, "department": {"id": 7, "name": "Legal Unit"}, "status": "pending",
              "status_label": "Pending", "round": 1, "opened_at": "2026-10-20T08:00:00+01:00",
              "waiting_days": 1, "remark": "", "decided_by": null, "decided_at": null,
              "application": {"id": 2, "reference": "NBTE/APP/2026/ZIT/0001", "status": "clearance",
                              "public_status": "Under review by NBTE departments", "round": 1,
                              "submitted_at": "2026-10-20T08:00:00+01:00",
                              "institution": {"id": 5, "code": "ZIT", "name": "Zaria Institute of Technology"}},
              "can_decide": true, "decide_reason": ""}],
 "next": null, "previous": null, "count": 1}
```

### GET `qa/applications/clearance/<id>/`
Row plus `documents` (the documents **this department** reviews:
`[{"kind", "mandatory", "state", "file"}]`), `contacts`, `other_departments`
(`[{"department", "status", "status_label"}]` this round) and `history` (flow events).

### POST `qa/applications/clearance/<id>/decide/`
```json
{"decision": "clear", "remark": "", "accept_documents": true}
```
`decision`: `clear` | `return` (`remark` ≥3, the institution sees it). On `clear`, documents still
waiting for review are accepted when `accept_documents` is true (website default); missing /
returned / expired mandatory documents block it (400 `"<Document> is not ready — return the application instead."`).
Response 200: `{"message": "Legal Unit: cleared. Waiting for the other departments.", "clearance": {…detail…}}`.
403: not my department / already decided / earlier round / on hold. 404: not visible.
Single-document accept/return and lifting holds stay web-only.

---

## QA requests and SSQ review (SOP-02)

### GET `qa/requests/?stage=&type=&q=`
`stage`: `draft`, `clearance`, `fee`, `ssq`, `case`, `closed`. Paginated.
```json
{"results": [{"id": 1, "reference": "NBTE/QAR/2026/KDP/00001",
              "institution": {"id": 1, "code": "KDP", "name": "Kaduna Demo Polytechnic (Phase 4)"},
              "qa_type": {"code": "reaccreditation", "name": "Reaccreditation"}, "status": "in_workflow",
              "public_status": "Under review by NBTE", "programme_count": 2, "fee_note": "",
              "created_at": "2026-10-19T04:33:50+01:00", "submitted_at": "2026-10-19T04:33:50+01:00"}],
 "next": null, "previous": null, "count": 1}
```

### GET `qa/requests/<id>/`
Row plus `programmes` (`{id, programme_id, name, level, status, is_new_programme, clearance_status, clearance_remark, requested_streams}`),
`ssqs` (`{id, programme, status, public_status, version, fields_done, fields_total, submitted_at}`),
`bills` (bill rows), `queries` (`{id, reference, title, status, due_at, created_at}`),
`case` (`{id, reference}` or null), `can_clear`, `clear_reason`, `history`.

### POST `qa/requests/<id>/clearance/`
`{"decision": "clear" | "return", "remark": "…"}` (`remark` required for return). Programme
clearance exactly as the website (`clear_programmes` / `return_programmes`). 403 with the reason
(e.g. `"You do not hold accreditation.clear_qa_programmes."`), 400 if the request is not at clearance.
Response: `{"message", "request": {…detail…}}`.

### GET `qa/requests/ssq/?status=&type=&level=&q=`
`status`: `submitted` (default, waiting for review), `accepted`, `returned`, `draft` (still with
the institution), `all`. Paginated rows:
```json
{"id": 1, "request": {"id": 1, "reference": "NBTE/QAR/2026/KDP/00001"},
 "institution": {"id": 1, "code": "KDP", "name": "Kaduna Demo Polytechnic (Phase 4)"},
 "programme": "Computer Science", "level": "National Diploma (ND)", "qa_type": "Reaccreditation",
 "status": "submitted", "public_status": "Submitted to NBTE", "version": 1, "is_current": true,
 "submitted_at": "2026-10-19T04:33:50+01:00", "waiting_days": 2}
```

### GET `qa/requests/ssq/<id>/` (read-only review)
Row plus `sections` (the immutable submission as rendered on the website:
`[{"key", "title", "fields": [{"key", "label", "type", "value"} | {"…", "type": "table", "columns": [{"key","label"}], "rows": [{…}]} | {"…", "type": "catalogue", "rows": [{"item_id","name","unit","required","quantity","version","short"}]}]}]`),
`evidence` (`[{"field_key", "status", "file"}]`), `versions`, `submission_checksum_ok`,
`review_remark`, `reviewed_by`, `reviewed_at`, `can_review`, `review_reason`, `history`.

### POST `qa/requests/ssq/<id>/decide/`
`{"decision": "accept" | "return", "remark": "…"}` (`remark` required for return).
Response: `{"message": "SSQ accepted.", "ssq": {…detail…}}`. 403 with the reason when the user may not review.
Raising/closing queries stays web-only.

---

## Complaints

### GET `qa/complaints/?tab=&status=&layer=&priority=&sla=&q=`
`tab`: `mine`, `department`, `escalated`, `all` (empty = the website's default: the first non-empty
of mine → department → escalated, else all). `status`: `open` (default), `resolved`, `closed`, `all`.
`sla`: `breached`, `near`, `on_track`, `paused`. Most urgent first. Paginated + `tab` + `counts`.
```json
{"results": [{"id": 2, "reference": "NBTE-CMP-2026-000002",
              "subject": "Cannot upload the laboratory inventory to the SSQ",
              "layer": {"code": "ssq-submission", "name": "SSQ submission issues"},
              "priority": "normal", "priority_label": "Normal", "status": "in_progress",
              "status_label": "In progress", "is_open": true,
              "institution": {"id": 3, "code": "DMP", "name": "Demo Polytechnic, Kaduna"},
              "raised_by": {"id": 34, "name": "Danjuma Admin (Demo Poly)"}, "raiser_type": "institution",
              "current_department": {"id": 15, "name": "DPAP"},
              "current_assignee": {"id": 10, "name": "Amina Officer (Demo)"},
              "sla": {"state": "breached", "pct": 100, "left": "4h 20m overdue (working time)",
                      "due": "2026-10-21T11:39:16+01:00"},
              "created_at": "2026-10-14T11:09:16+01:00"}],
 "next": null, "previous": null, "count": 1, "tab": "mine",
 "counts": {"mine": 1, "department": 2, "escalated": 0, "all": 2, "breached": 1}}
```

### GET `qa/complaints/<id>/`
Row plus:
```json
{ …row…, "body": "When I upload the inventory …",
 "answers": [{"label": "SSQ reference", "value": "NBTE/QAR/2026/DMP/00002"}],
 "related": {"type": "qarequest", "id": "2", "label": "NBTE/QAR/2026/DMP/00002"},
 "resolution": "", "resolved_at": null, "closed_at": null, "reopened_count": 0,
 "messages": [{"id": 5, "author": {"id": 34, "name": "Danjuma Admin (Demo Poly)"}, "body": "…",
               "visibility": "public", "is_system": false, "created_at": "2026-10-14T11:09:16+01:00",
               "attachments": [ …file objects… ]}],
 "stages": [{"code": "triage", "label": "Triage", "department": {"id": 15, "name": "DPAP"},
             "assigned_to": {"id": 10, "name": "Amina Officer (Demo)"}, "entered_at": "…", "due_at": "…",
             "left_at": null, "breached_at": "…", "escalation_level": 0}],
 "watchers": [], "escalations": [],
 "rights": {"can_write": true, "can_write_internal": true, "can_act": true, "act_why": "",
            "actions": [{"code": "request_info", "label": "Request information from the raiser", "requires_remark": true, "needs_referral_target": false},
                        {"code": "refer", "label": "Refer internally", "requires_remark": true, "needs_referral_target": true},
                        {"code": "resolve", "label": "Resolve", "requires_remark": true, "needs_referral_target": false},
                        {"code": "close", "label": "Close (duplicate or out of scope)", "requires_remark": true, "needs_referral_target": false}],
            "can_escalate": false}}
```
Internal notes (`visibility: "internal"`) are included only for handlers who may read them
(`can_write_internal`). `rights.actions` are exactly the current stage's handler transitions.

### POST `qa/complaints/<id>/messages/` — reply or internal note
JSON `{"body": "We are checking.", "visibility": "public"}` or multipart with repeated `files`
parts (pdf, jpg, png, doc(x), xls(x); size limit `qa.documents.max_upload_mb`).
`visibility`: `public` (the raiser sees it and is notified) | `internal` (default; NBTE only).
Response **201** `{"message": "Reply sent to the raiser.", "complaint": {…detail…}}`.
Errors: 400 `fields.body` ("Write a message."), file type/size; 403 `"You cannot write on this complaint."`; 404.

### POST `qa/complaints/<id>/act/` — assign/refer, request info, resolve, close …
```json
{"action": "refer", "remark": "Finance owns this bill.", "refer_department": 7, "refer_user": null}
```
`action` must be one of `rights.actions[].code` (else 400 `fields.action`); `remark` required when
`requires_remark` (400 `fields.remark`); `refer` needs `refer_department` (department id) and/or
`refer_user` (staff user id). Response 200 `{"message": "Done. NBTE-CMP-2026-000002 is now: referred.", "complaint": {…}}`.
403 `"This complaint is handled by another department."` etc.

### POST `qa/complaints/<id>/escalate/`
`{"reason": "Raiser waiting two weeks.", "to_user": null}` — `to_user` = staff user id, or null for
the next configured level. Needs `complaints.escalate_complaint` (`rights.can_escalate`).
400 `fields.reason` / `"No one is configured to receive the next escalation level. Choose a person."`.
Response 200 `{"message": "Escalated. <names> is now watching NBTE-CMP-…", "complaint": {…}}`.
Holds on the related record stay web-only.

---

## Inspections (read-only)

### GET `qa/inspections/?mine=&when=&status=&q=`
`mine`: `1` (default) = visits I lead or am on the team of; `0` = every visit I may see (cases
visibility). `when`: `upcoming` | `past`. Newest scheduled first. Paginated.
```json
{"results": [{"id": 1, "reference": "NBTE/INS/2026/DPAP/0001", "kind": "accreditation_visit",
              "kind_label": "Accreditation visit", "status": "scheduled",
              "case": {"id": 1, "reference": "NBTE/QAC/2026/DPAP/0001"},
              "institution": {"id": 1, "code": "KDP", "name": "Kaduna Demo Polytechnic (Phase 4)"},
              "scheduled_start": "2026-11-09", "scheduled_end": "2026-11-11",
              "actual_start": null, "actual_end": null,
              "team_lead": {"id": 26, "name": "Sadiq Team Lead (Demo)"}, "my_role": "Team member"}],
 "next": null, "previous": null, "count": 1}
```
`my_role`: `"Team lead"`, the role on the team, or `null` (not on the team).

### GET `qa/inspections/<id>/`
Row plus `team` (`[{"name", "role_on_team", "kind": "staff|resource_person|qa_personnel", "programme", "confirmed"}]`),
`attendance` (after the visit: `[{"name", "role_on_team", "duty", "planned", "attended"}]`) and
`reports` (`[{"id", "programme", "status"}]`). Recording the visit, attendance overrides and
report writing/endorsement stay web-only.

---

## Web-only (not in the app)

QA settings, flows, templates, reference formats, rate table, fee schedules, bank accounts;
outbox; Case Board batch approval, drift reports; starting SOP-03; desk officer / supervising
staff selection, cost sheet lines, personnel list, visit details, fund release reference, stage
task reassignment, capacity runs, decision recording (stages 14–16), letter regeneration;
institution actions (Add TVET, bulk import, profile/status changes, exports, record PDFs);
application document-by-document review and hold lifting; QA fee retry; raising and closing
queries; finance obligations, statements, unmatched lines, waivers, refunds, webhooks;
resource persons, nominations, mapping finder, visit calendar, recording visits, attendance
overrides, post-visit reports and endorsements; complaint holds, SLA dashboard and CSV report.
