# Three working documents

Below are the three files, written to be dropped into the repo root and used as the standing reference. They're consistent with each other, with `structure.md`, with `PROJECT_IMPROVEMENT_BRIEF.md`, and with the master plan. I've been careful to only reference files and features that actually exist or are explicitly specced.

---

## 1. `overview.md`

```markdown
# NBTE ICT — Project Overview

**Last updated:** 12 October 2026
**Audience:** Any engineer, designer, or AI assistant joining the project.
**Companion docs:** `structure.md` (how the code is organised),
`design.md` (how it should look and feel), `prompt.md` (how to work on it),
`PROJECT_IMPROVEMENT_BRIEF.md` (the review that produced this plan),
`ICT Directorate Total Master Plan (Consolidated Edition).docx` (the product spec).

---

## 1. What this project is

The **NBTE ICT Directorate Internal Operating System** — a Django application
that runs the day-to-day operations of the ICT Directorate of the National
Board for Technical Education (NBTE), plus a public-facing site.

The system has three layers:

- **Core Platform** — authentication, RBAC, staff directory, config registry,
  email delivery engine.
- **Primary Operations** — ticketing/helpdesk, asset custody ledger,
  complaints/requests routing, hierarchical approval engine, tasks,
  attendance.
- **Workplace Portal** — announcements, events, gallery, awards, documents
  library, staff profiles, internal messaging, holidays/birthdays/absence.

The public site (landing page, contact form) is a small but visible part of
the same Django project.

The full product specification is in the master plan. This document is about
**what exists today, what state it's in, and what to improve next.**

---

## 2. Current state — an honest assessment

### 2.1 What exists and works

- **Django 6.x, Python 3.15, PostgreSQL/SQLite.** Settings split into
  `base.py`, `dev.py`, `prod.py`. Celery + Redis for background work.
- **14 feature apps:** `accounts`, `staff`, `workflow`, `tasks`, `assets`,
  `tickets`, `attendance`, `portal`, `messaging`, `notifications`, `config`,
  `dashboards`, `core`, `public`.
- **A shared internal shell** (`templates/base.html` + `static/css/app-shell.css`)
  with top bar, sidebar, mobile drawer, theme toggle.
- **A shared public shell** (`templates/base_public.html` + landing).
- **A semantic token palette** in `static/css/tokens.css` covering light and
  dark themes.
- **Working domain logic** for department scoping (`accounts/scoping.py`),
  approval workflows, task state transitions, asset custody, portal content,
  and internal messaging.
- **Some real tests** — `accounts/test_departments.py`,
  `notifications/tests.py`, `notifications/test_routing_logs_delay.py`,
  `messaging/test_thread_features.py`.

### 2.2 What is partial or thin

- **Test coverage is uneven.** `workflow/tests.py` is a URL smoke test;
  `tasks/tests.py` is a stub. The state machines in workflow and tasks are
  the highest-risk parts of the system and are the least tested.
- **The design language is applied inconsistently.** `app-shell.css` uses
  breakpoints around 900px and 700px; feature stylesheets add their own.
  Dark-mode overrides are re-implemented per module instead of via tokens.
- **Confirmation dialogs are implemented three different ways** — shared
  modal helpers in `static/js/app.js`, page-local dialog code, and
  `window.confirm`. The recent messaging dialog has a reported mobile
  positioning defect.
- **The two dashboard screens specified in the master plan (§9.2 Self-Service
  Dashboard, §9.4 "Awaiting My Action" queue) are not yet built.** These are
  the highest-leverage screens for daily use.
- **No global search.** Users navigate to the module that owns a record to
  find it.
- **The public contact inbox has no staff-facing workspace.** Submissions
  are stored as `core.ContactMessage` and only available in Django admin.
- **`static/js/theme.js` is orphaned** — `NBTE.Theme` in `app.js` is the
  live implementation. `theme.js` is not loaded by any template.

### 2.3 What is currently uncommitted / in-flight

Per `structure.md` §11 and the improvement brief: recent work on messaging
UI, list filters, chat confirmations, and task-tab positioning is present in
the working tree but not confirmed committed. **Before starting any new
work, run `git status --short` and review the current diff.** Do not reset
or overwrite.

### 2.4 Known defects

- Mobile confirmation dialog in messaging renders off-centre / oversized.
- Public landing page images did not render when the Celery thumbnail task
  had not yet run (fix in progress; guard on the raw `image` field, fall
  back to `.url`).
- `INSTALLED_APPS` typo risk — `"dashboards"` and `"public"` on adjacent
  lines without a comma will concatenate into `"dashboardspublic"`.

---

## 3. What to improve — prioritised

The order below is deliberate. Do not skip ahead.

### P0 — Foundations (do first, do once)

1. **Commit the design token system.** Replace `static/css/tokens.css` with
   the version in `design.md`. Freeze it. All new CSS uses tokens only.
2. **Resolve the duplicate theme implementation.** Confirm `theme.js` has
   no external use, delete it.
3. **Establish the five shared components** (page header, empty state,
   dialog, list row, button system) in `static/css/components.css` and
   `templates/partials/`. See `design.md` §4 for the actual code.
4. **Replace all `window.confirm` and inline `onclick="return confirm(...)"`**
   with the shared `NBTEConfirm` helper.
5. **Write `DESIGN.md`-equivalent rules into `design.md`** and reference it
   in every future PR description.

### P1 — The two screens that matter most

6. **Build §9.2 Staff Self-Service Dashboard.** One screen per staff member:
   own tickets, tasks, leave balance, assets, profile, pending approvals.
7. **Build §9.4 "Awaiting My Action" queue.** One inbox merging complaints,
   requests awaiting approval, overdue asset returns, and escalated tickets.

These two are what makes the system feel like *a system* rather than a
collection of forms.

### P2 — Mobile-first redesign of the top five flows

8. **Tasks** — list, detail, kanban/list toggle, filters, sticky mobile
   action bar.
9. **Workflow** — request list, detail with clear current-step/next-owner
   display, distinct approve/return/reject actions.
10. **Messaging** — verify filters, thread header, composer, attachment
    display at 320–390px.
11. **Tickets** — search, status filters, assignment, empty states.
12. **Assets** — register list, custody ledger, scan-to-checkout flow.

For each: design the mobile layout, don't just test the desktop layout
at 390px.

### P3 — Permission-aware global search

13. **One search endpoint, one UI.** Start with **tasks only**, prove the
    pattern (debounced, keyboard-navigable, permission-filtered, empty
    state, dark theme), then replicate to staff, workflow, tickets.

### P4 — Public contact inbox

14. **Protected staff workspace** for `core.ContactMessage` — new/unread,
    assigned-to-me, handled filters; assignment; internal notes; response
    history. Kept strictly separate from internal messaging.

### P5 — Tests + recovery paths

15. **Workflow tests:** access control, valid/invalid transitions,
    self-approval block (FI-25), rejection/return, attachment permissions,
    department scoping on detail views.
16. **Task tests:** same categories, plus multi-assignee progress,
    dependency rules, bulk operations.
17. **Recovery UX:** clearer "no department assigned" and forced-password-
    change screens with a support route.

---

## 4. Success criteria

The improvement pass is done when:

- [ ] Every screen uses only tokens from `tokens.css`. No hardcoded colors,
      spacing, radii, shadows, or animation durations.
- [ ] Every modal, drawer, and confirmation uses the shared dialog helper.
- [ ] No page requires horizontal scrolling except an intentionally
      scrollable control or data table.
- [ ] Every screen has a designed mobile layout (stacked, bottom sheets,
      sticky actions) tested at 320px.
- [ ] Light and dark themes have consistent contrast and hierarchy.
- [ ] Every async action shows loading, success, and failure states.
- [ ] Every list has distinct empty-states for "nothing exists" vs.
      "nothing matches these filters."
- [ ] Every destructive action requires confirmation with explicit
      consequence text.
- [ ] Workflow and task views have behaviour tests for access control and
      state transitions.
- [ ] The Self-Service Dashboard and Awaiting-My-Action queue are live and
      used daily.

---

## 5. Guardrails — what NOT to do

- **Do not adopt a new frontend framework.** No React, Vue, Svelte, Next.js.
  HTMX + Alpine + Django templates are sufficient and already integrated.
- **Do not rewrite working features.** Refactor surgically.
- **Do not enforce authorisation in JavaScript.** Client-side hiding is
  presentation, never access control.
- **Do not add per-module dark-mode overrides.** If a component needs a
  different color in dark mode, it means a token is missing — add the token.
- **Do not build animation for its own sake.** Motion is for orientation
  (where am I going, where did this come from) and feedback (did my action
  land). Nothing else.
- **Do not overwrite uncommitted work in the tree.** Always start with
  `git status --short`.
- **Do not treat the landing page as the project.** It is one template in
  a ~200-template application.

---

## 6. Reference: master plan sections to consult

| Topic | Master plan section |
| --- | --- |
| Organisational roles & hierarchy | §3 |
| Platform overview | §4 |
| Auth & RBAC | §5.1 |
| Staff directory | §5.2 |
| Config registry | §5.3 |
| Email delivery engine | §5.4 |
| Ticketing | §6.1 |
| Asset custody | §6.2, §18 (full spec) |
| Complaints routing | §6.3 |
| Hierarchical approval engine | §6.4, §19.7 |
| Workplace portal | §7, §19 (full spec) |
| Tasks & attendance | §8 |
| Self-Service Dashboard | §9.2 |
| Approval delegation on leave | §9.3 |
| Awaiting-My-Action queue | §9.4 |
| 2FA for sensitive roles | §9.5 |
| Combined analytics dashboard | §9.8 |
| Onboarding checklist | §9.11 |
| Further improvements (FI-01–30) | §10 (page 11–12) |
| Business rules (NR-series) | §11 |
| Non-functional requirements | §13 |
| Phased roadmap | §14 |
| Open questions | §15 |

---

## 7. Quick start for a new session

```bash
# 1. Confirm what's in flight
git status --short
git diff --stat

