# NBTE ICT — Codebase review (Phase 0)

**Date:** 13 October 2026  
**Scope:** Study only. No application code was changed. This file is the deliverable.

This review is of the **current working tree**, not of `HEAD`. Almost every app, template, and stylesheet is already modified or untracked. `git status` is the source of truth for what is in flight.

---

## Safety note before any later phase

The working tree is large and mixed: feature work, migrations, SQLite (`db.sqlite3`), staff photos, generated PDFs under `workflow/records/`, and a deleted `.env`. I did **not** commit, reset, or stash anything.

The master prompt’s “commit everything first” advice conflicts with the same prompt’s “never commit without being asked.” **Please say explicitly if you want a commit**, and which paths to include. Do not commit `.env`, `db.sqlite3`, or generated media unless you intend to.

`REPORT.md` is deleted in the working tree. `README.md` exists but could not be opened as text in this session (the reader treated it as binary). Treat README operational steps as unverified here.

Docs (`overview.md`, `structure.md`, `PROJECT_IMPROVEMENT_BRIEF.md`, nested drafts in `master_startwork.md`) lag the working copy. Several items they still list as missing already exist in uncommitted files (contact inbox, leave app, self-service dashboard, shared partials, `dialog.js`, deleted `theme.js`).

---

## 1. App list and what each one owns

Django project package: `nbte_ict/` (settings, root URLs, Celery). `AUTH_USER_MODEL` is `accounts.User`. Root URLs: `nbte_ict/urls.py`.

| App | Owns |
| --- | --- |
| **accounts** | Custom email-login `User`, `Role` (rank, permissions, `max_open_tasks`, unused `requires_2fa`), per-user permission overrides, login/password/profile, user admin, department queryset helper `accounts/scoping.py`, force-password-change middleware, `RolePermissionBackend`. |
| **staff** | `Department`, `Staff` (photo, state, reporting), executive positions/tenures, directory and leadership UIs, import/export, department-aware `staff/permissions.py`. |
| **config** | `ConfigEntry` registry, `config.get` / `get_for_user` / `get_for_department`, `/settings/` UI, `seed_config` (creates missing keys only). |
| **workflow** | Request types/chains/steps, `Request` lifecycle, approvals, comments, attachments, generated `record_pdf`, management UI under `/manage/workflow/`, emails. |
| **tasks** | Categories, assignable tasks, review queue, comments, attachments, dependencies, timers, bulk ops, reminders via config. |
| **tickets** | Helpdesk tickets, categories/SLA, assignment, comments, attachments, escalation Celery jobs. |
| **assets** | Register, photos, assignments (including expected return), attachments, labels/scan. |
| **attendance** | Locations, GPS and rotating-code manual check-in, records, imports, leave-linked rows, emails. |
| **leave** | Annual entitlements, `LeaveApplication` tied 1:1 to a workflow `Request`, management UI, signals into attendance. **Not in older “14 apps” lists; it is installed.** |
| **portal** | Announcements, events, documents (restricted versions), awards, holidays, gallery, reminders. |
| **messaging** | Staff threads, participants, messages, read state, archive, attachments. Separate from public contact. |
| **notifications** | In-app `Notification`, email engine (`engine.py` + `api.queue_email`), quota, Celery send, routing logs. |
| **dashboards** | Role dashboards (director/deputy/ES/chairman/superadmin), audit log, **self-service** at `/dashboards/me/`, **action-queue aggregation** in `dashboards/action_queue.py` (rendered by `workflow.views.awaiting_action`). |
| **core** | Public landing + contact POST, contact inbox/detail, home card grid, protected downloads, import/export wizard, impersonation, health, audit wiring, storage helpers, shared forms/excel/dates. |
| **public** | Landing/leadership helpers (`public/services.py`). `public/models.py` is empty; public content mostly lives in `staff` + `portal`. |

**Permissions and scoping (how, not just where):**

