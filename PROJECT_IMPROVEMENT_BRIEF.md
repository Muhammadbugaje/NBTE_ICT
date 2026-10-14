# Project Improvement Brief

**Prepared:** 12 October 2026  
**Purpose:** A practical reference for the next improvement pass. This is a
review and implementation plan only; it does not authorize or describe a
redesign already made.

## Executive overview

NBTE ICT is a broad Django operations system, not a small brochure site. It
already has a shared application shell, a semantic light/dark token palette,
responsive navigation, and substantial workflows for staff, approvals, tasks,
assets, attendance, tickets, portal content, and messaging. The best next step
is to make these existing features feel like one dependable, polished product:

1. Establish shared rules for spacing, responsive breakpoints, color, and
   interactive components.
2. Apply those rules to the busiest list, detail, and form screens, with mobile
   layouts treated as first-class layouts rather than compressed desktop pages.
3. Make confirmation, navigation, filtering, feedback, and loading behavior
   consistent and accessible.
4. Add regression tests around the highest-risk workflows before extending
   them.

The goal is **clarity and confidence**, not decoration: users should quickly
understand where they are, what needs their attention, what an action will do,
and how to recover if something goes wrong.

## Review scope and important context

This review covered the current working tree across the Django app and its
templates, styles, scripts, routes, tests, settings, and project documentation.
The repository has a broad feature surface: `templates/` contains 197 tracked
files, with separate apps for accounts, staff, workflow, tasks, assets,
attendance, tickets, notifications, portal content, and messaging.

**This is a working-tree review, not a clean-branch audit.** Many application,
template, and static files already have uncommitted changes. In particular,
the messaging UI, list filters, chat confirmations, and task-tab positioning
have recently been edited in this working copy. Start the next session by
reviewing `git status` and the current diff; do not overwrite or assume these
changes are already committed or fully released.

## What is already working well

- **Shared visual foundation:** [`static/css/tokens.css`](./static/css/tokens.css)
  defines common light and dark semantic colors. Build on this instead of
  introducing a second design system.
- **Reusable application shell:** [`templates/base.html`](./templates/base.html)
  and [`static/css/app-shell.css`](./static/css/app-shell.css) provide the
  authenticated app layout, sticky top bar, and responsive sidebar.
- **Purposeful small-screen patterns:** The internal sidebar drawer and public
  landing navigation already have mobile-specific behavior.
- **A broad, useful product:** Existing routes cover meaningful day-to-day
  work. Improve discoverability, reliability, and flow rather than replacing
  mature features wholesale.
- **Some strong reliability patterns:** Account department tests and
  notification tests already cover permission/scoping behavior and delivery
  handling in useful depth.

## Highest-value opportunities

### 1. Make the interface feel consistent at every width

**Why:** The internal shell, public pages, and individual modules use different
breakpoints and often different page-level spacing. A screen can therefore
feel carefully composed at one width but cramped, over-wrapped, or unusually
sparse at another. The shell uses breakpoints around 900/700px, while public
and feature pages add their own thresholds.

**How:**

- Agree on a small set of shared layout breakpoints (for example, wide/tablet/
  phone) and document their purpose in one place. Adapt existing pages
  progressively; do not mechanically replace breakpoints that are solving
  different layout constraints.
- Define shared spacing and content-width conventions for page title,
  description, action group, filter area, content section, and mobile gutters.
- Check the main flow for each high-use area: list → detail → edit/create →
  success/error feedback.
- Let desktop actions sit beside their page heading when space allows. At phone
  widths, stack or compact them intentionally, preserve tap targets, and avoid
  forcing multi-column forms.
- Keep horizontally navigable controls, such as tabs, visibly scrollable and
  keyboard accessible; do not let important actions disappear offscreen.

**Evidence to start from:** [`static/css/app-shell.css`](./static/css/app-shell.css),
[`static/css/landing.css`](./static/css/landing.css), and the page-specific
styles in `static/css/`.

### 2. Make light and dark themes use the same design language

**Why:** A shared semantic palette exists, but several modules add separate
hard-coded dark colors. This makes contrast and surface hierarchy drift, and
increases the work needed for each future component.

**How:**

- Treat the variables in [`static/css/tokens.css`](./static/css/tokens.css) as
  the source of truth for page, card, border, text, muted text, accent, focus,
  and danger surfaces.
