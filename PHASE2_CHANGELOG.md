# NBTE QA System — Phase 2 changelog

Phase 2 of PHASES.md: **institutions, onboarding (SOP-01) and payments**, built on the Phase 1 engine (ActionPolicy / PendingAction for every confirmed change, FlowDefinition for every workflow). Dated 18 Oct 2026. Nothing in the backups app, SSQ templates or the internal `workflow` app was touched.

## Decisions taken at "Go"

| Question | Decision |
|---|---|
| Who confirms a bulk import? | A second officer, like every other §6 action (`institutions.bulk_import`, rank 50, same department). |
| Do staff open portal payment pages? | No. Finance sees the bill, proofs, provider evidence and statement lines in its own queue on the internal host. |
| Where does onboarding stop in Phase 2? | At `programme_selection`: the institution lists its programmes. Programme clearance and programme fees arrive in Phase 3 (the stages exist and are shown as "next"). |
| Online payments | Remita and Paystack are on with the simulator in development/demo (no keys needed), and **seeded off in production** (`prod.py` forces `QA_PAYMENTS_SIMULATE=False`; a provider is offered only when its setting is on *and* its keys are in the environment). |
| Demo accounts | Six added: Director of Finance, the four clearance officers (Legal, Finance, Academic Planning, Physical Planning) and a second institution's administrator. |

## Public site

* **Quality Assurance Portal** button in the landing-page hero (gold, next to *Learn more*), a one-line note under it, and links in the footer (*Platform*) and the mobile menu. The URL is `settings.QA_PORTAL_URL`, or `https://QA_PORTAL_HOST/` when that is blank (new context processor `qa_core.context_processors.qa_portal_link`).

## institutions

* Full institution record: ownership, type, category, state/LGA (validated against the LGA list), zone (recomputed), year, CAC RC number, website, origin, portal switch, approval/delisting dates, former names, desk officer. Contacts and addresses keep their history (a change closes the old row).
* Lookups editable in **Settings → QA → Lookups**: ownerships, institution types, categories (each mapped to its handling department), disciplines, programme levels, programme categories.
* Statutory documents: **Settings → QA → Documents** defines document kinds and the rules that make them required, by ownership and/or institution type. Private institutions need the CAC certificate and bank guarantee; Federal and State need the enabling law and funding commitment; every institution needs C of O, academic and physical master plans and the need-assessment survey (organogram optional); health/nursing types also need a clinical MOU. Uploads are checked by extension, size and file signature, hashed (sha256), versioned, and served only through protected downloads.
* Every §6 action is a registered handler run through the Phase 1 engine (initiate → pending → second officer confirms → applied in one transaction; stale → fails safely; reject needs a remark; expiry): Add TVET, Initiate Approve Inst., Update Contact, Update Address, Update State & LGA, Update Category (**shows the department transfer on the confirm screen**), Update TVET Name (old name kept), Update Ownership (**re-checks required documents, lists the newly required ones and holds the open application** — `qa.ownership_change.hold_open_cases`), Drop Programmes, Wipe Institution Requests (never deletes; submitted requests excluded), Cancel Request, Revert QA History (compensating flow event), Delist / Relist, Suspend / Restore Portal Access, Send Login Credentials (single step; one-time set-password link, never a password), Impersonate Institution (single step; read-only, time-boxed, banner, every page audited), Bulk Import. The five actions that need Phase 3–5 records (Add Programme to QA, Edit QA Personnel, Edit QA Mapping, Override Post-Visit Personnel, Send Back To Audit) are present and shown disabled with the reason.
* Sidebar menus **New Institution** and **TVET Support** are built from the ActionPolicy rows (labels and groups editable), with a *Confirm …* entry and a count for each two-step action the user can confirm.
* Institution list (filters: search, status, ownership, type, department, zone; two-mode pagination) and institution page (tabs: overview, programmes, documents, users, applications, payments, changes, timeline; Actions menu).
* **Bulk import / export (openpyxl)**: downloadable templates with a Reference sheet; the whole sheet is validated first and one bad row rejects the batch (nothing is written); a valid batch waits for a second officer and is applied atomically (re-validated at confirmation); rows match by code, then exact name; new rows are always **proposed** and the status column cannot change status; re-importing an unchanged export changes nothing.
* Self-registration on the portal (`/register/`, switchable with `qa.portal.self_registration_enabled`, rate-limited, honeypot).