- Django permissions come from the user’s `Role` plus overrides (`accounts.backends.RolePermissionBackend`). Templates hide links with `{% if perms... %}`; that is presentation only.
- Department fail-closed helper: `accounts.scoping.scope_to_department`. Must be **called** by a view/service. It is not automatic on every queryset.
- `User.can_access_department` / `has_perm_for_department` are the object-level pair.
- Workflow detail uses a custom `_can_view` (requester, assignee, past actors, current resolved approver, receiving-dept director, superuser) — **not** `scope_to_department`.
- Role dashboards use `_guard` on role **codes** (`director`, `deputy-director`, …) plus optional permissions. White-labelling later will collide with hardcoded role slugs and “ES” copy.
- Portal create/edit/delete uses `@permission_required`. List visibility is via portal services (`live_announcements(user=...)` etc.).
- Contact inbox: `core.can_handle_contact_messages`.
- Leave management: `leave.manage_leave_entitlements`.
- Settings: `config.change_configentry`.
- Protected files: `RestrictedFileModel.can_download` → `core.views.protected_download`.

---

## 2. Current UI patterns and where they drift

### What already exists (use these; do not invent parallels)

- Tokens: `static/css/tokens.css` labelled **v4** (not the v2 file nested inside `master_startwork.md`). Semantic aliases `--accent`, `--danger`, etc. exist at the bottom of the file.
- Shell: `templates/base.html` + `static/css/app-shell.css` + `static/css/components.css`.
- Public shell: `templates/base_public.html`.
- Shared partials: `templates/partials/page_header.html`, `empty_state.html`, `list_row.html`, `sidebar.html`, `topbar.html`.
- Confirm helper: `static/js/dialog.js` → `NBTEConfirm.open()` and forms with `data-confirm`.
- Theme: `NBTE.Theme` in `static/js/app.js`, storage key **`nbte-theme`**, default from `prefers-color-scheme`. `static/js/theme.js` is **already deleted**.
- Buttons: `.btn` in `components.css`.
- Toasts: `NBTE.Toast` in `app.js`.
- Global page loader + HTMX request hooks in `app.js`.

`window.confirm` / `onclick="return confirm("` were **not** found in templates/JS. HTMX itself still has a native `confirm()` path if anyone sets `hx-confirm`.

### Drift

**Tokens vs design docs.** Live tokens use a different spacing scale (`--space-5` = 1.5rem here vs 1.25rem in the v2 draft), different radii, and **durations above 360ms** (`--duration-slow: 420ms`, `--duration-slower: 700ms`). `--ease-spring` is a bounce curve. Replacing `tokens.css` wholesale would restyle the whole product.

**Two token languages.** Older CSS uses `--primary`; newer/docs use `--accent`. Aliases exist, but feature sheets still hardcode hex and `[data-theme="dark"]` overrides (especially `leadership.css`, `manage.css`, `attendance.css`, `action-queue.css`, `tickets.css`).

**Two confirmation systems.**

1. `NBTEConfirm` (`dialog.js`) — intended shared path.
2. `data-confirm-open` / `data-confirm-modal` in `app.js` — a second modal.
3. `NBTE.Modal` also lives in `app.js`.

**Shared partials barely adopted.** `page_header` / `list_row` / `empty_state` appear on leave, self-service, and contact inbox. Most lists still use local markup (`page-header`, custom empty blocks, table layouts).

**Breakpoints are not the four-width scale in the vision prompt.** Observed:

| Location | Breakpoints |
| --- | --- |
| Vision / `design.md` (short file) | 320 / 390 / 768 / 1024 / 1440 checks; mobile rethink below 640 |
| `app-shell.css` | 700, 900, 901, 1024 |
| `app.js` mobile sidebar/impersonation | `max-width: 720px` |
| Many feature CSS files | their own 520 / 640 / 768 / 900 mixes |

**Alpine.js is documented as allowed but is not used.** Interaction is vanilla JS + HTMX 2.0.4 (`static/vendor/htmx.min.js`). Chart.js is loaded from a **CDN** in `base.html`.

