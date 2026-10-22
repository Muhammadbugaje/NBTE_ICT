# Portal door — resource persons (mobile API)

What a **resource person** (`user_type = resource_person`) can see and do on
the QA portal, as JSON. Base URL, auth, errors, pagination, dates and file
downloads are as in `portal_institution.md` ("Conventions"); development
uses header `X-NBTE-Door: portal`.

Everything is read through `inspection.services` (`rp_of`, `rp_engagements`,
`rp_service`, `rp_files`), so a resource person only ever sees their own
engagements and records. Another person's engagement answers **404**;
institution pages (`institution/`, `payments/`, `documents/` …) answer **404**
to a resource person, and these pages answer **404** to an institution account.

| Status | code | When |
| --- | --- | --- |
| 400 | `validation_error` | bad input (`fields`) or the website's refusal (`fields.non_field_errors`) |
| 401 | `not_authenticated` / `token_expired` | |
| 403 | `wrong_door` / `password_change_required` / `account_inactive` | as in the contract (demo resource persons start with a temporary password) |
| 403 | `permission_denied` | "This engagement is not yours." etc. |
| 404 | `not_found` | not yours / not a resource-person account |

---

## Home — GET `home/`

The contract's resource-person home plus `sections` (the portal navigation
for this account):

```json
{"next_visit": {"id": 12, "institution": "Kaduna Demo Polytechnic (Phase 4)", "date": "2026-11-09",
                "role": "Team lead", "programmes": 2, "route": "/visits/12"},
 "todo": [{"kind": "report", "id": 2, "title": "Write findings: ND Computer Science (Kaduna Demo Polytechnic (Phase 4))",
           "due_label": "Wed 11 Nov"}],
 "history": [{"institution": "Kaduna Demo Polytechnic (Phase 4)", "date": "2026-10-17", "allowance_status": "pending"}],
 "sections": [{"key": "engagements", "label": "Engagements", "route": "/engagements"},
              {"key": "service", "label": "History", "route": "/service-history"},
              {"key": "profile", "label": "Profile", "route": "/profile"},
              {"key": "complaints", "label": "Complaints", "route": "/complaints"}]}
```

`next_visit.id` and `todo[].id` are engagement ids (`engagements/<id>/`).

---

## Engagements

### GET `engagements/` (paginated)

Filters: `status` (`all` default · `live` = upcoming and current · `released` = completed), `duty`
(`ssq_review|physical_verification|resource_inspection|tracking`), `when` (`upcoming|past`), `q` (institution or programme).

```json
{"results": [{"id": 2, "programme": {"id": 1, "name": "ND Computer Science"},
              "institution": "Kaduna Demo Polytechnic (Phase 4)", "duty": "resource_inspection",
              "duty_label": "Resource inspection", "from_date": "2026-11-09", "to_date": "2026-11-11",
              "status": "released", "status_label": "Completed", "role": "Resource inspection",
              "findings": "", "can_write_findings": true, "findings_closed_reason": ""}],
 "next": null, "previous": null, "count": 1}
```

* `status_label`: "Awaiting confirmation" (proposed) · "Active" · "Completed" (released) · "Overridden".
* `role`: the role on the visit team (e.g. "Team lead"), else the duty.
* `can_write_findings` false → show `findings_closed_reason` ("Findings open
  once your engagement is confirmed." / "The team has submitted this
  programme's report.") and the saved `findings` read-only.

### GET `engagements/<id>/`

Row fields plus the case and the visit:

```json
{"id": 2, "programme": {"id": 1, "name": "ND Computer Science"}, "institution": "Kaduna Demo Polytechnic (Phase 4)",
 "duty": "resource_inspection", "duty_label": "Resource inspection", "from_date": "2026-11-09", "to_date": "2026-11-11",
 "status": "released", "status_label": "Completed", "role": "Resource inspection", "findings": "",
 "can_write_findings": true, "findings_closed_reason": "",
 "case_reference": "NBTE/QAC/2026/DPAP/0001",
 "visit": {"reference": "NBTE/INS/2026/DPAP/0001", "kind": "accreditation_visit", "kind_label": "Accreditation visit",
           "scheduled_start": "2026-11-09", "scheduled_end": "2026-11-11", "status": "closed", "status_label": "Closed"}}
```

`visit` is `null` for SSQ reviews and tracking, or before the visit is approved.

### POST `engagements/<id>/findings/` — "My findings"

```json
{"findings": "Workshops are adequately equipped; the library needs more current titles."}
```

→ 200 with the engagement detail (the Team Lead sees the findings on the
report). Same rule as the website (`inspection.services.submit_findings`):
visit duties only, engagement confirmed, until the team submits the
programme's report. Errors: 400 `fields.findings` ("Write your findings (at
least 20 characters).") or the closed reason in `fields.non_field_errors`;
404 not yours.

