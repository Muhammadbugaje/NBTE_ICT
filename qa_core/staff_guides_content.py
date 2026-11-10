"""v2.2 — staff guides (internal system), in the same block format as the
institution guides (see qa_core.guides_content). Read online at /qa/guides/."""

STAFF_GUIDES = {
    "new-institutions": {
        "title": "New institutions and ICT oversight",
        "subtitle": "From registration to an ICT officer tracking the institution",
        "audience": "ES, super admins, Director of ICT, ICT officers, handling directors",
        "summary": "The new-institution spotlight, assigning an ICT officer, the tracker and the Chief Executive account.",
        "intro": ["When an institution registers on the QA portal, NBTE must see it at once and give it an ICT "
                  "officer who follows it to the end (SOP-03 stage 1). This guide shows where that happens."],
        "blocks": [
            ("part", "The spotlight", "A bold card for every new institution on the right dashboards."),
            ("bullets", [
                "**ES, super admins and the Director of ICT** see every institution registered in the last 30 days "
                "(setting **qa.spotlight.days**) and any still without an ICT officer.",
                "**A department director** sees the new institutions their department handles.",
                "Orange cards have **no ICT officer yet**; the progress bar shows how far onboarding has gone.",
            ]),
            ("part", "Assign an ICT officer", "Director of ICT, ES or super admin."),
            ("steps", [
                "Find the card | On QA home or your dashboard, under **New on the QA portal**.",
                "Pick the officer | The rotation's suggestion is already selected (**suggested next**).",
                "Press Assign | The officer is notified and the card shows **Tracked**.",
            ]),
            ("tip", "From the institution page too",
             "Institution → Overview → **ICT oversight** does the same, with the full list of ICT staff."),
            ("part", "Tracking an institution", "For the assigned ICT officer."),
            ("p", "Accept the assignment in **My work**. Your dashboard then shows **Institutions you are "
                  "tracking**, each with its onboarding steps and the SOP-03 stage. You can open every tab and "
                  "document **read-only**; only the responsible department acts."),
            ("part", "The Chief Executive account", "Only the Chief Executive sets their password."),
            ("p", "At registration the set-up link is e-mailed to the Chief Executive's own address. It is never "
                  "shown to the Head of ICT who registered. On Institution → **Users**, the **Chief Executive "
                  "account** card lets the ES, super admins, the Director of ICT and the assigned ICT officer:"),
            ("bullets", [
                "**E-mail set-up link to the CE** — a fresh link to the CE's own e-mail;",
                "**Get link to send manually** — shown once, to send privately (e.g. WhatsApp to the CE's number);",
                "**Issue one-time password** — shown once; the CE must choose a new password at first sign-in.",
            ]),
            ("important", "Every action is recorded", "Who sent or saw a link or password, and when, is in the "
                                                      "institution's audit trail."),
            ("note", "Manual mode", "Settings → Rules → **qa.onboarding.ce_auto_email** off: nothing is e-mailed at "
                                    "registration; NBTE sends the link when the Chief Executive asks."),
            ("part", "People and contacts", "Institution → Users."),
            ("p", "Staff with access to an institution see its whole management team (office, name, e-mail, "
                  "phone), every portal account and the contact and address history."),
        ],
    },
    "documents-access": {
        "title": "Who sees an institution's documents",
        "subtitle": "Department-only documents",
        "audience": "Directors and officers",
        "summary": "Each statutory document belongs to the department that reviews it.",
        "blocks": [
            ("part", "The rule", "A document is seen only by the department that reviews it."),
            ("table", ["Who", "Sees", "Can assign an officer"], [
                ["Director of the reviewing department", "That department's documents", "Yes"],
                ["Officer assigned to a document", "That document, while the work is open", "—"],
                ["Directors of other departments", "The document's name only (locked)", "No"],
                ["ES, super admins", "Everything", "Yes"],
                ["Director of ICT", "Everything", "Yes"],
                ["ICT officer overseeing the institution", "Everything, read-only (progress)", "No"],
            ]),
            ("part", "Open before you decide", "Accept, return and approve unlock only after you look."),
            ("p", "On a document's page the decision buttons stay locked, with a gold notice, until you open the "
                  "document (**Open document** or **Download**). The server checks it too: a decision on a file "
                  "you never opened is refused. Setting: `qa.review.require_preview`."),
            ("part", "Your work, one click to each task", "QA home, your dashboard and My work."),
            ("bullets", [
                "Every item opens the task itself — the document review, the SSQ, the case stage — with "
                "**Institution** as a second button.",
                "Filters with counts: Decide, Assign, Review, SOP-03, Overdue.",
                "Directors: tick the documents your officers **recommended for acceptance** and press "
                "**Approve selected** — one confirmation, approved with your name. Only officer-reviewed items "
                "can be approved this way.",
            ]),
            ("part", "When the last document is cleared", "The application moves on by itself."),
            ("p", "There is no extra approval after the documents: each department's clearance is the decision "
                  "(SOP-01). The institution is told its next step at once (pay the institutional fee, or choose "
                  "programmes), and the handling department's director, the ES and super admins get a notice for "
                  "information. The institution is approved at the end of SOP-03."),
        ],
    },
    "fees-and-payments": {
        "title": "Fees, payments and reconciliation",
        "subtitle": "For the Director of Finance and Finance staff",
        "audience": "Finance, ES, super admins",
        "summary": "Fees by institution category, confirming payments, the ledger and the nightly reconciliation.",
        "blocks": [
            ("part", "Fees by institution category", "QA settings → Money → Fees by institution category."),
            ("p", "Each category (Polytechnic, Monotechnic, College of Health, College of Nursing, Technical "
                  "College, IEI, VEI …) has its own registration fee, registration processing fee, programme fee, "
                  "programme processing fee (once per bill or per programme), extra-stream fee and QA request fees."),
            ("steps", [
                "Open the category | Use **At a glance** or the list below it.",
                "Type the amounts | Naira, numbers only. 0 = that line is not charged.",
                "Switch it on | **In use** bills this category from the table (off = the older formula).",
                "Confirm | Tick **Confirmed by Finance** once approved, then **Save**.",
            ]),
            ("tip", "Bills never change afterwards", "A bill keeps the amount it was issued with."),
            ("part", "When the institutional fee is due", "QA settings → `qa.onboarding.fee_timing`."),
            ("table", ["Setting", "Order for a new institution"], [
                ["after_clearance (default, SOP-01)", "Documents reviewed first → fee due when the last one is "
                                                      "approved → Finance confirms → programmes"],
                ["before_review", "Fee paid and confirmed first → documents → review → programmes"],
            ]),
            ("p", "A change applies only to applications whose Chief Executive has not yet signed the declaration; "
                  "those already under way keep their order."),
            ("part", "Confirming payments", "Finance (QA) → Verification queue."),
            ("p", "Online payments arrive already verified with the provider. Only the holder of the **Director "
                  "of Finance seat** confirms or rejects. Anyone else sees who holds the seat; the ES or a super "
                  "admin moves it under Organisation → Departments → Finance."),
            ("part", "Online payments that never double-charge", "What the system does for you."),
            ("bullets", [
                "Every attempt has its own reference (idempotency key); a double click reuses the same checkout.",
                "Before any new checkout, earlier open attempts are checked with the provider; a paid one stops it.",
                "When the provider cannot be reached, the attempt stays **pending** and is asked again later with "
                "the same reference — never marked failed, never retried with a new key.",
                "Webhooks are accepted only with a valid signature (Paystack) or from Remita's addresses, and are "
                "always followed by a server-side check.",
            ]),
            ("part", "Ledger and reconciliation", "Finance (QA) → Ledger & reconciliation."),
            ("p", "Every bill, confirmed payment, waiver and honorarium is posted to a double-entry ledger. Each "
                  "night at 01:45 the system asks Paystack and Remita about recent payments, compares them with "
                  "NBTE's records and the ledger, corrects what is safe, and lists the rest: double payments, "
                  "amount differences, payments the provider no longer confirms. **Run now** does it on demand; "
                  "mark each issue resolved with a note."),
        ],
    },
    "honoraria": {
        "title": "Paying honoraria",
        "subtitle": "Who's next to be paid",
        "audience": "Director of Finance, ES",
        "summary": "Document assessment, internal and external SSQ reviews and visits, paid in order.",
        "blocks": [
            ("part", "What is earned", "Rates are in QA settings → Honoraria."),
            ("table", ["Work", "Setting", "Default"], [
                ["Internal document assessment or SSQ review", "honoraria.rate.internal_item", "₦10,000"],
                ["External (resource person) SSQ review", "honoraria.rate.external_ssq_review", "₦30,000"],
                ["Academic resource person, per visit", "honoraria.rate.academic_resource_person", "₦30,000"],
            ]),
            ("part", "One honorarium file per institution", "Finance (QA) → Honoraria → Institution files."),
            ("p", "Nobody has to build anything: as each stage completes — SOP-01 documents cleared, SSQ reviews "
                  "done, the SOP-03 visit closed — everyone who worked on it is added to the institution's file "
                  "with their rate and amount, and the Director of Finance, the ES and super admins are told "
                  "\"Honoraria ready to settle\". Unpaid work from later stages joins the same file; nothing is "
                  "counted twice."),
            ("steps", [
                "Choose a pay officer (once) | The Director of Finance picks a Finance officer for all files, or one "
                "file. The officer sees masked account numbers only.",
                "Prepare | The pay officer (or the DoF) ticks the ready lines and prepares a batch; the DoF is "
                "notified.",
                "Approve and pay | The DoF presses **Approve & mark paid** once: posted to the ledger and audited.",
                "Receipt | Each payee is told \"Paid — please confirm receipt\" and confirms on My earnings.",
            ]),
            ("tip", "Visits already paid through fund release", "If a case's funds were released at SOP-03 stage 11, "
                    "its visit lines show \"Settled (fund release)\" so the visit is never paid twice "
                    "(setting `honoraria.files.fund_release_settles_visit`)."),
            ("part", "Who's next (by person)", "Finance (QA) → Honoraria → Who's next."),
            ("p", "The same money, listed by person: confirmed bank details first, oldest finished work first."),
            ("important", "Bank details", "Only the owner can change them. The Director of Finance, the ES and "
                                          "super admins can view them to pay; every view is logged."),
        ],
    },
    "facilities": {
        "title": "Facilities and equipment register",
        "subtitle": "ND/HND minimum resources, in Excel",
        "audience": "Curriculum office and assigned handlers",
        "summary": "Import, edit, analyse and publish the minimum facilities and equipment per programme.",
        "blocks": [
            ("part", "What it is", "QA → Facilities & equipment."),
            ("p", "The minimum physical facilities and equipment of every ND/HND programme, taken from the NBTE "
                  "curricula (87 programmes, 665 facilities, about 15,400 items)."),
            ("part", "Keep it up to date", "Three ways."),
            ("bullets", [
                "**Type in the table** on a programme's page: changes save when you leave the box.",
                "**Excel**: Export, edit, then Import Excel — you see New / Changed / Unchanged before saving.",
                "**Word**: Import Word files (.docx or .zip) — the curriculum lists are read and previewed.",
            ]),
            ("part", "Publish to the SSQ catalogue", "Make the list count in SSQ reviews."),
            ("p", "Link the programme to the national master list, then **Publish to catalogue** (ND or HND "
                  "quantities). Institutions' SSQ shortfall scores then use this list; items marked **Critical** "
                  "count double."),
            ("part", "Handlers", "Who may change the register."),
            ("p", "The ES, super admins and the Curriculum director add or remove handlers at the bottom of the "
                  "register page; each programme can also have its own maintaining officer."),
        ],
    },
    "organisation": {
        "title": "Departments and units",
        "subtitle": "One list for departments, units and offices",
        "audience": "ES, super admins, HR",
        "summary": "How departments, units, offices, zonal and liaison offices work together.",
        "blocks": [
            ("part", "One list", "Organisation → Departments & units."),
            ("p", "Departments, units, offices, zonal offices and liaison offices are one list. Each has a "
                  "**kind**, can sit under another unit (e.g. zonal offices under ZONAL OFFICE), can have one "
                  "director, and can carry permission groups every member gets automatically."),
            ("tip", "Transfers", "Transfer a staff member to any department or unit from their staff file."),
        ],
    },
}

