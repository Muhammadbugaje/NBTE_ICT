# Phase 7 changelog — Complaints, dashboards, polish (20 Oct 2026)

## Part 1 — UI/UX fixes and polish
- **Notification bell (mobile):** a floating card, at most 70% of the screen high, scrolls inside, "View all notifications" button. Internal (`templates/partials/topbar.html`) and portal (`qa_portal/base_portal.html`, new page `/alerts/`, `qa_core.portal_views.notifications`).
- **Settings hub redesign** (`templates/config/settings.html`, QA settings rows): card per area, section navigator (sticky list on wide screens, pill strip on phones), every setting a row with title, plain description and its control; yes/no settings are iOS-style switches that save on change.
- **Self-registration control:** featured card at the top of Settings → QA with a live state chip; OFF shows the portal banner "Registration is managed by NBTE…" with the QA Directorate's contacts (`qa.portal.registration_contact_email/phone`).
- **Events & announcements grid:** compact left-aligned CSS grid; 0–2 items no longer stretch; dashed "Awaiting more announcements…" placeholder cards.
- **NBTE crest watermark** on the public landing and hero; scroll reveals and hover lift (respect reduced motion). New `static/css/polish.css`, `static/js/polish.js`.

## Part 2 — Dashboards
- **QA overview** on the ES, Director, Deputy and Super-admin dashboards (`dashboards/qa_metrics.py`, `templates/dashboards/_qa_overview.html`, `static/js/qa-insights.js`): confirmed revenue (12 months, Chart.js), institutions by status (doughnut), deadlines met (line), open cases by stage (bar), top complaint layers; count-up figures; overdue cases glow.
- **Staff home "Your desk"** (`core/workboard.py`, `templates/core/_workboard.html`): Tasks, Pending confirmations (incl. payments ready for the Director of Finance), Recent QA activity and complaints on my desk; urgent items glow.

## Part 3 — Phase 7 Complaints (Master Plan §5.6, §11)
- New models: ComplaintLayer, RoutingRule, Complaint, ComplaintStage, ComplaintMessage, ComplaintAttachment, EscalationRule, Escalation, KnowledgeArticle (`complaints/models.py`, migration `complaints/0001_initial`). Permissions = the §12 complaints row.
- Engine `complaints/services.py`: raising (custom questions, related record — Requirement 13), routing (ordered rules, formula conditions, five target kinds, fallback queue), business-hour SLA clocks (`complaints/sla.py`: work week, `qa.calendar.business_hours`, `portal.Holiday`), pause while awaiting the raiser, actions from the flow's transitions, internal/public messages (TR-12), escalation chain (reminder at 50%, Director → Deputy/Division Head → ES for critical or repeat; watcher + task, never reassigns), holds (off until `qa.suspension.enabled_for`), reopen window, auto-close, knowledge-base search, timeline on every step (TR-18).
- Task bridge: complaint stages open Tasks; escalations are `QATaskLink(kind="assignment")`; `qa_url` → `/qa/complaints/<id>/`; stage reminders skip complaints (they have their own sweep).
- Runners: `manage.py complaints_sla_sweep`, Celery task `qa_core.complaints.sla_sweep` (beat every minute, honours `qa.complaints.sla_poll_minutes`).
- Internal pages (`/qa/complaints/`): queue with tabs, search and 4 filters, pagination and live clocks; complaint page; SLA dashboard + CSV; Settings → Complaints (layers, routing, escalation, knowledge base).
- Portal pages: Complaints, Raise a complaint (KB suggestions while typing), complaint conversation (confirm with rating / reopen / withdraw), Help articles; "Raise a complaint about this" on payment, SSQ, programme, inspection and timeline rows; Complaints in the portal menu.
- Seed (`qa_core/seed_data_phase7.py`): default `complaint` flow, 10 layers, 11 routing rules, 3 escalation levels, 5 KB articles, 8 settings, 9 templates/rules, role and group permissions, task category `qa-complaint`.
- Demo: `seed_phase7_demo` (also run by `seed_startup --with-demo`): three open complaints (on track, near breach, breached + escalated through 3 levels) and 12 months of confirmed demo revenue.

## Phase 8 (started)
- `seed_startup --with-demo` on an empty database now creates every role, group, flow, setting and the demo data including complaints (test: `complaints/tests/test_seed_phase8.py`).
- `set_demo_passwords`: one known password for every demo account (refused when DEBUG is off).
- Security review of `ExternalUserGuardMiddleware` — see `SECURITY_REVIEW_PHASE8.md`; one fix (resolve `path_info`).

## Changed files outside the complaints app (all additive)
`qa_core/task_bridge.py`, `qa_core/portal_urls.py`, `qa_core/portal_views.py` (nav), `qa_core/settings_urls.py`, `qa_core/views.py` (tab), `qa_core/permissions.py`, `qa_core/context_processors.py`, `qa_core/workspace.py`, `qa_core/settings_catalogue.py`, `qa_core/templatetags/qa_tags.py`, `qa_core/management/commands/seed_startup.py`, `accounts/middleware.py`, `core/views.py`, `dashboards/views.py`, `nbte_ict/urls.py`, `nbte_ict/celery.py`, portal templates (raise-link includes), `templates/partials/sidebar_qa.html`.