**Nav copy is still org-specific.** Sidebar brand is hardcoded “Digital NBTE”; subtitle falls back to “ICT Directorate” instead of `site.department_name` from config. Ticket/task email prefixes in config still say `[NBTE ICT]`.

**Messaging list search is client-side** (`static/js/messaging.js` filters `[data-message-thread]` in the DOM). Fine for a small inbox page; it is the anti-pattern the global-search phase must not copy.

---

## 3. Where config values are read vs. hardcoded

**Registry pattern (correct):** `config/defaults.py` → `seed_config` `get_or_create` (never overwrites) → `config.get(key, default, scope)` cached in `config/registry.py`. Department override scope is `department:<code>` via `Department.config_scope`. Secrets are rejected on `ConfigEntry.save()` via `FORBIDDEN_FRAGMENTS`.

**Already in `config/defaults.py` (and generally read from the registry in services):** site name/contact, profile completeness weights/threshold, email provider/limits/delays, thumbnail sizes, disk warn %, workflow cross-dept routing, leave entitlement JSON, ticket SLA/escalation/resolution email copy, task caps/reminders/escalation, attendance week/GPS/grace/manual codes.

**`get_for_user` / `get_for_department`:** implemented and tested; **almost unused in production views**. Rank/task caps often live on `Role` instead.

**Hardcoded (candidates for Phase 8, not changes now):**

- Pagination: `PER_PAGE` / `LIST_PER_PAGE` constants 25–50 in workflow, tasks, tickets, assets, attendance, notifications, leave, staff, users.
- Upload memory limits in `nbte_ict/settings/base.py` (`25 * 1024 * 1024`).
- Django session length: **no** `SESSION_COOKIE_AGE` (framework default 2 weeks).
- Role dashboard access by **role code string**.
- Workflow `_can_view` special-case `role.code == "director"`.
- Email branding `EMAIL_LOGO_URL` env default (Cloudinary NBTE logo).
- `SITE_NAME` env default `"NBTE ICT Directorate"`.
- Sidebar/topbar literal strings.
- Chart.js CDN URL.
- Attendance timezone also exists as config `attendance.report_timezone` **and** Django `TIME_ZONE = Africa/Lagos`.

Secrets and deploy identity correctly stay in env (`SECRET_KEY`, DB, Redis, Brevo, Gmail, `SITE_URL`). Production settings fail closed if those are missing.

---

## 4. File uploads and deletions today

### Intended architecture

- `MEDIA_ROOT` default: **already** `BASE_DIR / "media"` (project root), overridable by env.
- `PROTECTED_MEDIA_ROOT` default: `BASE_DIR / "protected_media"`.
- `STORAGES["default"]` = `core.storage.PublicMediaStorage`.
- `STORAGES["restricted"]` = `core.storage.RestrictedMediaStorage` (no public `.url()`).
- Canonical upload helper: `core.storage.uuid_upload_to` + `MEDIA_SUBDIR` on the model + `capture_original_filename`.
- Restricted subclasses: `StaffAttachment`, portal `DocumentVersion` → `/files/<label>/<pk>/`.

### Where files actually go (models)

| Kind | Mechanism | On-disk prefix |
| --- | --- | --- |
| Staff / executive photos | `uuid_upload_to` | `staff/photos`, `staff/executive` |
| Staff private docs | restricted + `uuid_upload_to` | `staff/private_documents` |
| Portal images/gallery | `uuid_upload_to` | `portal/announcements`, `events`, `awards`, `gallery/...` |
| Portal document files | restricted | `portal/document_versions` |
| Workflow PDF + attachments | `uuid_upload_to` | `workflow/records`, `workflow/attachments` |
| Asset photos/attachments | `uuid_upload_to` | `assets/photos`, `assets/attachments` |
| Messaging attachments | `uuid_upload_to` | `messaging/attachments` |
| **Task attachments** | **local `task_attachment_upload`** | `tasks/<uuid>` (not `tasks/attachments/`) |
| **Ticket attachments** | **local `ticket_attachment_upload`** | `tickets/<uuid>` (not `tickets/attachments/`) |

