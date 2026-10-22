# `api` — REST API for the Digital NBTE mobile app (Phase 9)

The contract the Flutter app is built against lives in `mobile/API_CONTRACT.md`
(shared with the app repository). This app is the server side of it.

## Layout

| File | What it holds |
| --- | --- |
| `urls_common.py` | routes both doors share (health, auth, me, home, schema/docs) |
| `urls_staff.py` | `api/v1/` on the internal host (included from `nbte_ict/urls.py`), plus staff-only routes |
| `urls_portal.py` | `api/v1/` on the QA portal host (included from `qa_core/portal_urls.py`), plus portal-only routes |
| `views/` | thin views: `auth.py`, `me.py`, `home.py`, `misc.py` (health, schema, docs) |
| `services/` | read-side helpers that shape existing website logic for JSON (`me.py`, `home.py`, `access.py`) |
| `serializers.py` | request validation **and** response shapes (responses are passed through them, so the OpenAPI schema matches the payload) |
| `authentication.py` | `ApiJWTAuthentication` (Bearer JWT + door + inactive checks) |
| `permissions.py` | `CorrectDoor`, `PasswordChangeCompleted`, `DocsAccess` |
| `exceptions.py` | contract error codes and the exception handler (error envelope) |
| `middleware.py` | `X-NBTE-Min-App-Version` header + JSON 404/500 for anything under `/api/` |
| `pagination.py` | `StandardPagination` (`page_size` 25, max 100) |
| `throttles.py` | `LoginRateThrottle` (10/min per IP + e-mail) |
| `tokens.py` | refresh/access tokens with the `door` and `user_type` claims |
| `devices.py` | app sessions: `register_app_session()` (one `accounts.UserSession` row of kind "app" per install, id in the `sid` token claim), rotation, `check_app_session()` (revoked device -> 401 `token_revoked`) |
| `docs/` | endpoint docs for the Flutter developer, one file per domain |

Settings are in the "Phase 9: mobile API" block of `nbte_ict/settings/base.py`
(`REST_FRAMEWORK`, `SIMPLE_JWT`, `SPECTACULAR_SETTINGS`, `API_MIN_APP_VERSION`,
`API_DEV_DOOR_HEADER`); `dev.py` adds the browsable API renderer and the dev
door header while `DEBUG` is on.

## Doors

| Door | Users | Host | URLconf |
| --- | --- | --- | --- |
| `staff` | `user_type = staff` | internal host | `nbte_ict/urls.py` → `api.urls_staff` |
| `portal` | `institution`, `resource_person` | `QA_PORTAL_HOST` | `qa_core/portal_urls.py` → `api.urls_portal` |

* The door of a request is decided by `QAPortalHostMiddleware` from the host
  (`request.is_qa_portal`). In development only (`DEBUG` **and**
  `API_DEV_DOOR_HEADER`), an `/api/` request with `X-NBTE-Door: portal` is
  treated as a portal request (`qa_core/hosts.py`), so a phone that reaches
  the dev server by IP can use the portal door. Website pages ignore it.
* `auth/token/` refuses a user of the other door with 403 `wrong_door` and
  `correct_door` (only after the password was right, so nothing leaks).
* Every token carries `door`; a token used on the other door is refused with
  403 `wrong_door`. `CorrectDoor` re-checks the user's *current* `user_type`
  and the institution's portal access (delisted / switched off → 403
  `account_inactive`).

## Auth flow

1. `POST auth/token/` `{email, password, device}` → `{access (15 min), refresh (30 days), user, door}`.
   Same backend as the website (e-mail or staff file number), the website's
   failed-sign-in lockout (`core.ratelimit` "login": 5 per e-mail+IP per 15 min)
   plus the DRF login throttle (10/min per IP+e-mail).
2. Every call: `Authorization: Bearer <access>`.
3. On 401 `token_expired`: `POST auth/refresh/` `{refresh}` → a new pair; the old
   refresh is blacklisted (a reused one answers 401 `token_revoked`).
4. `POST auth/logout/` `{refresh}` → 204, blacklists it (no access token needed).
5. If `user.must_change_password` is true, everything except `me/`,
   `auth/password/change/` and `auth/logout/` answers 403
   `password_change_required`. `auth/password/change/` uses the website's
   own forms (same validators); a temporary password needs no `old_password`.
6. `POST auth/password/forgot/` → always 204; staff door sends the website's
   reset e-mail, portal door sends the portal's set-password link.

## Errors

`HTTP status + {"error": {"code", "message", "fields"?, "correct_door"?, "retry_after"?}}`

| Code | Status | When |
| --- | --- | --- |
| `validation_error` | 400 | serializer/form errors (`fields`, nested keys dotted: `device.platform`), unreadable JSON |
| `not_authenticated` | 401 | no/invalid token |
| `invalid_credentials` | 401 | wrong e-mail or password |
| `token_expired` / `token_revoked` | 401 | expired access or refresh / blacklisted refresh |
| `permission_denied` | 403 | not allowed |
| `wrong_door` | 403 | other door; `correct_door` says which |
| `password_change_required` | 403 | temporary password still in use |
| `account_inactive` | 403 | deactivated account or institution portal access off |
| `not_found` | 404 | unknown object or URL under `/api/` |
| `method_not_allowed` | 405 | |
| `throttled` | 429 | `retry_after` seconds (+ `Retry-After` header) |
| `not_ready` | 501 | endpoint stub waiting for its feature |
| `server_error` | 500 | anything unexpected (logged; no internals in the body) |

