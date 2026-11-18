"""Guided tours ("Guide me"): the registry the tour engine (static/js/tour.js) runs.

A tour is plain data. The engine dims and blurs the page, cuts a hole round
one element, draws an arrow to it and shows a short card. Tours can span
pages: progress is kept in the browser, so the tour picks up after a
navigation (full page load or a boosted HTMX swap).

Adding a tour
-------------
Append a dict to ``TOURS``:

    {"id": "portal-something",            # unique, stable (saved per user)
     "title": "Do something",             # shown in the picker and on the card
     "summary": "One line on what it covers.",
     "audience": ("institution",),        # institution / resource_person / professional_body / staff
     "host": "portal",                    # "portal" (qa.* host) or "staff"
     "area": "qa",                        # staff tours only: "main" (the internal system) or "qa" (the QA
                                          # workspace, offered only to staff who can open it)
     "group": "Everyday work",            # optional: heading the tour is listed under in the picker
     "needs": "finance",                  # optional, QA tours: offered only when this part of the QA menu is
                                          # open to the person (a key of the ``qa_nav`` context: institutions,
                                          # applications, finance, qa_requests, qa_cases, inspection, complaints,
                                          # professional_bodies, programmes, documents_register, settings, pending)
     "when": "onboarding",                # optional: onboarding / approved (institution status)
     "role": "Chief Executive",           # optional: a label shown in the picker
     "start": "/payments/",               # the page the tour begins on
     "pages": ("/payments/",),            # path prefixes where it is listed under "On this page";
                                          # end one with "$" to match that exact page only ("/home/$")
     "guides": ("payments",),             # guide slugs that offer it as "Guide me through it"
     "keywords": "pay bill remita",       # extra search words
     "auto": False,                       # True = offered once to a new user on `start`
     "next": "portal-other",              # optional: tour offered when this one ends
     "steps": [ ... ]}

A step:

    {"type": "info",                      # info (Next) / click (wait for a click on the target)
                                          # fill (wait for a value) / navigate (go to `url`)
     "target": ["[data-tour='x']", ".fallback"],   # CSS selectors, first visible one wins; omit = centred card
     "closest": "section",                # optional: widen the highlight to this ancestor
     "via": ".pp-tabbar [data-pp-menu]",  # optional: what to point at when the target is hidden (phone menu)
     "path": "/payments/",                # optional: the step only exists on this exact path
     "path_re": r"^/payments/\\d+/$",      # ... or on paths matching this
     "title": "…", "text": "…",           # short and practical; *word* renders bold
     "optional": True,                    # skip quietly when the target is not on the page
     "missing": "No bill yet — …",        # what to say when a required target is not there (the tour stops here)
     "skippable": True,                   # ... unless the later steps still make sense without it
     "open": {"label": "Open Payments", "url": "/payments/"},   # link offered with `missing`
     "url": "/documents/"}                # navigate steps only

Selectors: prefer a ``data-tour="name"`` attribute on the template; ids and
aria-labelledby hooks that already exist are fine as fallbacks.

Where the tours live
--------------------
The core tours are below. The rest are in sibling modules, one per audience,
gathered into ``TOURS`` at the bottom of this file:

    tours_internal.py      NBTE internal (non-QA) staff system: everyday work, requests, tasks, tickets
    tours_internal_self.py ... leave, attendance, my record and tools
    tours_qa_staff.py      NBTE QA workspace (/qa/): onboarding, finance, cases
    tours_qa_staff_b.py    ... inspection, visits, records and set-up
    tours_portal_more.py   institution portal
    tours_people.py        resource persons and professional bodies

How they reach the browser
--------------------------
``boot()`` puts a few bytes on every page (what the person has seen, the
welcome tour to offer, and ``dataUrl``). tour.js fetches the tours themselves
from ``dataUrl`` (GET /me/tours/, see tour_views.py); the address carries a
hash of the registry, so the browser keeps its copy until a tour changes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging

from .tour_lib import LAUNCHER, MENU, PB_HOME, STAFF_MENU, nav as _nav, staff_nav as _staff_nav

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Institution portal
# ---------------------------------------------------------------------------
PORTAL_TOURS = [
    {
        "id": "portal-welcome",
        "group": "Getting started",
        "title": "A quick look around",
        "summary": "Your home page, your journey with NBTE, and where to find help.",
        "audience": ("institution",), "host": "portal", "start": "/dashboard/", "pages": ("/dashboard/",),
        "guides": ("starter",), "keywords": "start begin new first dashboard home tour welcome", "auto": True,
        "next_by_when": {"onboarding": "portal-setup", "approved": "portal-qa-request"},
        "steps": [
            {"type": "info", "title": "Welcome to the NBTE QA Portal",
             "text": "This is where your institution deals with NBTE: registration, documents, fees, programmes "
                     "and accreditation. Let me show you round. It takes about a minute.",
             "next_label": "Show me around", "skip_label": "Not now"},
            {"type": "info", "target": ["[data-tour='dash-hero']", ".pp-dash .pp-hero"], "title": "This is Home",
             "text": "You land here every time you sign in. The tags show your institution's code, its status "
                     "with NBTE and your application reference."},
            {"type": "info", "target": ["[data-tour='journey-track']", ".lj-track", ".pp-journey .pp-steps"],
             "title": "Your journey with NBTE", "optional": True,
             "text": "Every step from registration to the accreditation decision, in order. A tick means done; "
                     "the highlighted step is where you are now."},
            {"type": "info", "target": ["[data-tour='journey-now']", ".lj-now .lj-box", ".pp-next"],
             "title": "What you need to do now", "optional": True,
             "text": "This box always names the *one* next thing and who has to do it. When it says "
                     "*Nothing to do now*, NBTE is working on your file and will notify you."},
            {"type": "info", "target": ["section[aria-labelledby='qa-action-title']"], "title": "Things to do",
             "optional": True,
             "text": "Bills to pay, documents NBTE returned and requests waiting for you are listed here, "
                     "most urgent first."},
            {"type": "info", "target": [".pp-nav"], "via": MENU, "title": "Everything has a place",
             "text": "Your application, documents, payments, letters and your institution's profile are all in "
                     "this menu. On a phone, open it with *Menu*."},
            {"type": "info", "target": ["[data-tour='bell']", ".pp-top [data-dropdown-trigger='qa-notif-menu']"],
             "title": "Notifications", "optional": True,
             "text": "NBTE tells you here, and by e-mail, whenever something changes or needs your action."},
            {"type": "info", "target": ["[data-tour='help']", ".pp-top__help"], "title": "Help on every page",
             "text": "The *?* button explains the page you are on and opens the step-by-step guides."},
            {"type": "info", "target": LAUNCHER, "title": "Stuck? Press Guide me",
             "text": "Choose what you want to do and I will take you to the right page and point at each step, "
                     "one at a time."},
        ],
    },
    {
        "id": "portal-setup",
        "group": "Getting started",
        "title": "Finish setting up your institution",
        "summary": "The setup checklist and your management team: who runs the institution.",
        "audience": ("institution",), "host": "portal", "start": "/setup/",
        "pages": ("/setup/", "/institution/management/"), "guides": ("starter", "onboarding"),
        "keywords": "setup checklist management team chief executive registrar roles profile contacts",
        "next": "portal-application",
        "steps": [
            {"type": "info", "path": "/setup/", "target": ["[data-tour='setup-hero']", "#qa-setup .qa-ob-hero"],
             "title": "Setup comes first",
             "text": "Before you upload documents, NBTE needs to know who runs your institution. The ring "
                     "shows how many items are done."},
            {"type": "info", "path": "/setup/", "target": ["section[aria-labelledby='qa-checklist-h']"],
             "title": "Your profile checklist",
             "text": "Work down the list. Each unfinished item has a button that opens the right form; "
                     "finished items show *Done*."},
            {"type": "info", "path": "/setup/", "target": ["section[aria-labelledby='qa-team-h']"],
             "title": "Management staff",
             "text": "NBTE sets the roles. Give a name, phone number and e-mail for each required one. "
                     "NBTE writes to these people."},
            {"type": "info", "path": "/setup/", "target": ["section[aria-labelledby='qa-accounts-h']"],
             "title": "Two accounts, two jobs",
             "text": "The Head of ICT manages users and uploads. Only the *Chief Executive* can sign "
                     "declarations and submit, using the account they activate from their own e-mail."},
            {"type": "navigate", "url": "/institution/management/"},
            {"type": "info", "path": "/institution/management/", "target": ["section[aria-labelledby='qa-roles-h']"],
             "title": "Fill every required role",
             "text": "Roles marked *Required* must have a holder. Press *Add* beside an empty role, or "
                     "*Replace* when someone new takes over."},
            {"type": "info", "path": "/institution/management/", "target": ["#qa-holder-form"], "optional": True,
             "title": "Add the holder",
             "text": "Choose the role, enter the full name, e-mail and phone, then press *Save*. Repeat for "
                     "each required role. Earlier holders stay in your history."},
            {"type": "info", "title": "That is setup",
             "text": "When the checklist is complete, document upload and the Chief Executive's declaration "
                     "open straight away. Your Home page will show the next step."},
        ],
    },
    {
        "id": "portal-application",
        "group": "Onboarding",
        "title": "Start your application and upload documents",
        "summary": "Open the onboarding application, then upload each required document.",
        "audience": ("institution",), "host": "portal", "when": "onboarding", "start": "/application/",
        "pages": ("/application/", "/documents/"), "guides": ("onboarding", "starter"),
        "keywords": "application onboarding documents upload pdf checklist start sop-01 returned",
        "next": "portal-ce-declare",
        "steps": [
            {"type": "info", "path": "/application/", "target": ["[data-tour='app-hero']", "#qa-application .pp-hero"],
             "title": "Your application to NBTE",
             "text": "Onboarding normally runs in this order: setup, declaration, documents, NBTE's review, "
                     "the institutional fee, programmes, the programme fee, then self-studies. This page follows it."},
            {"type": "info", "path": "/application/", "target": ["#your-journey", ".pp-journey"], "optional": True,
             "title": "Where you are",
             "text": "The tracker shows the step you are on and who has it: you, your Chief Executive, or NBTE."},
            {"type": "info", "path": "/application/", "target": ["#qa-next"], "optional": True,
             "title": "The step that is open now",
             "text": "If you see *Start application*, press it. Next the Chief Executive signs the "
                     "declaration. Normally the institutional fee is paid later, after NBTE approves your "
                     "documents; if a fee is due first, this panel says so."},
            {"type": "navigate", "url": "/documents/"},
            {"type": "info", "path": "/documents/", "target": ["#qa-doc-summary"],
             "title": "How many documents are in",
             "text": "The bar counts the *required* documents you have uploaded and lists the ones still needed."},
            {"type": "info", "path": "/documents/", "target": ["section[aria-label='Document checklist']"],
             "title": "Your document checklist",
             "text": "One row for each document NBTE needs from your type of institution. Gold rows are not "
                     "uploaded yet, red rows need a new file, green rows are accepted."},
            {"type": "info", "path": "/documents/",
             "target": ["#qa-doc-rows .qa-upload__btn", "#qa-doc-rows .qa-doc__note", "#qa-doc-rows .qa-doc"],
             "title": "Upload a file",
             "missing": "No document rows are showing. Clear the filters above the list, or check that setup is complete.", "skippable": True,
             "text": "Press *Upload* on a row and choose the file (PDF or image). It saves at once. If a row is "
                     "locked, the note on that row says what has to happen first."},
            {"type": "info", "path": "/documents/", "target": ["#qa-doc-rows .qa-doc"], "optional": True,
             "title": "Who reviews it",
             "text": "Each document goes to the NBTE department responsible for it. If one is returned, only "
                     "that document comes back, with the reason. Upload the corrected file on the same row."},
            {"type": "info", "title": "Then submit",
             "text": "When every required document is uploaded, the Chief Executive submits them to NBTE from "
                     "the Application page. You never resubmit the whole application."},
        ],
    },
    {
        "id": "portal-ce-declare",
        "group": "Onboarding",
        "title": "Declare and submit to NBTE",
        "summary": "For the Chief Executive: sign the declaration and submit the documents.",
        "audience": ("institution",), "host": "portal", "when": "onboarding", "role": "Chief Executive",
        "start": "/application/", "pages": ("/application/",), "guides": ("onboarding",),
        "keywords": "chief executive ce declare declaration sign submit rector provost",
        "next": "portal-payment",
        "steps": [
            {"type": "info", "title": "This one is for the Chief Executive",
             "text": "Only the Chief Executive's account can sign the declaration and submit to NBTE. "
                     "Anyone else sees a note saying NBTE is waiting for the Chief Executive."},
            {"type": "info", "path": "/application/", "target": ["#qa-next"],
             "missing": "There is nothing to sign or submit right now. Your Home page shows who has the next step.",
             "open": {"label": "Open Home", "url": "/dashboard/"},
             "title": "The step waiting for you",
             "text": "This panel always holds the action NBTE is waiting for. Anything still blocking it is "
                     "listed here in plain words."},
            {"type": "fill", "path": "/application/", "target": ["#qa-next .pp-declare"], "optional": True,
             "title": "Tick the declaration",
             "text": "Confirm that the information and documents are true and that you are authorised to "
                     "apply for your institution."},
            {"type": "info", "path": "/application/",
             "target": ["#qa-next form button[type='submit']", "#qa-next .pp-note--lock"], "optional": True,
             "title": "Sign, then later submit",
             "text": "The first time, this button signs the declaration and starts your application. When "
                     "every required document is in, the same place shows *Submit documents to NBTE*. "
                     "End this guide to press it."},
            {"type": "info", "title": "What NBTE does next",
             "text": "Each department reviews its documents and approves or returns them. When all are "
                     "approved, the institutional processing fee is billed under *Payments*, unless it was "
                     "already paid at the start."},
        ],
    },
    {
        "id": "portal-payment",
        "group": "Payments",
        "title": "Pay a bill",
        "summary": "Find a bill, choose how to pay, and upload proof for a bank transfer.",
        "audience": ("institution",), "host": "portal", "start": "/payments/", "pages": ("/payments/",),
        "guides": ("payments",), "keywords": "pay payment fee bill remita paystack bank transfer proof teller receipt rrr",
        "steps": [
            {"type": "info", "path": "/payments/", "target": ["[data-tour='pay-head']", "#qa-main .qa-page-head", "#qa-main h1"],
             "title": "Payments",
             "text": "Every NBTE fee is a bill with its own reference. Normally the institutional fee is "
                     "billed after NBTE approves your documents, and the programme fee after your programmes are cleared."},
            {"type": "info", "path": "/payments/", "target": ["section[aria-label='Bills']"], "title": "Your bills",
             "text": "Bills waiting for payment show *Pay now*. Paid bills stay here with their official receipt."},
            {"type": "click", "path": "/payments/", "target": ["#qa-pay-rows a.qa-rowlink"],
             "missing": "You have no bill yet. NBTE issues one when your application reaches a fee stage; "
                        "it appears here and on your Home page.",
             "open": {"label": "Open Home", "url": "/dashboard/"},
             "title": "Open a bill", "text": "Click the bill you want to pay."},
            {"type": "info", "path_re": r"^/payments/\d+/", "target": [".qa-methods"], "optional": True,
             "title": "Choose how to pay",
             "text": "*Remita* and *Paystack* confirm in seconds. *Bank transfer* also works, but NBTE "
                     "Finance has to confirm it against the bank statement."},
            {"type": "info", "path_re": r"^/payments/\d+/", "target": [".qa-paysteps", ".qa-method-help"],
             "optional": True, "title": "Follow the numbered steps",
             "text": "Online: continue to the provider and pay; NBTE is told automatically. Bank transfer: "
                     "download the slip, pay quoting the reference exactly as printed, then upload your receipt."},
            {"type": "info", "path_re": r"^/payments/\d+/", "target": ["#qa-proof"], "optional": True,
             "title": "Upload proof of payment",
             "text": "After a bank payment press *I have paid*, enter the amount, date, bank and teller "
                     "number, attach the receipt and press *Submit proof*."},
            {"type": "info", "title": "When does it count?",
             "text": "A payment counts once the bill shows *Confirmed*. Your receipt is then on the bill, "
                     "and the next step of your journey opens by itself."},
        ],
    },
    {
        "id": "portal-programmes",
        "group": "Onboarding",
        "title": "Add your first programme",
        "summary": "List the programmes you plan to offer and how many streams each will admit.",
        "audience": ("institution",), "host": "portal", "when": "onboarding", "start": "/application/",
        "pages": ("/application/",), "guides": ("onboarding",),
        "keywords": "programme programmes add streams nd hnd course master list professional body",
        "next": "portal-self-study",
        "steps": [
            {"type": "info", "path": "/application/", "target": ["#qa-prog-card"],
             "missing": "The programmes step is not open yet. It opens after NBTE approves your documents "
                        "and the institutional fee is confirmed.",
             "open": {"label": "Open Home", "url": "/dashboard/"},
             "title": "Programmes you plan to offer",
             "text": "List each programme your institution wants to run. Nothing goes to NBTE until the "
                     "Chief Executive submits the list."},
            {"type": "fill", "path": "/application/", "target": ["#qa-prog-q"], "optional": True,
             "title": "Find the programme",
             "text": "Start typing its name and pick it from NBTE's master list. The professional body "
                     "fills itself in. Not listed? Use *Not in the list?* just below."},
            {"type": "fill", "path": "/application/", "target": [".pp-add [name='streams']"], "optional": True,
             "title": "How many streams?", "text": "Enter the number of streams you plan to admit for this programme."},
            {"type": "info", "path": "/application/", "target": [".pp-add button[type='submit']"], "optional": True,
             "title": "Add it to your list",
             "text": "Press *Add programme*. Add as many as you need. The fee estimate below updates as you go."},
            {"type": "info", "path": "/application/", "target": ["#qa-prog-submit"], "optional": True,
             "title": "Submit the list",
             "text": "Only the Chief Executive can press *Submit programmes to NBTE*. NBTE clears each "
                     "programme, then the programme fee appears under Payments."},
        ],
    },
    {
        "id": "portal-self-study",
        "group": "Programmes and QA requests",
        "title": "Fill your self-study questionnaire",
        "summary": "Answer the self-study (SSQ) for each programme and send it to NBTE.",
        "audience": ("institution",), "host": "portal", "start": "/application/", "start_by_when": {"approved": "/qa-requests/"},
        "pages": ("/application/", "/qa-requests/"), "guides": ("onboarding", "qa-requests"),
        "keywords": "self-study self study ssq questionnaire sections evidence answers submit department",
        "steps": [
            {"type": "info", "path_re": r"^/(application|qa-requests(/\d+)?)/$",
             "target": ["#qa-ssq", "section[aria-labelledby='rq-ssq']", "#qa-req-rows"],
             "missing": "No self-study is open yet. One questionnaire opens per programme after NBTE "
                        "confirms the programme fee (or the QA fee, for a QA request).",
             "open": {"label": "Open Home", "url": "/dashboard/"},
             "title": "One self-study per programme",
             "text": "Each programme has its own questionnaire. For a QA request, open the request first: "
                     "its self-studies are listed on it."},
            {"type": "click", "path_re": r"^/(application|qa-requests(/\d+)?)/$",
             "target": ["#qa-ssq a[href*='/ssq/']", "section[aria-labelledby='rq-ssq'] a[href*='/ssq/']",
                        "#qa-req-rows a[href*='/qa-requests/']"],
             "optional": True, "title": "Open it", "text": "Click a programme's self-study (or the request it belongs to)."},
            {"type": "click", "path_re": r"^/qa-requests/\d+/$", "target": ["section[aria-labelledby='rq-ssq'] a[href*='/ssq/']"],
             "optional": True, "title": "Open a self-study", "text": "Click the questionnaire for one of the programmes."},
            {"type": "info", "path_re": r"/ssq/\d+/", "target": [".qa-ssq__nav", "#qa-onboarding-ssq h2[id^='ssq-sec-']"],
             "optional": True, "title": "Work section by section",
             "text": "Answer every required question. Each section shows how many required answers are still missing."},
            {"type": "info", "path_re": r"/ssq/\d+/", "target": ["#ssq-section", "#qa-onboarding-ssq form"],
             "optional": True, "title": "Answer and save",
             "text": "Type your answers and attach evidence where it is asked for. Save as often as you "
                     "like: nothing goes to NBTE until you submit."},
            {"type": "info", "path_re": r"/ssq/\d+/", "target": ["#ssq-submit", "#lj-resubmit"], "optional": True,
             "title": "Send it to NBTE",
             "text": "When no required answer is missing, tick the declaration and submit. If NBTE sends "
                     "one back, the reason is shown and you correct only that questionnaire."},
            {"type": "info", "title": "After you submit",
             "text": "NBTE officers review each self-study. When all are accepted, NBTE clears you for "
                     "quality assurance and tells you the next step."},
        ],
    },
    {
        "id": "portal-qa-request",
        "group": "Programmes and QA requests",
        "title": "Request accreditation or a resource inspection",
        "summary": "Start a QA request: choose the type of QA and the programmes.",
        "audience": ("institution",), "host": "portal", "when": "approved", "start": "/qa-requests/",
        "pages": ("/qa-requests/", "/programmes/"), "guides": ("qa-requests",),
        "keywords": "qa request accreditation re-accreditation resource inspection sop-02 new request",
        "next": "portal-case-progress",
        "steps": [
            {"type": "info", "path": "/qa-requests/", "target": ["#qa-main .qa-page-head", "#qa-main h1"],
             "title": "QA requests",
             "text": "This is where you ask NBTE for a resource inspection, accreditation or "
                     "re-accreditation of your programmes."},
            {"type": "click", "path": "/qa-requests/", "target": ["#qa-main a.btn-primary[href$='/qa-requests/new/']"],
             "title": "Start a new request", "text": "Click *New QA request*."},
            {"type": "fill", "path": "/qa-requests/new/", "target": ["section[aria-labelledby='nq-type']"],
             "title": "1. Choose the QA type",
             "text": "Pick what you want NBTE to do. A type you cannot choose yet shows the reason."},
            {"type": "info", "path": "/qa-requests/new/", "target": ["#qa-new-programmes"],
             "title": "2. Tick the programmes",
             "text": "Tick each programme and set its streams. Only programmes that qualify can be ticked; "
                     "the reason is shown for the others."},
            {"type": "info", "path": "/qa-requests/new/", "target": ["#qa-fee-estimate"], "optional": True,
             "title": "See the fee first", "text": "The estimate updates as you tick programmes, before you commit to anything."},
            {"type": "info", "path": "/qa-requests/new/", "target": ["#qa-new-programmes button[type='submit']"],
             "optional": True, "title": "Save, then submit",
             "text": "This saves a draft. The Chief Executive then opens the request, ticks the "
                     "declaration and submits it to NBTE."},
            {"type": "info", "title": "What happens next",
             "text": "NBTE clears the request and bills the QA fee. Once Finance confirms the payment, a "
                     "self-study questionnaire opens for each programme."},
        ],
    },
    {
        "id": "portal-case-progress",
        "group": "Programmes and QA requests",
        "title": "Follow your request to the decision",
        "summary": "Where to watch the visit, the report and NBTE's decision.",
        "audience": ("institution",), "host": "portal", "when": "approved", "start": "/qa-requests/",
        "pages": ("/qa-requests/", "/inspections/", "/letters/"), "guides": ("qa-requests",),
        "keywords": "status progress visit inspection decision letter sop-03 stages case team report board approval",
        "steps": [
            {"type": "info", "path": "/qa-requests/", "target": ["#qa-req-rows"], "title": "Your requests",
             "text": "Each request shows where it stands. Open one to follow it."},
            {"type": "click", "path": "/qa-requests/", "target": ["#qa-req-rows a[href*='/qa-requests/']"],
             "missing": "You have no QA request yet. Start one with *New QA request*.", "skippable": True,
             "open": {"label": "New QA request", "url": "/qa-requests/new/"},
             "title": "Open a request", "text": "Click a request to open it."},
            {"type": "info", "path_re": r"^/qa-requests/\d+/$", "target": [".lj--compact", "#qa-request .qa-page-head"],
             "title": "This request, step by step",
             "text": "Inside NBTE your request becomes a case that passes 17 stages: costing, the visit "
                     "team, the visit, the report, committee review and final approval. You see it here in plain steps."},
            {"type": "info", "path_re": r"^/qa-requests/\d+/$", "target": ["section[aria-labelledby='rq-ssq']"],
             "optional": True, "title": "Self-study questionnaires",
             "text": "One per programme. NBTE reviews them before the visit is planned."},
            {"type": "info", "path_re": r"^/qa-requests/\d+/$", "target": ["section[aria-labelledby='rq-q']"],
             "optional": True, "title": "Queries from NBTE",
             "text": "If NBTE needs more information it asks here. Answer on the portal so the review can continue."},
            {"type": "navigate", "url": "/inspections/"},
            {"type": "info", "path": "/inspections/", "target": ["#insp-rows", "#qa-main h1"], "title": "Inspections",
             "text": "When NBTE schedules the visit, the date appears here and you are notified. After the "
                     "visit NBTE writes its report and the decision goes through its committees."},
            {"type": "info", "target": _nav("/letters/"), "via": MENU, "title": "Your decision letter",
             "text": "The outcome is issued under *Letters*, and each programme's status and expiry date "
                     "update under *Programmes*."},
        ],
    },
    # ---- Resource person ----
    {
        "id": "rp-welcome",
        "group": "Getting started",
        "title": "A quick look around",
        "summary": "Invitations, engagements, your service history and your profile.",
        "audience": ("resource_person",), "host": "portal", "start": "/dashboard/", "pages": ("/dashboard/",),
        "keywords": "start welcome tour resource person", "auto": True, "next": "rp-engagements",
        "steps": [
            {"type": "info", "title": "Welcome",
             "text": "This portal is where NBTE shares the quality assurance visits you are invited to: "
                     "dates, programmes and what to submit. Here is where everything is.",
             "next_label": "Show me around", "skip_label": "Not now"},
            {"type": "info", "target": _nav("/visit-invitations/"), "via": MENU, "optional": True,
             "title": "Invitations", "text": "New invitations to a visit arrive here. Accept or decline so NBTE can finalise the team."},
            {"type": "info", "target": _nav("/engagements/"), "via": MENU, "title": "Engagements",
             "text": "The visits and SSQ reviews NBTE has mapped you to. After a visit you write your findings here."},
            {"type": "info", "target": _nav("/service-history/"), "via": MENU, "title": "History",
             "text": "A record of every engagement you have completed for NBTE."},
            {"type": "info", "target": _nav("/profile/"), "via": MENU, "title": "Profile",
             "text": "Keep your details up to date. NBTE uses them when choosing visit teams."},
            {"type": "info", "target": LAUNCHER, "title": "Stuck? Press Guide me",
             "text": "Pick a guide and I will point at each step on the real pages."},
        ],
    },
    {
        "id": "rp-engagements",
        "group": "Visits and engagements",
        "title": "Your engagements and service history",
        "summary": "Find a visit you are on, write your findings, and see past service.",
        "audience": ("resource_person",), "host": "portal", "start": "/engagements/",
        "pages": ("/engagements/", "/service-history/"), "keywords": "engagement visit findings report history service duty",
        "steps": [
            {"type": "info", "path": "/engagements/", "target": ["#qa-main .qa-page-head"], "title": "My engagements",
             "text": "Every QA visit and SSQ review NBTE has mapped you to, with the institution, programme and your duty."},
            {"type": "info", "path": "/engagements/", "target": ["#qa-main form.qa-toolbar"], "title": "Find one quickly",
             "text": "Search by institution or programme, or filter by status, duty, and past or upcoming."},
            {"type": "info", "path": "/engagements/", "target": ["#rp-rows"], "title": "Your list",
             "text": "Each row is one engagement. After a visit, write your findings for the programme on "
                     "its row; the Team Lead sees them while writing the post-visit report."},
            {"type": "navigate", "url": "/service-history/"},
            {"type": "info", "path": "/service-history/", "target": ["#rp-rows", "#qa-main h1"], "title": "My service history",
             "text": "Completed engagements stay here as your permanent record with NBTE."},
        ],
    },
    # ---- Professional body ----
    {
        "id": "pb-welcome",
        "group": "Getting started",
        "title": "A quick look around",
        "summary": "Programmes under your body, visits, reports and your people.",
        "audience": ("professional_body",), "host": "portal", "start": "/dashboard/", "pages": ("/dashboard/", "/pb/"),
        "keywords": "start welcome tour professional body council", "auto": True, "next": "pb-report",
        "steps": [
            {"type": "info", "title": "Welcome",
             "text": "This is your body's view of NBTE quality assurance: every programme under your body, "
                     "at every institution, and where each one stands.",
             "next_label": "Show me around", "skip_label": "Not now"},
            {"type": "info", "path_re": PB_HOME, "target": [".pb-stats"], "optional": True, "title": "At a glance",
             "text": "Programmes and institutions under your body, visits scheduled, and letters NBTE is waiting for from you."},
            {"type": "info", "path_re": PB_HOME, "target": ["section[aria-labelledby='pb-list-h']"],
             "title": "Programmes under your body",
             "text": "Search, or filter by stage and level. You see the stage of each programme, never the "
                     "self-study content. Open a programme to upload your letter."},
            {"type": "info", "path_re": PB_HOME, "target": ["section[aria-labelledby='pb-visits-h']"], "optional": True,
             "title": "Upcoming visits", "text": "Visits to programmes under your body. You are notified and may send a representative."},
            {"type": "info", "path_re": PB_HOME, "target": [".pb-tabs"], "optional": True, "title": "Your sections",
             "text": "*Institutions* and *Visits* show where your programmes run and when NBTE visits. "
                     "*Reports* is where you write and send your body's report. *People* are those who act for your body."},
            {"type": "info", "target": LAUNCHER, "title": "Stuck? Press Guide me",
             "text": "Pick a guide and I will point at each step on the real pages."},
        ],
    },
    {
        "id": "pb-report",
        "group": "Visits and reports",
        "title": "Send your body's visit report",
        "summary": "Assign a representative, then write and send the report to NBTE.",
        "audience": ("professional_body",), "host": "portal", "start": "/pb/reports/",
        "pages": ("/pb/visits/", "/pb/reports/"), "keywords": "report visit representative assign assessor send letter",
        "steps": [
            {"type": "info", "path": "/pb/reports/", "target": ["#qa-main .qa-page-head", "#qa-main h1"],
             "title": "Your body's visit reports",
             "text": "After an NBTE visit to a programme under your body, your report is written and sent from here."},
            {"type": "info", "path": "/pb/reports/", "target": [".pb-tabs a[href$='/pb/visits/']", ".pb-tabs"], "optional": True,
             "title": "First, the visit",
             "text": "Under *Visits* you see each scheduled visit and assign the representative who attends "
                     "for your body. They join NBTE's visit team and are notified."},
            {"type": "info", "path": "/pb/reports/", "target": ["#qa-main a[href$='/pb/reports/new/']"], "optional": True,
             "title": "Start a report", "text": "After the visit, start your body's report for that programme here."},
            {"type": "info", "path": "/pb/reports/", "target": ["#qa-main .qa-card", "#qa-main .qa-portal-page"],
             "title": "Draft, review, send",
             "text": "A report is drafted, reviewed inside your body, then sent. NBTE and the institution "
                     "see it once it is sent."},
        ],
    },
]

# ---------------------------------------------------------------------------
# NBTE staff (QA workspace)
# ---------------------------------------------------------------------------
STAFF_TOURS = [
    {
        "id": "staff-qa-home",
        "group": "QA: getting started",
        "title": "QA home: a quick look around",
        "summary": "Your work, what you can see, and where the guides are.",
        "audience": ("staff",), "host": "staff", "start": "/qa/", "pages": ("/qa/",), "auto": True,
        "keywords": "start welcome home qa dashboard tour", "next": "staff-my-work",
        "steps": [
            {"type": "info", "title": "Welcome to the QA workspace",
             "text": "Onboarding (SOP-01), QA requests (SOP-02) and accreditation cases (SOP-03) are all "
                     "handled here. A one-minute look around?",
             "next_label": "Show me around", "skip_label": "Not now"},
            {"type": "info", "path": "/qa/", "target": ["#your-work"], "optional": True, "title": "Your work",
             "text": "Everything waiting for *you*, most urgent first. Each row has one button that opens "
                     "the exact place to act."},
            {"type": "info", "path": "/qa/", "target": ["#qh-my_work-h"], "closest": "section, .ux-card", "optional": True,
             "title": "Assigned to me", "text": "Work a director or the system has assigned to you by name."},
            {"type": "info", "path": "/qa/", "target": ["#qh-decisions-h"], "closest": "section, .ux-card", "optional": True,
             "title": "Waiting for your confirmation",
             "text": "Sensitive changes need a second person. Requests waiting for you to confirm or reject are here."},
            {"type": "info", "target": [".app-sidebar .app-nav"], "via": STAFF_MENU, "optional": True, "title": "The QA menu",
             "text": "Institutions, applications, QA requests, cases, finance and settings. You only see what your role allows."},
            {"type": "info", "target": _staff_nav("/qa/guides/"), "via": STAFF_MENU, "optional": True, "title": "Guides",
             "text": "Short written guides for each team, and these live walk-throughs."},
            {"type": "info", "target": LAUNCHER, "title": "Stuck? Press Guide me",
             "text": "Pick a task and I will take you to the page and point at each step."},
        ],
    },
    {
        "id": "staff-my-work",
        "group": "QA: getting started",
        "title": "My work: what is waiting for you",
        "summary": "Your next item, everything assigned to you, and handing work back.",
        "audience": ("staff",), "host": "staff", "start": "/qa/my-work/", "pages": ("/qa/my-work/", "/qa/"),
        "keywords": "my work assigned queue tasks return hand back temporary access", "next": "staff-doc-review",
        "steps": [
            {"type": "info", "path": "/qa/my-work/", "target": ["#mw-next-h"], "closest": "section, .ux-card, .mw-next",
             "optional": True, "title": "Start here",
             "text": "Your most urgent item, with one button that opens it."},
            {"type": "info", "path": "/qa/my-work/", "target": ["#your-work"], "optional": True,
             "title": "Everything waiting for you",
             "text": "All your open work in one list: cases at your stage, documents to analyse, SSQs to review, complaints."},
            {"type": "info", "path": "/qa/my-work/", "target": ["#mw-h"], "closest": "section, .ux-card",
             "title": "Assigned to you",
             "missing": "Nothing is assigned to you by name at the moment.", "skippable": True,
             "text": "Grouped by kind of work. *Open* takes you to the item. If you cannot do one, hand it "
                     "back with a reason so it can be reassigned."},
            {"type": "info", "path": "/qa/my-work/", "target": ["#mw-g"], "closest": "section, .ux-card", "optional": True,
             "title": "Temporary access", "text": "Access you were given for a limited time, and when it ends."},
            {"type": "info", "path": "/qa/my-work/", "target": ["#mw-l"], "closest": "section, .ux-card", "optional": True,
             "title": "Who sees what", "text": "Check who else can see an item before you share anything about it."},
        ],
    },
    {
        "id": "staff-doc-review",
        "needs": "applications",
        "group": "QA: onboarding (SOP-01)",
        "title": "Review an onboarding document",
        "summary": "Open the file, recommend, and (directors) approve or return it.",
        "audience": ("staff",), "host": "staff", "start": "/qa/applications/documents/",
        "pages": ("/qa/applications/",), "guides": ("new-institutions", "documents-access"),
        "keywords": "document review analyse approve return onboarding sop-01 recommend director officer",
        "next": "staff-doc-chain",
        "steps": [
            {"type": "info", "path": "/qa/applications/documents/", "target": ["#qa-dr-q-h"], "closest": "section",
             "title": "Documents for your department",
             "missing": "This queue is only for staff who analyse or decide onboarding documents.",
             "text": "Each onboarding document goes to the department responsible for it. Yours are listed "
                     "here, most urgent first. Use the pills to filter by status."},
            {"type": "info", "path": "/qa/applications/documents/", "target": ["#dq-how-h"], "closest": "section", "optional": True,
             "title": "How a document moves",
             "text": "The director assigns an officer, the officer analyses and recommends, the director "
                     "approves or returns. Only a returned document goes back to the institution."},
            {"type": "click", "path": "/qa/applications/documents/",
             "target": [".dq-queue a.btn-primary[href*='/qa/applications/documents/']", ".dq-queue a[href*='/qa/applications/documents/']"],
             "missing": "No document is waiting for you right now.",
             "title": "Open a document", "text": "Click the button on a row to open that document."},
            {"type": "info", "path_re": r"^/qa/applications/documents/\d+/", "target": ["#qa-dr-doc-h"], "closest": "section",
             "title": "Open the file first",
             "text": "Preview the uploaded file before anything else. The decision buttons stay locked until you have opened it."},
            {"type": "info", "path_re": r"^/qa/applications/documents/\d+/", "target": ["#qa-dr-assign-h"], "closest": "section",
             "optional": True, "title": "Director: assign an officer",
             "text": "Accept the suggested officer or pick someone else. They are notified at once."},
            {"type": "info", "path_re": r"^/qa/applications/documents/\d+/",
             "target": ["form[action*='/recommend/']", "form[action*='/decide/']"], "optional": True,
             "title": "Recommend or decide",
             "text": "Officers write a recommendation and press *Send to director*. Directors *Approve*, "
                     "*Return to the institution* with a clear reason, or send it back to the officer."},
            {"type": "info", "title": "What the institution sees",
             "text": "An approved document turns green on their checklist. A returned one comes back alone, "
                     "with your reason. When every department has approved, the institutional fee is billed."},
        ],
    },
    {
        "id": "staff-ssq-queue",
        "needs": "qa_requests",
        "group": "QA: requests and cases",
        "title": "Review a self-study (SSQ)",
        "summary": "The SSQ review queue: assign, assess, approve or return.",
        "audience": ("staff",), "host": "staff", "start": "/qa/requests/ssq/", "pages": ("/qa/requests/",),
        "keywords": "ssq self-study review queue assess approve return query officer sop-02", "next": "staff-case-board",
        "steps": [
            {"type": "info", "path": "/qa/requests/ssq/", "target": ["#qa-panel h1"], "closest": "header, .qa-hero, .ux-hero",
             "title": "SSQ review queue",
             "text": "Self-study questionnaires institutions have submitted. One per programme on a QA request."},
            {"type": "info", "path": "/qa/requests/ssq/", "target": ["#qa-panel a.btn[href='?status=unassigned']"],
             "optional": True, "title": "Needs an officer",
             "text": "Directors start here: SSQs nobody is assessing yet. *Overdue* shows the ones past their time."},
            {"type": "info", "path": "/qa/requests/ssq/", "target": ["#qa-panel form.qa-toolbar"], "optional": True,
             "title": "Narrow the list", "text": "Search, or filter by QA type and level."},
            {"type": "click", "path": "/qa/requests/ssq/", "target": ["#qa-rows a[href*='/qa/requests/ssq/']"],
             "missing": "The queue is empty with these filters. Try *Needs an officer* or clear the filters.",
             "title": "Open an SSQ", "text": "Click a row to open the questionnaire."},
            {"type": "info", "path_re": r"^/qa/requests/ssq/\d+/", "target": ["#ssq-who-h"], "closest": "section", "optional": True,
             "title": "Who is handling it", "text": "The officer assessing it and the approver. Directors assign or change the officer here."},
            {"type": "info", "path_re": r"^/qa/requests/ssq/\d+/", "target": ["#ssq-assess-h"], "closest": "section", "optional": True,
             "title": "Step 2: the assessment",
             "text": "The officer reads every section and the evidence, then submits an assessment: "
                     "recommend approval, or recommend a return with a note."},
            {"type": "info", "path_re": r"^/qa/requests/ssq/\d+/", "target": ["#rev-h"], "closest": "section", "optional": True,
             "title": "Step 3: approve or return",
             "text": "The approver accepts the SSQ or returns it. A return needs a remark, and the "
                     "institution sees that remark."},
            {"type": "info", "path_re": r"^/qa/requests/ssq/\d+/", "target": ["#rev-q"], "closest": "section", "optional": True,
             "title": "Queries", "text": "Need one more fact? Raise a query instead of returning the whole SSQ."},
        ],
    },
    {
        "id": "staff-case-board",
        "needs": "qa_cases",
        "group": "QA: requests and cases",
        "title": "The case board and moving a case",
        "summary": "Find a case, see its 17 SOP-03 stages, and pass it to the next stage.",
        "audience": ("staff",), "host": "staff", "start": "/qa/cases/", "pages": ("/qa/cases/",),
        "keywords": "case board sop-03 stage move approve forward return cost visit team report decision",
        "next": "staff-case-stages",
        "steps": [
            {"type": "info", "path": "/qa/cases/", "target": ["#case-board"],
             "title": "Every case, by stage",
             "text": "A case opens when a QA request is cleared for a visit. It passes 17 stages: opening, "
                     "costing, visit and funds, post-visit report, committee review, decision and final approval."},
            {"type": "info", "path": "/qa/cases/", "target": ["#qa-panel a.btn[href$='/qa/cases/my-stages/']"], "optional": True,
             "title": "My stage work", "text": "Only the cases sitting at a stage *you* hold."},
            {"type": "click", "path": "/qa/cases/", "target": ["#case-board a[href*='/qa/cases/']"],
             "missing": "No case matches. Clear the filters, or there are no active cases yet.",
             "title": "Open a case", "text": "Click a case card to open it."},
            {"type": "info", "path_re": r"^/qa/cases/\d+/", "target": [".c3-prog"], "title": "Where the case is",
             "text": "All the stages in order. Done stages are ticked; the current one is highlighted with who holds it."},
            {"type": "info", "path_re": r"^/qa/cases/\d+/", "target": [".c3-turn"], "optional": True, "title": "Your turn",
             "text": "When a stage is waiting for you, this banner says what to do and opens the right section."},
            {"type": "info", "path_re": r"^/qa/cases/\d+/", "target": [".c3-tabs"], "title": "Case sections",
             "text": "Cost, personnel, visit, reports, decisions and letters each have a section. A dot marks one that needs work."},
            {"type": "info", "path_re": r"^/qa/cases/\d+/", "target": [".c3-stage.is-mine", ".c3-stage", ".c3-turn", "#c3-jr-h"],
             "closest": "section, .c3-stage, .c3-turn", "optional": True,
             "title": "Moving the case on",
             "text": "When a stage is yours it is marked *Your turn*. Do the work listed under *What to do*, "
                     "then press the green button: the case moves to the next stage and its holder is "
                     "notified. *Return* sends it back with a remark."},
            {"type": "info", "title": "Nobody skips a stage",
             "text": "Only the holder of the current stage can pass it on. If a case is stuck, use *Remind* "
                     "on the stage to nudge the person holding it."},
        ],
    },
]


def _extra(module, name):
    """The tours of a sibling registry. A broken one is logged and left out: a guide must never break a page."""
    try:
        mod = __import__(f"{__package__}.{module}", fromlist=[name])
        return list(getattr(mod, name))
    except Exception:
        logger.exception("guided tours: could not load %s.%s", module, name)
        return []


def _gather():
    rows, ids = [], set()
    internal = _extra("tours_internal", "INTERNAL_TOURS") + _extra("tours_internal_self", "INTERNAL_SELF_TOURS")
    qa_staff = (STAFF_TOURS + _extra("tours_qa_staff", "QA_STAFF_TOURS")
                + _extra("tours_qa_staff_b", "QA_STAFF_B_TOURS"))
    portal = PORTAL_TOURS + _extra("tours_portal_more", "PORTAL_MORE_TOURS") + _extra("tours_people", "PEOPLE_TOURS")
    for t in internal:
        t.setdefault("area", "main")
    for t in qa_staff:
        t.setdefault("area", "qa")
    for t in portal + internal + qa_staff:
        if t["id"] in ids:
            logger.error("guided tours: duplicate id %s ignored", t["id"])
            continue
        ids.add(t["id"])
        rows.append(t)
    return rows


TOURS = _gather()
BY_ID = {t["id"]: t for t in TOURS}
VERSION = hashlib.sha1(json.dumps(TOURS, sort_keys=True, default=list).encode()).hexdigest()[:12]

STATE_KEY = "tours"  # User.preferences["tours"] = {"done": [...], "dismissed": [...]}
STATES = ("done", "dismissed")
DATA_URL = "/me/tours/"


def _when(institution):
    status = getattr(institution, "status", "") if institution is not None else ""
    if status == "approved":
        return "approved"
    if status in ("proposed", "under_review"):
        return "onboarding"
    return ""


def _steps(t):
    return sum(1 for s in t["steps"] if s["type"] != "navigate")


def tours_for(user, *, host, institution=None, qa=True, can=None):
    """The tours this person may run on this host, most useful first.

    ``qa=False`` leaves out the QA workspace tours (staff who cannot open /qa/).
    ``can`` is the person's QA menu (the ``qa_nav`` dict): a tour with ``needs`` is
    left out when that part of the menu is closed to them. None = do not check."""
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    audience = getattr(user, "user_type", "staff") or "staff"
    when = _when(institution)
    out = []
    for t in TOURS:
        if t["host"] != host or audience not in t["audience"]:
            continue
        if t.get("when") and when and t["when"] != when:
            continue
        if not qa and t.get("area") == "qa":
            continue
        if can is not None and t.get("needs") and not can.get(t["needs"]):
            continue
        t = copy.deepcopy(t)
        t["start"] = t.pop("start_by_when", {}).get(when, t["start"])
        nxt = t.pop("next_by_when", {}).get(when)
        if nxt:
            t["next"] = nxt
        t["audience"] = list(t["audience"])
        t["pages"] = list(t.get("pages", ()))
        t["guides"] = list(t.get("guides", ()))
        out.append(t)
    ids = {t["id"] for t in out}
    for t in out:
        if t.get("next") and t["next"] not in ids:
            t.pop("next")
    return out


def public_tours(host="portal", audience="institution", guide=None, area=None):
    """Tours to advertise on help pages (title, summary, start link)."""
    rows = []
    for t in TOURS:
        if t["host"] != host or audience not in t["audience"]:
            continue
        if guide and guide not in t.get("guides", ()):
            continue
        if area and t.get("area") != area:
            continue
        rows.append({"id": t["id"], "title": t["title"], "summary": t["summary"], "role": t.get("role", ""),
                     "group": t.get("group", ""), "steps": _steps(t), "url": f"{t['start']}?tour={t['id']}"})
    return rows


def seen(user):
    """{"done": [...], "dismissed": [...]} from the user's saved preferences."""
    prefs = getattr(user, "preferences", None) or {}
    state = prefs.get(STATE_KEY) if isinstance(prefs, dict) else None
    state = state if isinstance(state, dict) else {}
    return {k: [i for i in state.get(k, []) if isinstance(i, str)][:400] for k in STATES}