Profile pictures live on **Staff**, not on `User`. There is no `accounts/profiles/` folder today.

### Deletion

- Django does **not** delete `FileField` files on model delete unless you add it.
- **`django-cleanup` is not in requirements.**
- The only consistent `storage.delete` on `post_delete` found: `messaging/signals.py` for `MessageAttachment`.
- Import/export wizard deletes temp paths via `default_storage.delete`.
- Other attachments (tasks, tickets, workflow, portal, assets, staff photos) are **orphan risk** on record delete.
- No `os.remove` in app code for uploads (good).

### Working-tree evidence that paths have drifted

- No `media/` or `protected_media/` tree was found in the workspace.
- Untracked files: `staff/photos/*.png/jpg` and `workflow/records/*.pdf` **inside app packages**, not under `MEDIA_ROOT`. Phase 6 must treat “move out of the app directory” as **real cleanup of existing misplaced files**, not only a settings tweak.
- `MEDIA_ROOT` is already conceptually at project root; the gap is **inconsistent `upload_to`**, **files that landed in app folders**, and **no delete-on-model-delete** except messaging.

---

## 5. Sidebar, topbar, and layout

| Piece | File |
| --- | --- |
| Authenticated chrome | `templates/base.html` — `.app-shell` → backdrop + sidebar + `.app-main` (topbar + `#app-content`) |
| Public chrome | `templates/base_public.html` |
| Sidebar markup / groups | `templates/partials/sidebar.html` (`data-group` on Requests, Tasks, Tickets, …) |
| Topbar | `templates/partials/topbar.html` — hamburger, title, notifications, theme, user menu. **No search control.** |
| Shell CSS | `static/css/app-shell.css` |
| Sidebar JS | `NBTE.Sidebar.toggleMobile` in `app.js` (class `sidebar-open`). Groups persist under **`nbte-nav-groups`**. |
| Site strings in context | `config.context_processors.site_info` and `core.context_processors.site` (duplicated site dicts) |
| Badge counts | `core.context_processors.sidebar_counts` |

**Already close to Phase 4:**

- Desktop: sidebar is part of a sticky shell (not a collapse-to-rail in JS; comment in `app.js` says “no desktop collapse”).
- Groups remember open/closed in `localStorage` (`nbte-nav-groups`).
- Active group is opened in the template via `{% if ns == 'workflow' %}is-open{% endif %}` etc.
- Mobile: drawer + backdrop click to close.

**Gaps vs Phase 4:**

- No evidence that the **active item is scrolled into view** on load.
- Escape handler in `app.js` closes confirm modals, not clearly the **sidebar drawer**.
- No “click any nav link closes the mobile drawer” handler found (full page loads may hide it anyway; HTMX navigations would not).
- Inline `onclick="NBTE.Sidebar.toggleMobile()"` on hamburger and backdrop (works, but not the semantic pattern the rest of the prompt wants).

Home for authenticated users is `core.views.home` (`/home/`). “My workspace” is `dashboards:self_service` (`/dashboards/me/`). Unified queue URL is **`/workflow/awaiting/`**, template `templates/dashboards/awaiting_action.html`. Older `templates/workflow/awaiting_action.html` still exists (likely leftover).

---

## 6. HTMX vs full page loads

**Vendor:** HTMX **2.0.4**, deferred from both base templates. CSRF: `hx-headers` on `<body>` plus `initCSRFRefresh` in `app.js`. `DrainHtmxMessagesMiddleware` exists for toasts after partials.

**HTMX is already used for list filter/sort/pagination (GET, `hx-push-url`, server-rendered HTML) on:**

- Workflow: my / office / incoming / assigned / all (and leftover awaiting template)
- Tasks: list, assigned, my, review queue; some detail actions
- Tickets: list, my, assigned; some detail
- Assets: list, my_assets
- Portal: announcements, events, documents, gallery, holidays, awards, reminders; pin button; RSVP widget
- Notifications list
- Import/export wizard preview/commit