---

## Service history

### GET `service-history/` (paginated)

Filters: `duty`, `year` (e.g. `2026`), `work` (`visits|ssq`), `q`.

```json
{"results": [{"id": 10, "served_from": "2026-10-17", "served_to": "2026-10-18", "duty": "resource_inspection",
              "duty_label": "Resource inspection", "is_correction": false,
              "institution": "Kaduna Demo Polytechnic (Phase 4)", "programme": "Computer Science",
              "role": "Resource inspection", "visit_count": 1, "ssq_count": 0, "allowance_status": "pending"}],
 "next": null, "previous": null, "count": 1}
```

`is_correction` = a compensating row (show the "Correction" chip).

### GET `service-history/summary/` (same filters)

```json
{"visits": 1, "ssqs": 0, "years": [2026],
 "duties": [{"value": "ssq_review", "label": "SSQ review"}, {"value": "physical_verification", "label": "Physical verification"},
            {"value": "resource_inspection", "label": "Resource inspection"}, {"value": "tracking", "label": "Tracking"}]}
```

---

## Profile & CV

### GET `profile/`

```json
{"id": 2, "full_name": "Dr. Zainab Abdullahi", "email": "rp.zainab@demo.nbte.local", "phone": "",
 "status": "active", "status_label": "Active", "source": "professional_body", "source_label": "Professional body",
 "rank": "principal-lecturer", "rank_label": "Principal lecturer", "qualifications": "Computing assessor",
 "disciplines": [{"name": "Science & Environmental", "is_primary": true}],
 "lapsed": [],
 "files": [{"id": 2, "kind": "cv", "kind_label": "CV", "name": "cv.pdf", "uploaded_at": "2026-10-19T12:13:34.975873+01:00"}],
 "file_kinds": [{"value": "cv", "label": "CV"}, {"value": "promotion_proof", "label": "Promotion proof"},
                {"value": "other", "label": "Other"}],
 "editable_fields": []}
```

`lapsed` (non-empty) → "No longer eligible for new engagements" with the
reasons; confirmed engagements continue. The record itself is **read-only**
(`editable_fields` is always `[]`): on the website "to change your details,
write to the QA Directorate". Names can be changed with `PATCH me/` (contract).
There are no bank details on the resource-person record.

### POST `profile/files/` (multipart)

`kind` (`cv` default · `promotion_proof` · `other`), `file` (PDF, Word or image; ≤ `qa.documents.max_upload_mb`, default 10 MB).
→ 201 with the profile. 400 `fields.file` ("Choose a file.", "Upload a PDF, Word document or image.", "The file is larger than 10 MB.").

The uploaded files are listed but not downloadable from the portal (the
website shows them without a link), so the app does not get a download URL.

---

## Complaints and help

Same endpoints as institutions (`complaints/`, `complaints/new/`,
`complaints/suggest/`, `complaints/<id>/`, `…/reply/`, `…/actions/<code>/`,
`…/files/<id>/`, `help/`, `help/<slug>/`, `…/vote/`) — see
`portal_institution.md`. A resource person sees only the complaints they
raised; the layers offered are those open to resource persons (e.g. visit
scheduling, allowances).

---

## Not in the app (web only / not on the website)

* **Service-history export (PDF)**: the website has none, so neither does the API.
* **Editing the record** (disciplines, rank, contact details): NBTE staff do
  it on the internal system; there are no bank details on the record.
* **Team list, checklists and scores of a visit**: the portal does not show
  them to resource persons (only their own findings box); scoring and the
  post-visit report are done by NBTE staff on the internal system.
* **Alerts / notifications**: separate feature (another developer).
