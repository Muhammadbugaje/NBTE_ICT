# Portal door — institutions (mobile API)

Everything an **institution account** can see and do on the QA portal
(`qa.<domain>`), as JSON. Base URL: `https://qa.<domain>/api/v1/` (dev:
`http://<mac-ip>:8000/api/v1/` with header `X-NBTE-Door: portal`).
Every call needs `Authorization: Bearer <access>` (see the contract for
sign-in, refresh, errors and pagination).

The API is the website: the same scoping (`Model.objects.for_user`), the
same services and form validation, the same messages. Anything that belongs
to another institution answers **404** (never 403 with data).

## Conventions used below

* Dates `YYYY-MM-DD`; date-times ISO 8601 with offset (`2026-10-20T03:39:16+01:00`).
* Money: string with two decimals + `*_display` (`"4000000.00"`, `"₦4,000,000.00"`).
* Lists are paginated: `{"results": [...], "next", "previous", "count"}`, `?page=`, `?page_size=` (max 100).
* `download_url` is an **API path**: `GET` it with the Bearer token; the
  answer is the file (`Content-Disposition: attachment`; add `?inline=1` to
  preview a PDF/image inline). Protected files are never exposed by a direct
  media URL.
* `route` values are app routes (the portal page path without the trailing slash),
  e.g. `/payments/12`, `/qa-requests/4/ssq/9`, `/application`.
* Uploads are `multipart/form-data` with the website's validators (extension
  list and MB limit per document kind / setting). A bad file is
  `400 validation_error`.

### Errors you will see on these endpoints

| Status | code | When |
| --- | --- | --- |
| 400 | `validation_error` | bad input; `fields` holds `{field: [messages]}`; the website's own business messages come back as `fields.non_field_errors` and `message` |
| 401 | `not_authenticated` / `token_expired` | no / expired token |
| 403 | `wrong_door` | a staff token on the portal door |
| 403 | `password_change_required` | temporary password not changed yet |
| 403 | `account_inactive` | institution delisted or portal access switched off |
| 403 | `permission_denied` | the action is not allowed for this account or not at this stage (`message` says why, e.g. "Only your institution administrator can invite users.") |
| 404 | `not_found` | unknown id, another institution's object, or a resource-person page asked by an institution account (and vice versa) |

Who may act: some actions are for the **institution administrator** only
(`inst-admin` role, or the primary contact for users). An academic-department
account (`inst-academic-dept`) gets **404** on administrator-only QA request
actions, exactly like the website.

---

## Home additions — `GET home/`

The contract's institution home plus the rest of the portal dashboard:

```json
{
  "institution": {"id": 3, "code": "DMP", "name": "Demo Polytechnic, Kaduna", "status": "approved"},
  "application": null,
  "bills_due": [{"id": 3, "title": "Institutional processing fee (demo, pending)", "amount": "4000000.00",
                 "amount_display": "₦4,000,000.00", "due_date": "2026-11-19", "status": "awaiting_payment"}],
  "programmes_expiring": [{"id": 3, "name": "Computer Science", "level": "National Diploma (ND)",
                           "expires_on": "2027-02-17", "days_left": 118}],
  "payments_by_month": [{"month": "2025-11", "count": 0}],
  "action_items": [
    {"tone": "warn", "title": "Payment due", "body": "Institutional processing fee · ₦4,000,000.00 · ref NBTE-QAP-2026-DMP-000003-27",
     "cta": "Open", "route": "/payments/3"},
    {"tone": "info", "title": "Complete the SSQ: Computer Science", "body": "NBTE-QAR-2026-… · Reaccreditation",
     "cta": "Continue", "route": "/qa-requests/4/ssq/9"}
  ],
  "recent_activity": [{"at": "2026-10-20T03:39:15.781945+01:00",
                       "text": "Bill NBTE-QAP-2026-DMP-000003-27 issued: Institutional processing fee (demo, pending), ₦4,000,000.00."}],
  "programme_board": [{"label": "Accredited", "count": 3}],
  "programme_count": 3,
  "sections": [{"key": "qa_requests", "label": "QA requests", "route": "/qa-requests"},
               {"key": "queries", "label": "Queries", "route": "/queries"},
               {"key": "inspections", "label": "Inspections", "route": "/inspections"},
               {"key": "programmes", "label": "Programmes", "route": "/programmes"},
               {"key": "letters", "label": "Outcome letters", "route": "/letters"},
               {"key": "documents", "label": "Documents", "route": "/documents"},
               {"key": "payments", "label": "Payments", "route": "/payments"},
               {"key": "institution", "label": "Institution", "route": "/institution"},
               {"key": "timeline", "label": "Timeline", "route": "/timeline"},
               {"key": "complaints", "label": "Complaints", "route": "/complaints"}]
}
```

`sections` is the website's navigation for this account, in order: show only
these. A **proposed / under review** institution gets `application`,
`documents`, `payments`, `institution`, `timeline`, `complaints`; an
**approved** one gets the list above (no `application`). `action_items` is
the dashboard's "Needs your attention" list (SOP-02 items first).

---

## Institution

### GET `institution/`

```json
{
  "id": 3, "code": "DMP", "name": "Demo Polytechnic, Kaduna", "status": "approved", "status_label": "Approved",
  "ownership": {"id": 3, "code": "private", "name": "Private"},
  "institution_type": {"id": 1, "code": "polytechnic", "name": "Polytechnic"},
  "category": {"id": 1, "code": "polytechnic", "name": "Polytechnic"},
  "state": {"id": 18, "code": "", "name": "KADUNA"},
  "lga": "Kaduna North", "zone": "NW", "year_established": 2015, "cac_rc_number": "RC 1234567",
  "website": "https://demo-poly.example.ng",
  "handling_department": {"id": 15, "name": "DPAP"},
  "contacts": [
    {"kind": "head_of_institution", "kind_label": "Head of institution", "name": "Dr. Musa Aliyu (Rector)", "value": ""},
    {"kind": "official_email", "kind_label": "Official e-mail", "name": "", "value": "registrar@demo-poly.local"},
    {"kind": "phone", "kind_label": "Phone", "name": "", "value": "08030000001"},
    {"kind": "registrar", "kind_label": "Registrar", "name": "Mrs. Grace Bako", "value": "registrar@demo-poly.local"}
  ],
  "addresses": [{"kind": "permanent_site", "kind_label": "Permanent site", "line1": "Km 4 Kaduna–Zaria Road",
                 "town": "Kaduna", "lga": "Kaduna North", "state": "KADUNA",
                 "one_line": "Km 4 Kaduna–Zaria Road, Kaduna, Kaduna North, KADUNA"}],
  "portal_enabled": true,
  "editable": false,
  "can_manage_users": true,
  "edit": null
}
```

