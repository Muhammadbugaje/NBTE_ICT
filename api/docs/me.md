# Account endpoints (`me/…`)

For the Flutter app. Base: `…/api/v1/` on the chosen door (portal door in development:
header `X-NBTE-Door: portal`). Every call needs `Authorization: Bearer <access>`.
Errors use the contract envelope `{"error": {"code", "message", "fields"?}}`.

Common errors on every endpoint below:

| Status | Code | When |
| --- | --- | --- |
| 401 | `not_authenticated` | no or bad token |
| 401 | `token_expired` | access token expired → call `auth/refresh/` |
| 401 | `token_revoked` | **this device was signed out** (from another device, the website, or logout) → sign out locally |
| 403 | `wrong_door` | token of the other door (`correct_door` says which) |
| 403 | `password_change_required` | temporary password still in use |
| 403 | `account_inactive` | account / institution switched off |
| 429 | `throttled` | `retry_after` seconds |

`GET/PATCH me/` itself is documented in `mobile/API_CONTRACT.md` ("Profile"). Changes in this
release: `unread.announcements` is the real count of unread announcements (staff; 0 for portal
accounts) and `capabilities.post_announcements` is true for staff who may post announcements.

---

## Notification preferences

### GET `me/preferences/` — both doors

Same settings as the website's "Notification settings" page (staff) and the portal's account
page (institutions, resource persons). Portal accounts **do not get** `tasks_tickets` and
`approvals_memos` (those switches do not exist for them); use `kinds` to draw the switches.

Response 200 (staff):
```json
{
  "chats": true, "tasks_tickets": true, "approvals_memos": true, "payments": true,
  "qa_cases": true, "announcements": true, "general": true,
  "email_digest": true,
  "quiet_hours_enabled": false, "quiet_from": "22:00", "quiet_to": "06:30",
  "allow_urgent_during_quiet": true,
  "show_previews": "always",
  "transcribe_voice_notes": true,
  "timezone": "Africa/Lagos",
  "kinds": [
    {"key": "chats", "label": "Chats and messages", "help": "New chat messages and replies."},
    {"key": "tasks_tickets", "label": "Tasks and tickets", "help": "Tasks assigned to you, ticket updates and reminders."},
    {"key": "approvals_memos", "label": "Approvals and memos", "help": "Requests waiting for your approval and memos sent to you."},
    {"key": "payments", "label": "Payments", "help": "Invoices, receipts and payment confirmations."},
    {"key": "qa_cases", "label": "Quality assurance", "help": "Applications, QA requests, queries, inspections and decisions."},
    {"key": "announcements", "label": "Announcements", "help": "NBTE-wide announcements and circulars."},
    {"key": "general", "label": "General", "help": "Account, system and everything else."}
  ],
  "timezones": ["Africa/Lagos", "Africa/Accra", "Africa/Cairo", "Africa/Johannesburg", "Africa/Nairobi",
                "Europe/London", "Europe/Paris", "Asia/Dubai", "Asia/Riyadh", "America/New_York", "UTC"]
}
```
`show_previews`: `always` | `unlocked` | `never`. Times are `HH:MM` (24 h) in `timezone`.

### PATCH `me/preferences/` — both doors

Send only what changed (any subset of the keys above except `kinds`/`timezones`). Response 200 =
the full GET shape after saving.

```json
{"chats": false, "quiet_hours_enabled": true, "quiet_from": "21:30", "quiet_to": "06:00"}
```

Errors: 400 `validation_error` with `fields`, e.g.
- `{"show_previews": ["\"sometimes\" is not a valid choice."]}`
- `{"timezone": ["Select a valid choice. Mars/Base is not one of the available choices."]}`
- portal account sending `tasks_tickets`/`approvals_memos`:
  `{"tasks_tickets": ["This setting is not available for your account."]}`
- empty body: `{"non_field_errors": ["Send at least one setting to change."]}`

Notes: in-app (bell) notifications are always stored; these switches decide push and e-mail
(`notifications.preferences.should_deliver`).

---

## Devices (signed-in sessions)

Each sign-in from the app (`auth/token/`) creates one **app** device row; signing in again from
the same install (`device.device_id`) reuses it. Website browsers appear as **web** rows. The
row follows refresh-token rotation, so one install = one row.

### GET `me/devices/` — both doors

Response 200 (a plain list, current device first, then most recently seen):
```json
[
  {"id": 31, "label": "NBTE app on Pixel 7", "kind": "app", "platform": "android", "door": "internal",
   "app_version": "1.0.0", "created_at": "2026-10-21T09:12:00+01:00",
   "last_seen_at": "2026-10-21T10:02:11+01:00", "current": true},
  {"id": 27, "label": "Chrome on Windows", "kind": "web", "platform": "web", "door": "internal",
   "app_version": "", "created_at": "2026-10-19T08:00:03+01:00",
   "last_seen_at": "2026-10-21T08:55:40+01:00", "current": false}
]
```
- `kind`: `app` | `web`. `door`: `internal` (staff) | `portal`.
- `label` for app rows: `NBTE app on <device.model>` (or `NBTE app on Android/iPhone`).
- `last_seen_at` is refreshed at most every 5 minutes.