# 2. Confirm the environment
python3 --version                    # expect 3.15
python3 manage.py check              # expect "System check identified no issues"

# 3. Read these in order
#    - overview.md (this file)
#    - design.md
#    - prompt.md
#    - structure.md

# 4. Pick a P0 task from §3 above and follow the matching prompt in prompt.md
```

---

## 8. Ownership

| Area | Owner |
| --- | --- |
| Product decisions, scope, priorities | Director of ICT |
| Architecture, data model | Lead developer |
| Design system, tokens, components | Design owner (to be named) |
| Test coverage, CI | Lead developer |
| Content, announcements, portal | Office Head, General Admin |
```

---

## 2. `design.md`

```markdown
# NBTE ICT — Design System

**Last updated:** 12 October 2026
**Companion docs:** `overview.md`, `prompt.md`, `structure.md`.

This document is the single source of truth for how the NBTE ICT interface
looks, moves, and behaves. If a screen deviates from this document, either
the screen is wrong or this document needs a formal amendment. There is no
third option.

The design language is **Apple-HIG-inspired**: large clear titles, generous
whitespace, subtle elevation, restrained accent colour, purposeful motion,
and a mobile experience that is *designed*, not merely reflowed.

---

## 1. Design principles

1. **Clarity before decoration.** Every screen answers: where am I, what
   needs my attention, what happens if I press this, how do I undo?
2. **One accent colour.** NBTE green (`--accent`) is the only chromatic
   accent for primary actions. Everything else is neutral or semantic
   (info / success / warning / danger).
3. **Whitespace is structure.** Density is not an achievement. Section
   spacing is deliberate, and it is on the spacing scale, never arbitrary.
4. **Elevation is subtle.** Two-layer shadows, low opacity, small offsets.
   Borders are 1px and low-contrast. Nothing shouts.
5. **Motion explains.** Animation exists to orient the user, not to
   entertain. Durations are short, easings are natural, and reduced-motion
   is respected automatically.
6. **Mobile is a first-class layout.** Below 640px, screens are
   *re-thought* — bottom sheets, sticky action bars, single-column,
   44px touch targets — not the desktop layout squeezed.
7. **Semantic tokens only.** No component ever hardcodes a color, radius,
   shadow, or duration. If a value is needed that does not exist as a token,
   the token is added here first.
8. **Server owns truth.** Authorisation, scoping, and validation are
   enforced by Django. The client only reflects what the server allowed.

---

## 2. Token system — `static/css/tokens.css`

**This file replaces the current `tokens.css` entirely.** Commit it once and
freeze it. Every subsequent change to it is a versioned amendment, not a
casual edit.

```css
/* ============================================================
   NBTE ICT — Design Tokens
   v2.0 — 12 October 2026
   Source of truth. No component may hardcode these values.
   ============================================================ */