- Move repeated dark-mode overrides toward semantic variables; keep a local
  override only where a component genuinely needs a different state color.
- Check contrast and legibility for muted labels, disabled controls, badges,
  borders, focus rings, error/success messages, and overlays in both themes.
- Consolidate the duplicate theme-toggle path: `NBTE.Theme` in
  [`static/js/app.js`](./static/js/app.js) is bootstrapped by the app, while
  [`static/js/theme.js`](./static/js/theme.js) contains another implementation
  and is not included by the base templates. Confirm it has no external use
  before removing or retiring it.

### 3. Improve high-traffic screens before lower-traffic polish

**Why:** Attractive components are not enough if users cannot find a record,
understand its state, or complete the next action without excess scrolling.
Prioritize screens used to triage or update work.

**Recommended order:**

1. **Tasks:** Make task views, filters, list/kanban switching, and primary
   actions easy to find at phone widths. Keep important navigation close to
   the top without consuming an excessive portion of the viewport.
2. **Workflow requests:** Clarify current approval status, next owner/action,
   history, and available actions on detail pages. Keep rejection, return, and
   approval actions distinct and explain their consequences.
3. **Messaging:** Validate the recent inbox/archive search and unread filters,
   thread header, compact composer, attachment display, and confirmation dialog
   at real phone widths before adding more controls.
4. **Tickets and assets:** Make search, status/ownership filters, scanning or
   assignment actions, and empty states behave consistently with tasks.
5. **Attendance and portal content:** Ensure report/export controls and
   date/category filters remain understandable on small screens.

For each page, use the same visual hierarchy: page identity and short context,
primary action, useful filters, results with clear state and metadata, then
secondary actions. Avoid overly large hero cards or toolbars that push the
actual records below the fold.

### 4. Standardize interactive behavior and user feedback

**Why:** Confirmation and navigation behavior is currently implemented through
more than one pattern (shared modal helpers, page-specific dialog code, and
browser prompts). Inline event handlers also make some controls harder to test
and keep consistent after partial page updates.

**How:**

- Create or standardize one reusable confirmation-dialog pattern with a clear
  title, consequence text, safe cancel action, explicit destructive action,
  keyboard dismissal, focus return, and dark-theme styling.
- Audit destructive actions across the app: delete, remove, archive where
  irreversible, bulk operations, and cancellation. A confirmation should be
  reserved for consequential actions, not routine navigation.
- Standardize success, error, loading, and empty states. Every async action
  should prevent accidental double submission, communicate progress, and
  expose failure rather than appearing to succeed silently.
- Prefer semantic buttons/links and delegated event listeners over inline
  `onclick` code. Keep behavior resilient after HTMX swaps and test keyboard
  and touch interaction.
- Add Escape-to-close and appropriate focus handling to the mobile sidebar and
  menus; ensure open overlays close on navigation or a switch back to desktop.

**Evidence to start from:** [`static/js/app.js`](./static/js/app.js) contains
global modal/theme/navigation helpers; `static/js/profile.js` and
`static/js/messaging.js` demonstrate additional local patterns.

**Specific messaging follow-up:** The recent custom confirmation dialog has
been reported to render toward the left edge and look oversized on mobile.
Treat the current dialog positioning as a defect to reproduce and fix, not as
a finished reusable component. Verify centering in the real page with and
without native `<dialog>` support, constrain its width to the viewport, account
for safe-area padding, and make the mobile action buttons readable and
comfortable to tap. Recheck light/dark themes, keyboard focus, Escape, backdrop
dismissal, and submit/cancel behavior after positioning changes.

### 5. Make filters and list pages useful, predictable, and scalable

**Why:** Lists are where users spend time finding the right work item.
Inconsistent filters, lost query state, unexplained empty results, or
client-only filtering on large datasets can make a complete feature feel
unreliable.

**How:**

- Give filters plain-language labels and sensible defaults. Include reset/clear
  behavior and a visible active-filter state where multiple filters combine.
- Preserve filters and search in the URL so reload, browser back, and sharing a
  filtered view behave predictably.
- Prefer server-side filtering for growing or paginated datasets. Use HTMX or
  another progressive enhancement for fast updates while retaining a usable
  non-JavaScript form submission path.
