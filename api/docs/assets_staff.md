# Assets, staff directory, My workspace (staff door)

Staff door only; Bearer token required. Common errors (401 `not_authenticated`/`token_expired`, 403 `wrong_door`/`password_change_required`/`account_inactive`, 429 `throttled`) apply everywhere. Lists are paginated (`results`, `next`, `previous`, `count`; `?page`, `?page_size` ≤ 100). `UserRef` = `{"id", "name", "initials"}`.

## Assets

Who sees which asset (the website's `assets/access.py`): everyone sees the assets in their own custody; directors / deputies / office heads (or "view department assets") see their department's; "view all assets" sees all. An asset you may not see is **404** — by tag and by scan alike, so a scan never confirms a hidden tag exists.

States: `new`, `in_service`, `under_repair`, `damaged`, `retired`, `lost`, `disposed`.

### GET `assets/mine/?status=current|returned&q=`
Assets in my custody (`current`, default) or handed back (`returned`). `q` searches tag, brand, model, serial.
```json
{"results": [{"asset": {"tag": "ICT-LAP-00007", "name": "Dell Latitude 5420", "category": "Laptop",
              "state": "in_service", "state_display": "In service"},
   "assigned_at": "2026-09-01T10:00:00+01:00",
   "assigned_by": {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD"},
   "expected_return_at": null, "returned_at": null, "is_overdue": false,
   "condition_on_assign": "good", "condition_on_return": ""}],
 "next": null, "previous": null, "count": 1}
```

### GET `assets/by-code/<code>/`
`<code>` = what the QR scanner read (our labels encode the asset URL, e.g. `https://…/assets/ICT-LAP-00007/`) or a typed tag (case-insensitive). The website's scan logic extracts the tag. Response: asset detail (below). 404 `not_found` if there is no such asset among those I may see. URL-encode nothing special: `assets/by-code/https://nbte.gov.ng/assets/ICT-LAP-00007/` works as a path.

### GET `assets/<tag>/`
```json
{"tag": "ICT-LAP-00007", "name": "Dell Latitude 5420", "category": "Laptop", "state": "damaged",
 "state_display": "Damaged", "serial_number": "5CD1234", "brand": "Dell", "model": "Latitude 5420", "color": "",
 "description": "", "location": "ICT block, Room 12", "owner_department": {"id": 1, "name": "ICT"},
 "custodian": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
 "assigned_since": "2026-09-01T10:00:00+01:00", "expected_return_at": null,
 "acquired_at": "2025-01-15", "warranty_until": "2028-01-15", "is_mine": true,
 "issues": [{"id": 3, "summary": "Screen cracked", "description": "Dropped it",
             "reported_by": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
             "reported_at": "2026-10-22T10:02:00+01:00", "resolved_at": null, "resolution_notes": ""}],
 "attachments": [{"id": 5, "name": "crack.jpg", "description": "Hinge loose too", "size": 81234,
                  "uploaded_by": {"id": 16, "name": "Hauwa Director of ICT (Demo)", "initials": "HD"},
                  "created_at": "2026-10-22T10:05:00+01:00",
                  "download_url": "http://…/api/v1/assets/ICT-LAP-00007/attachments/5/"}],
 "can_edit": false, "can_report_issue": true, "can_attach_photo": false}
```
`can_edit` = website edit rights (`assets.change_asset` on this asset). The app is read-only for asset management (assign / return / change state stay on the website).

### POST `assets/<tag>/issues/` — report an issue
JSON `{"summary": "Screen cracked", "description": "Dropped it"}`, or multipart with an optional `photo`.
* Anyone who can see the asset may report (summary 5+ characters). An `in_service` asset becomes `damaged`, as on the website.
* `photo` is stored as an asset photo — the website allows that only to users with edit rights (`can_attach_photo`). Others get **403** "You cannot add photos to this asset. Raise a ticket for it with the photo instead." — use `POST tickets/` with `asset_tag` + `photo` (the website's "Raise a ticket" button on the asset page does the same).

Response **201**: asset detail. Errors: 400 `fields.summary` ("Give the issue a short but descriptive summary."), 400 `fields.photo` (file type/size), 403 (photo), 404.

### GET `assets/<tag>/attachments/<attachment_id>/`
The file (`?inline=1` to preview). 404 if the asset is not visible.

## Staff directory

What a staff member sees follows the website's staff list (`staff/permissions.py`): with "view staff" + cross-department access → everyone; "view staff" or "view own department staff" → my department (+ myself); otherwise only my own record. Only the fields the website's staff page shows to every viewer are returned (no HR fields: no date of birth, emergency contacts, history).

### GET `staff/?q=&department=<id>`
`q` searches first/last name, file number, e-mail, designation. Ordered by surname.
```json
{"results": [{"id": 18, "user_id": 17, "full_name": "Ibrahim ICT Officer (Demo)", "initials": "II", "photo_url": null,
  "designation": null, "department": {"id": 1, "name": "ICT"}, "office": null, "state": null,
  "email": "qa.ict_officer@demo.nbte.local", "phone": ""}],
 "next": null, "previous": null, "count": 1}
```
`id` = staff record id (for `staff/<id>/`); `user_id` = account id (start a chat; null when the person has no account). `state` = staff state (e.g. "On leave"), `photo_url` absolute or null.

### GET `staff/<id>/` — profile card
```json
{"id": 18, "user_id": 17, "full_name": "Ibrahim ICT Officer (Demo)", "initials": "II", "photo_url": null,
 "designation": null, "department": {"id": 1, "name": "ICT"}, "office": null, "state": null,
 "email": "qa.ict_officer@demo.nbte.local", "phone": "", "level": "admin", "subsection": "", "office_location": "",
 "category": null, "grade_level": "9", "reports_to": null, "date_joined_service": null,
 "contact_preference": "Email", "duties": "", "can_message": true}
```
`level`: `admin` / `manager` / `self` / `colleague` (the website's view level). `can_message`: show "Message". 404 for someone outside my scope.

### GET `staff/departments/`
The department filter of the list: all active departments if I see every department, else only mine.
```json
{"results": [{"id": 1, "code": "ict", "name": "ICT"}], "next": null, "previous": null, "count": 1}
```

## My workspace

### GET `workspace/`
The website's "My workspace" page (`dashboards.services.self_service_context`): one card per module with counts and up to 4 recent items, my leave, and approvals waiting for me (up to 5).
```json
{"profile": {"staff_id": 18, "employee_no": "USR-00017", "designation": null,
             "department": {"id": 1, "name": "ICT"}, "state": null},
 "widgets": [
   {"key": "tickets", "title": "My tickets", "count": 1, "count_label": "open",
    "items": [{"id": "3", "kind": "ticket", "title": "Monitor flickers", "subtitle": "TKT-2026-00003",
               "status": "Assigned", "route": "/tickets/3"}]},
   {"key": "tasks", "title": "My tasks", "count": 0, "count_label": "open", "items": []},
   {"key": "requests", "title": "My leave & requests", "count": 1, "count_label": "active",
    "items": [{"id": "18", "kind": "request", "title": "No internet in Room 12",
               "subtitle": "REQ-2026-00018 · Network / internet issue", "status": "Awaiting approval",
               "route": "/requests/18"}]},
   {"key": "assets", "title": "My assets", "count": 0, "count_label": "in your custody", "items": []}],
 "leave": {"staff_linked": true, "can_apply": true,
           "balances": [{"leave_type": "annual-leave", "name": "Annual leave", "year": 2026, "entitlement": "12",
                         "used": "0", "pending": "0", "remaining": "12", "is_set": true, "entitlement_source": "role"}],
           "current": null, "upcoming": null, "resumption_date": null, "days_until_upcoming": null},
 "pending_approvals": [{"id": 21, "reference": "REQ-2026-00021", "subject": "Vehicle for Kaduna trip",
                        "step": "Director approval",
                        "requester": {"id": 17, "name": "Ibrahim ICT Officer (Demo)", "initials": "II"},
                        "route": "/requests/21"}]}
```
* Counts follow the website: tickets = my open tickets; tasks = my open tasks (drafts and tasks where my part is done excluded); requests = my requests not closed; assets = in my custody.
* `items[].id` is a string (asset tag for assets); `route` is the app route to open.
* `profile` and `leave` are null when the account has no staff record. `leave` has the shape of `GET leave/`.