def record(user, tour_id, state):
    """Remember that the user finished or dismissed a tour. Returns the new state."""
    if tour_id not in BY_ID or state not in STATES:
        raise ValueError("Unknown tour or state.")
    current = seen(user)
    for k in STATES:
        if tour_id in current[k]:
            current[k].remove(tour_id)
    current[state].append(tour_id)
    prefs = dict(user.preferences or {})
    prefs[STATE_KEY] = current
    user.preferences = prefs
    user.save(update_fields=["preferences"])
    return current


def reset(user):
    prefs = dict(user.preferences or {})
    prefs.pop(STATE_KEY, None)
    user.preferences = prefs
    user.save(update_fields=["preferences"])
    return seen(user)


def qa_menu(request):
    """The person's QA menu: what the QA side menu shows them (the ``qa_nav`` context). {} when unknown."""
    try:
        from .context_processors import qa_nav

        return qa_nav(request)["qa_nav"] or {}
    except Exception:  # the page must never break on the guide
        logger.exception("guided tours: QA access check failed")
        return {}


def can_use_qa(request):
    """Does this member of staff have anything in the QA workspace? (Same test as the workspace switch.)"""
    return bool(qa_menu(request).get("show"))


def tours_for_request(request, *, host, institution=None):
    """``tours_for`` with the request's own context filled in (institution on the portal, QA access for staff)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return []
    if host == "portal" and institution is None and getattr(user, "user_type", "") == "institution":
        try:
            from .portal_views import _institution_for

            institution = _institution_for(user)
        except Exception:  # the page must never break on the guide
            institution = None
    qa, can = True, None
    if host == "staff" and getattr(user, "user_type", "staff") == "staff":
        can = qa_menu(request)
        qa = bool(can.get("show"))
    return tours_for(user, host=host, institution=institution, qa=qa, can=can)


def boot(request, *, host, institution=None):
    """The little tour.js needs on every page, as a JSON-able dict (or None).

    The tours themselves are fetched from ``dataUrl`` and kept by the browser."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    tours = tours_for_request(request, host=host, institution=institution)
    if not tours:
        return None
    # Where the portal is mounted by path (Render: /qa-portal/), tour data keeps
    # its plain paths; the browser puts this in front of every link it follows.
    try:
        from .hosts import portal_mount

        base = portal_mount(request) if host == "portal" else ""
    except Exception:  # a guide must never break a page
        base = ""
    state = seen(user)
    touched = set(state["done"]) | set(state["dismissed"])
    impersonating = bool(getattr(request, "is_impersonating", False))
    auto = ""
    if not impersonating:
        # One welcome per place: offer the one that starts on this very page (staff have a
        # main-system welcome and a QA-workspace welcome), else the first not yet seen.
        here = (request.path or "/").rstrip("/") + "/"
        if base and (here == base + "/" or here.startswith(base + "/")):
            here = here[len(base):] or "/"
        waiting = [t for t in tours if t.get("auto") and t["id"] not in touched]
        auto = next((t["id"] for t in waiting if t["start"] == here), "") or (waiting[0]["id"] if waiting else "")
    # The address is only a cache key: the view works the list out again from the signed-in user.
    key = hashlib.sha1("|".join([VERSION, str(user.pk), host] + [t["id"] + t["start"] + t.get("next", "")
                                                                 for t in tours]).encode()).hexdigest()[:14]
    help_url = "/how-to/" if host == "portal" else ("/qa/guides/" if can_use_qa(request) else "")
    return {"dataUrl": f"{base}{DATA_URL}?v={key}", "count": len(tours), "seen": state, "autostart": auto,
            "saveUrl": f"{base}{DATA_URL}", "canSave": not impersonating, "host": host, "base": base,
            "helpUrl": f"{base}{help_url}" if help_url else ""}


def data(request, *, host):
    """What GET /me/tours/ returns: this person's tours on this host."""
    return {"tours": tours_for_request(request, host=host), "version": VERSION}