:root {
  /* ---------- TYPOGRAPHY ---------- */
  --font-sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI",
               system-ui, sans-serif;
  --font-mono: "SF Mono", "JetBrains Mono", ui-monospace, monospace;

  /* Fluid type — clamp() so no breakpoint fights are needed */
  --text-micro:   0.75rem;                                    /* 12 */
  --text-xs:      0.8125rem;                                  /* 13 */
  --text-sm:      0.875rem;                                   /* 14 */
  --text-base:    0.9375rem;                                  /* 15 — body */
  --text-lg:      1.0625rem;                                  /* 17 */
  --text-xl:      clamp(1.25rem,  0.9rem + 1vw,   1.5rem);    /* h3 */
  --text-2xl:     clamp(1.5rem,   1rem + 1.5vw,   1.875rem);  /* h2 */
  --text-3xl:     clamp(1.875rem, 1.2rem + 2vw,   2.5rem);    /* h1 */
  --text-display: clamp(2.25rem,  1.4rem + 3vw,   3.5rem);    /* hero */

  --leading-tight:   1.15;
  --leading-snug:    1.3;
  --leading-normal:  1.5;
  --leading-relaxed: 1.65;

  --tracking-tight:  -0.02em;
  --tracking-normal: 0;
  --tracking-wide:   0.02em;
  --tracking-wider:  0.06em;

  /* ---------- SPACING — 4pt grid ---------- */
  --space-0:  0;
  --space-1:  0.25rem;   /*  4 */
  --space-2:  0.5rem;    /*  8 */
  --space-3:  0.75rem;   /* 12 */
  --space-4:  1rem;      /* 16 */
  --space-5:  1.25rem;   /* 20 */
  --space-6:  1.5rem;    /* 24 */
  --space-8:  2rem;      /* 32 */
  --space-10: 2.5rem;    /* 40 */
  --space-12: 3rem;      /* 48 */
  --space-16: 4rem;      /* 64 */
  --space-20: 5rem;      /* 80 */
  --space-24: 6rem;      /* 96 */

  /* ---------- RADII ---------- */
  --radius-xs:   6px;
  --radius-sm:   8px;
  --radius-md:   12px;
  --radius-lg:   16px;
  --radius-xl:   22px;
  --radius-2xl:  28px;
  --radius-pill: 999px;

  /* ---------- ELEVATION ---------- */
  --shadow-xs: 0 1px 2px rgb(15 23 42 / 0.04);
  --shadow-sm: 0 1px 3px rgb(15 23 42 / 0.06),
               0 1px 2px rgb(15 23 42 / 0.04);
  --shadow-md: 0 4px 12px rgb(15 23 42 / 0.07),
               0 2px 4px  rgb(15 23 42 / 0.04);
  --shadow-lg: 0 12px 32px rgb(15 23 42 / 0.09),
               0 4px 8px   rgb(15 23 42 / 0.04);
  --shadow-xl: 0 24px 56px rgb(15 23 42 / 0.12),
               0 8px 16px  rgb(15 23 42 / 0.05);

  /* ---------- MOTION ---------- */
  --ease-out:    cubic-bezier(0.22, 1, 0.36, 1);
  --ease-spring: cubic-bezier(0.32, 0.72, 0, 1);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);

  --duration-fast:   140ms;
  --duration-base:   220ms;
  --duration-slow:   360ms;
  --duration-slower: 560ms;

  /* ---------- COLOR — LIGHT (semantic names only) ---------- */
  --bg:            #FFFFFF;
  --bg-subtle:     #F8FAFC;
  --bg-sunken:     #F1F5F9;
  --bg-elevated:   #FFFFFF;
  --bg-overlay:    rgb(15 23 42 / 0.42);

  --fg:            #0B1220;
  --fg-muted:      #64748B;
  --fg-subtle:     #94A3B8;
  --fg-on-accent:  #FFFFFF;
  --fg-inverse:    #F8FAFC;

  --border:        #E2E8F0;
  --border-strong: #CBD5E1;
  --border-focus:  #1F6B3E;

  --accent:        #1F6B3E;
  --accent-hover:  #185C33;
  --accent-subtle: #ECFDF5;

  --info:          #0369A1;
  --info-subtle:   #E0F2FE;
  --success:       #15803D;
  --success-subtle:#DCFCE7;
  --warning:       #B45309;
  --warning-subtle:#FEF3C7;
  --danger:        #B91C1C;
  --danger-hover:  #991B1B;
  --danger-subtle: #FEE2E2;

  --focus-ring:        0 0 0 3px rgb(31 107 62 / 0.28);
  --focus-ring-danger: 0 0 0 3px rgb(185 28 28 / 0.25);

  /* ---------- LAYOUT ---------- */
  --content-narrow:  680px;
  --content-default: 960px;
  --content-wide:    1200px;
  --gutter-mobile:   var(--space-4);
  --gutter-tablet:   var(--space-6);
  --gutter-desktop:  var(--space-8);
}

/* ============================================================
   DARK THEME — same token names, different values.
   Components never branch on theme; tokens do.
   ============================================================ */
[data-theme="dark"] {
  --bg:            #0B1220;
  --bg-subtle:     #0F172A;
  --bg-sunken:     #0A0F1A;
  --bg-elevated:   #131C2E;
  --bg-overlay:    rgb(0 0 0 / 0.6);

  --fg:            #E2E8F0;
  --fg-muted:      #94A3B8;
  --fg-subtle:     #64748B;
  --fg-on-accent:  #06210F;
  --fg-inverse:    #0B1220;

  --border:        #1E293B;
  --border-strong: #334155;
  --border-focus:  #6EE7B7;

  --accent:        #34D399;
  --accent-hover:  #6EE7B7;
  --accent-subtle: #052E1A;

  --info:          #7DD3FC;
  --info-subtle:   #082F49;
  --success:       #6EE7B7;
  --success-subtle:#052E1A;
  --warning:       #FCD34D;
  --warning-subtle:#422006;
  --danger:        #FCA5A5;
  --danger-hover:  #F87171;
  --danger-subtle: #450A0A;

  --focus-ring:        0 0 0 3px rgb(110 231 183 / 0.30);
  --focus-ring-danger: 0 0 0 3px rgb(252 165 165 / 0.30);

  --shadow-sm: 0 1px 3px rgb(0 0 0 / 0.4), 0 1px 2px rgb(0 0 0 / 0.3);
  --shadow-md: 0 4px 12px rgb(0 0 0 / 0.45), 0 2px 4px rgb(0 0 0 / 0.3);
  --shadow-lg: 0 12px 32px rgb(0 0 0 / 0.5), 0 4px 8px rgb(0 0 0 / 0.35);
  --shadow-xl: 0 24px 56px rgb(0 0 0 / 0.55), 0 8px 16px rgb(0 0 0 / 0.4);
}

/* ============================================================
   BASE
   ============================================================ */
*, *::before, *::after { box-sizing: border-box; }