STAFF_ORDER = ["new-institutions", "documents-access", "fees-and-payments", "honoraria", "facilities", "organisation"]

# v2.3 — help-centre presentation: icon, colour, who it is for (filter tags)
# and the "start here" path. Reading time is worked out from the text.
STAFF_META = {
    "new-institutions": {"icon": "building", "tone": "green", "tags": ["es", "ict", "directors"],
                         "kicker": "Onboarding"},
    "documents-access": {"icon": "lock", "tone": "blue", "tags": ["directors", "officers"],
                         "kicker": "Need-to-know"},
    "fees-and-payments": {"icon": "bank", "tone": "gold", "tags": ["finance", "es"], "kicker": "Money"},
    "honoraria": {"icon": "card", "tone": "violet", "tags": ["finance", "es"], "kicker": "Money"},
    "facilities": {"icon": "layers", "tone": "teal", "tags": ["officers", "directors"], "kicker": "Curriculum"},
    "organisation": {"icon": "users", "tone": "red", "tags": ["es", "hr"], "kicker": "Organisation"},
}
STAFF_AUDIENCES = [("es", "ES & super admins"), ("directors", "Directors"), ("officers", "Officers"),
                   ("ict", "ICT"), ("finance", "Finance"), ("hr", "HR")]
STAFF_START_HERE = ["new-institutions", "documents-access", "fees-and-payments"]
