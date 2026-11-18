"""Guided tours: Institution portal (qa.* host), beyond the core tours in tours.py.

Plain data, gathered into ``qa_core.tours.TOURS``. The fields of a tour and
of a step are documented at the top of qa_core/tours.py.
"""
from .tour_lib import LAUNCHER, MENU, PB_HOME, STAFF_MENU, nav, staff_nav  # noqa: F401

HEAD = ["#qa-main .qa-page-head", "#qa-main h1"]  # the title block of a portal page
BILL = r"^/payments/\d+/$"
REQUEST = r"^/qa-requests/\d+/$"
QUERY = r"^/queries/\d+/$"
COMPLAINT = r"^/complaints/\d+/$"
PROGRAMME = r"^/programmes/\d+/$"
HOME = {"label": "Open Home", "url": "/dashboard/"}

PORTAL_MORE_TOURS = [
    # ------------------------------------------------------------------ Payments
    {
        "id": "portal-bank-transfer",
        "title": "Pay by bank transfer and upload proof",
        "summary": "Choose bank transfer, get the slip, pay, then press I have paid and upload your receipt.",
        "audience": ("institution",), "host": "portal", "group": "Payments",
        "start": "/payments/", "pages": ("/payments/",), "guides": ("payments",),
        "keywords": "bank transfer teller slip proof receipt upload i have paid deposit finance confirm",
        "next": "portal-letters",
        "steps": [
            {"type": "info", "path": "/payments/", "target": ["#qa-main .ux-stats", "section[aria-label='Bills']"],
             "title": "Start from your bills",
             "text": "*To pay* counts the bills waiting for you. *Being confirmed* are payments NBTE Finance is "
                     "still checking against the bank statement."},
            {"type": "click", "path": "/payments/", "target": ["#qa-pay-rows a.qa-rowlink"],
             "missing": "You have no bill yet. NBTE issues one when your application or request reaches a fee "
                        "stage; it appears here and on your Home page.",
             "open": HOME,
             "title": "Open the bill", "text": "Click the bill you want to pay. Bills still to pay show *Pay now*."},
            {"type": "info", "path_re": BILL, "target": ["section[aria-labelledby='qa-total-label']"],
             "missing": "This bill is already settled, so there is nothing to pay. Go back to Payments and open "
                        "one that shows *Pay now*.",
             "open": {"label": "Back to Payments", "url": "/payments/"},
             "title": "The amount and the reference",
             "text": "This is exactly what to pay. The *payment reference* identifies your bill: press it to "
                     "copy it, and quote it at the bank exactly as shown."},
            {"type": "fill", "path_re": BILL, "target": [".qa-methods"],
             "missing": "This bill cannot be paid now: it is already paid, being confirmed, cancelled or expired.",
             "open": {"label": "Back to Payments", "url": "/payments/"},
             "title": "Choose Bank transfer",
             "text": "Click *Bank transfer* here, then press *Next*. Remita and Paystack confirm by themselves; "
                     "a bank transfer needs your proof and a check by NBTE Finance."},
            {"type": "info", "path_re": BILL, "target": [".qa-paysteps"],
             "missing": "The bank steps show after you choose *Bank transfer* above. Choose it and I will carry on.",
             "skippable": True,
             "title": "Three steps, in order",
             "text": "1. Download your *payment slip*: it carries NBTE's bank account and your reference. "
                     "2. Pay at the bank or by transfer. 3. Come back and upload proof."},
            {"type": "click", "path_re": BILL, "target": [".qa-paystep--proof a[data-qa-open-proof]"], "optional": True,
             "title": "After paying, press I have paid",
             "text": "Click *I have paid* to open the proof form. Do this only after the money has left your account."},
            {"type": "info", "path_re": BILL, "target": ["#qa-proof"], "optional": True,
             "title": "Upload your proof",
             "text": "Enter the amount, the date, the bank, the teller or transfer number and the payer's name, "
                     "attach the receipt (PDF or photo), then press *Submit proof*."},
            {"type": "info", "path_re": BILL, "target": ["#qa-pay-status"], "optional": True,
             "title": "Then watch the status",
             "text": "The bill shows *Being confirmed* while Finance matches it to the bank statement. When it "
                     "turns *Confirmed*, your official receipt appears here and under *Letters*."},
        ],
    },
    # ------------------------------------------------------------------ Onboarding
    {
        "id": "portal-returned-document",
        "title": "Fix a document NBTE returned",
        "summary": "Find the returned document, read NBTE's reason and upload the corrected file.",
        "audience": ("institution",), "host": "portal", "group": "Onboarding",
        "start": "/documents/", "pages": ("/documents/",), "guides": ("onboarding",),
        "keywords": "returned document rejected correct re-upload reason remark resubmit red row new version",
        "steps": [
            {"type": "info", "path": "/documents/", "target": ["#qa-doc-summary"],
             "title": "Only that document comes back",
             "text": "When a department is not satisfied with a document, it returns that one document with a "
                     "reason. Everything else you sent stays where it is."},
            {"type": "info", "path": "/documents/", "target": ["#qa-doc-rows .qa-doc[data-state='returned']"],
             "missing": "No document is returned on this page right now. If NBTE returns one you are notified, "
                        "and its row turns red here. Use the *Status* filter to find it in a long list.",
             "skippable": True,
             "title": "The returned document", "text": "A returned document is marked *Returned* and shown in red."},
            {"type": "info", "path": "/documents/",
             "target": ["#qa-doc-rows .qa-doc[data-state='returned'] .qa-doc__remark"], "optional": True,
             "title": "Read NBTE's reason",
             "text": "The officer's remark says exactly what is wrong: an unclear scan, an expired certificate, "
                     "the wrong document. Correct that before you upload again."},
            {"type": "fill", "path": "/documents/",
             "target": ["#qa-doc-rows .qa-doc[data-state='returned'] .qa-upload__btn"], "optional": True,
             "title": "Upload the corrected file",
             "text": "Press *Re-upload* and choose the corrected file. It saves at once as a new version and "
                     "goes straight back to the same department."},
            {"type": "info", "path": "/documents/", "target": ["details.pp-versions"], "optional": True,
             "title": "Earlier versions are kept",
             "text": "Every file you uploaded stays on record here, so you and NBTE can see what changed."},
            {"type": "info", "title": "No need to resubmit",
             "text": "You do *not* submit the whole application again. The department reviews the new file and "
                     "approves it, or returns it once more with a reason. You are notified either way."},
        ],
    },
    # ------------------------------------------------------------------ Programmes and QA requests
    {
        "id": "portal-new-programme",
        "title": "Propose a new programme",
        "summary": "Add a programme you plan to run, then ask NBTE for a Resource Inspection.",
        "audience": ("institution",), "host": "portal", "when": "approved", "group": "Programmes and QA requests",
        "start": "/programmes/", "pages": ("/programmes/", "/qa-requests/new-programme/"), "guides": ("qa-requests",),
        "keywords": "new programme propose add course resource inspection approval to commence master list streams",
        "next": "portal-qa-request",
        "steps": [
            {"type": "info", "path": "/programmes/", "target": HEAD,
             "title": "Adding a programme after approval",
             "text": "Your institution is approved, so a new programme no longer goes through onboarding. "
                     "You propose it here, then NBTE inspects your resources before you may start it."},
            {"type": "click", "path": "/programmes/", "target": ["#qa-main a[href$='/qa-requests/new-programme/']"],
             "missing": "*Propose a new programme* is not available to you. It is for approved institutions, "
                        "and for the Head of ICT or Chief Executive account.",
             "open": HOME,
             "title": "Start here", "text": "Click *Propose a new programme*."},
            {"type": "info", "path": "/qa-requests/new-programme/", "target": ["#qa-propose-programme .pp-steps"],
             "title": "Three stages",
             "text": "1. Propose the programme (this page). 2. A *Resource Inspection* request: the fee, then "
                     "the self-study. 3. NBTE's visit and decision, which gives *approval to commence*."},
            {"type": "fill", "path": "/qa-requests/new-programme/", "target": ["#id_source"], "closest": ".qa-field",
             "title": "Pick the programme",
             "text": "Choose it from NBTE's master list. If it is not listed, choose the *not listed* option "
                     "and type its name and level; NBTE then reviews the name too."},
            {"type": "fill", "path": "/qa-requests/new-programme/", "target": ["#id_streams"], "closest": ".qa-field",
             "title": "How many streams?",
             "text": "Enter the number of streams you plan to admit. NBTE uses it to judge the resources you need."},
            {"type": "info", "path": "/qa-requests/new-programme/",
             "target": ["#qa-propose-programme form button[type='submit']"],
             "title": "Add and continue",
             "text": "This records the programme as *Proposed* and opens a new Resource Inspection request "
                     "with the programme already ticked. End this guide to press it."},
            {"type": "info", "title": "What happens next",
             "text": "Save the request, then the Chief Executive submits it. NBTE clears the new programme, bills "
                     "the fee, and a self-study opens. After the inspection you get the decision under *Letters*."},
        ],
    },
    {
        "id": "portal-programme-board",
        "title": "Your programmes: status and expiry",
        "summary": "Read each programme's status and expiry date, and start re-accreditation in time.",
        "audience": ("institution",), "host": "portal", "when": "approved", "group": "Programmes and QA requests",
        "start": "/programmes/", "pages": ("/programmes/",), "guides": ("qa-requests",),
        "keywords": "programme status board expiry expires countdown re-accreditation renew accredited interim denied letter",
        "next": "portal-new-programme",
        "steps": [
            {"type": "info", "path": "/programmes/", "target": HEAD, "title": "Programme status board",
             "text": "Every programme NBTE holds on record for your institution, with its accreditation status."},
            {"type": "info", "path": "/programmes/", "target": [".qa-prog-board .qa-prog-card"],
             "missing": "No programme is on NBTE's record for your institution yet. They appear here once NBTE "
                        "clears them.",
             "skippable": True,
             "title": "One card per programme",
             "text": "The tag shows the status and, where it applies, the date it expires. The facts below "
                     "give the level, streams and the last decision."},
            {"type": "info", "path": "/programmes/", "target": [".qa-prog-board .qa-countdown"], "optional": True,
             "title": "The expiry countdown",
             "text": "This counts down to the expiry date and tells you when the re-accreditation window "
                     "opens. Apply inside the window so the programme never lapses."},
            {"type": "info", "path": "/programmes/",
             "target": [".qa-prog-card__actions a.btn-primary", ".qa-prog-card__actions"], "optional": True,
             "title": "Renew, details, letter",
             "text": "When the window is open, a button here starts the re-accreditation request with the "
                     "programme chosen. *Details* shows its history; *Letter* downloads the decision letter."},
            {"type": "click", "path": "/programmes/", "target": [".qa-prog-card__actions a[href^='/programmes/']"],
             "optional": True, "title": "Open one", "text": "Click *Details* on a programme."},
            {"type": "info", "path_re": PROGRAMME, "target": ["section[aria-labelledby='pp-req']"], "optional": True,
             "title": "Its QA requests", "text": "Every request that included this programme, and how each ended."},
            {"type": "info", "path_re": PROGRAMME, "target": ["section[aria-labelledby='pp-tl']"], "optional": True,
             "title": "Its history", "text": "What happened to this programme, in order, from proposal to the latest decision."},
        ],
    },
    {
        "id": "portal-answer-query",
        "title": "Answer a query from NBTE",
        "summary": "Find a question NBTE asked about a self-study or request, and send your response.",
        "audience": ("institution",), "host": "portal", "group": "Programmes and QA requests",
        "start": "/queries/", "pages": ("/queries/", "/qa-requests/"), "guides": ("qa-requests", "onboarding"),
        "keywords": "query queries question answer respond response nbte asks self-study ssq clarification due date",
        "steps": [
            {"type": "info", "path": "/queries/", "target": HEAD, "title": "Queries",
             "text": "While reviewing a self-study or a request, an NBTE officer may need one more fact. "
                     "Instead of returning everything, they ask a question here."},
            {"type": "info", "path": "/queries/", "target": ["#qa-main form.qa-toolbar"], "optional": True,
             "title": "Find the open ones",
             "text": "Filter by *Status* to see those waiting for you, and by *Due* to see the most urgent. "
                     "The review waits until you answer."},
            {"type": "click", "path": "/queries/", "target": ["#qa-query-rows a.qa-rowlink"],
             "missing": "NBTE has not asked you anything. When it does, you are notified, and the query also "
                        "shows on the request it is about.",
             "open": {"label": "Open QA requests", "url": "/qa-requests/"},
             "title": "Open a query", "text": "Click the query you want to answer."},
            {"type": "info", "path_re": QUERY, "target": ["#qa-query section.qa-card"],
             "title": "What NBTE asks",
             "text": "Read the question carefully. It names the programme or section it is about, and the "
                     "date an answer is due."},
            {"type": "fill", "path_re": QUERY, "target": ["#qr-body"], "closest": ".qa-field", "optional": True,
             "title": "Write your answer",
             "text": "Answer the question directly and in full. You can add to your response later while "
                     "the query is still open."},
            {"type": "info", "path_re": QUERY, "target": ["#qr-file"], "closest": ".qa-field", "optional": True,
             "title": "Attach evidence",
             "text": "If the answer needs a document, a photo or a list, attach it here."},
            {"type": "info", "path_re": QUERY, "target": ["section[aria-labelledby='qr-h'] button[type='submit']"],
             "optional": True, "title": "Send it",
             "text": "*Send response* notifies NBTE's officer at once, and the review carries on. End this "
                     "guide to press it."},
        ],
    },
    {
        "id": "portal-cancel-refund",
        "title": "Cancel a request and follow a refund",
        "summary": "What comes back at each stage, how to cancel, and where to give your bank details.",
        "audience": ("institution",), "host": "portal", "role": "Chief Executive", "group": "Payments",
        "start": "/refund-policy/", "pages": ("/refund-policy/", "/refunds/", "/qa-requests/"),
        "guides": ("payments", "qa-requests"),
        "keywords": "cancel cancellation refund withdraw policy percent bank details money back request application",
        "steps": [
            {"type": "info", "path": "/refund-policy/", "target": ["section[aria-labelledby='pol-h']"],
             "title": "How much is refunded",
             "text": "The refund depends on how far NBTE's work has gone. The table gives the percentage at "
                     "each stage: the earlier you cancel, the more comes back."},
            {"type": "info", "path": "/refund-policy/", "target": ["section[aria-labelledby='how-h']"],
             "title": "How to cancel",
             "text": "Only the *Chief Executive* can cancel. A QA request is cancelled from its own page; an "
                     "onboarding application from the *Application* page."},
            {"type": "navigate", "url": "/qa-requests/"},
            {"type": "click", "path": "/qa-requests/", "target": ["#qa-req-rows a[href*='/qa-requests/']"],
             "missing": "You have no QA request to cancel.", "skippable": True,
             "title": "Open the request", "text": "Click the request you are thinking of cancelling."},
            {"type": "info", "path_re": REQUEST, "target": [".rf-cancel-card", ".rf-status", ".rf-ce-only"],
             "missing": "This request can no longer be cancelled here: NBTE has already decided it.",
             "skippable": True,
             "title": "Cancel, with the refund shown first",
             "text": "Open *Cancel request* to see what you would get back today. The next page asks for your "
                     "reason and bank details before anything is cancelled. Other accounts see a note instead."},
            {"type": "navigate", "url": "/refunds/"},
            {"type": "info", "path": "/refunds/", "target": ["#qa-refunds .qa-page-head", "#qa-refunds h1"],
             "title": "Follow your refunds",
             "text": "Each cancellation that earns a refund is listed here with its amount and status, from "
                     "*Awaiting approval* to *Paid*."},
            {"type": "info", "path": "/refunds/", "target": ["#qa-refunds .rf-card"], "optional": True,
             "title": "Bank details and progress",
             "text": "If NBTE is waiting for your bank details, enter them on the refund's card. NBTE Finance "
                     "then approves and pays, and you are notified at each step."},
        ],
    },
    # ------------------------------------------------------------------ Letters
    {
        "id": "portal-letters",
        "title": "Letters, receipts and checking they are genuine",
        "summary": "Download NBTE's letters and receipts, and verify any NBTE document by its QR code.",
        "audience": ("institution",), "host": "portal", "group": "Your institution",
        "start": "/letters/", "pages": ("/letters/", "/scan/"), "guides": ("payments", "starter"),
        "keywords": "letter letters receipt download verify qr code scan genuine provisional official copy decision",
        "steps": [
            {"type": "info", "path": "/letters/", "target": HEAD, "title": "Letters",
             "text": "Everything NBTE has issued to your institution is kept here: you do not need to wait "
                     "for a paper copy."},
            {"type": "info", "path": "/letters/", "target": ["section[aria-label='Letters and documents from NBTE']"],
             "title": "Letters and receipts",
             "text": "Registration, clearance and approval letters, and an official receipt for every "
                     "confirmed payment. Open one to read it, see its QR code and download it."},
            {"type": "info", "path": "/letters/",
             "target": ["section[aria-label='Letters and documents from NBTE'] a.qa-rowlink"], "optional": True,
             "title": "Provisional or official",
             "text": "A letter may first be issued as a *provisional* copy. The signed official copy replaces "
                     "it later on the same page, and you are notified."},
            {"type": "info", "path": "/letters/", "target": ["section[aria-label='Your outcome letters']"],
             "optional": True, "title": "Programme decision letters",
             "text": "The outcome of each accreditation. Keep the *Original* for your records and share the "
                     "watermarked *Duplicate*."},
            {"type": "navigate", "url": "/scan/"},
            {"type": "info", "path": "/scan/", "target": ["section[aria-label='Scanner']"],
             "title": "Check a document is genuine",
             "text": "Every NBTE letter and receipt carries a QR code. Point your camera at it, upload a "
                     "picture of it, or type the short code printed beside it."},
            {"type": "info", "path": "/scan/", "target": ["#scan-result"],
             "title": "The result",
             "text": "A genuine document shows what it is, who it was issued to and when. Anything forged, "
                     "altered or withdrawn is flagged here. Anyone can check, even without an account."},
        ],
    },
    # ------------------------------------------------------------------ Help and complaints
    {
        "id": "portal-complaint",
        "title": "Raise a complaint",
        "summary": "Tell NBTE about a problem, attach evidence and send it to the right desk.",
        "audience": ("institution",), "host": "portal", "group": "Help and complaints",
        "start": "/complaints/", "pages": ("/complaints/", "/help/"), "guides": ("starter",),
        "keywords": "complaint complain problem issue report raise help support delay not confirmed",
        "next": "portal-complaint-follow",
        "steps": [
            {"type": "info", "path": "/complaints/", "target": HEAD, "title": "Complaints",
             "text": "If something has gone wrong or is taking too long, raise it here. It is given a "
                     "reference and a responsible desk, and you can follow it to the end."},
            {"type": "click", "path": "/complaints/", "target": ["#qa-main a.btn-primary[href$='/complaints/new/']"],
             "optional": True, "title": "Start one", "text": "Click *Raise a complaint*."},
            {"type": "fill", "path": "/complaints/new/", "target": ["#cx-subject"], "closest": ".qa-field",
             "title": "Say it in one line",
             "text": "Type a short subject. As you type, help articles that may already answer you appear "
                     "just below."},
            {"type": "fill", "path": "/complaints/new/", "target": ["#qa-main form fieldset.qa-fieldset"],
             "title": "What is it about?",
             "text": "Pick the area: a payment, a document, a visit and so on. This decides which NBTE desk "
                     "receives it, and may ask one or two extra details."},
            {"type": "fill", "path": "/complaints/new/", "target": ["#cx-body"], "closest": ".qa-field",
             "title": "Describe what happened",
             "text": "Say what you expected, what happened instead, and quote any reference number."},
            {"type": "info", "path": "/complaints/new/", "target": ["#cx-files"], "closest": ".qa-field", "optional": True,
             "title": "Attach evidence",
             "text": "A receipt, a screenshot or a letter helps NBTE settle it faster."},
            {"type": "info", "path": "/complaints/new/", "target": ["#qa-main form button[type='submit']"],
             "title": "Send it",
             "text": "*Send the complaint* gives it a reference and notifies the desk. End this guide to press it."},
            {"type": "info", "title": "A quicker way",
             "text": "Bills, inspections, self-studies and your timeline each have a *Raise a complaint about "
                     "this* link. It opens this form already tied to that item."},
        ],
    },
    {
        "id": "portal-complaint-follow",
        "title": "Follow a complaint to the end",
        "summary": "Read NBTE's replies, answer, and confirm or reopen when it is resolved.",
        "audience": ("institution",), "host": "portal", "group": "Help and complaints",
        "start": "/complaints/", "pages": ("/complaints/",),
        "keywords": "complaint follow reply status progress resolved confirm reopen withdraw conversation",
        "steps": [
            {"type": "info", "path": "/complaints/", "target": ["section[aria-label='Your complaints']"],
             "title": "Your complaints",
             "text": "Each one shows its reference and where it stands. Search, or filter by status."},
            {"type": "click", "path": "/complaints/", "target": ["#cx-rows a.cx-rowlink", "#cx-rows a[href*='/complaints/']"],
             "missing": "You have not raised a complaint. Start one with *Raise a complaint*.",
             "open": {"label": "Raise a complaint", "url": "/complaints/new/"},
             "title": "Open one", "text": "Click a complaint to open it."},
            {"type": "info", "path_re": COMPLAINT, "target": ["section[aria-labelledby='cx-conv']"],
             "title": "The conversation",
             "text": "Your message and every reply from NBTE, in order. You are notified when NBTE answers."},
            {"type": "fill", "path_re": COMPLAINT, "target": ["#cx-reply"], "closest": "form", "optional": True,
             "title": "Reply or add information",
             "text": "Answer NBTE's questions here and attach files if needed, then press *Send*."},
            {"type": "info", "path_re": COMPLAINT, "target": ["section[aria-labelledby='cx-prog']"], "optional": True,
             "title": "Progress",
             "text": "The stages your complaint passes: received, with the responsible desk, resolved, closed."},
            {"type": "info", "path_re": COMPLAINT, "target": ["section[aria-labelledby='cx-yours']"], "optional": True,
             "title": "Your options",
             "text": "When NBTE marks it resolved, *confirm* that you are satisfied or *reopen* it and say "
                     "what is still wrong. You may also withdraw a complaint you no longer need."},
        ],
    },
    # ------------------------------------------------------------------ Your institution
    {
        "id": "portal-users",
        "title": "Manage who can sign in",
        "summary": "See your institution's users, invite a colleague, or ask NBTE to change them.",
        "audience": ("institution",), "host": "portal", "role": "Head of ICT", "group": "Your institution",
        "start": "/institution/", "pages": ("/institution/",), "guides": ("starter",),
        "keywords": "users invite colleague account remove sign in head of ict team access primary contact",
        "next": "portal-profile",
        "steps": [
            {"type": "info", "path": "/institution/", "target": ["#qa-users section.pp-team", "#qa-users"],
             "title": "Your institution's users",
             "text": "Everyone who can sign in for your institution. The Head of ICT manages this list."},
            {"type": "info", "path": "/institution/", "target": ["#qa-users .pp-team__list"], "optional": True,
             "title": "Who they are",
             "text": "Each person's name and role. *Invited* means they have not set a password yet; "
                     "*Primary contact* is the person NBTE writes to first."},
            {"type": "info", "path": "/institution/",
             "target": ["#qa-users .pp-team__invite summary", "#qa-users a[href*='type=user_invite']",
                        "#qa-users .pp-team__locked"],
             "missing": "Your account cannot change the users. Ask your Head of ICT.", "skippable": True,
             "title": "Add a colleague",
             "text": "Before your first application is submitted, *Invite a user* sends them a one-time link "
                     "to set a password. After that the list is locked, and you send NBTE a request instead."},
            {"type": "info", "path": "/institution/", "target": ["#qa-users a[href*='type=user_remove']"], "optional": True,
             "title": "Remove someone",
             "text": "When a colleague leaves, ask NBTE to remove their account so they can no longer sign in."},
            {"type": "info", "path": "/institution/",
             "target": ["#qa-main a[href$='/institution/change-requests/']"], "optional": True,
             "title": "Follow your requests",
             "text": "*Change requests* lists what you have asked NBTE to change and whether it was approved."},
            {"type": "info", "title": "The Chief Executive's account",
             "text": "The Chief Executive is not invited here. They activate their own account from the e-mail "
                     "NBTE sends to the address on your management team, and only they can sign and submit."},
        ],
    },
    {
        "id": "portal-profile",
        "title": "Update your profile and management team",
        "summary": "Correct your institution's details and replace a role holder after setup.",
        "audience": ("institution",), "host": "portal", "group": "Your institution",
        "start": "/institution/", "pages": ("/institution/",), "guides": ("starter",),
        "keywords": "profile address phone email edit change request management team replace rector registrar holder",
        "steps": [
            {"type": "info", "path": "/institution/",
             "target": ["#qa-profile-card", "#qa-profile-region section[aria-labelledby='pp-prof']", "#qa-profile-region"],
             "title": "Your institution's profile",
             "text": "The name, address and contacts NBTE holds for you. They are printed on your letters, so "
                     "keep them correct."},
            {"type": "info", "path": "/institution/",
             "target": ["#qa-profile-card form button[type='submit']", "#qa-main a[href*='type=profile']",
                        "#qa-main a[href$='/institution/change-requests/']"],
             "optional": True, "title": "Changing it",
             "text": "Before you submit your first application you edit and *Save profile* here. After that the "
                     "profile is protected: send NBTE a change request, and it updates when NBTE approves."},
            {"type": "click", "path": "/institution/", "target": ["#qa-main a.btn[href$='/institution/management/']"],
             "optional": True, "title": "Now the people", "text": "Click *Management team*."},
            {"type": "info", "path": "/institution/management/", "target": ["section[aria-labelledby='qa-roles-h']"],
             "title": "Who holds each role",
             "text": "The Chief Executive, Registrar, Head of ICT and the other roles NBTE needs, with the "
                     "current holder of each."},
            {"type": "info", "path": "/institution/management/",
             "target": ["section[aria-labelledby='qa-roles-h'] a[href*='?role=']", "#qa-management a[href*='type=management']"],
             "optional": True, "title": "When someone new takes over",
             "text": "Press *Replace* beside the role and enter the new holder. If the team is protected, "
                     "use *Request a management change* instead; NBTE confirms it."},
            {"type": "navigate", "url": "/institution/change-requests/"},
            {"type": "info", "path": "/institution/change-requests/",
             "target": ["section[aria-label='Submitted change requests']"],
             "title": "Your change requests",
             "text": "Every protected change you asked for, with NBTE's answer. An approved request updates "
                     "your record by itself."},
        ],
    },
    {
        "id": "portal-inspections",
        "title": "Prepare for an NBTE visit",
        "summary": "See your visit dates, add them to your calendar and know what happens after.",
        "audience": ("institution",), "host": "portal", "when": "approved", "group": "Programmes and QA requests",
        "start": "/inspections/", "pages": ("/inspections/",), "guides": ("qa-requests",),
        "keywords": "inspection visit date team calendar schedule resource inspection accreditation visit prepare",
        "next": "portal-case-progress",
        "steps": [
            {"type": "info", "path": "/inspections/", "target": HEAD, "title": "Inspections",
             "text": "Every visit NBTE has scheduled to your institution: resource inspections and "
                     "accreditation visits."},
            {"type": "info", "path": "/inspections/", "target": ["#qa-main form.qa-toolbar"], "optional": True,
             "title": "Find a visit", "text": "Search, or filter by status, by past or upcoming, and by year."},
            {"type": "info", "path": "/inspections/", "target": ["#insp-rows"],
             "title": "Dates and programmes",
             "text": "Each visit shows its dates and the programmes it covers. NBTE sets the dates; you are "
                     "notified when a visit is scheduled or moved."},
            {"type": "info", "path": "/inspections/", "target": ["#insp-rows details.nbte-cal summary"], "optional": True,
             "title": "Put it in your calendar",
             "text": "*Add to calendar* saves the visit, with reminders, to your phone or computer calendar."},
            {"type": "info", "path": "/inspections/", "target": ["#insp-rows a.cx-raise-link"], "optional": True,
             "title": "A problem with the date?",
             "text": "If the date cannot work, raise it here straight away. It opens a complaint tied to this visit."},
            {"type": "info", "title": "On the day, and after",
             "text": "Have the staff, facilities and records named in your self-study ready. Afterwards the "
                     "team writes its report, NBTE's committees decide, and the letter appears under *Letters*."},
        ],
    },
    {
        "id": "portal-account",
        "title": "Your own account: password, alerts and security",
        "summary": "Change your password, choose which notifications you get and check signed-in devices.",
        "audience": ("institution",), "host": "portal", "group": "Your institution",
        "start": "/account/", "pages": ("/account/",), "guides": ("starter",),
        "keywords": "account password change name notifications email quiet hours devices sign out passkey security",
        "steps": [
            {"type": "info", "path": "/account/", "target": ["section[aria-labelledby='qa-signin-title']"],
             "title": "Your sign-in details",
             "text": "The e-mail address you sign in with, and the institution and role your account belongs to."},
            {"type": "info", "path": "/account/", "target": ["#qa-password-card"],
             "title": "Change your password",
             "text": "Enter your current password, then the new one twice. Never share a password: each "
                     "person should have their own account."},
            {"type": "info", "path": "/account/", "target": ["section[aria-labelledby='p9-kinds-title']"], "optional": True,
             "title": "What NBTE sends you",
             "text": "Switch each kind of notification on or off, and choose whether you also get an e-mail summary."},
            {"type": "info", "path": "/account/", "target": ["section[aria-labelledby='p9-quiet-title']"], "optional": True,
             "title": "Quiet hours",
             "text": "Hold notifications back outside working hours. Urgent ones can still come through."},
            {"type": "info", "path": "/account/", "target": ["#p9-devices"], "optional": True,
             "title": "Signed-in devices",
             "text": "Every phone and computer signed in to your account. Sign out any you do not recognise, "
                     "then change your password."},
            {"type": "info", "path": "/account/", "target": ["#p9-passkeys"], "optional": True,
             "title": "Passkeys",
             "text": "A passkey lets you sign in with your fingerprint, face or screen lock instead of a password."},
        ],
    },
]
