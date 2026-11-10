# NBTE Digital — v2.3 release notes (5 November 2026)

Builds on v2.2: a design sweep, simpler processes and automatic honoraria.

## How to upgrade your copy

```bash
pip install -r requirements.txt          # dev; requirements-prod.txt on the server
python manage.py migrate                 # new settings, honorarium files, reference formats, audit column
python manage.py seed_nbte --only qa.onboarding_flow_v3,honoraria.references   # onboarding v4 flow + formats
python manage.py build_css               # the new design layer is in the CSS bundles
python manage.py generate_institution_guides
```

Applications already under way keep the flow they started with; new ones use onboarding v4.

## Processes

### Onboarding (SOP-01), in the SOP order
- The onboarding steps run in this order:
  1. Register and set up.
  2. Upload documents.
  3. Each department reviews its own documents.
  4. When the last document is approved, the application moves on by itself.
  5. Pay the institutional fee; Finance confirms it.
  6. Programmes: choose them, NBTE clears them, pay the programme fees.
  7. Self-study (SSQ), then SOP-03.
- No one approves a second time after the departments. When the last document is cleared:
  - the institution is told its next step;
  - the handling department's director, the ES and super admins get a notice for information.
- The institution becomes **Approved at the end of SOP-03** (stage 17), as long as at least one programme came out approved. It then moves to its category's department.
- **Setting `qa.onboarding.fee_timing`** decides when the institutional fee is paid:
  - `after_clearance` (the default, SOP order): after the documents are cleared.
  - `before_review`: before documents can be uploaded.
  - The order is fixed for each application when its Chief Executive signs the declaration.

### Decide only after opening the file
- On a document review, the Accept, Return and Approve buttons stay locked until the reviewer has opened the document.
- The server refuses a decision on a file the reviewer never opened.
- Setting: `qa.review.require_preview` (on by default).

### Your work: every task one click away
- One list on the QA home, on every dashboard and on My work.
- Each item opens the task itself: the document review, the SSQ, the SOP-03 stage. "Institution" is a second button.
- Filters with counts.
- **Directors' group approval:** a director ticks the documents their officers recommended for acceptance and approves them in one go.
- SOP-03 case pages open with a **"Your next action"** banner.

### Honoraria: one file per institution, automatic
- **What goes into the file**, from SOP-01 to SOP-03:
  - document reviews, ₦10,000 each;
  - SSQ reviews, ₦10,000 internal and ₦30,000 external;
  - visit staff, days × state rate;
  - academic resource persons.
- **How it fills.** Lines are added as each stage completes, and the DoF, the ES and super admins are notified. Unpaid lines from later stages join the same file, and nothing is counted twice.
- **Paying:**
  1. The DoF chooses a **pay officer**. Pay officers see masked account numbers only.
  2. The pay officer prepares a batch.
  3. The DoF clicks **Approve & mark paid** once, which posts it to the ledger.
  4. Each payee is told "Paid — please confirm receipt".
- **Fixed:** "No active reference format". The missing reference formats are created by a migration, the seed and on demand. A switched-off format falls back to a safe reference instead of blocking a payment.

## Design
- **Design layer.** A new NBTE design layer (`static/css/nbte-ux.css`): hero headers, stat tiles, cards, chips, filter pills with counts, steppers, checklists and charts. Button rows are always separated from what follows them.
- **Confirmations.** Every confirmation uses NBTE's own dialog (crest, brand bar, tone), including every `hx-confirm`.
- **QA home.** Numbers and charts by permission:
  - income vs expenditure;
  - the onboarding pipeline;
  - institutions by status;
  - SOP-03 cases by phase;
  - document reviews.
- **Institution page:**
  - a green hero with the journey strip;
  - programme filters with counts, plus ND/HND;
  - a People & access tab with the Chief Executive action card and a "shown once" panel for links and passwords;
  - the management team as cards.
- **Guides help centre.** Search, a "Start here" path, guide cards, and a reader with a contents panel that follows you and a reading-progress bar.
- **My work and the document queue** are redesigned around the task.
- **Finance pages:** Institution files, Who's next, Payment batches, and Income vs expenditure (with charts) are all redesigned.
- **The institution portal** is fully redesigned:
  - a green NBTE sidebar, with a drawer and bottom tabs on phones;
  - one **"Your next step"** card on the dashboard and the application page;
  - a journey stepper;
  - Documents, Payments (with a chart), Letters and Programmes pages;
  - an add-programme panel with clear spacing.
  - Fixed: fee rows on the application page.
  - Fixed: a 500 error on Change requests.
- **Guides.** New process diagrams and fresh screenshots of the new portal. Screenshots can now be captured on any system with Playwright (`scripts/capture_guide_screenshots.py`).