## accreditation (SOP-01 onboarding)

* `ApplicationCycle` (optional application windows), `Application`, `DepartmentalClearance`.
* The `onboarding` flow: draft → submitted → **parallel gate** (Legal, Finance, Academic Planning, Physical Planning) → join → institutional fee due → payment pending confirmation → fee confirmed → programme selection (→ Phase 3 stages). All four must clear; any one *Return to institution* (remark required) sends the application back to draft for everyone and closes the sibling branches; resubmission opens round 2.
* Clearance queue per department (badge in the sidebar) and an application page with the four branch cards, the department's own documents to accept or return, all documents by reviewing department, and the workflow history.
* Portal **Application** page: progress tracker, what is missing before submission, declaration, clearance cards, fees, programme selection.

## payments

* Models: bank accounts (NUBAN-validated), fee schedules (formula setting + due days + account), obligations with a frozen formula snapshot, slips (versioned), attempts, proofs, verifications, advices, credits, refunds, bank statement imports and lines, webhook log.
* Providers: Remita (RRR, SHA-512 hashes, status poll), Paystack (HMAC-SHA512 webhook signature, server-side verify, fee gross-up when the institution bears the charge), bank transfer, offline (waiver). A development simulator stands in for both APIs and sends correctly signed webhooks.
* **A webhook never confirms.** Signature-checked webhooks and polls can only move a bill to `provider_reported_paid` → `awaiting_finance_confirmation`, after a server-side verify ("verify wins"). `payments.services.final_decision` is the only path to `confirmed`, and only for the role in `qa.payment.final_approver_role` holding `payments.final_confirm_payment`, who did not do the first pass. Online payments also need the provider receipt before confirmation (`qa.payment.receipt_required_for_provider_payments`).
* Slip PDF (Appendix C): NBTE header, institution and application, locked payment reference with mod-97 check digits, Code 128 barcode, QR code to the public verification page, amount in figures and words, bank details, Remita RRR, validity, instructions. Payment Advice PDF generated on confirmation, with its own QR code. DejaVu fonts are bundled so ₦ prints.
* Finance (QA): verification queue (first pass by a Finance officer, optional second check above `qa.payment.dual_verify_threshold`, final decision by the Director of Finance), bills, CSV statement import with configurable column maps and automatic matching by reference, unmatched lines with mod-97 typo suggestions and manual matching, waivers, refunds, webhook log.
* §10.6 edge cases: partial payments (setting), overpayment recorded and held as credit, duplicate payment and money after cancellation flagged with a refund opened, expired slip regenerated with the same reference, the same proof file on two bills flagged, future payment dates refused, open online attempts closed when a bill is settled or cancelled.
* `python manage.py qa_payments_sweep` and the Celery beat task `payments.sweep` (every 15 minutes): SLA reminders, overdue confirmations, provider polls. Never confirms.

## Portal

Dashboard (action items, recent activity), **Application**, **Documents** (checklist with filters, inline upload with progress, version history), **Payments** (list with filters and totals; bill page with tracker, pay online, bank transfer, *I have paid*, receipt upload, slip and advice downloads), **Institution** (profile, contacts, users and invitations), **Activity** timeline, public slip/advice verification page, access-blocked page for delisted or suspended institutions.

## qa_core additions

* Flow engine: parallel gates with automatic fan-out and join, auto stages, an effects registry, status mirroring, `force_stage`, `branch_states`, `public_label`, `stage_changed` signal.
* PendingAction: `on_closed` hook (reject, withdraw, expire **and failed apply**), `payload_label`, confirm-page preview for create-type actions (Add TVET, imports).
* `OutboundEmail` and **QA e-mail outbox** (e-mails are logged, not sent: `QA_EMAIL_DELIVERY=console`).
* Settings catalogue: every `qa.fee.*`, `qa.payment.*`, `qa.ownership_change.*` key plus `qa.institution.*` and `qa.documents.*`, seeded by `seed_startup`; new Settings → QA tabs **Fees & accounts**, **Payment methods**, **Documents**, **Lookups**.