- Distinguish “there are no records” from “no records match these filters.”
- Show a result count and useful sort order; keep filter controls compact and
  rearrange them rather than shrinking controls below a usable size on phones.
- In the next session, confirm the current working-tree message filters are
  initialized correctly, search across the intended row content, update their
  counts, and retain a sensible empty state.

### 6. Add permission-aware global search

**Why:** Staff need a quick way to find records across the system instead of
remembering which module owns each item. A persistent search entry in the top
bar would make tasks, requests, tickets, assets, staff, and other permitted
records easier to discover.

**How:**

- Add one shared search control to the internal top bar. On desktop, show a
  compact labeled search field. On phones, show a search icon; tapping it
  should expand a prominent, usable search field without permanently taking
  top-bar space.
- Use a debounced, server-backed search endpoint with results grouped by
  record type and links to the matching detail pages. Show a clear no-results
  state and allow keyboard navigation/selection.
- Search only fields appropriate to each record type. Apply the same
  authentication, department scoping, and per-object permissions as the
  destination pages. Never treat client-side hiding as access control; do not
  expose private notes, protected attachment content, or other sensitive
  fields in previews.
- Make the control work with keyboard, touch, screen readers, dark theme, and
  narrow viewports. Close it predictably on Escape, outside click, selection,
  or navigation, and preserve focus.
- Start with a small set of high-value objects (for example staff, tasks,
  workflow requests, and tickets); add other modules once result quality and
  permission behavior are tested. Do not scan/render every page's DOM as a
  substitute for a permission-filtered server search.

### 7. Create a public contact-message handling workspace

**Why:** The public homepage accepts messages from anyone, including
unauthenticated external senders. They are a different workflow from
staff-to-staff `/messages/` conversations and need an explicit staff owner.
Today, submissions are stored as `core.ContactMessage` and are available
through Django admin; a notification email is attempted at
`CONTACT_NOTIFY_EMAIL`, falling back to `DEFAULT_FROM_EMAIL` if the contact
address is not configured. This does not provide a dedicated page for
designated staff to handle them.

**How:**

- Keep the public submission separate from internal messaging. Do not
  automatically create a staff account or internal chat for an external
  sender.
- Build a protected staff inbox and detail page for contact messages, with
  new/unread, assigned-to-me, handled, and unassigned filters; search by
  sender/subject; and sort by submission time.
- Add an explicit permission for viewing and handling public contact messages.
  Let an authorized administrator grant it to selected staff/roles. Decide
  whether assignments can cross departments and encode that rule server-side.
- Support assignment, mark-read, mark-handled/reopen, internal notes, and a
  safe way to record or send a response. Preserve a history of who handled the
  request and when; never expose internal notes to the public sender.
- Ensure notification routing targets a monitored support mailbox and fails
  visibly/operationally if misconfigured. Database persistence must remain the
  source of truth when email delivery fails.
- Add tests for external submission and validation, spam controls, permission
  denial, assignment and status transitions, note privacy, and notification
  failure.

**Evidence to start from:** [`templates/public/landing.html`](./templates/public/landing.html),
[`core/forms.py`](./core/forms.py), [`core/models.py`](./core/models.py),
[`core/views.py`](./core/views.py), and [`core/admin.py`](./core/admin.py).
[`structure.md`](./structure.md) explains the current
public-contact vs. internal-messaging split.

### 8. Add regression coverage in proportion to feature complexity

**Why:** The system has many routes and meaningful state transitions, but test
coverage is uneven. The review found `workflow/tests.py` is only a small URL
check and `tasks/tests.py` is a stub, while the workflow/task features include
approval paths, assignments, comments, attachments, dependencies, and state
transitions.

**How:**

- Add behavior tests for the most consequential workflow/task paths: access
  control, valid and invalid transitions, assignment and department scoping,
  approval/rejection/return, attachment permissions, and empty/error states.
- Add focused view/template tests for common list filters and detail actions in
  assets, attendance, tickets, portal, and messaging.
- Use tests to verify that unauthorized users cannot act on or discover records
  outside their permitted scope.
- Keep tests narrow and deterministic. Use fakes/mocks for email providers and
  brokers in normal CI; reserve real-provider/broker checks for staging or an
  explicitly controlled integration test.