Raise `api.exceptions.ApiError` subclasses (or DRF/Django exceptions) — never
build error responses by hand.

## Endpoints now

| Endpoint | Door | Status |
| --- | --- | --- |
| `health/` | both | done (no auth) |
| `auth/token/`, `auth/refresh/`, `auth/logout/`, `auth/password/change/`, `auth/password/forgot/` | both | done |
| `me/` GET/PATCH | both | done |
| `home/` | both | done (staff `pinned_chats` from messaging, `announcement` = newest waiting action_needed/urgent) |
| `me/preferences/`, `me/devices/…`, `me/push-token/`, `me/deletion-request/` | both | done — `docs/me.md` |
| `me/staff-id/` | staff | done — `docs/me.md` |
| `notifications/…` | both | done (incl. `notifications/feed/` for background polling) — `docs/notifications.md` |
| `search/` | staff | done (the website's `core.search` scoping, grouped) — `docs/search.md` |
| `dashboard/executive/` | staff | done (director / deputy / ES / chairman boards, same access rule) — `docs/dashboard.md` |
| push (FCM HTTP v1) | both | `notifications/push.py`, off unless `FCM_SERVICE_ACCOUNT_FILE` is set — `web/PUSH_NOTIFICATIONS.md` |
| `announcements/…` | staff | done — `docs/announcements.md` |
| `insights/` | staff | done — `docs/insights.md` |
| `schema/`, `docs/` | both | DEBUG, or a signed-in superuser |
| institution/, documents/, application/, qa-requests/ (+ SSQ), queries/, programmes/, letters/, payments/, timeline/, inspections/, files/ | portal (institutions) | done — `api/docs/portal_institution.md` |
| engagements/, service-history/, profile/ | portal (resource persons) | done — `api/docs/portal_resource_person.md` |
| complaints/, help/ | portal (both) | done — `api/docs/portal_institution.md` |
| `chats/…` (threads, messages, updates, polls, voice, search) | staff | done — `api/docs/chats.md` |
| `memos/…` (tabs, detail, actions, create, files) | staff | done — `api/docs/memos.md` |
| `tasks/…`, `tickets/…`, `requests/…`, `leave/…` | staff | done — see `docs/work.md` |
| `attendance/…` | staff | done (check-out 501: the website records check-in only; office-code check-in `attendance/check-in/code/`) — `docs/attendance.md` |
| `assets/…`, `staff/…`, `workspace/` | staff | done — `docs/assets_staff.md` |
| `qa/…` (summary, cases, confirmations, finance, institutions, applications, requests/SSQ, complaints, inspections, files) | staff | done — see `api/docs/qa_staff.md` |

## Adding an endpoint

1. **Service first.** Put the logic in the owning app's services (reuse what
   the website view calls; never copy a query). `api/services/` only shapes data.
2. **Serializers.** A request serializer for every input (query params too) and
   a response serializer; pass the response dict through it.
3. **View.** An `APIView` (or generic view) in `api/views/<area>.py`:
   default permissions already require auth + correct door + changed password.
   Set `allow_while_password_change_required = True` only for profile/auth
   screens. Lists use `api.pagination.StandardPagination`. Decorate with
   `@extend_schema(operation_id=..., request=..., responses={200: ..., **AUTHED_ERRORS})`.
4. **Route.** Both doors → `urls_common.py`; one door → `urls_staff.py` or
   `urls_portal.py`.
5. **Scope.** Institution data must go through `Model.objects.for_user(user)`
   (or an existing scoped helper) — the API is not a way around scoping.
6. **Tests** in `api/tests/` (use the fixtures in `api/tests/conftest.py`):
   - happy path on the right door; wrong door → 403 `wrong_door`;
   - no token → 401; `must_change_password` → 403 `password_change_required`;
   - another user's/institution's object → 404 (never 403 with data);
   - validation errors come back as `fields`;
   - pagination shape for lists;
   - the schema still validates (`test_schema_generates_without_warnings`).
7. Update `mobile/API_CONTRACT.md` and this table.

## Docs

With `DEBUG=True` (dev settings): open `http://localhost:8000/api/v1/docs/`
(staff door) or `http://qa.localhost:8000/api/v1/docs/` (portal door). Use
"Authorize" with `Bearer <access>`. Raw schema: `api/v1/schema/`.
In production both answer only for a signed-in superuser.

Offline / CI:

```bash
python manage.py spectacular --validate --fail-on-warn --urlconf nbte_ict.urls --file api-staff.yaml
python manage.py spectacular --validate --fail-on-warn --urlconf qa_core.portal_urls --file api-portal.yaml
python -m pytest api -q -p no:cacheprovider
```

Deploying: `pip install -r requirements.txt` then `python manage.py migrate`
(adds the `token_blacklist` tables). Run `python manage.py flushexpiredtokens`
daily to prune expired tokens.