**Mostly full page loads:**

- Auth, profile, settings, dashboards (self-service, role dashboards, audit)
- Contact inbox/detail
- Leave
- Attendance (check-in pages)
- Staff directory / leadership admin
- Messaging thread (JS-enhanced, not primarily HTMX lists)
- Workflow/task/ticket **create** forms
- Public landing (contact submit is fetch/JSON from the landing script, not HTMX)

**Loading:** global `#page-loader` plus `htmx:beforeRequest` / `afterRequest`. Pagination swaps scroll the list container. **No 140ms/220ms fade** on `#app-content`. HTMX history cache exists (library default); explicit “preserve scroll on back” for the app shell is not custom-implemented.

Interactivity is generally progressive: filter forms still `method="get"` with HTMX enhancement.

---

## 7. Things I am not sure about — need you before any code

1. **Uncommitted work.** Is this entire working tree the intended baseline for Phases 1–9, or should later phases only touch a subset? I will not overwrite it.

2. **Commit.** Do you want a snapshot commit now (and which files)? I recommend excluding `.env`, `db.sqlite3`, `.DS_Store`, and binary photos/PDFs unless those are meant to be in git.

3. **Token file conflict.** Live `tokens.css` is v4 and already aliased. The nested v2 in `master_startwork.md` would be a visual reset. Keep v4 and add missing tokens, or replace?

4. **File layout vs current `MEDIA_SUBDIR`.** Vision wants `media/accounts/profiles`, `messages/attachments`, `announcements/images`, … Current code uses `staff/photos`, `messaging/attachments`, `portal/announcements`, etc. Rename on disk (needs `migrate_media_paths`) or keep namespaces and only move roots + delete-on-delete?

5. **Misplaced files.** Are `staff/photos/` and `workflow/records/*.pdf` in the app folders production-like data to migrate, or local junk that can be ignored?

6. **Global search start set.** This prompt says tasks + staff + requests first. `master_startwork.md` says tasks only first. Which one?

7. **Two “awaiting” UIs.** `/workflow/awaiting/` is already a cross-module queue. Is Phase 4/7 work on that screen, or is a separate `/dashboards/...` queue still desired?

8. **Multi-tenant / white-label.** Is the next horizon a **single-org config skin** (name, logo, terminology) or true **multi-database / hostname tenants**? That changes search, media roots, and role codes.

9. **Alpine.** Docs allow it; the codebase has none. Stay vanilla + HTMX?

10. **Rate limiting.** None found (login, reset, upload, search, manual check-in). Attendance has `manual_code_max_attempts_per_hour` in config — is that enforced in `attendance/codes.py` today? I did not fully trace it. Confirm expected limiter (cache, nginx, Django).

11. **2FA.** `Role.requires_2fa` and `User.two_factor_enabled` / `totp_secret` are scaffold only. In or out of this improvement pass?

12. **`README.md` / `REPORT.md`.** README unreadable as text here; REPORT deleted. Can you confirm the current local run command (`runserver` port, venv) for later browser verification?

13. **Chart.js CDN and Cloudinary logo.** Keep third-party URLs or vendor locally / config?

14. **Tests vs coverage claims.** Docs say `workflow/tests.py` and `tasks/tests.py` are stubs; the working tree has substantial test files plus `dashboards/test_action_queue.py`, `core/test_contact_inbox.py`, `messaging/test_thread_features.py`. I did not run the suite in Phase 0.

---

## Phase 0 report (deliverable format)

1. **Files changed:** none (application code).
2. **Files created:** `CODEBASE_REVIEW.md` (this file).
3. **Tests added:** none.
4. **What to verify manually:** open this file; skim sections 1–7; answer the questions in section 7.
5. **Screenshots:** none (no UI change).
6. **Open questions:** section 7.

**Stopped here per Phase 0.** Phase 1 (`DESIGN_PLAN.md`) should wait until you have read this review and said to proceed.