- Add a deployment checklist for migration and seed order, provider/broker
  configuration, and department setup.

**Evidence to start from:** [`REPORT.md`](./REPORT.md) already records
unverified production mail/broker behavior and the need for module-level
department-scope adoption. `accounts/test_departments.py`,
`notifications/tests.py`, and `notifications/test_routing_logs_delay.py` are
useful patterns to follow.

### 9. Make recovery paths as clear as the security rules

**Why:** Department scoping intentionally fails closed when a user has no
department unless they have broader access. Forced password change is also a
strong gate. Both are appropriate controls, but a user who hits a setup or
recovery issue can experience a blank or blocked app rather than a clear next
step.

**How:**

- Keep authorization enforcement server-side. Improve the empty/blocked states
  with a short explanation and the correct support/admin route.
- Test password reset and forced-change flows end-to-end, including expired
  links and users who cannot proceed.
- Review new views against the department-scope checklist before release.
- Give support staff a searchable, permission-appropriate way to inspect
  delivery and routing problems if admin-only logs are not sufficient for
  operational support.

**Evidence to start from:** [`accounts/scoping.py`](./accounts/scoping.py),
[`accounts/middleware.py`](./accounts/middleware.py), and
[`REPORT.md`](./REPORT.md).

## Suggested implementation plan for the next work session

### Step 0 — Establish a safe baseline

- Inspect `git status --short` and the current diff. Separate prior local work
  from new changes; do not reset or overwrite it.
- Confirm the intended Python environment and run the current lightweight
  Django checks and focused test suites.
- Capture baseline screenshots of representative task, workflow, message,
  ticket, and asset screens in light and dark themes.

### Step 1 — Agree on shared UI rules

- Record the shared breakpoints, page gutters, content widths, spacing scale,
  focus style, and semantic colors.
- Decide which component behaviors are global (dialog, dropdown, drawer,
  loading state) and which are page-specific.
- Resolve the duplicate theme implementation after checking for uses outside
  the standard templates.

### Step 2 — Improve one representative flow at a time

- Begin with task list/detail because it combines navigation, filters, dense
  records, and actions.
- Apply the conventions to workflow list/detail next, then messaging and
  tickets/assets.
- Validate each change at 1440px desktop, 1024px tablet, 768px narrow tablet,
  390px phone, and 320px narrow phone. Check both light and dark theme.
- Include keyboard-only navigation, visible focus, touch targets, long names,
  empty lists, long text, and validation errors in each pass.

### Step 3 — Strengthen behavior and tests

- Replace inconsistent confirmations and inline interactions with the shared
  patterns.
- Add targeted feature tests before changing high-risk state transitions.
- Verify HTMX partial updates, URL state, browser back/forward, and no-JS
  submission for forms that need to remain functional without JavaScript.
- Compare screenshots and run the focused Django checks/tests again.

## Completion checklist

- [ ] Page headings, descriptions, actions, filters, lists, and forms have
  consistent hierarchy and spacing.
- [ ] No page requires horizontal scrolling except an intentionally
  horizontally navigable control or data table.
- [ ] Critical actions remain visible and usable at narrow phone widths.
- [ ] Light and dark themes retain readable contrast for text, borders, focus,
  validation, badges, dialogs, and overlays.
- [ ] Search and filters have clear labels, reset behavior, accurate result
  counts, and correct empty states.
- [ ] Dialogs are keyboard accessible, return focus, close safely, and explain
  destructive consequences.
- [ ] Loading, success, and failure states are explicit; repeated submissions
  are guarded.
- [ ] Authorization and department-scope tests cover new and changed views.
- [ ] Focused tests and Django checks pass; broader suites are run where the
  change touches shared behavior.
- [ ] No unrelated existing worktree changes were overwritten.

## Guardrails

- Prefer surgical improvements to working features over a new frontend
  framework or full rewrite.
- Keep real permission checks in Django; client-side filtering is presentation,
  never access control.
- Prefer shared semantic tokens and components over another layer of
  page-specific CSS.
- Make animation purposeful, brief, and optional under reduced-motion
  preferences. A “good feeling” should come from clarity, responsive feedback,
  and visual consistency—not motion added for its own sake.
- Do not treat the tracked landing-page attachment as the whole project; it is
  one file in a much broader application.