## Fixes found during the walkthrough

* Bulk import confirmations always failed as stale (the batch was saved after the pending action recorded its version). The batch is now marked pending before initiation, in the same transaction; a failed confirmation releases the batch so it can be re-sent.
* Finance staff saw 404 on bills and queue items: the Finance roles now hold `qa_core.view_all_institutions` (Finance handles every institution's payments).
* The confirm page showed "no preview" for Add TVET; create-type actions now preview from the payload.
* Slip/advice PDF title was cut off at the right margin.
* Portal file links failed outside a portal request; they now reverse against the portal URLconf.
* The institution timeline tab crashed on system events (no actor); names are now shown with a null-safe `who` filter everywhere in the Phase 2 templates.
* The PendingAction engine now records the target's version from the locked database row rather than from the object passed in (an older in-memory copy made a later confirmation fail as stale). This is a small Phase 1 engine change; all Phase 1 pending-action tests still pass.
* Console e-mails are now readable plain text (no hidden preheader or HTML entities).
* The QA e-mail outbox is also open to QA settings administrators (it holds one-time set-password links, so it stays restricted).
* Fee settings show the account actually printed on slips (the default account) instead of the placeholder.

## Changes to Phase 1 tests (pinned Phase 1 counts)

| Test | Change | Why |
|---|---|---|
| `test_seed_startup.py::test_first_run_creates_everything` | Expected flows now include `institution_lifecycle`, `onboarding`, `refund` | Phase 2 seeds three flows |
| `test_seed_startup.py::test_reset_demo_removes_exactly_the_tagged_rows` | 11 demo users instead of 5 | Six Phase 2 demo users |
| `test_payment_flow.py::test_final_confirm_rejects_actor_without_final_approver_role` | Removes `final_confirm_payment` from the role inside the test before checking | Phase 2 grants the permission to `qa-finance-director`, as Phase 1 anticipated |
| `test_interaction_contract.py::test_pagination_25_per_page_and_show_all` | Counts only the 40 templates the test creates (search filter) | Phase 2 seeds more templates |
| `test_host_routing.py::test_url_tables_are_disjoint` | Lists the Phase 2 portal routes | New portal pages. The portal download route is `download/…` so it stays disjoint from the internal `files/…` route |

`test_interaction_contract.py::test_settings_screens_need_permission` was kept unchanged: the sidebar's *Quality Assurance* group is now shown only to people who can open Pending confirmations, QA settings or the outbox.

## Files changed outside the QA apps

Everything else is inside `qa_core`, `institutions`, `accreditation`, `payments` and their templates. No file was renamed or moved; no new dependency was added.

| File | Change |
|---|---|
| `nbte_ict/settings/base.py`, `dev.py`, `prod.py` | `QA_PORTAL_URL`, `QA_EMAIL_DELIVERY`, Remita/Paystack/QR-signing environment settings, `QA_PAYMENTS_SIMULATE` (dev on, prod forced off), the QA impersonation middleware, the `qa_portal_link` context processor |
| `nbte_ict/urls.py` | Mounts `qa/institutions/`, `qa/applications/`, `qa/finance/`, `qa/settings/` (Phase 2 settings tabs) |
| `nbte_ict/celery.py` | Adds the `qa.payments.sweep` beat entry (every 15 minutes) |
| `core/views.py` | `protected_download` uses a model's `unscoped` manager when it has one (QA file models are institution-scoped) |
| `core/files_register.py` | Registers the QA file models for clean-up |
| `templates/public/landing.html`, `static/css/landing.css` | The Quality Assurance Portal button, note, footer and mobile-menu links (CSS version bumped to `v=13`) |
| `templates/partials/sidebar_qa.html` | New Institution, TVET Support, Applications and Finance menus |
| `static/js/qa.js`, `static/css/qa-p2.css` | HTMX 422 swaps, upload progress; Phase 2 components |