### DELETE `me/devices/<id>/` — both doors

Signs that device out. 204, no body.
- app row: its refresh token is blacklisted and its access tokens are refused at once
  (401 `token_revoked` on that phone);
- web row: the browser's session is deleted (signed out on its next page load).

Deleting the **current** device signs this app out (prefer `auth/logout/`).
Errors: 404 `not_found` (unknown id, someone else's device, or already signed out).

### POST `me/devices/revoke-others/` — both doors

No body. Signs out every other device (app and web), keeps this one. 204.

### POST `me/push-token/` — both doors

Stores the push token on **this** device row. When the server has FCM configured
(`web/PUSH_NOTIFICATIONS.md`), every new in-app notification is pushed to each signed-in app
device with a token (FCM HTTP v1), following `me/preferences/` (kind switches, quiet hours —
urgent ones break through when `allow_urgent_during_quiet` — and `show_previews: never` sends
"You have a new notification. Open the app to read it." instead of the text). Tokens FCM reports
as `UNREGISTERED` are cleared; send a fresh one when the FCM SDK rotates it.

Push message: `notification` = `{title, body}`; `data` (all strings) =
`{"notification_id": "9021", "category": "task", "kind": "tasks_tickets", "target": "<JSON of the
notification target, or empty>"}`; Android `priority: high`, `tag` `n<id>` (use it to replace /
dedupe with the background feed, `notifications/feed/`).
```json
{"push_token": "fcm-or-apns-token", "device_id": "uuid-generated-on-first-launch"}
```
`push_token` empty or `null` clears it (user turned push off). `device_id` optional (used only
if the access token has no device, e.g. issued before this release). 204.

Errors: 400 `validation_error` — `{"push_token": ["This field is required."]}`; or
`{"device_id": ["This device is not signed in. Sign in again to turn on notifications."]}`
when no device row matches (sign in again).

You can also send `device.push_token` with `auth/token/`.

---

## Account deletion request

Apple/Google require that deletion can be started in the app. NBTE keeps statutory records, so
this files a request that an administrator handles (`accounts.deletion`). Show `removed_items`
and `kept_items` on the confirm screen.

### GET `me/deletion-request/` — both doors

Response 200:
```json
{
  "request": {"id": 12, "status": "pending", "status_label": "Pending", "reason": "Leaving NBTE",
              "created_at": "2026-10-21T09:30:00+01:00", "handled_at": null, "note": "",
              "can_withdraw": true},
  "open": true,
  "removed_items": ["Your ability to sign in (the account is switched off)", "Your phone number and profile photo",
                    "Your emergency contact details", "Signed-in devices and passkeys"],
  "kept_items": ["Payment records and receipts", "Accreditation and quality-assurance decisions",
                 "Audit logs of actions taken with the account",
                 "Official correspondence and records NBTE must keep by law"]
}
```
`request` is the latest request in any status (or `null`). `status`: `pending` | `approved` |
`declined` | `completed` | `withdrawn`. `open` is true while it is pending or approved.

### POST `me/deletion-request/` — both doors

```json
{"reason": "optional, up to 2000 characters"}
```
201 with the `request` object above (new request; administrators are notified).
200 with the existing request if one is already open (idempotent).
Errors: 400 `{"reason": ["Ensure this field has no more than 2000 characters."]}`.

### DELETE `me/deletion-request/` — both doors

Withdraws a **pending** request. 204. 404 `not_found` when there is no pending request
(an approved request can no longer be withdrawn).

---

## Digital staff ID

### GET `me/staff-id/` — staff door only

Response 200 (header `Cache-Control: no-store`):
```json
{
  "token": "eyJ1Ijo0Miwidy…:1tC…:Xq…",
  "verify_url": "https://<internal host>/verify/staff/eyJ1Ijo0Miwidy…/",
  "expires_in": 60,
  "valid_for": 120,
  "qr_svg": "<svg xmlns=\"http://www.w3.org/2000/svg\" …>…</svg>",
  "card": {"full_name": "Fatima Danladi", "initials": "FD", "designation": "Director of Finance",
           "department": "Finance", "staff_number": "NBTE/1234", "photo_url": "https://…/photo.jpg"}
}
```
- Render `qr_svg` (or draw your own QR of `verify_url`). Fetch again every `expires_in` seconds;
  a scanned code is accepted by the public verify page for `valid_for` seconds.
- `verify_url` uses `INTERNAL_HOST` when the server sets it, otherwise the host the app called.
- `photo_url` may be `null`; `designation`, `department`, `staff_number` may be `""`.
- Never shows e-mail or phone.

Errors: on the portal door this route does not exist (404 `not_found`).

Passkeys are website-only: there are no passkey endpoints.