html {
  -webkit-text-size-adjust: 100%;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

body {
  margin: 0;
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: var(--leading-normal);
  color: var(--fg);
  background: var(--bg);
  font-feature-settings: "ss01", "cv11", "kern";
}

h1, h2, h3, h4 {
  margin: 0;
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  line-height: var(--leading-tight);
  color: var(--fg);
}
h1 { font-size: var(--text-3xl); }
h2 { font-size: var(--text-2xl); }
h3 { font-size: var(--text-xl);  }
h4 { font-size: var(--text-lg);  }

p { margin: 0; }

:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  border-radius: var(--radius-sm);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

---

## 3. Layout scale

Every page uses one of these content widths. Nothing else.

| Token | Width | Used for |
| --- | --- | --- |
| `--content-narrow` | 680px | Reading content, forms, single-column detail |
| `--content-default` | 960px | Standard lists, dashboards |
| `--content-wide` | 1200px | Dense tables, kanban, galleries |

Container:

```css
.container {
  width: min(100% - 2 * var(--gutter-mobile), var(--content-default));
  margin-inline: auto;
}
.container--narrow { --content-default: var(--content-narrow); }
.container--wide   { --content-default: var(--content-wide); }

@media (min-width: 720px) {
  .container { width: min(100% - 2 * var(--gutter-tablet), var(--content-default)); }
}
@media (min-width: 1080px) {
  .container { width: min(100% - 2 * var(--gutter-desktop), var(--content-default)); }
}
```

**Vertical rhythm** — every page follows this structure:

```
[ page header        ]   padding-block: space-6  space-8
[ filter bar         ]   margin-bottom: space-6
[ content section    ]   margin-bottom: space-8  (between sections)
[ pagination / footer]   padding-block: space-8  space-12
```

---

## 4. Component library — `static/css/components.css`

The five primitives every screen reuses. Code is authoritative.

### 4.1 Buttons

```css
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  height: 40px;
  padding-inline: var(--space-4);
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: var(--text-sm);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  border: 1px solid transparent;
  cursor: pointer;
  user-select: none;
  text-decoration: none;
  transition:
    background var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out),
    transform var(--duration-fast) var(--ease-out),
    box-shadow var(--duration-fast) var(--ease-out);
  -webkit-tap-highlight-color: transparent;
}
.btn:active { transform: scale(0.98); }
.btn:disabled, .btn[aria-disabled="true"] {
  opacity: 0.5; pointer-events: none;
}

.btn-primary { background: var(--accent); color: var(--fg-on-accent); }
.btn-primary:hover { background: var(--accent-hover); }

.btn-ghost {
  background: transparent;
  color: var(--fg);
  border-color: var(--border);
}
.btn-ghost:hover {
  background: var(--bg-subtle);
  border-color: var(--border-strong);
}

.btn-danger { background: var(--danger); color: #fff; }
.btn-danger:hover { background: var(--danger-hover); }

.btn-sm { height: 32px; padding-inline: var(--space-3); font-size: var(--text-xs); }
.btn-lg { height: 48px; padding-inline: var(--space-5); font-size: var(--text-base); }

@media (max-width: 640px) {
  .btn     { height: 44px; }
  .btn-sm  { height: 36px; }
  .btn-lg  { height: 48px; }
}
```

### 4.2 Page header — `templates/partials/page_header.html`

```html
<header class="page-header">
  <div class="page-header__text">
    {% if eyebrow %}<span class="page-header__eyebrow">{{ eyebrow }}</span>{% endif %}
    <h1 class="page-header__title">{{ title }}</h1>
    {% if description %}<p class="page-header__description">{{ description }}</p>{% endif %}
  </div>
  {% if actions %}<div class="page-header__actions">{{ actions }}</div>{% endif %}
</header>
```

```css
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-6);
  padding-block: var(--space-6) var(--space-8);
}
.page-header__text { min-width: 0; flex: 1; }

.page-header__eyebrow {
  display: inline-block;
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: var(--tracking-wider);
  text-transform: uppercase;
  color: var(--fg-muted);
  margin-bottom: var(--space-2);
}
.page-header__title { font-size: var(--text-3xl); }
.page-header__description {
  margin-top: var(--space-2);
  color: var(--fg-muted);
  max-width: 62ch;
}
.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

@media (max-width: 640px) {
  .page-header {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-4);
    padding-block: var(--space-5) var(--space-6);
  }
  .page-header__actions { flex-wrap: wrap; }
  .page-header__actions > .btn { flex: 1 1 auto; }
}
```

### 4.3 Empty state — `templates/partials/empty_state.html`

Two variants — `empty` (nothing exists) and `filtered` (nothing matches).

```html
<div class="empty-state empty-state--{{ variant|default:'empty' }}">
  <div class="empty-state__icon" aria-hidden="true">{{ icon|safe }}</div>
  <h3 class="empty-state__title">{{ title }}</h3>
  <p class="empty-state__body">{{ body }}</p>
  {% if action %}<div class="empty-state__action">{{ action }}</div>{% endif %}
</div>
```

```css
.empty-state {
  display: grid;
  justify-items: center;
  text-align: center;
  gap: var(--space-3);
  padding: var(--space-16) var(--space-6);
  max-width: 420px;
  margin-inline: auto;
}
.empty-state__icon {
  display: grid;
  place-items: center;
  width: 56px; height: 56px;
  border-radius: var(--radius-lg);
  background: var(--bg-sunken);
  color: var(--fg-muted);
  margin-bottom: var(--space-2);
}
.empty-state__title {
  font-size: var(--text-lg);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
}
.empty-state__body {
  font-size: var(--text-sm);
  color: var(--fg-muted);
  line-height: var(--leading-relaxed);
  max-width: 42ch;
}
.empty-state__action { margin-top: var(--space-3); }

.empty-state--filtered { padding-block: var(--space-12); }
.empty-state--filtered .empty-state__icon {
  background: transparent;
  color: var(--fg-subtle);
}
```

### 4.4 List row — `templates/partials/list_row.html`

```html
<a href="{{ url }}" class="list-row">
  {% if leading %}<span class="list-row__leading">{{ leading }}</span>{% endif %}
  <span class="list-row__main">
    <span class="list-row__title">{{ title }}</span>
    {% if meta %}<span class="list-row__meta">{{ meta }}</span>{% endif %}
  </span>
  {% if trailing %}<span class="list-row__trailing">{{ trailing }}</span>{% endif %}
</a>
```

```css
.list-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-3);
  border-radius: var(--radius-md);
  color: inherit;
  text-decoration: none;
  transition: background var(--duration-fast) var(--ease-out);
  min-height: 60px;
}
.list-row + .list-row {
  border-top: 1px solid var(--border);
  border-radius: 0;
}
.list-row:hover  { background: var(--bg-subtle); }
.list-row:active { background: var(--bg-sunken); }

.list-row__main {
  display: flex; flex-direction: column; gap: 2px;
  flex: 1; min-width: 0;
}
.list-row__title {
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--fg);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.list-row__meta {
  font-size: var(--text-xs);
  color: var(--fg-muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.list-row__leading, .list-row__trailing {
  flex-shrink: 0;
  display: grid; place-items: center;
  color: var(--fg-muted);
}
```

### 4.5 Dialog — `static/js/dialog.js` + CSS

Centered modal on desktop, bottom sheet on mobile. Focus trapped, Escape
closes, focus returned on close. Replaces all `window.confirm` and
page-local modals.

```js
// static/js/dialog.js
const NBTEConfirm = (() => {
  function build({ title, body, confirmLabel, cancelLabel, tone }) {
    const root = document.createElement("div");
    root.className = "confirm-root";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.innerHTML = `
      <div class="confirm-backdrop" data-cancel></div>
      <div class="confirm-panel confirm-panel--${tone || "default"}" role="document">
        <h2 class="confirm-title"></h2>
        <p class="confirm-body"></p>
        <div class="confirm-actions">
          <button type="button" class="btn btn-ghost" data-cancel></button>
          <button type="button" class="btn ${tone === "danger" ? "btn-danger" : "btn-primary"}" data-confirm></button>
        </div>
      </div>`;
    root.querySelector(".confirm-title").textContent = title;
    root.querySelector(".confirm-body").textContent = body;
    root.querySelector(".confirm-actions [data-cancel]").textContent = cancelLabel || "Cancel";
    root.querySelector(".confirm-actions [data-confirm]").textContent = confirmLabel || "Confirm";
    return root;
  }

  function open(opts) {
    return new Promise((resolve) => {
      const el = build(opts);
      document.body.appendChild(el);
      document.body.style.overflow = "hidden";
      const previouslyFocused = document.activeElement;
      const confirmBtn = el.querySelector("[data-confirm]");
      requestAnimationFrame(() => confirmBtn?.focus());

      const close = (result) => {
        document.body.style.overflow = "";
        el.classList.add("is-closing");
        el.addEventListener("transitionend", () => el.remove(), { once: true });
        previouslyFocused?.focus?.();
        resolve(result);
      };
      el.querySelectorAll("[data-cancel]").forEach((b) =>
        b.addEventListener("click", () => close(false)));
      confirmBtn.addEventListener("click", () => close(true));
      el.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.preventDefault(); close(false); }
        if (e.key === "Tab") {
          const focusables = el.querySelectorAll(
            "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
          );
          const list = Array.from(focusables)
            .filter((n) => !n.disabled && n.offsetParent !== null);
          if (!list.length) return;
          const first = list[0], last = list[list.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      });
    });
  }
  return { open };
})();

// Usage:
// const ok = await NBTEConfirm.open({
//   title: "Delete this message?",
//   body: "This cannot be undone.",
//   confirmLabel: "Delete",
//   tone: "danger",
// });
```

```css
.confirm-root {
  position: fixed; inset: 0; z-index: 1000;
  display: grid; place-items: center;
  padding: var(--space-4);
  animation: confirm-in var(--duration-base) var(--ease-out);
}
.confirm-backdrop {
  position: absolute; inset: 0;
  background: var(--bg-overlay);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}
.confirm-panel {
  position: relative;
  width: min(420px, 100%);
  background: var(--bg-elevated);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-xl);
  padding: var(--space-6);
  animation: panel-in var(--duration-base) var(--ease-spring);
}
.confirm-title {
  font-size: var(--text-lg);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  margin-bottom: var(--space-2);
}
.confirm-body {
  font-size: var(--text-sm);
  color: var(--fg-muted);
  line-height: var(--leading-relaxed);
}
.confirm-actions {
  display: flex; gap: var(--space-2);
  justify-content: flex-end;
  margin-top: var(--space-6);
}

@keyframes confirm-in { from { opacity: 0 } to { opacity: 1 } }
@keyframes panel-in {
  from { opacity: 0; transform: translateY(8px) scale(0.98); }
  to   { opacity: 1; transform: translateY(0)   scale(1); }
}

@media (max-width: 520px) {
  .confirm-root { place-items: end center; padding: 0; }
  .confirm-panel {
    width: 100%;
    border-radius: var(--radius-2xl) var(--radius-2xl) 0 0;
    padding: var(--space-6) var(--space-5)
             calc(var(--space-6) + env(safe-area-inset-bottom, 0px));
    animation: sheet-in var(--duration-base) var(--ease-spring);
  }
  .confirm-actions { flex-direction: column-reverse; }
  .confirm-actions .btn { width: 100%; }
}
@keyframes sheet-in {
  from { transform: translateY(100%); }
  to   { transform: translateY(0); }
}
```

---

## 5. Motion

Motion is used in exactly four places. Nowhere else.

1. **Enter / exit of overlays** — dialogs, drawers, sheets. Use
   `--ease-spring` and `--duration-base`.
2. **HTMX swaps** — fade out 140ms, fade in 220ms. See below.
3. **Hover / active feedback on interactive elements** — 140ms `--ease-out`.
   Active press scale is `0.98`.
4. **Scroll-driven UI (mobile header collapse)** — 220ms `--ease-out`.

```js
// static/js/app.js — add to the shared bootstrap
document.addEventListener("htmx:beforeSwap", (e) => {
  const t = e.detail.target;
  if (!t) return;
  t.style.transition = "opacity 140ms cubic-bezier(0.22, 1, 0.36, 1)";
  t.style.opacity = "0";
});
document.addEventListener("htmx:afterSwap", (e) => {
  const t = e.detail.target;
  if (!t) return;
  requestAnimationFrame(() => {
    t.style.transition = "opacity 220ms cubic-bezier(0.22, 1, 0.36, 1)";
    t.style.opacity = "1";
  });
});
```

**Never** animate: layout properties (width, height, top, left), color
transitions longer than 220ms, or anything that delays the user's next
interaction.

---

## 6. Responsive strategy

Four widths. Documented. Every screen is checked at all four.

| Width | Name | Layout |
| --- | --- | --- |
| ≥1280px | Desktop | Full sidebar, side-by-side page header, wide tables |
| 1024–1279px | Small desktop | Sidebar collapses to icons; content 960px |
| 768–1023px | Tablet | No sidebar; top-nav drawer; content full-width minus gutters |
| 520–767px | Large phone | Single column, stacked headers, sticky primary action |
| <520px | Phone | Bottom sheets, sticky action bars, minimum 44px touch targets |

Rules that apply **below 640px and are non-negotiable**:

- Every page header stacks: title above, actions below, actions full-width.
- Every modal becomes a bottom sheet.
- Every list row keeps its leading icon and trailing metadata; only the
  middle text truncates.
- Every primary action is either sticky at the bottom of the viewport, or
  in the page header, never in a floating corner button.
- Forms are single-column. Never two inputs side by side.
- Data tables scroll horizontally inside their own container. The page
  does not scroll horizontally. Ever.

---

## 7. Accessibility

- **Contrast:** WCAG 2.1 AA. Body text ≥ 4.5:1, large text ≥ 3:1, UI
  components ≥ 3:1. Verify in both themes before merging.
- **Focus:** Never `outline: none` without a replacement. Use
  `:focus-visible` with `--focus-ring`.
- **Keyboard:** Every interactive element reachable by Tab. Dialogs trap
  focus. Escape closes overlays. Focus returns to the trigger on close.
- **Touch targets:** Minimum 44×44px on phones. If a control is smaller,
  give it padding to reach 44px.
- **Screen readers:** Use semantic HTML (`<button>`, `<nav>`, `<dialog>`,
  `<label>`). ARIA only when semantics are unavailable.
- **Motion:** Respect `prefers-reduced-motion`. The tokens file handles
  this globally.
- **Form errors:** Associate errors with their input via `aria-describedby`.
  Show errors adjacent to the field, not in a toast.

---

## 8. Dark theme

Dark theme is not "light theme with the colors flipped." It is the same
system with different token values. That is the entire strategy.

Rules:

- **No component branches on theme.** No `[data-theme="dark"] .some-component`.
  If you need different values, add tokens.
- Shadows become lighter ambient + deeper contact.
- Accent shifts from `#1F6B3E` to `#34D399` for legibility.
- Surfaces step: `--bg` (darkest page), `--bg-subtle` (secondary), `--bg-elevated`
  (cards, panels, modals).
- Borders are darker and subtler; rely on elevation and spacing, not
  contrast, for separation.

---

## 9. Reference implementation — Tasks list screen

This is the worked example. Every other list screen copies this structure.

```html
{% extends "base.html" %}
{% load static %}

{% block content %}
<div class="container">

  {% include "partials/page_header.html" with
      eyebrow="Work"
      title="Tasks"
      description="Everything assigned to you and your division."
      actions=header_actions %}

  <form class="filter-bar" method="get" action="">
    <input type="search" name="q" value="{{ request.GET.q|default:'' }}"
           placeholder="Search tasks…" class="filter-bar__search">
    <select name="status" class="filter-bar__select">
      <option value="">All statuses</option>
      {% for value, label in status_choices %}
        <option value="{{ value }}"
          {% if request.GET.status == value %}selected{% endif %}>{{ label }}</option>
      {% endfor %}
    </select>
    <button type="submit" class="btn btn-ghost btn-sm">Apply</button>
    {% if request.GET %}
      <a href="?" class="filter-bar__reset">Clear</a>
    {% endif %}
  </form>

  {% if tasks %}
    <div class="list">
      {% for task in tasks %}
        {% include "partials/list_row.html" with
            url=task.get_absolute_url
            leading=task.icon_html
            title=task.title
            meta=task.meta_line
            trailing=task.status_badge_html %}
      {% endfor %}
    </div>
    {% include "partials/pagination.html" %}
  {% elif request.GET %}
    {% include "partials/empty_state.html" with
        variant="filtered"
        icon=icons.search
        title="No tasks match those filters"
        body="Try a different status or clear the search."
        action='<a class="btn btn-ghost" href="?">Clear filters</a>' %}
  {% else %}
    {% include "partials/empty_state.html" with
        variant="empty"
        icon=icons.inbox
        title="No tasks yet"
        body="When work is assigned to you or your division, it will appear here."
        action='<a class="btn btn-primary" href="{% url "tasks:create" %}">Create the first task</a>' %}
  {% endif %}

</div>
{% endblock %}
```

```css
/* filter-bar — used on every list screen */
.filter-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-6);
}
.filter-bar__search {
  flex: 1; min-width: 0;
  height: 40px;
  padding-inline: var(--space-4);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: var(--text-sm);
  background: var(--bg-elevated);
  color: var(--fg);
  transition: border-color var(--duration-fast) var(--ease-out);
}
.filter-bar__search:focus-visible {
  border-color: var(--border-focus);
  box-shadow: var(--focus-ring);
}
.filter-bar__select {
  height: 40px;
  padding-inline: var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  color: var(--fg);
  font-family: inherit;
  font-size: var(--text-sm);
}
.filter-bar__reset {
  font-size: var(--text-sm);
  color: var(--fg-muted);
  text-decoration: none;
  padding: var(--space-2);
}
.filter-bar__reset:hover { color: var(--fg); }

/* Mobile: filter bar stacks and search takes full width */
@media (max-width: 640px) {
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-3);
  }
  .filter-bar__search,
  .filter-bar__select,
  .filter-bar__select + button,
  .filter-bar__reset {
    width: 100%;
  }
}

.list {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
```

---

## 10. Amendment log

Any change to this document must be recorded here with a date, a one-line
reason, and the affected token or component.

| Date | Change | Reason |
| --- | --- | --- |
| 2026-10-12 | v2.0 initial publication | Foundation for the current improvement pass. |
```

---

## 3. `prompt.md`

```markdown
# NBTE ICT — Working Prompts

**Last updated:** 12 October 2026
**Audience:** AI assistants and human engineers working on the NBTE ICT codebase.
**Read first:** `overview.md`, `design.md`, `structure.md`.

This file contains the standing prompt set. Every working session starts
with the **Session Context** prompt, then picks one **Task Prompt**. Nothing
is done without following the **Closing Checklist**.

---

## 0. Session Context — paste this at the top of every session

```
You are working on the NBTE ICT Directorate Internal Operating System:
a Django 6.x / Python 3.15 application with 14 feature apps, an internal
authenticated shell, a public landing site, and a workplace portal.

READ BEFORE STARTING:
- overview.md    — current state, priorities, guardrails
- design.md      — the design system (tokens, components, motion)
- structure.md   — how the code is organised
- The relevant master-plan section for the feature you're touching

NON-NEGOTIABLE RULES:

1. TOKENS ONLY. All colors, spacing, radii, shadows, and durations come
   from static/css/tokens.css. Never hardcode a value. If a value is
   missing, add the token first (see design.md §10 amendment log).

2. MOBILE IS DESIGNED, NOT REFLOWED. Every screen has a documented mobile
   layout below 640px: stacked headers, bottom sheets for modals, sticky
   primary actions, single-column forms, 44px touch targets. Test at
   320px, 390px, 768px, 1024px, 1440px.

3. DARK THEME IS FREE. No component branches on theme. If you find
   yourself writing [data-theme="dark"] .foo, add a token instead.

4. USE THE SHARED COMPONENTS. page_header, empty_state, list_row,
   NBTEConfirm, .btn. Do not invent parallel versions.

5. NO window.confirm. No inline onclick="return confirm(...)".
   Use NBTEConfirm.open({...}).

6. SERVER OWNS AUTHORISATION. Never gate data or actions in JavaScript.
   Permission checks live in Django views/services. The client reflects
   what the server already allowed.

7. RESPECT UNCOMMITTED WORK. Always start with:
     git status --short
     git diff --stat
   Do not reset, stash, or overwrite files you did not create in this
   session without explicit instruction.

8. NO NEW FRONTEND FRAMEWORKS. No React, Vue, Svelte, Next.js. HTMX,
   Alpine, and Django templates only.

9. MOTION IS PURPOSEFUL. Animate only: overlay enter/exit, HTMX swaps,
   hover/press feedback, mobile header collapse. Nothing else.

10. REDUCED MOTION IS HONOURED. The tokens file handles this. Do not
    re-implement it.

11. ACCESSIBILITY IS NOT OPTIONAL. WCAG 2.1 AA contrast. Visible
    :focus-visible. Keyboard-reachable. Escape closes overlays. Focus
    returns to the trigger. Errors tied to fields.

12. TESTS FOLLOW BEHAVIOUR. Any state transition, access check, or
    filter changes requires a focused test in the same PR.

DELIVERABLE FORMAT:

For every change, output:
  1. The template partial(s) — one component per file, in
     templates/partials/ if reusable, templates/<app>/ otherwise.
  2. The CSS — appended to the appropriate stylesheet, using tokens only.
  3. The JS — only if behaviour is needed; vanilla, no framework.
  4. A one-line note on 320px behaviour.
  5. A one-line note on dark-theme behaviour.
  6. A test suggestion for the highest-risk behaviour in the change.

If you are unsure about scope, stop and ask. Do not guess.
```

---

## 1. Task Prompts

Pick one. Each ends with the Closing Checklist (§3).

### 1.1 Foundation — Replace tokens.css

```
TASK: Replace static/css/tokens.css with the v2.0 file in design.md §2.

STEPS:
1. git status --short  — confirm tokens.css is not mid-edit by someone else.
2. Copy the token file verbatim.
3. Bump the ?v= query string on any <link> that references tokens.css
   in templates/base.html and templates/base_public.html.
4. Run: python3 manage.py check
5. Load the internal shell, the public landing, and one list screen.
   Verify nothing visually regressed in light or dark theme.
6. Do NOT change any other stylesheet yet. This is a pure token swap.

REPORT: a diff summary, and a screenshot description of the shell in both
themes.
```

### 1.2 Foundation — Build the shared components

```
TASK: Add the five shared components to the codebase.

FILES TO CREATE:
- static/css/components.css          (styles from design.md §4)
- static/js/dialog.js                (NBTEConfirm from design.md §4.5)
- templates/partials/page_header.html
- templates/partials/empty_state.html
- templates/partials/list_row.html

FILES TO MODIFY:
- templates/base.html                (link components.css and dialog.js)
- static/css/app-shell.css           (remove any duplicated .btn rules)

STEPS:
1. Create each file exactly as specified in design.md.
2. Link components.css AFTER tokens.css and app-shell.css so it wins where
   it needs to.
3. Load any existing screen and confirm no regression.
4. Do not refactor any feature screen in this task.

REPORT: file list, line counts, and confirmation that existing screens
still render.
```

### 1.3 Foundation — Replace window.confirm everywhere

```
TASK: Remove every window.confirm and inline onclick="return confirm(...)"
in the codebase, replacing them with NBTEConfirm.open().

STEPS:
1. Search: grep -rn "window.confirm\|return confirm(" templates/ static/js/
2. For each occurrence:
   - Identify the destructive action and its consequence.
   - Replace with an async handler using NBTEConfirm.open({...}).
   - Use tone: "danger" and a specific consequence text.
   - Preserve the original form submission or fetch call, now conditional
     on the resolved value.
3. Where a confirmation was previously inline HTML, extract to a small
   function in the page's feature JS file.
4. Test each affected action: cancel closes without side effects, confirm
   performs the action.

REPORT: table of every replaced occurrence (file, action, tone, new location).
```

### 1.4 Feature — Refactor the Tasks list screen

```
TASK: Rebuild the Tasks list screen as the reference implementation of the
design system.

FILES:
- templates/tasks/list.html (or whatever it is named — check first)
- static/css/tasks.css (or append to components.css if small)
- templates/partials/page_header.html  (already built)
- templates/partials/list_row.html     (already built)
- templates/partials/empty_state.html  (already built)

STEPS:
1. Read the current template and view.
2. Apply the pattern in design.md §9 verbatim.
3. Preserve server-side filtering and the query string in URLs.
4. Distinguish the two empty states (no records vs. no matches).
5. Add a test that verifies filters persist in the URL and that the
   correct empty state renders.
6. Screenshot at 320px, 390px, 768px, 1024px, 1440px in both themes.

REPORT: before/after description, the test name, and any view changes.
```

### 1.5 Feature — Build the Self-Service Dashboard (§9.2)

```
TASK: Build the Staff Self-Service Dashboard as specified in the master
plan §9.2 and depicted in Diagram 11.

CONTENT (in priority order):
1. Profile card — photo, name, designation, department, staff state.
2. Four widgets: My Tickets, My Tasks, My Leave/Requests (with countdown),
   My Assets.
3. Below: "Pending My Approval" strip (only if the user holds an approver role).

RULES:
- Read-optimised: no new writes; aggregation view over existing data.
- Every widget links out to its full module view.
- No widget renders if the underlying module is unavailable to the user
  (department scope, permission).
- Layout: 2-column grid on tablet+, single column on phone.
- Use .kpi-tile (design.md §4, add it if missing) and .list-row.

FILES:
- views: core/views.py (add a dashboard_home view) OR dashboards/views.py
- template: templates/dashboards/self_service.html
- css: static/css/dashboards.css

TESTS:
- Unauthorised user cannot access.
- User in department X sees only department-X data.
- Widget links resolve.

REPORT: template tree, view name, URL, tests added.
```

### 1.6 Feature — Build the "Awaiting My Action" queue (§9.4)

```
TASK: Build the unified queue described in master plan §9.4.

SOURCES (four, merged in one query set or one Python aggregation):
- Complaints awaiting the viewer's office.
- Requests/approvals awaiting the viewer's step.
- Overdue asset returns the viewer chases.
- Escalated tickets at the viewer's level.

FEATURES:
- Filters by source module, priority, age.
- Bulk approve / return with one comment.
- Priority sort.
- Each item retains a "source module" tag and deep-links back.

RULES:
- Server-side filtering. Permission checks per source module.
- No bulk action bypasses the source module's rules — the bulk handler
  calls the same service function a single action would.
- Mobile: items stack; bulk bar sticks to the bottom.

FILES:
- view: a new view in workflow/ or core/
- template: templates/dashboards/awaiting_action.html
- service: the merge logic in a service module, not the view

TESTS:
- Cross-module permission — user sees only what they're allowed to see.
- Bulk approve respects the underlying chain.
- Empty state when the user has nothing pending.

REPORT: merged-data approach, service function name, tests added.
```

### 1.7 Feature — Global search (start with tasks only)

```
TASK: Add a permission-aware global search endpoint and UI, starting with
tasks only.

STEP 1 — endpoint:
- URL: /search/
- Query params: q, type (default: task)
- Returns JSON: { results: [{ type, id, title, meta, url }], total }
- Server-side: filter by the requesting user's scope, using the same
  queryset the destination view uses. Do not re-implement scope logic.

STEP 2 — UI:
- Add a search control to the internal top bar.
- Desktop: compact labeled input.
- Phone: icon-only button; tapping expands a full-width field.
- Debounced (200ms). Server-backed. No client-side filtering.
- Results grouped, keyboard navigable, Escape closes.
- No match → empty state with the query echoed.

STEP 3 — tests:
- User A cannot see tasks User B can see.
- Department scope enforced.
- Empty result for non-matching query.

REPORT: endpoint name, template partial, JS file, tests added.
```

### 1.8 Feature — Public contact inbox

```
TASK: Build a protected staff workspace for core.ContactMessage.

FILES:
- view: core/views.py (or a new view module)
- template: templates/core/contact_inbox.html + contact_detail.html
- permission: a new permission, e.g. can_handle_contact_messages
- URL: /contact-inbox/ and /contact-inbox/<pk>/

FEATURES:
- Filters: new/unread, assigned to me, handled, unassigned.
- Search by sender and subject.
- Assign to staff, mark read, mark handled, reopen.
- Internal notes (never visible to the public sender).
- Response history log.

RULES:
- Separate from internal messaging. No automatic staff account for the
  external sender. No automatic chat thread.
- Server enforces the permission. Anyone without can_handle_contact_messages
  gets 403.
- Notification to CONTACT_NOTIFY_EMAIL remains the operational alert, but
  the DB record is the source of truth.

TESTS:
- External submission creates a ContactMessage.
- Unauthorised user gets 403 on the inbox.
- Assignment, status transition, note privacy.

REPORT: permission name, view names, tests added.
```

### 1.9 Mobile fix — Messaging confirmation dialog

```
TASK: Fix the reported mobile rendering defect in the messaging
confirmation dialog.

STEPS:
1. Reproduce: open messages at 390px, trigger a delete confirmation.
2. Confirm the dialog is now NBTEConfirm (if not, migrate it first).
3. Verify: centred on desktop, bottom sheet below 520px, safe-area padding,
   buttons stacked and full-width, focus trapped, Escape closes.
4. Test with and without native <dialog> support.
5. Test light and dark theme.

REPORT: before/after screenshots at 390px and 1440px, both themes.
```

### 1.10 Tests — Workflow state machine

```
TASK: Add behaviour tests for workflow/ covering the highest-risk paths.

TESTS TO ADD (one file, workflow/test_state_machine.py):
- A requester cannot approve their own request (FI-25).
- A user outside the department cannot view a request detail.
- An approver at step N can approve to N+1.
- An approver at step N can return to N-1 (mandatory comment enforced).
- An approver at step N can reject (terminates; reason recorded).
- A request at step N cannot be acted on by step N+2's approver.
- An approver who is on leave and has a delegate — the delegate can act
  (if §9.3 is implemented).

PATTERN: follow accounts/test_departments.py and notifications/tests.py.

REPORT: test names, pass count, any production code changes required
(bugs found by the tests).
```

### 1.11 Tests — Tasks state machine

```
TASK: Add behaviour tests for tasks/ covering state transitions and
multi-assignee rules.

TESTS TO ADD (tasks/test_behaviour.py):
- A task cannot be marked Done until every assignee track is Confirmed.
- A user cannot act on a task outside their division.
- Dynamic limits: a new assignment is refused when the assignee is at
  their category cap.
- Reviewer confirmation is required and cannot be self-performed by the
  assignee.
- Bulk operations call the same service function a single action does
  (assert via mock or spy).

REPORT: test names, pass count, production bugs found.
```

---

## 2. Design Prompts

### 2.1 "Design this screen" — reusable template

```
DESIGN TASK: [screen name]

GIVEN:
- The current template: [file]
- The current view: [file]
- What the screen must do: [1–3 sentences]

DELIVER:
1. A written layout sketch at three widths: 1440px, 768px, 390px.
   Describe column counts, stacking order, sticky elements, primary action
   placement.
2. The token usage — list every token consumed. If a token is missing,
   propose it in the amendment log.
3. The template partials to create or reuse.
4. The CSS additions — using tokens only.
5. The empty, loading, error, and success states — for each, describe
   the user sees and does.
6. The 320px behaviour note.
7. The dark-theme behaviour note.
8. The test suggestion.

CONSTRAINTS:
- Apple-HIG-inspired: large title, generous whitespace, subtle elevation.
- Mobile is designed, not reflowed.
- No new components if an existing one fits.
- No JS beyond what HTMX / a small vanilla helper provides.
```

### 2.2 "Review this screen" — reusable template

```
DESIGN REVIEW: [screen name]
FILE: [path]

REVIEW AGAINST design.md:
- Token usage: any hardcoded values? List them.
- Typography: is the type scale respected? Any arbitrary font-sizes?
- Spacing: is every gap on the scale?
- Buttons: is .btn used, or a parallel implementation?
- Empty states: are both variants present?
- Dialogs: is NBTEConfirm used?
- Mobile: does it stack correctly at 320px?
- Dark theme: any per-component overrides that should be tokens?
- Accessibility: focus visible? contrast AA? keyboard reachable?
- Motion: is anything animated that shouldn't be?

OUTPUT: a numbered list of issues, each with the exact file:line and the
exact fix. Do not rewrite the file — give the diff.
```

---

## 3. Closing Checklist — every session

Before ending any session, verify and report:

```
CLOSING CHECKLIST

[ ] git status --short shows only intended changes.
[ ] python3 manage.py check passes with no issues.
[ ] Focused test for the change passes.
[ ] Relevant broader test suite still passes.
[ ] Screenshots captured at 320 / 390 / 768 / 1024 / 1440px.
[ ] Light and dark theme both verified.
[ ] No hardcoded values introduced (grep for hex colors, px values
    outside tokens.css, ms durations outside tokens.css).
[ ] No window.confirm, no inline onclick="return confirm(".
[ ] No per-component [data-theme="dark"] overrides introduced.
[ ] Templates reference partials rather than duplicating markup.
[ ] Accessibility: focus-visible on every new interactive element.
[ ] Accessibility: contrast AA on every new color pairing.
[ ] If a design token was added, it's recorded in design.md §10.
[ ] If a new component was created, it's documented in design.md §4.
[ ] Commit message names the token or component touched (if any).

REPORT BACK:
- Files created.
- Files modified.
- Tests added or changed.
- Any bugs found and not fixed (with a note).
- Any assumptions made.
```

---

## 4. Anti-patterns — never do these

| Anti-pattern | Why it's wrong | Do this instead |
| --- | --- | --- |
| `color: #1F6B3E` in a component | Bypasses the token system | `color: var(--accent)` |
| `padding: 17px` | Not on the scale | `padding: var(--space-4)` |
| `[data-theme="dark"] .my-card { background: … }` | Duplicates theme logic | Add a token, or use `--bg-elevated` |
| `onclick="return confirm('Delete?')"` | Inconsistent, untestable | `NBTEConfirm.open({…})` |
| `<div class="button" role="button">` | Semantic HTML exists | `<button class="btn">` |
| `outline: none` alone | Accessibility failure | `:focus-visible` + `--focus-ring` |
| `transition: all 0.3s` | Animates layout, jank | Name the properties, use tokens |
| Fetching every record then filtering in JS | Scale, security | Server-side filter, permission-scoped |
| Two-column form on mobile | Cramped, mistappable | Single column below 640px |
| Floating corner action button | No platform precedent here | Sticky bottom bar or header action |
| A new modal implementation | Fragments UX | `NBTEConfirm` or a new shared partial |

---

## 5. Amendment log

| Date | Change | Reason |
| --- | --- | --- |
| 2026-10-12 | Initial publication | Standing prompt set for the improvement pass. |
```

---

## A note on how these three fit together

- **`overview.md`** is what you read first — it answers "where is this project, what matters, what's the plan."
- **`design.md`** is what every screen must look like — the tokens and components are authoritative, not illustrative.
- **`prompt.md`** is what every working session follows — a fixed context block, a menu of task prompts, and a non-negotiable closing checklist.

Together they let any new engineer or AI assistant produce work that is consistent, reviewable, and mobile-first without re-litigating decisions.

Two small cautions before you commit these:

1. **The token file in `design.md` §2 replaces the existing `tokens.css`.** If the current file already has module-specific values you rely on, grep for `var(--` across the codebase and confirm every token used is present in v2.0 before swapping.
2. **The `partials/` templates use `{% include with %}` syntax.** Confirm your Django version supports the multi-line `with` form used (Django 3.1+, which you have). If it trips on the line breaks, they can be single-lined — no other change needed.