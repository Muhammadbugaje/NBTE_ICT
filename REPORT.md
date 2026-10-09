# NBTE ICT - Report: email routing/logs/delay + departments

**Result:** 98 tests (was 62) pass on **SQLite, PostgreSQL 16 and MariaDB 10.11** (MariaDB with no timezone tables). Fresh migrate + seed verified on Postgres and MariaDB. The department migration was also run on a Postgres database that already contained departments. A real Celery worker + Redis was used to verify the delay. UI untouched (still white; your design work).

## 1. Your answers, and what I did
**1. Manual override still tries the other provider** - this was already how it worked; it is covered by tests (pinned provider down -> the other sends; works for every category).

**2. "Quota" and NAMETS - I got this wrong in the last report.** Quota = the provider's daily send limit (Brevo ~300/day, Gmail ~500/day; editable in Settings). I told you NAMETS lets critical emails bypass it. **It doesn't.** I checked the NAMETS code: limits apply to everything. What NAMETS actually does is route by *category*: critical mail (credentials, reset, task...) tries **Brevo first**; bulk/general mail tries **Gmail first** to save Brevo's allowance. I copied exactly that. Our critical list is `credentials, reset, task, approval, test` and is editable in Settings (`email.critical_categories`). If a provider's limit is used up, mail goes to the other one regardless of category.

**Email logs.** Every email now records: recipient(s), the linked user (when it's one recipient matching an account), subject, category, when it was queued/planned/sent, final status (queued/sent/retrying/failed), the provider that delivered it, and a per-attempt trail showing each provider tried and why it failed (error text, or "daily limit reached - skipped"). The message text is stored too, **except** credentials and reset emails (they contain passwords/links, so the log says "not stored"). Where to look: Admin > Email logs (search by email address, subject, or the user's name; filter by status/provider/category/date), and a read-only **email history on each user's page**. Difference from NAMETS: NAMETS writes one log row per recipient; we write one row per email (a multi-recipient email is one row with all addresses).

**Delay.** Every email waits `email.initial_delay_seconds` (default 10) and consecutive emails are kept at least `email.min_gap_seconds` (default 5) apart, so 100 new accounts trickle out over ~8 minutes instead of firing at once. Both editable in Settings. Verified with a real worker: 4 emails queued in 0.12s, then sent exactly 2.0s apart with the gap set to 2s (this caught and fixed a rounding bug that had shortened one gap to 1s).

**Departments.**
- `Department` (name + stable `code` such as `ict`, active flag, order). Users now have a **department** field (admin add/edit user, list filter). Staff records already had one; the two are kept in step (see judgment calls).
- **Permissions for one department only:** `user.can_access_department(dept)`, `user.has_perm_for_department(perm, dept)` (needs the permission AND being in that department), and `scope_to_department(queryset, user)` for lists. New permission **`access_all_departments`** lifts the limit (given by default to Director, Deputy Director, Office Head General Admin, Super Admin). Fails closed: a user with no department and no such right sees nothing.
- **Different behaviour for ICT vs others:** add a Settings row whose scope is `department:ict`; read with `config.get_for_user(key, user, default)`. Everyone else automatically gets the global value. Tested: ICT user gets 2 approval levels, others get 4.
- Already applied: the **Staff list in admin shows only your own department's staff** unless you hold `access_all_departments`.
- Commands: `seed_departments` (creates ICT), `seed_roles --sync` (adds missing baseline permissions to roles that already exist; never removes any).

## 2. Files changed
```
NEW  accounts/scoping.py  accounts/test_departments.py  staff/management/commands/seed_departments.py
NEW  notifications/test_routing_logs_delay.py  notifications/migrations/0002  accounts/migrations/0002  staff/migrations/0003
MOD  staff/models.py (Department code/scope, department sync, admin scoping)  staff/admin.py
MOD  accounts/models.py (User.department, access methods, new permission)  accounts/admin.py  forms.py  services.py  seed_roles.py
MOD  notifications/ models.py (log fields, EmailSendSlot)  engine.py (category routing, trail)  api.py (delay)  tasks.py  admin.py
MOD  config/__init__.py (get_for_user/get_for_department)  config/defaults.py  nbte_ict/settings/base.py  README.md
```

## 3. Commands
```bash
python manage.py migrate
python manage.py seed_config          # adds the 3 new email settings
python manage.py seed_departments
python manage.py seed_roles --sync    # existing databases: gives roles the new permission
python -m pytest                      # 98 tests; add DATABASE_URL=... to run on Postgres/MySQL
```

## 4. Tested vs unverified
**Tested (and mutation-checked: I broke department scoping, category routing, access check, spacing and "don't store secrets"; tests failed each time):** category routing + quotas applying to critical mail + editable critical list; override pinning + failover; per-attempt trail; log contents, user link, body stored/not stored, admin search/filter/detail/user history; delay spacing, config-driven, reset after quiet period, passed to Celery; real-worker spacing; department code generation/uniqueness/stability; access rules; scoped queryset; staff admin scoping (including guessing another department's URL); department config override; Staff<->User sync; role seeding and `--sync`; admin add-user POST with department (credentials email actually sent) - this closes the "admin add-user form untested" gap from before; migration backfill on real data.

**Unverified / limits - be skeptical:**
- **Spam:** the delay only addresses burst behaviour. I cannot measure inbox-vs-spam from here, and the biggest spam factors are outside the code: SPF/DKIM/DMARC on your sending domain, a verified Brevo sender, and a normal "From" address. Set those up or the delay won't save you. Real Brevo/Gmail were still never contacted.
- The delay applies to **all** email including password reset (10s default). A burst of 1,000 emails would spread over ~83 minutes (no cap). The "send test email" button is immediate (no delay).
- Real-broker run covered the success path only; retry/backoff timing is still untested on a real broker.
- Department scoping is a **mechanism**, applied so far only to the Staff admin. Every future module (tickets, assets...) must call `scope_to_department` / `has_perm_for_department` itself - it is not automatic. Plain `user.has_perm(...)` is still global.
- Dept-limited users also can't pick a `reports_to` outside their department (the picker is scoped too).
- Email log search is by text; no dedicated non-admin page (admin only).
- Not done (unchanged): real Excel import/export, modal/loading/HTMX/dark mode UI, 2FA enforcement.

## 5. Judgment calls - please confirm
1. **Existing users have no department after migrating.** Until you assign one they see nothing in department-scoped lists (superusers and `access_all_departments` holders are unaffected). Assign departments in Admin > Users.
2. **Staff and User department sync rule:** a non-blank value is copied to the partner; a blank never wipes the partner (it's filled in instead). So to *clear* a department you must clear it on both records.
3. `Department` lives in the `staff` app (Offices - Technical Support / Dev / General Admin - remain a separate list inside ICT). Deleting a department that has users is blocked (deactivate it instead).
4. Only **ICT** is seeded; add the other NBTE departments in Admin.
5. Default role baselines for `access_all_departments` (Director, Deputy, Office Head General Admin, Super Admin) are a guess - review. Office Heads of Technical Support/Dev see only ICT.
6. Credentials and reset emails: body never stored; a multi-recipient email gets one log row and no user link.
7. "task" and "approval" categories are in the critical (Brevo-first) list by analogy with NAMETS; no module sends them yet.
