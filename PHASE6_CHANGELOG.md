# NBTE QA System — Phase 6 changelog

**Phase 6 — Professional bodies, workspace switcher, public portal, UI overhaul, institution guides.** Built 19–20 Oct 2026.

What you can do now: keep a register of **professional bodies** and the programmes they regulate (with Excel import / export); a QA request for a mapped programme becomes a **joint QA exercise** and the body is e-mailed when the case opens. Staff switch between the **main system** and a **QA workspace** that shows only QA links. Visitors to the portal see a **landing page** and a **how-to page** with diagrams, and can download four **Word guides**. Every screen uses the new Apple-grade design, with a floating **liquid-glass tab bar** on phones.

---

## 1. Professional bodies and joint QA (Checkpoint A)

| Screen (internal, `/qa/professional-bodies/…`) | What it does |
|---|---|
| **Bodies** | Search + filters (status, level of the programmes it regulates, e-mail on file), 25 per page or *Show all*. Add / edit a body (inline field errors). Export to Excel |
| **Body detail** | Contact details, the programmes it regulates (map, turn on / off), the joint QA cases it was told about |
| **Programme mappings** | Every mapping, with search + filters (body, level, status); map a programme |
| **Import from Excel** | Template download; *Check the file* (nothing saved) or *Import*. One bad row rejects the whole file, every error is listed with its sheet and row. Re-importing an unchanged export changes nothing |

* `ProfessionalBody` (name, acronym, address, contact person, e-mail, phone, active) and `ProgrammeBodyMapping` (body, programme name, level or *every level*, note, active).
* **Mapping is by curriculum programme (name + level), not by one institution's programme row.** "ND Pharmacy Technician → PCN" covers that programme at every institution, so the Curriculum Department maps it once. This is the same convention as the curriculum catalogue (`CatalogueItem.programme_name`). Matching ignores case and punctuation.
* `QARequest.is_joint_qa` and `QARequest.joint_bodies` are kept in step with the request's active programmes (created, programme added / dropped / replaced, submitted).
* When the SOP-03 case opens, each mapped body is e-mailed once (`emails/professional_body_notice.html`, through `qa_core.mail`), a `ProfessionalBodyNotice` is recorded (sent / no e-mail on file / failed) and the case timeline says so. A notice failure never stops the case.
* The case header and Overview tab, the internal request header and the portal request page show **Joint QA** and the bodies.
* Permission `institutions.manage_professional_bodies`: Curriculum Officer, Director, *QA Settings Admin* group. Anyone who can see institutions can read the lists. Object routes answer 404 when out of scope (§4 layer 3).

## 2. Workspace switcher (Checkpoint B)

* A **Main | QA** segmented control at the top of the sidebar for staff who can open a QA screen.
* **QA workspace**: only QA links (New Institution, TVET Support, Applications, QA requests, QA cases, Inspection, Professional bodies, Finance (QA), Quality Assurance) and **Return to Main System** in the sidebar footer. **Main**: the NBTE system without the QA groups.
* The choice is saved on the user (`User.workspace`), so it follows them to any device. Switching swaps the sidebar over HTMX (no reload); without JavaScript the form posts and redirects back. Any page under `/qa/` always shows the QA sidebar, so deep links never lose their navigation. *Return to Main System* from a QA page goes to the home page.

## 3. Public portal pages (Checkpoint C)

| Page (portal host) | What it shows |
|---|---|
| `/` | Visitors: the landing page (hero with a live-looking request tracker, the four stages from registration to decision, who uses the portal, a call to action). Signed-in users: their dashboard, as before |
| `/how-to/` | Getting started, onboarding (SOP-01), QA requests (SOP-02), the seven QA types, what happens inside NBTE (SOP-03), payments, joint QA, guides to download, questions. Four diagrams; a table of contents that follows the reader (a pill strip on phones) |
| `/guides/<slug>/` | Downloads a Word guide |
| `/register/` | When `qa.portal.self_registration_enabled` is **off**, the form is hidden and the page says: *"Institution registration is currently managed by NBTE. Please contact [e-mail or phone] to begin your registration."* |

* New settings: `qa.portal.registration_contact_email`, `qa.portal.registration_contact_phone` (blank = the site's public contact details).
* The diagrams are inline SVG built from the real flow stages by `scripts/build_howto_diagrams.py`: theme-aware (light and dark), accessible (`role="img"`, title, description), scroll sideways on phones.
* Design: `static/css/qa-public.css` (scoped under `.qp`), glass only on the sticky nav, the floating tracker and the mobile Log in / Register dock. Reduced motion, reduced transparency and more-contrast preferences are honoured.

## 4. UI overhaul (Checkpoint D)

* **Tokens** (`static/css/tokens.css`): the system SF face everywhere (the serif display face and Inter / Roboto are gone), Apple greys (`#f5f5f7` ground, `#1d1d1f` text), hairline dividers, two-layer shadows, radius tiers 6 / 10 / 12 / 18 / 22 / 26, a spring curve without overshoot, true-black dark mode with graphite surfaces.
* **Design layer** (`static/css/apple.css`, loaded last on the internal app and the portal): calm page headers instead of tinted gradient cards, sentence-case labels instead of ALL CAPS (67 rules), white surfaces with soft shadows, segmented tabs, an accent ring instead of coloured left stripes, a quiet institution monogram, tabular numbers, a scroll-edge hairline on the top bar.
* **Mobile**: a floating liquid-glass **tab bar** (`partials/mobile_tabbar.html`): Home, Tasks, Messages, Alerts, Menu in the main system; Cases, Requests, Institutions, Tasks, Menu in the QA workspace. It tucks away while scrolling down and returns on the way up. The case stepper says *"Stage 12 of 17 · Post-visit report"* on phones and centres the current stage; tabs keep the active one in view with an edge fade.
* **§20 contract** — lists brought up to *search + three facets + 25 rows with Show all*: portal QA requests (search, level), portal queries (search, about, due), portal inspections (search, when, year), portal timeline (who acted), portal documents (pagination), resource-person engagements (when) and service history (visits / SSQ reviews), SSQ review queue (level), payment webhooks (period), QA e-mail outbox (period, sent to), curriculum catalogue (pagination). The pagination partial gained an optional `swap`.
* `<meta name="theme-color">` follows the new ground colours.

## 5. Institution guides (Checkpoint E)

* `python manage.py generate_institution_guides [--only SLUG] [--output-dir DIR] [--images-dir DIR]` writes four Word documents to `MEDIA_ROOT/qa/guides/`: **Starter**, **Onboarding (SOP-01)**, **QA requests (SOP-02)**, **Payments**. Cover page, running header, page numbers, numbered steps, tip boxes, tables, embedded screenshots and diagrams with figure captions, and a help section with the contact details from Settings.
* The content is plain data in `qa_core/guides_content.py`.
* Screenshots and diagram images live in `docs/guides/images/` and are refreshed with `scripts/capture_guide_screenshots.py` (macOS Quick Look). A missing image becomes a short note; the build never fails.
* The how-to page lists the guides and offers each for download once built.

## 6. Fixes found on the way

* The cross-tenant test (`test_layer3_cross_tenant_requests_answer_404`) caught professional-body object routes answering 403; they now answer 404.
* The QA workspace switch used the lazy user object as a model class; fixed (`get_user_model()`).
* Inserting a helper above `ssq_queue` had moved its access decorators; caught in review and restored.
* The public pages' headings inherited the serif display face from `base.css`; now SF.
* At 390 px the public nav's *Log in* collided with the brand name; the nav now drops *Register* on phones (the dock carries it).
* Status chips no longer wrap mid-word.
