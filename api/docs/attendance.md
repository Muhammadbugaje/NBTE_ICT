# Attendance (staff door)

GPS check-in from the phone, using exactly the website's check-in rules (`attendance.services.check_in_user`). Staff door only; Bearer token required. Common errors (401 `not_authenticated`/`token_expired`, 403 `wrong_door`/`password_change_required`/`account_inactive`, 429 `throttled`) apply to every endpoint.

Statuses: `present`, `late`, `absent`, `excused`, `holiday`, `on_leave`. Sources: `gps`, `manual` (office code or HR edit), `excel` (uploaded register), `auto` (auto-absent), `leave` (approved leave).

## GET `attendance/today/`
```json
{"date": "2026-10-22", "is_working_day": true, "checked_in": false, "record": null,
 "can_check_in": true, "reason": "",
 "locations": [{"id": 2, "name": "test sambo road", "latitude": 10.537013, "longitude": 7.432678,
                "radius_meters": 250, "work_start_time": "09:00", "grace_minutes": 120}],
 "max_accuracy_meters": 200, "manual_check_in_enabled": true, "check_out_supported": false}
```
* `can_check_in: false` comes with `reason` (the website's message), one of:
  "Today is not a working day, so attendance can't be recorded. …", "Today is a holiday (Independence Day), so attendance can't be recorded. …", "You already checked in today (Present).", "You are not assigned to a department, so we can't match a location."
  Being on approved leave does not block (the record is replaced by the check-in, as on the website).
* `locations` = active locations serving my department (all of them if I have no department). `radius_meters` is the effective geofence (radius + GPS offset); a check-in inside it matches. Use them to show "you are X m from the office" before posting.
* `record` (after check-in or for leave/holiday): same shape as a history row.
* `max_accuracy_meters`: readings less precise than this are refused.
* `manual_check_in_enabled`: the website also offers a rotating office code check-in; the app does not (yet).

## POST `attendance/check-in/`
```json
{"latitude": 10.537013, "longitude": 7.432678, "accuracy": 12, "is_mocked": false}
```
`accuracy` (metres) is required. `is_mocked`: send the OS flag (Android `Location.isMock()`, iOS `sourceInformation.isSimulatedBySoftware`).

Response **201**:
```json
{"status": "present", "status_display": "Present", "message": "Checked in — you're on time.",
 "distance_meters": 0, "location": "test sambo road",
 "record": {"date": "2026-10-22", "status": "present", "status_display": "Present",
            "check_in_at": "2026-10-22T01:06:02.653521+01:00", "location": "test sambo road",
            "distance_meters": 0, "source": "gps", "source_display": "GPS check-in", "detail": ""}}
```
`late` (message "Checked in — you're late. This has been logged.") when after the location's start time + grace; a late notice is e-mailed as on the website.

Errors (all 400):

| Code | Message / when |
| --- | --- |
| `validation_error` `fields.latitude/longitude/accuracy` | missing or not a number |
| `validation_error` (`non_field_errors`) | "Invalid coordinates." (out of range or 0,0); "Your device did not report location accuracy. …"; "Your location is only accurate to about 900 m (limit 200 m). Move to an open area and try again." |
| `mock_location` | `is_mocked` true: "Your phone reports a mock (fake) location. Turn off any location-faking app or developer setting and try again." |
| `check_in_refused` | the website's refusal: non-working day / holiday, "You already checked in today (Present).", no department, "You're outside every approved attendance location for your department. Move closer to the office and try again." |

```json
{"error": {"code": "check_in_refused",
  "message": "You're outside every approved attendance location for your department. Move closer to the office and try again."}}
```

**Mock-location policy.** The website (a browser) cannot detect fake GPS, so it has no rule. The API refuses any reading the phone marks as mocked and writes the attempt to the attendance log (`[attendance] mock_location · user=… — Refused mobile check-in: mock location reported at lat,lng (accuracy N m)`); no record is created. A phone that does not report the flag is treated like the website.

## POST `attendance/check-out/`
Always **501** `not_ready`: "Check-out is not recorded: attendance only records your check-in." The website has no check-out (only check-in time is stored). `today.check_out_supported` is `false`; hide the button.

## GET `attendance/history/?year=2026&month=10&status=absent`
My records, newest first. `year` defaults to this year, `month` optional (1–12), `status` optional. Like the website's "My attendance", past working days of the period without a record are first filled in as absent.
```json
{"results": [
  {"date": "2026-10-22", "status": "present", "status_display": "Present", "check_in_at": "2026-10-22T01:06:02.653521+01:00",
   "location": "test sambo road", "distance_meters": 0, "source": "gps", "source_display": "GPS check-in", "detail": ""},
  {"date": "2026-10-21", "status": "absent", "status_display": "Absent", "check_in_at": null, "location": null,
   "distance_meters": null, "source": "manual", "source_display": "Manual entry", "detail": ""}],
 "next": "http://…/api/v1/attendance/history/?page=2&page_size=2", "previous": null, "count": 4}
```
`detail` explains credited days: "On approved leave — approved by Hauwa Director of ICT (Demo)", "Excused by … — note", "Holiday". Errors: 400 `fields.month` / `fields.year` / `fields.status`.

## GET `attendance/summary/`
This week (Monday–Sunday) day by day, its tally up to today, and the last 30 days.
```json
{"week_start": "2026-10-19", "week_end": "2026-10-25",
 "week": {"present": 1, "late": 0, "absent": 3, "excused": 0, "holiday": 0, "on_leave": 0,
          "credited": 1, "expected": 4, "percent": 25},
 "days": [{"date": "2026-10-19", "weekday": "Mon", "is_working_day": true, "is_future": false,
           "status": "absent", "status_display": "Absent", "check_in_at": null},
          {"date": "2026-10-22", "weekday": "Thu", "is_working_day": true, "is_future": false,
           "status": "present", "status_display": "Present", "check_in_at": "2026-10-22T01:06:02.653521+01:00"},
          {"date": "2026-10-24", "weekday": "Sat", "is_working_day": false, "is_future": true,
           "status": null, "status_display": null, "check_in_at": null}],
 "last_30_days": {"present": 1, "late": 0, "absent": 3, "excused": 0, "holiday": 0, "on_leave": 0,
                  "credited": 1, "expected": 4, "percent": 25},
 "streak": 1}
```
(`days` always has 7 entries; abbreviated here.) Counting follows the website: only recorded working days count (`expected`); `credited` = everything except absent; `percent` = credited / expected (integer). `streak` = consecutive working days attended (leave/excused/holiday days neither add nor break it).