`editable` is true **only while the institution is proposed and its onboarding
application is still a draft** (website rule). Then `edit` holds the current
form values:

```json
"edit": {"name": "Zaria Institute of Technology", "ownership": 3, "institution_type": 1, "category": 1, "state": 18,
         "lga": "Zaria", "year_established": 2024, "cac_rc_number": "RC1", "website": "",
         "official_email": "info@zit.edu.ng", "phone": "08031234567", "head_name": "Dr. Hauwa Bello",
         "registrar_name": "", "bursar_name": "", "address_line1": "Km 5 Sokoto Road", "address_town": "Zaria"}
```

### PATCH `institution/`

Send any subset of the `edit` keys; the server merges them with the current
values and validates with the website's form (`PortalProfileForm`).

```json
{"phone": "08099990000", "website": "https://zit.edu.ng"}
```

→ 200 with the full profile (as GET). Errors: 400 `fields` per key
(e.g. `{"official_email": ["Enter a valid email address."]}`,
`{"category": ["Technical College is a category of …"]}`); 403
`permission_denied` when `editable` is false ("NBTE keeps this record. To
change it, contact NBTE …").

### GET `institution/options/?state=<id>`

Choices for the edit form.

```json
{"ownerships": [{"id": 3, "code": "private", "name": "Private"}],
 "institution_types": [{"id": 1, "code": "polytechnic", "name": "Polytechnic"}],
 "categories": [{"id": 1, "code": "polytechnic", "name": "Polytechnic", "institution_type_id": 1}],
 "states": [{"id": 18, "name": "KADUNA"}],
 "lgas": ["Birnin Gwari", "Chikun", "Giwa", "…"]}
```

`lgas` is filled only when `?state=` is given. 400 if `state` is not a number.

### GET `institution/users/`

```json
{"can_manage": true,
 "results": [{"id": 7, "user_id": 34, "full_name": "Danjuma Admin (Demo Poly)", "initials": "DA",
              "role": "Institutional Administrator", "job_title": "Registrar", "is_primary_contact": true,
              "invited": false}],
 "invite_roles": [{"value": "inst-academic-dept", "label": "Academic department (SSQ sections)"},
                  {"value": "inst-chief-executive", "label": "Chief Executive (submits SSQs)"},
                  {"value": "inst-admin", "label": "Institutional Administrator"}]}
```

`invited` = invited but no password set yet. `invite_roles` is empty when
`can_manage` is false.

### POST `institution/users/` (administrator / primary contact)

```json
{"first_name": "Ada", "last_name": "Obi", "email": "ada@zit.edu.ng", "role": "inst-academic-dept",
 "job_title": "HOD Computing", "institution_department": "Computing", "grade_level": null}
```

→ 201 with the users list (as GET). The invitee receives the website's
set-password e-mail. `grade_level` is required (and ≥ the configured minimum,
default 12) for `inst-admin`. Errors: 400 `fields`; 403 when the account
cannot manage users.

---

## Documents

### GET `documents/` (paginated)

Filters: `state` (`missing|submitted|accepted|returned|expired`), `dept`
(reviewing department code), `need` (`required|optional`), `q` (name).
One row per **requirement**, with the latest upload:

```json
{"results": [{
   "kind": {"id": 1, "code": "cac", "name": "CAC Certificate of Incorporation",
            "description": "Corporate Affairs Commission certificate (private proprietors)."},
   "mandatory": true, "state": "accepted", "state_label": "Accepted",
   "department": {"code": "legal", "name": "Legal Unit"},
   "document": {"id": 2, "version": 1, "status": "accepted", "uploaded_at": "2026-10-20T03:39:16.349114+01:00",
                "review_remark": "", "valid_until": null,
                "file": {"id": 2, "name": "demo-cac.pdf", "size_bytes": 26, "size_label": "26 B",
                         "download_url": "/api/v1/documents/2/download/"}},
   "can_upload": true, "max_mb": 5, "extensions": ["pdf", "jpg", "jpeg", "png"]}],
 "next": "…/api/v1/documents/?page=2&page_size=1", "previous": null, "count": 7}
```

`state_label`: Not uploaded · Awaiting review · Accepted · Returned · Needs a
new version. Show `review_remark` when `state` is `returned`.
`can_upload` false = documents are locked while NBTE reviews the application.

### GET `documents/summary/`

```json
{"mandatory": 6, "ready": 4, "complete": false, "percent": 66,
 "missing": [{"kind_id": 5, "name": "Approved academic brief", "state": "missing"}]}
```

### GET `documents/history/` (paginated)

Every uploaded version:

```json
{"results": [{"id": 2, "kind": "CAC Certificate of Incorporation", "version": 1, "status": "accepted",
              "uploaded_at": "2026-10-20T03:39:16+01:00", "uploaded_by": "Danjuma Admin", "review_remark": "",
              "file": {"id": 2, "name": "demo-cac.pdf", "size_bytes": 26, "size_label": "26 B",
                       "download_url": "/api/v1/documents/2/download/"}}],
 "next": null, "previous": null, "count": 1}
```

### POST `documents/upload/<kind_id>/` (multipart: `file`)

→ 201 with the refreshed row (as in the list, `state` now `submitted`).
Errors: 400 `fields.file` (missing) or `validation_error` with the website's
message ("Upload a PDF, JPG …", "The file is larger than 5 MB."); 403
`permission_denied` ("Documents are locked while NBTE reviews your
application."); 404 unknown / not-required kind.

### GET `documents/<id>/download/`

The file. 404 for another institution's document.

### GET `files/<label>/<id>/`

The generic protected download every `download_url` that is not one of the
specific paths above points to. `label` is one of
`institutions.institutiondocument`, `accreditation.evidence`,
`accreditation.response`, `payments.paymentslip`, `payments.paymentadvice`,
`payments.proofofpayment`, `accreditation.outcomelettercopy`. Same rule as
the website: found through `for_user`, then `can_download`; else 404.

---

## Onboarding application (SOP-01) — proposed institutions

### GET `application/`

```json
{
  "institution": {"id": 9, "code": "ZIT", "name": "Zaria Institute of Technology", "status": "proposed"},
  "can_start": false,
  "application": {"id": 4, "reference": "NBTE-APP-2026-ZIT-0001", "status": "draft", "public_status": "Draft",
                  "round": 1, "cycle": "2026 onboarding", "submitted_at": null, "return_remark": ""},
  "stages": [{"code": "draft", "label": "Draft & documents", "state": "current"},
             {"code": "clearance", "label": "Departmental clearance", "state": "todo"},
             {"code": "institutional_fee_due", "label": "Institutional fee", "state": "todo"},
             {"code": "fee_confirmed", "label": "Fee confirmed by Finance", "state": "todo"},
             {"code": "programme_selection", "label": "Programme selection", "state": "todo"},
             {"code": "programme_clearance", "label": "Programme clearance", "state": "todo"},
             {"code": "ssq_in_progress", "label": "Questionnaire (SSQ)", "state": "todo"}],
  "hold": null,
  "gaps": [],
  "blockers": ["Upload the required documents: CAC Certificate of Incorporation, Approved academic brief."],
  "can_submit": false,
  "documents": {"mandatory": 6, "ready": 4, "complete": false, "percent": 66, "missing": [{"kind_id": 1, "name": "CAC Certificate of Incorporation", "state": "missing"}]},
  "clearances": [{"department": "Legal Unit", "status": "returned", "status_label": "Returned",
                  "decided_at": "2026-10-21T10:00:00+01:00", "opened_at": "2026-10-20T09:00:00+01:00",
                  "remark": "The CAC certificate is not legible."}],
  "fees": [{"id": 3, "reference": "NBTE-QAP-2026-ZIT-000001-66", "description": "Institutional processing fee",
            "amount": "4000000.00", "amount_display": "₦4,000,000.00", "balance": "4000000.00",
            "balance_display": "₦4,000,000.00", "due_date": "2026-11-19", "status": "awaiting_payment",
            "route": "/payments/3"}],
  "programmes": [{"id": 31, "name": "Computer Science", "level": {"id": 1, "code": "nd", "name": "National Diploma (ND)"},
                  "category": "Computing & Information Technology", "status": "proposed", "status_label": "Proposed",
                  "requested_streams": 2, "approved_streams": 0}],
  "estimate": {"total": "300000.00", "total_display": "₦300,000.00",
               "lines": [{"label": "Computer Science — 2 streams", "amount": "300000.00", "amount_display": "₦300,000.00"}],
               "error": "", "blocked": false},
  "selecting_programmes": false,
  "programme_options": null,
  "handled_by": "DPAP"
}
```

* `application` is `null` until started; then show `stages` (`done|current|todo`).
* `gaps` = profile items still missing; `blockers` = why it cannot be submitted.
* `remark` on a clearance is shown only when it was returned.
* `programme_options` (`levels`, `categories`, `max_streams`) is filled when
  `selecting_programmes` is true (status `programme_selection`).

### POST `application/start/` → 200 (as GET)

400 `validation_error` with the website's message (e.g. "Applications are
closed at the moment. The next window will be announced.").

### POST `application/submit/`

```json
{"declaration": true}
```

→ 200 (as GET). 400 when the declaration is missing or `blockers` is not empty
(the messages come back in `fields.non_field_errors`). 404 when there is no
application. 403 `permission_denied` when the account is not the Chief Executive
(v2.1 X12: only the Chief Executive submits applications, programmes, QA
requests and SSQs; `is_chief_executive` in the GET payload says which).

### Programme selection (only at stage `programme_selection`; else 403)

* POST `application/programmes/` `{"name": "Computer Science", "level": 1, "category": 4, "streams": 2}` → 201 (as GET).
  400 `fields` (`name`, `level`, `streams` …; "This programme is already on your list.").
* PATCH `application/programmes/<id>/` `{"streams": "3"}` → 200; 400 `fields.streams`.
* DELETE `application/programmes/<id>/` → 200 (as GET).
* Only `proposed` programmes can be changed (403 otherwise); another institution's programme → 404.
* Programmes NBTE has cleared stay locked when a list is returned (403).
* POST `application/programmes/submit/` (Chief Executive only, else 403) → 200 (as GET): sends the list to
  the programme-clearance department (NTIRD by default, `qa.onboarding.programme_clearance_department`).
  `programme_submission` in the GET payload carries `can_submit`, `blockers`, `chief_executive_only`,
  `count`, `streams`, `department`, `expected_time`, `round` and each programme's `state`
  (`draft | pending | cleared | returned`, with the `remark` of a returned one). When every programme is
  cleared, the programme processing fee appears under `fees`.

---

## Timeline — GET `timeline/` (paginated)

Filters: `kind` (`application|payment|document|institution`), `period`
(`30d|90d|1y`), `q`, `by` (`you|nbte`).

```json
{"results": [{"id": 88, "at": "2026-10-20T03:39:15.781945+01:00",
              "text": "Bill NBTE-QAP-2026-DMP-000003-27 issued: Institutional processing fee (demo, pending), ₦4,000,000.00.",
              "event_code": "payment.issued", "by": "nbte"}],
 "next": null, "previous": null, "count": 1}
```

## Inspections — GET `inspections/` (paginated)

Filters: `status` (`scheduled|completed|closed|cancelled`), `q`, `when` (`upcoming|past`), `year`.

```json
{"results": [{"id": 1, "reference": "NBTE/INS/2026/DPAP/0001", "kind": "accreditation_visit",
              "kind_label": "Accreditation visit", "scheduled_start": "2026-11-09", "scheduled_end": "2026-11-11",
              "actual_start": null, "actual_end": null, "status": "scheduled", "status_label": "Scheduled"}],
 "next": null, "previous": null, "count": 1}
```

---

## QA requests (SOP-02) — approved institutions

### GET `qa-requests/` (paginated)

Filters: `show` (`open` default · `closed` · `all`), `type` (QA type code), `level` (programme level code), `q`.

```json
{"results": [{"id": 4, "reference": "NBTE-QAR-2026-DMP-0001", "qa_type": {"code": "reaccreditation", "name": "Reaccreditation"},
              "status": "ssq_in_progress", "public_status": "Self-study in progress",
              "created_at": "2026-10-20T09:00:00+01:00", "submitted_at": "2026-10-20T09:10:00+01:00",
              "programmes": [{"id": 3, "name": "Computer Science", "level": "nd"}]}],
 "next": null, "previous": null, "count": 1}
```

### GET `qa-requests/new/?type=<code>` (administrators; others 404)

```json
{"can_create": true,
 "types": [{"code": "reaccreditation", "name": "Reaccreditation", "description": "Renewal for accredited programmes …",
            "available": true, "reason": "", "asks_streams": false, "max_programmes": null},
           {"code": "accreditation", "name": "Accreditation", "description": "…", "available": false,
            "reason": "NBTE has not published the questionnaire for this QA type yet.", "asks_streams": false,
            "max_programmes": null}],
 "qa_type": {"code": "reaccreditation", "name": "Reaccreditation"},
 "programmes": [{"id": 3, "name": "Computer Science", "level": {"id": 1, "code": "nd", "name": "National Diploma (ND)"},
                 "status": "accredited", "status_label": "Accredited", "expiry_date": "2027-02-17",
                 "eligible": true, "reason": "", "streams": 2},
                {"id": 5, "name": "Mechanical Engineering Technology", "level": {"id": 1, "code": "nd", "name": "National Diploma (ND)"},
                 "status": "accredited", "status_label": "Accredited", "expiry_date": "2029-10-21",
                 "eligible": false, "reason": "Too early: reassessment opens on 21 Apr 2029, 6 months before expiry (Requirement 4).",
                 "streams": 1}],
 "window_months": 6, "max_streams": 6}
```

### GET `qa-requests/estimate/?type=<code>&programmes=3,4&streams=3:2,4:1`

```json
{"estimate": {"total": "350000.00", "total_display": "₦350,000.00",
              "lines": [{"label": "Fixed amount", "amount": "50000.00", "amount_display": "₦50,000.00"},
                        {"label": "Computer Science — 1 stream", "amount": "150000.00", "amount_display": "₦150,000.00"}],
              "error": "", "blocked": false},
 "problems": [], "count": 2}
```

`estimate.blocked` = NBTE has not confirmed the fee yet (`error` says so).

### POST `qa-requests/` (administrators)

```json
{"type": "reaccreditation", "programmes": [3, 4], "streams": {"3": "2"}}
```

→ 201 with the request detail. 400 `fields.type` (unknown type) or the
website's message (type unavailable, programme not eligible …).

### GET `qa-requests/<id>/`

```json
{
  "id": 4, "reference": "NBTE-QAR-2026-DMP-0001", "qa_type": {"code": "reaccreditation", "name": "Reaccreditation"},
  "status": "draft", "public_status": "Draft", "created_at": "2026-10-20T09:00:00+01:00", "submitted_at": null,
  "fee_note": "", "cancel_reason": "",
  "stages": [{"label": "Draft", "state": "current"}, {"label": "Submitted", "state": "todo"},
             {"label": "QA fee", "state": "todo"}, {"label": "Self-study (SSQ)", "state": "todo"},
             {"label": "NBTE review", "state": "todo"}],
  "programmes": [{"row_id": 11, "programme": {"id": 3, "name": "Computer Science", "level": "nd"}, "status": "active",
                  "requested_streams": 1, "is_new_programme": false, "clearance_status": "", "clearance_remark": "",
                  "dropped_reason": "", "replaced_by": "",
                  "ssq": {"id": 9, "version": 1, "status": "draft", "public_status": "Draft", "done": 4, "total": 13,
                          "review_remark": "", "last_saved_at": "2026-10-21T10:00:00+01:00"}}],
  "candidates": [{"id": 4, "name": "Electrical/Electronic Engineering Technology", "level": "hnd"}],
  "fees": [], "queries": [{"id": 2, "reference": "NBTE-QRY-2026-0002", "title": "Staff list", "status": "open",
                           "due_at": "2026-11-04T10:00:00+01:00"}],
  "estimate": {"total": "200000.00", "total_display": "₦200,000.00", "lines": [], "error": "", "blocked": false},
  "next_step": "Check the programmes and streams, then submit the request.",
  "can_manage": true, "can_add": true, "can_drop": true, "can_replace": true, "can_cancel": true, "can_submit": true,
  "cancellable_stages": ["Draft", "Submitted"], "max_streams": 6
}
```

`ssq` is set on active programmes once the SSQs are open (after the fee):
open it with `qa-requests/<id>/ssq/<ssq.id>/`. `estimate` only while draft.

### Actions (administrators; others 404) — each answers 200 with the detail

| Method + path | Body | Notes |
| --- | --- | --- |
| POST `qa-requests/<id>/submit/` | `{"declaration": true}` | 400 without the declaration / when the website refuses |
| POST `qa-requests/<id>/cancel/` | `{"reason": "…"}` | only in the cancellable stages (R-25) |
| POST `qa-requests/<id>/programmes/` | `{"programme": 4, "streams": "1"}` | add (`can_add`) |
| PATCH `qa-requests/<id>/programmes/<row_id>/` | `{"streams": "2"}` | draft only |
| POST `qa-requests/<id>/programmes/<row_id>/drop/` | `{"reason": "…"}` | `can_drop` |
| POST `qa-requests/<id>/programmes/<row_id>/replace/` | `{"programme": 5, "reason": "…"}` | `can_replace` |

---

## SSQ (self-study questionnaire)

### GET `qa-requests/<id>/ssq/<ssq_id>/`

The whole questionnaire at once (store it for offline drafting):

```json
{
  "id": 9, "request": {"id": 4, "reference": "NBTE-QAR-2026-DMP-0001", "qa_type": {"code": "reaccreditation", "name": "Reaccreditation"}},
  "programme": {"id": 3, "name": "Computer Science", "level": "nd"},
  "template": {"id": 2, "name": "Reaccreditation SSQ", "version": 1},
  "version": 1, "status": "draft", "public_status": "Draft", "is_current": true,
  "review_remark": "", "department_remark": "", "assigned_sections": [], "assigned_department_user": "",
  "last_saved_at": "2026-10-21T10:00:00+01:00", "submitted_at": null,
  "sections": [{
    "key": "programme", "title": "The programme", "description": "", "department": true, "editable": true,
    "progress": {"filled": 2, "total": 4, "complete": false}, "saved_at": "2026-10-21T10:00:00+01:00",
    "fields": [
      {"key": "head_of_department", "type": "text", "label": "Head of department", "required": true, "help": "",
       "placeholder": "", "width": "half", "validation": {}, "options": [], "value": "Dr. A. Musa",
       "prefilled": false, "edited": false, "multiple": false, "document_kind": "",
       "columns": null, "min_rows": null, "max_rows": null, "items": null, "evidence": null, "reusable_documents": null},
      {"key": "nbte_curriculum", "type": "yes_no", "label": "Do you use the NBTE curriculum?", "required": true,
       "help": "", "placeholder": "", "width": "full", "validation": {}, "options": [], "value": "yes",
       "prefilled": false, "edited": false, "multiple": false, "document_kind": "", "columns": null,
       "min_rows": null, "max_rows": null, "items": null, "evidence": null, "reusable_documents": null},
      {"key": "enrolment", "type": "table", "label": "Enrolment", "required": true, "help": "", "placeholder": "",
       "width": "full", "validation": {"min_rows": 1}, "options": [],
       "value": [{"session": "2025/26", "year_one": "40", "year_two": "38"}], "prefilled": false, "edited": false,
       "multiple": false, "document_kind": "",
       "columns": [{"key": "session", "type": "text", "label": "Session", "options": []},
                   {"key": "year_one", "type": "integer", "label": "Year one", "options": []}],
       "min_rows": 1, "max_rows": 200, "items": null, "evidence": null, "reusable_documents": null},
      {"key": "equipment", "type": "catalogue", "label": "Workshop equipment", "required": false, "help": "",
       "placeholder": "", "width": "full", "validation": {}, "options": [], "value": {"12": "3"},
       "prefilled": false, "edited": false, "multiple": false, "document_kind": "", "columns": null,
       "min_rows": null, "max_rows": null,
       "items": [{"id": 12, "name": "Oscilloscope", "unit": "no.", "quantity": "3"}],
       "evidence": null, "reusable_documents": null},
      {"key": "staff_list", "type": "file", "label": "Staff list", "required": true, "help": "", "placeholder": "",
       "width": "full", "validation": {}, "options": [], "value": null, "prefilled": true, "edited": false,
       "multiple": false, "document_kind": "staff_list", "columns": null, "min_rows": null, "max_rows": null,
       "items": null,
       "evidence": [{"id": 40, "name": "staff.pdf", "status": "active", "reused_from": null,
                     "file": {"id": 40, "name": "staff.pdf", "size_bytes": 20480, "size_label": "20 KB",
                              "download_url": "/api/v1/files/accreditation.evidence/40/"},
                     "uploaded_at": "2026-10-21T10:02:00+01:00"}],
       "reusable_documents": [{"id": 2, "kind": "Staff list", "version": 1, "name": "staff-2025.pdf"}]}
    ]}],
  "editable_sections": ["programme", "staff", "facilities"],
  "problems": [{"key": "graduates_last_3", "message": "Required."}],
  "can_submit": true, "can_revise": false, "can_send_department": true, "can_recall": false,
  "can_department_submit": false,
  "department_users": [{"id": 51, "name": "Computing Department"}],
  "is_department": false,
  "versions": [{"id": 9, "version": 1, "status": "draft", "is_current": true}],
  "submission": null,
  "max_mb": 10
}
```

Value shapes by `type`: text-like / number / date (`YYYY-MM-DD`) / `choice` /
`yes_no` (`"yes"|"no"`) → string; `multi_choice` → list of option values;
`catalogue` → `{"<item id>": "<quantity>"}` (only quantities are accepted —
item names are NBTE's); `table` → list of `{column key: value}`; `file` →
`null` (files are in `evidence`). `required` is enforced only on submit
(`problems` lists what is still missing). After submission (`submitted`,
`accepted`, `returned`, `analysed`) `submission.snapshot` is the frozen
rendered copy (list of sections with labels and display values).

### PATCH `qa-requests/<id>/ssq/<ssq_id>/` — partial save (offline sync)

```json
{"answers": {"head_of_department": "Dr. A. Musa", "running_streams": "abc",
             "enrolment": [{"session": "2025/26", "year_one": "40", "year_two": "38"}]}}
```

→ 200

```json
{"saved": ["head_of_department", "enrolment"],
 "errors": {"running_streams": "Enter a whole number."},
 "last_saved_at": "2026-10-21T10:05:00+01:00",
 "progress": {"programme": {"filled": 3, "total": 4, "complete": false}},
 "problems": [{"key": "graduates_last_3", "message": "Required."}]}
```

The website's autosave: only the keys sent are touched (other answers are
never blanked), each value is cleaned with the same rules, good values are
stored even when others fail (they come back in `errors`; keep the local
draft for those). Unknown keys and file fields are ignored. If any key is in
a section the user may not edit now (`editable_sections`), **nothing** is
saved and the answer is 403 `permission_denied` ("You cannot edit this
section now.") — refresh with GET and drop the local draft for that section.
Last write wins per field.

### Evidence (files)

* POST `qa-requests/<id>/ssq/<ssq_id>/evidence/<field_key>/` multipart `file` → 201 with the SSQ (as GET). A single-file field replaces the previous file.
  400 `fields.file` / website message (extension, size `max_mb`, "This field does not take files."); 403 when the section is not editable.
* POST `…/evidence/<field_key>/reuse/` `{"document": 2}` → 201 (re-use an **accepted** institution document from `reusable_documents`).
* DELETE `…/evidence/<evidence_id>/` → 200 (SSQ as GET); 404 if already removed.

### SSQ actions — each answers 200 with the SSQ (as GET)

| Method + path | Body | Who / when |
| --- | --- | --- |
| POST `…/ssq/<ssq_id>/submit/` | `{"declaration": true}` | `can_submit` (chief executive or administrator); 400 while `problems` is not empty or without the declaration |
| POST `…/ssq/<ssq_id>/revise/` | – | `can_revise` (status `returned`): answers with the **new version** (new `id`) |
| POST `…/ssq/<ssq_id>/department/` | `{"department_user": 51, "sections": ["programme"], "remark": "Please complete"}` | `can_send_department` |
| POST `…/ssq/<ssq_id>/recall/` | – | `can_recall` |
| POST `…/ssq/<ssq_id>/department-done/` | – | the assigned department account (`can_department_submit`) |

---

## Queries from NBTE

### GET `queries/` (paginated)

Filters: `status` (`active` default = open + answered · `open` · `answered` · `closed` · `all`), `q`, `about` (`request|other`), `due` (`overdue|week`).

```json
{"results": [{"id": 2, "reference": "NBTE-QRY-2026-0002", "title": "Staff list", "subject_label": "SSQ v1 — Computer Science",
              "status": "open", "status_label": "Open", "due_at": "2026-11-04T10:00:00+01:00", "is_overdue": false,
              "created_at": "2026-10-21T10:00:00+01:00", "qa_request": {"id": 4, "reference": "NBTE-QAR-2026-DMP-0001"}}],
 "next": null, "previous": null, "count": 1}
```

### GET `queries/<id>/`

Row fields plus:

```json
{"body": "Send the staff payroll.", "close_remark": "", "closed_at": null,
 "messages": [{"kind": "query", "id": 2, "author": "NBTE", "from_nbte": true, "body": "Send the staff payroll.",
               "at": "2026-10-21T10:00:00+01:00", "attachment": null},
              {"kind": "response", "id": 5, "author": "Danjuma Admin", "from_nbte": false, "body": "Attached, as asked.",
               "at": "2026-10-21T11:00:00+01:00",
               "attachment": {"id": 5, "name": "payroll.pdf", "size_bytes": 1024, "size_label": "1 KB",
                              "download_url": "/api/v1/files/accreditation.response/5/"}}],
 "can_respond": true, "max_mb": 10}
```

### POST `queries/<id>/reply/` (multipart `body`, optional `file`; or JSON `{"body": "…"}`)

→ 201 with the detail (status becomes `answered`). 400 "Write your response."
(fewer than 3 characters) / file validation; 400 "This query is closed.".

---

## Programmes and outcome letters

### GET `programmes/board/`

```json
{"window_months": 6, "renewal_type": {"code": "reaccreditation", "name": "Reaccreditation"},
 "cards": [{"id": 3, "name": "Computer Science", "level": {"id": 1, "code": "nd", "name": "National Diploma (ND)"},
            "status": "accredited", "status_label": "Accredited", "approval_type": "", "approved_streams": 2,
            "commencement_date": "2024-10-20", "expiry_date": "2027-02-17", "days_left": 118, "urgency": "ok",
            "window_opens": "2026-08-17", "window_open": true, "can_renew": true, "letter": null}]}
```

`urgency`: `expired` · `soon` (≤ 90 days) · `ok` · `""` (no expiry date).
`can_renew` → button "Request re-accreditation" →
`qa-requests/new/?type=<renewal_type.code>`.

### GET `programmes/` (paginated)

Filters: `q`, `status`, `level` (code).

```json
{"results": [{"id": 3, "name": "Computer Science", "level": {"id": 1, "code": "nd", "name": "National Diploma (ND)"},
              "category": "Computing & Information Technology", "status": "accredited", "status_label": "Accredited",
              "approved_streams": 2, "commencement_date": "2024-10-20", "expiry_date": "2027-02-17",
              "days_left": 118, "expiry_label": "118 days left", "professional_bodies": ["Computer Professionals Registration Council"]}],
 "next": null, "previous": null, "count": 3}
```

### GET `programmes/<id>/`

Row fields plus `approval_type`, `requests` (`[{"id", "reference", "qa_type", "created_at", "public_status"}]`),
`history` (`[{"at", "text"}]`, newest first, max 50) and `letters` (as below). Dropped programmes and
other institutions' programmes → 404.

### GET `letters/` (paginated)

```json
{"results": [{"id": 6, "reference": "NBTE/DPAP/ACC/2026/0006", "programme": "Computer Science (ND)", "programme_id": 3,
              "is_current": true, "generated_at": "2026-10-21T12:00:00+01:00", "outcome": "approve",
              "approval_type": "Full accreditation", "expiry_date": "2031-10-21",
              "copies": [{"id": 11, "kind": "original", "kind_label": "Original", "name": "letter.pdf",
                          "download_url": "/api/v1/letters/11/download/"},
                         {"id": 12, "kind": "duplicate", "kind_label": "Duplicate (watermarked)", "name": "letter-dup.pdf",
                          "download_url": "/api/v1/letters/12/download/"}]}],
 "next": null, "previous": null, "count": 1}
```

`outcome` other than `approve` = "Not approved". `is_current: false` = replaced by a newer letter.

### GET `letters/<copy_id>/download/` → the PDF.

---

## Payments

The app never collects card data. "Pay online" opens the provider's own
checkout page in the browser; the provider sends the payer back to the
portal payment page; the app then asks the server to verify.

### GET `payments/` (paginated)

Filters: `group` (`due` = to pay · `checking` = being confirmed · `settled` = paid · `closed` = cancelled/expired), `fee` (fee schedule code), `period` (`30d|90d|1y`), `q`.

```json
{"results": [{"id": 3, "reference": "NBTE-QAP-2026-DMP-000003-27", "description": "Institutional processing fee (demo, pending)",
              "fee": "Institutional processing fee", "amount": "4000000.00", "amount_display": "₦4,000,000.00",
              "balance": "4000000.00", "balance_display": "₦4,000,000.00", "status": "awaiting_payment",
              "status_label": "Awaiting payment", "is_settled": false, "created_at": "2026-10-20T03:39:15.775904+01:00",
              "due_date": "2026-11-19", "source_reference": ""}],
 "next": "…/api/v1/payments/?page=2&page_size=1", "previous": null, "count": 6}
```

### GET `payments/summary/`

```json
{"due_total": "4000000.00", "due_total_display": "₦4,000,000.00",
 "counts": {"due": 1, "checking": 1, "settled": 3},
 "groups": [{"value": "", "label": "All bills"}, {"value": "due", "label": "To pay"},
            {"value": "checking", "label": "Being confirmed"}, {"value": "settled", "label": "Settled"},
            {"value": "closed", "label": "Cancelled / expired"}],
 "fees": [{"code": "institutional_processing", "name": "Institutional processing fee"}]}
```

### GET `payments/<id>/`

Row fields plus:

```json
{"amount_in_words": "Four million naira only", "amount_paid": "0.00", "amount_paid_display": "₦0.00", "remita_rrr": "",
 "steps": [{"label": "Bill issued", "state": "current"}, {"label": "You pay", "state": "todo"},
           {"label": "Finance checks", "state": "todo"}, {"label": "Director of Finance confirms", "state": "todo"}],
 "rejected_reason": "", "needs_info": "", "awaiting_confirmation": false,
 "payable": true,
 "online_methods": [{"code": "remita", "label": "Remita (RRR — online or at any bank)", "test_mode": true},
                    {"code": "paystack", "label": "Paystack (card, transfer, USSD)", "test_mode": true}],
 "bank_transfer": true,
 "bank_account": {"bank_name": "Central Bank of Nigeria — TSA (DEMO)", "account_name": "National Board for Technical Education",
                  "account_number": "1234567890"},
 "receipt_needed": false, "allow_partial": false, "proof_extensions": ["pdf", "jpg", "jpeg", "png"], "proof_max_mb": 5,
 "slip": {"id": 3, "version": 1, "expires_at": "2026-11-19T03:39:15.788384+01:00", "is_expired": false,
          "file": {"id": 3, "name": "slip.pdf", "size_bytes": 0, "size_label": "0 B", "download_url": "/api/v1/payments/3/slip/"}},
 "can_regenerate_slip": false,
 "advices": [],
 "proofs": [],
 "attempts": [{"id": 3, "provider": "remita", "provider_label": "Remita", "reference": "822614102305", "amount": "4000000.00",
               "amount_display": "₦4,000,000.00", "status": "pending", "status_label": "Pending",
               "created_at": "2026-10-20T03:39:15.785691+01:00", "failure_reason": ""}]}
```

* `payable` → show "How to pay": one button per `online_methods` (`test_mode` = simulator, no real money) and, when `bank_transfer`, the `bank_account` + slip + "I have paid".
* `rejected_reason` (Finance could not confirm the last payment) and `needs_info` (Finance needs more information) are callouts.
* `awaiting_confirmation` → "Payment received — awaiting Finance confirmation".
* `receipt_needed` → ask for the provider's receipt (POST `receipt/`).
* `advices` = NBTE's Payment Advice (receipt) once confirmed.

### GET `payments/<id>/slip/` → the current slip PDF (404 if none).
### POST `payments/<id>/slip/` → 200 detail with a new slip version (only while unpaid; 400 otherwise). Offer it when `can_regenerate_slip`.

### POST `payments/<id>/online/`

```json
{"provider": "paystack"}
```

(`amount` optional; defaults to the balance — part payments only where allowed.
`return_to` optional: `"web"` (default) | `"app"`, see "Back to the app" below.)
→ 201

```json
{"checkout_url": "https://checkout.paystack.com/…",
 "reference": "NBTE-QAP-2026-DMP-000003-27-P2",
 "provider": "paystack",
 "return_url": "https://qa.nbte.gov.ng/payments/3/",
 "return_to": "web"}
```

Open `checkout_url` in the system browser / an in-app browser tab. When
the browser reaches `return_url` (Paystack adds `?reference=&trxref=`,
Remita `?RRR=&orderID=`), close it and call `verify/` with those query
parameters.

**Back to the app (`"return_to": "app"`).** Send it when the checkout opens
in the phone's *system browser* (no website session there). `return_url`
is then `https://<portal>/payments/<id>/app-return/<signed token>/`. After
the provider (or, in development, the simulator) sends the payer back, that
page checks the payment with the provider exactly like the portal page
(`verify_on_return`), shows the result with a **"Return to the Digital NBTE
app"** button and opens, after ~0.6 s (the button is the fallback):

```
digitalnbte://payment-return?bill=3&status=paid&reference=NBTE-QAP-2026-DMP-000003-27-P2
```

`status`: `paid` (the provider reports it paid — NBTE Finance still
confirms) · `pending` (no answer yet) · `failed`. Register the
`digitalnbte` scheme (host `payment-return`) in the app; on open, reload
`payments/<bill>/` (and you may call `verify/` with `{"params":
{"reference": "<reference>"}}`). The page needs no sign-in (the token in the
path, valid 3 days, is the permission) and shows no bill details. Without
`return_to` nothing changes: the payer lands on the normal portal payment
page. Same initiation as the website's "Pay with …" button
(`payments.services.start_online_payment`). 400 `fields.provider` (not
`remita|paystack`), or the website's message ("Part payments are not
accepted online. Pay the full balance of ₦…", "Paystack is not available.",
"… cannot take a payment now (…)").

### POST `payments/<id>/verify/`

```json
{"params": {"reference": "NBTE-QAP-2026-DMP-000003-27-P2", "trxref": "NBTE-QAP-2026-DMP-000003-27-P2"}}
```

→ 200

```json
{"matched": true,
 "attempt": {"id": 4, "provider": "paystack", "reference": "NBTE-QAP-2026-DMP-000003-27-P2",
             "status": "provider_reported_paid", "status_label": "Provider reported paid", "failure_reason": ""},
 "message": "Payment received by the provider. NBTE Finance will now confirm it. You will be notified.",
 "bill": {"…": "as GET payments/<id>/"}}
```

`matched: false` = no attempt with that reference on this bill (nothing
changes). A provider "paid" is **never final**: the bill moves to
"awaiting Finance confirmation"; only the Director of Finance confirms. If the
user closed the browser without returning, the webhook or the scheduled poll
catches up — refresh the bill later.

### POST `payments/<id>/proof/` (multipart)

`amount_claimed` (naira), `paid_on` (`YYYY-MM-DD`, not in the future),
`bank_name`, `teller_number`, `payer_name` (optional), `file` (teller or
transfer receipt; `proof_extensions`, ≤ `proof_max_mb`).
→ 201 detail ("Institution claims paid — awaiting Finance confirmation").
400 `fields` for missing/invalid ones or the website's message.

### GET `payments/<id>/receipt/` → the latest Payment Advice PDF (404 until Finance confirms).
### POST `payments/<id>/receipt/` (multipart `file`) → 201 detail: the provider's receipt for a provider-reported payment (404 if there is none).

---

## Complaints and help (institutions and resource persons)

Institutions see every complaint of their institution; resource persons only
their own. Internal NBTE notes are never returned.

### GET `complaints/` (paginated) — `status` (`open` default · `closed` · `all`), `q`

```json
{"results": [{"id": 1, "reference": "NBTE-CMP-2026-000001", "subject": "Processing fee paid but still awaiting confirmation",
              "layer": {"id": 1, "name": "Payment issues"}, "status": "received", "stage_label": "Received",
              "is_open": true, "created_at": "2026-10-19T14:39:16.362652+01:00", "closed_at": null, "related_label": ""}],
 "next": null, "previous": null, "count": 3}
```

### GET `complaints/new/?about=payment:3`

```json
{"layers": [{"id": 1, "code": "payment-issues", "name": "Payment issues", "description": "…", "parent": "",
             "custom_fields": [{"key": "teller", "label": "Teller number", "type": "text", "required": false, "options": []}]}],
 "suggested_layer": 1, "about": "payment:3", "related_label": "Payment obligation NBTE-QAP-2026-DMP-000003-27",
 "popular": [{"slug": "payment-not-confirmed", "title": "My payment is not confirmed yet", "summary": "…", "layer": "Payment issues"}]}
```

`about` kinds: `payment`, `qa_request`, `ssq`, `programme`, `inspection`,
`case`, `timeline` (`kind:id`). An `about` the user cannot see is ignored.

### GET `complaints/suggest/?subject=…&body=…&layer=<id>` → `{"results": [article]}` (knowledge-base hits while typing)

### POST `complaints/` (multipart or JSON)

`layer` (id), `subject` (≥ 5 chars), `body` (≥ 10 chars), `about` (optional
`kind:id`), `answers` (`{custom field key: value}`; in multipart send one
part per answer named `answers.<key>`), `files` (multipart only, repeat the
`files` part per file).
→ 201 with the detail. 400 `fields.layer` / `fields.subject` / `fields.body` /
`fields.<custom key>`.

### GET `complaints/<id>/`

Row fields plus:

```json
{"body": "We paid on 2 Oct …", "resolution": "", "resolved_at": null, "department": "Finance", "officer": "",
 "steps": [{"label": "Received", "state": "now"}, {"label": "Being worked on", "state": "todo"},
           {"label": "Resolved", "state": "todo"}, {"label": "Closed", "state": "todo"}],
 "messages": [{"id": 3, "author": "Danjuma Admin", "initials": "DA", "mine": true, "is_system": false,
               "body": "We paid on 2 Oct …", "at": "2026-10-19T14:39:16+01:00",
               "attachments": [{"id": 1, "name": "teller.pdf", "size_bytes": 2048, "size_label": "2 KB",
                                "download_url": "/api/v1/complaints/1/files/1/"}]}],
 "actions": [{"code": "withdraw", "label": "Withdraw"}],
 "can_reply": true, "waiting_for_me": false, "reopen_days": 14}
```

* POST `complaints/<id>/reply/` (`body`, `files`) → 201 detail.
* POST `complaints/<id>/actions/<code>/` `{"remark": "…", "satisfaction": 4}` → 200 detail. Only codes in `actions`
  (`confirm` with an optional 1–5 `satisfaction`, `reopen` with a `remark`, `withdraw`); anything else 403.
* GET `complaints/<id>/files/<file_id>/` → the attachment (public messages only).

### Help

* GET `help/?q=` → `{"results": [{"slug", "title", "summary", "layer"}]}`
* GET `help/<slug>/` → `{"slug", "title", "summary", "layer", "body"}` (body is plain text with line breaks / simple Markdown as written by NBTE)
* POST `help/<slug>/vote/` `{"helpful": true}` → 204

---

## Not in the app (web only)

* **Self-registration** of a new institution (`/register/`) and the
  read-only "view as" impersonation sessions.
* **Account settings** beyond `me/` (passkeys, notification settings):
  other endpoints of the contract.
* **Alerts / notifications**: separate feature (another developer).
* The website's HTMX-only helpers (live LGA list, live estimate fragments)
  are replaced by `institution/options/` and `qa-requests/estimate/`.
