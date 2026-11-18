"""Guided tours: NBTE QA workspace (/qa/). Every tour here is host "staff", area "qa".

Onboarding documents, finance verification, institution files, TVET Support,
SOP-03 case stages, endorsements and complaints.

Plain data, gathered into ``qa_core.tours.TOURS``. The fields of a tour and
of a step are documented at the top of qa_core/tours.py.
"""
from .tour_lib import LAUNCHER, MENU, PB_HOME, STAFF_MENU, nav, staff_nav  # noqa: F401

DOC = r"^/qa/applications/documents/\d+/"
FIN_ITEM = r"^/qa/finance/queue/\d+/"
INST = r"^/qa/institutions/(?!actions/|import/|export/|add/|change-requests/)[^/]+/$"
EDIT_PROG = "/qa/institutions/actions/institutions.update_programme/"
EDIT_PROG_FORM = r"^/qa/institutions/[^/]+/actions/institutions\.update_programme/$"
CASE = r"^/qa/cases/\d+/"
REPORT = r"^/qa/inspection/reports/\d+/"
COMPLAINT = r"^/qa/complaints/\d+/"

QA_STAFF_TOURS = [
    {
        "id": "staff-doc-chain",
        "needs": "applications",
        "group": "QA: onboarding (SOP-01)",
        "title": "A document from assignment to decision",
        "summary": "The whole chain: the director assigns, the officer recommends, the director decides.",
        "audience": ("staff",), "host": "staff", "start": "/qa/applications/documents/",
        "pages": ("/qa/applications/documents/",), "guides": ("new-institutions", "documents-access"),
        "keywords": "document review assign officer recommend approve return send back director sop-01 chain end to end",
        "next": "staff-finance-verify",
        "steps": [
            {"type": "info", "path": "/qa/applications/documents/", "target": ["#qa-dr-q-h"], "closest": "section",
             "title": "Three people, three steps",
             "missing": "This queue is only for staff who analyse or decide onboarding documents.",
             "text": "Every onboarding document passes the same chain in your department: the *director* "
                     "assigns it, an *officer* analyses and recommends, the *director* decides."},
            {"type": "info", "path": "/qa/applications/documents/",
             "target": [".dq-stats a[href='?status=awaiting_assignment']", ".dq-stats"], "optional": True,
             "title": "1. Need an officer",
             "text": "Directors start here. These documents have arrived and nobody is analysing them yet."},
            {"type": "info", "path": "/qa/applications/documents/",
             "target": [".dq-stats a[href='?status=assigned']"], "optional": True,
             "title": "2. With officer",
             "text": "Documents an officer is analysing now. Officers find their own under *My work* too."},
            {"type": "info", "path": "/qa/applications/documents/",
             "target": [".dq-stats a[href='?status=recommended']"], "optional": True,
             "title": "3. Awaiting your decision",
             "text": "The officer has sent a recommendation. The director approves or returns each one here."},
            {"type": "click", "path": "/qa/applications/documents/",
             "target": [".dq-queue a.btn-primary[href*='/qa/applications/documents/']",
                        ".dq-queue a[href*='/qa/applications/documents/']"],
             "missing": "No document is in this list right now. Try another status above the list.",
             "title": "Open one", "text": "Click the button on a row to open that document."},
            {"type": "info", "path_re": DOC, "target": ["#qa-dr-doc-h"], "closest": "section",
             "title": "Open the file first",
             "text": "Whoever acts on a document must preview the file first. The buttons stay locked until then."},
            {"type": "info", "path_re": DOC, "target": ["#qa-dr-assign-h"], "closest": "section", "optional": True,
             "title": "Director: assign",
             "text": "Keep the suggested officer or choose another, then assign. The officer is notified and "
                     "the document appears in their work."},
            {"type": "info", "path_re": DOC, "target": ["form[action*='/recommend/']"], "optional": True,
             "title": "Officer: recommend",
             "text": "Write what you checked, choose *approve* or *return*, and send it to the director. "
                     "Nothing reaches the institution yet."},
            {"type": "info", "path_re": DOC, "target": ["form[action*='/decide/']"], "optional": True,
             "title": "Director: decide",
             "text": "*Approve* the document, *return it to the institution* with a remark they will read, "
                     "or send it back to the officer for another look."},
            {"type": "info", "title": "When every department has approved",
             "text": "The institution's documents step is complete and the institutional fee is billed. "
                     "A returned document comes back to the same queue when the institution uploads a new file."},
        ],
    },
    {
        "id": "staff-finance-verify",
        "needs": "finance",
        "group": "QA: onboarding (SOP-01)",
        "title": "Confirm a payment (Finance)",
        "summary": "The verification queue: check the evidence, then confirm or reject the payment.",
        "audience": ("staff",), "host": "staff", "start": "/qa/finance/queue/", "pages": ("/qa/finance/",),
        "guides": ("fees-and-payments",), "role": "Finance",
        "keywords": "finance payment verify verification queue confirm reject teller proof remita bank transfer bill receipt",
        "steps": [
            {"type": "info", "path": "/qa/finance/queue/", "target": ["section[aria-label='Verification queue']"],
             "title": "Payments waiting for Finance",
             "missing": "The verification queue is only for Finance (QA) staff.",
             "text": "Every payment an institution reports lands here: a bank transfer with proof, or one a "
                     "provider has already verified. No payment counts until Finance confirms it."},
            {"type": "info", "path": "/qa/finance/queue/", "target": [".fin-tabs"], "optional": True,
             "title": "The rest of Finance (QA)",
             "text": "Bills, bank statements, unmatched bank lines, waivers and refunds each have a tab."},
            {"type": "info", "path": "/qa/finance/queue/",
             "target": ["section[aria-label='Verification queue'] form.qa-toolbar"], "optional": True,
             "title": "Narrow the queue",
             "text": "Search by institution or reference, or filter by stage, kind of evidence and age."},
            {"type": "click", "path": "/qa/finance/queue/", "target": ["#qa-rows a.qa-rowlink"],
             "missing": "The queue is clear: no payment is waiting for verification with these filters.",
             "title": "Open a payment", "text": "Click a row. The tag on the right says what it is waiting for."},
            {"type": "info", "path_re": FIN_ITEM, "target": ["section[aria-labelledby='bill-title']"],
             "title": "The bill",
             "text": "What was billed, the reference the institution had to quote, and the balance still owed."},
            {"type": "info", "path_re": FIN_ITEM, "target": ["section[aria-labelledby='ev-title']"],
             "title": "Check the evidence",
             "text": "The institution's proof, the provider's answer and any matching bank line. Open the "
                     "receipt and compare the amount, date and reference with the bank statement."},
            {"type": "info", "path_re": FIN_ITEM, "target": ["aside[aria-labelledby='dec-title']"],
             "title": "Confirm or reject",
             "text": "The Director of Finance makes the final decision: enter the amount received and "
                     "confirm, or reject with a reason. The institution sees that reason."},
            {"type": "info", "path_re": FIN_ITEM, "target": ["section[aria-labelledby='hist-title']"], "optional": True,
             "title": "History", "text": "Who did what to this payment, and when."},
            {"type": "info", "title": "After you confirm",
             "text": "The bill shows *Confirmed*, the official receipt is issued, and the institution's next "
                     "step opens by itself. A rejected payment goes back to them with your reason."},
        ],
    },
    {
        "id": "staff-ict-supervisor",
        "needs": "institutions",
        "group": "QA: onboarding (SOP-01)",
        "title": "Assign an ICT supervisor to an institution",
        "summary": "Open an institution's file and choose the ICT officer who looks after it.",
        "audience": ("staff",), "host": "staff", "start": "/qa/institutions/", "pages": ("/qa/institutions/",),
        "role": "ICT", "keywords": "ict supervisor oversight assign institution file officer support continuity",
        "next": "staff-tvet-edit-programme",
        "steps": [
            {"type": "info", "path": "/qa/institutions/", "target": ["section[aria-label='Institutions']"],
             "title": "Every institution has a file",
             "missing": "The institutions list is not open to your role.",
             "text": "Search by name or code, or filter by status, ownership, type, department and zone."},
            {"type": "info", "path": "/qa/institutions/", "target": ["#f-status"], "optional": True,
             "title": "Still being onboarded",
             "text": "A supervisor is assigned while an institution is being onboarded. Choose *Proposed* or "
                     "*Under review* here to list those."},
            {"type": "click", "path": "/qa/institutions/", "target": ["#qa-rows a.qa-rowlink"],
             "missing": "No institution matches. Clear the filters and try again.",
             "title": "Open an institution", "text": "Click the institution you want to assign."},
            {"type": "info", "path_re": INST, "target": [".ix-tabs"], "optional": True,
             "title": "The institution's file",
             "text": "Overview, programmes, documents, payments and history. ICT oversight is on *Overview*."},
            {"type": "info", "path_re": INST, "target": ["section[aria-labelledby='ov-oversight']"],
             "missing": "The *ICT oversight* card is not on this file. It shows on the Overview tab while the "
                        "institution is being onboarded, to those allowed to assign a supervisor.",
             "title": "ICT oversight",
             "text": "The current supervisor, or a note that nobody is assigned. If someone supervised this "
                     "institution before, you can give it back to them in one click."},
            {"type": "info", "path_re": INST, "target": ["section[aria-labelledby='ov-oversight'] form.qa-stack"],
             "optional": True, "title": "Choose the officer",
             "text": "Pick an ICT staff member (the suggested one is already selected) and press the button "
                     "below the list to assign."},
            {"type": "info", "title": "What the supervisor gets",
             "text": "They are notified, the institution appears under *My work*, and they follow its ICT "
                     "matters (accounts, portal access) until the work closes or you assign someone else."},
        ],
    },
    {
        "id": "staff-tvet-edit-programme",
        "needs": "institutions",
        "group": "QA: requests and cases",
        "title": "TVET Support: edit a programme",
        "summary": "Correct an institution's programme against the NBTE master list, with a second officer's check.",
        "audience": ("staff",), "host": "staff", "start": "/qa/institutions/", "pages": ("/qa/institutions/",),
        "keywords": "tvet support edit programme update master list rename level streams second officer confirm action",
        "next": "staff-pending-confirmations",
        "steps": [
            {"type": "info", "path": "/qa/institutions/", "target": ["section[aria-labelledby='qa-menu-title']"],
             "title": "Institution actions",
             "missing": "The institution actions are not open to your role.",
             "text": "The *New Institution* and *TVET Support* menus. Each card says what the action does "
                     "and who has to confirm it. The same actions are in the side menu."},
            {"type": "click", "path": "/qa/institutions/",
             "target": ["section[aria-labelledby='qa-menu-title'] a[href*='institutions.update_programme']"],
             "missing": "*Edit Programme* is not available to you. It needs the TVET Support permission to start it.",
             "title": "Edit Programme", "text": "Click *Edit Programme* under TVET Support."},
            {"type": "info", "path": EDIT_PROG, "target": ["section[aria-labelledby='vx-launch-h']"],
             "title": "Choose the institution",
             "text": "Search by name or code. Institutions where the action does not apply are greyed out, "
                     "with the reason."},
            {"type": "click", "path": EDIT_PROG, "target": ["#qa-rows a.qa-rowlink"],
             "missing": "No institution here can have a programme edited right now.",
             "title": "Start", "text": "Click the institution whose programme you want to correct."},
            {"type": "info", "path_re": EDIT_PROG_FORM, "target": [".vx-actflow"], "optional": True,
             "title": "How the action runs", "text": "You fill the form, a second officer confirms, then the change is applied."},
            {"type": "info", "path_re": EDIT_PROG_FORM, "target": ["section[aria-labelledby='act-form-title']"],
             "title": "Pick the programme and the correction",
             "text": "Choose the programme on this institution, then correct its name, level, category and "
                     "professional body so they match *NBTE's programme list*. The preview shows before and after."},
            {"type": "info", "path_re": EDIT_PROG_FORM, "target": ["section[aria-labelledby='act-how']"], "optional": True,
             "title": "Nothing changes yet",
             "text": "Sending the form only raises a request. Give a clear reason: the confirming officer reads it."},
            {"type": "info", "title": "Then it is confirmed",
             "text": "The request waits under *Pending confirmations* for a second officer. Once confirmed "
                     "the programme is updated everywhere and the change is written to the audit trail."},
        ],
    },
    {
        "id": "staff-case-stages",
        "needs": "qa_cases",
        "group": "QA: requests and cases",
        "title": "A case from opening to final approval",
        "summary": "SOP-03 in order: desk officer, cost, personnel, visit, report, CQAC and the Executive Secretary.",
        "audience": ("staff",), "host": "staff", "start": "/qa/cases/", "pages": ("/qa/cases/",),
        "next": "staff-endorsements",
        "keywords": "sop-03 case stages desk officer cost sheet personnel visit fund release report cqac executive secretary es decision letters",
        "steps": [
            {"type": "info", "path": "/qa/cases/", "target": ["#case-board"],
             "title": "Cases move left to right",
             "missing": "The case board is not open to your role.",
             "text": "Each column is a part of SOP-03. A case sits with one person at a time and only that "
                     "person can pass it on."},
            {"type": "click", "path": "/qa/cases/", "target": ["#case-board a[href*='/qa/cases/']"],
             "missing": "There is no active case to open yet. A case opens when a QA request is cleared for a visit.",
             "title": "Open a case", "text": "Click any case card."},
            {"type": "info", "path_re": CASE, "target": [".c3-prog"], "optional": True,
             "title": "The stages, in order",
             "text": "Done stages are ticked. The highlighted one is where the case is now, with the name of "
                     "the person holding it."},
            {"type": "info", "path_re": CASE, "target": [".c3-tabs a[href^='?tab=overview']"],
             "title": "1. Desk officer opens the case",
             "text": "*Overview* holds the request, the programmes and the self-studies. The desk officer "
                     "checks them and starts the case."},
            {"type": "info", "path_re": CASE, "target": [".c3-tabs a[href^='?tab=cost']"],
             "title": "2. Cost sheet and funds",
             "text": "*Money* has the cost sheet for the visit (allowances, transport) and, once it is "
                     "approved, the fund release."},
            {"type": "info", "path_re": CASE, "target": [".c3-tabs a[href^='?tab=personnel']"],
             "title": "3. The visit team",
             "text": "*Team & tasks* is where NBTE staff and resource persons are chosen, invited and "
                     "approved, and where case tasks are tracked."},
            {"type": "info", "path_re": CASE, "target": [".c3-tabs a[href^='?tab=visit']"],
             "title": "4. The visit",
             "text": "Dates, the institution's confirmation and the record that the visit took place."},
            {"type": "info", "path_re": CASE, "target": [".c3-tabs a[href^='?tab=reports']"],
             "title": "5. The post-visit report",
             "text": "The Team Lead compiles the report from the team's findings. It is endorsed up the "
                     "chain before it goes to committee."},
            {"type": "info", "path_re": CASE, "target": [".c3-tabs a[href^='?tab=decisions']"],
             "title": "6. CQAC, then the Executive Secretary",
             "text": "*Decision* records the committee's review and the Executive Secretary's final "
                     "approval. The decision letters are issued from here."},
            {"type": "info", "path_re": CASE, "target": [".c3-tabs a[href^='?tab=timeline']"], "optional": True,
             "title": "History", "text": "Every move, return and reminder on this case, with who and when."},
            {"type": "info", "path_re": CASE, "target": [".c3-turn"], "optional": True,
             "title": "When it is your turn",
             "text": "This banner appears when the case is waiting for you. It says what to do and opens the right section."},
        ],
    },
    {
        "id": "staff-endorsements",
        "needs": "inspection",
        "group": "QA: visits and people",
        "title": "Endorse a post-visit report",
        "summary": "The endorsement chain: find the reports waiting for you, then endorse or return.",
        "audience": ("staff",), "host": "staff", "start": "/qa/inspection/endorsements/",
        "pages": ("/qa/inspection/endorsements/", "/qa/inspection/reports/"),
        "keywords": "endorse endorsement report post-visit division head deputy director director return chain",
        "steps": [
            {"type": "info", "path": "/qa/inspection/endorsements/", "target": [".vx-chain"],
             "title": "The endorsement chain",
             "missing": "Endorsements are only for staff on the inspection endorsement chain.",
             "text": "After the Team Lead submits a post-visit report it is endorsed level by level, in "
                     "this order. Each level can endorse it or return it with a remark."},
            {"type": "info", "path": "/qa/inspection/endorsements/",
             "target": ["section[aria-label='Reports'] select[name='mine']"], "optional": True,
             "title": "Only yours", "text": "Choose *Waiting on me* to see just the reports at your level."},
            {"type": "click", "path": "/qa/inspection/endorsements/", "target": ["#qa-rows a.qa-rowlink"],
             "missing": "No report matches these filters. Nothing is waiting for an endorsement.",
             "title": "Open a report", "text": "Click a report to read it."},
            {"type": "info", "path_re": REPORT, "target": ["section[aria-labelledby='rep-h']", "section[aria-labelledby='team-reports-h']"],
             "optional": True, "title": "Read the report",
             "text": "The Team Lead's compiled report, with each team member's contribution and the "
                     "resource persons' findings."},
            {"type": "info", "path_re": REPORT, "target": ["section[aria-labelledby='end-h']"], "optional": True,
             "title": "Endorse or return",
             "text": "If it is at your level, endorse it to pass it up, or return it with a remark saying "
                     "what must change. If it is greyed out, the card says who holds it."},
            {"type": "info", "path_re": REPORT, "target": ["section[aria-labelledby='hist-h']"], "optional": True,
             "title": "Who has endorsed", "text": "Each endorsement and return so far, with remarks."},
            {"type": "info", "title": "After the last endorsement",
             "text": "The report goes on to committee review on the case. A returned report goes back to "
                     "the Team Lead and climbs the chain again."},
        ],
    },
    {
        "id": "staff-complaints",
        "needs": "complaints",
        "group": "QA: requests and cases",
        "title": "Handle a complaint",
        "summary": "The complaint queue: deadlines, replying to the raiser, acting on a stage and escalating.",
        "audience": ("staff",), "host": "staff", "start": "/qa/complaints/", "pages": ("/qa/complaints/",),
        "keywords": "complaint complaints queue reply internal note deadline sla escalate hold refer resolve close",
        "steps": [
            {"type": "info", "path": "/qa/complaints/", "target": ["section[aria-label='Complaints']"],
             "title": "The complaint queue",
             "missing": "The complaint queue is not open to your role.",
             "text": "Complaints from institutions and the public that you may see, the most urgent first. "
                     "A red row has passed its deadline."},
            {"type": "info", "path": "/qa/complaints/", "target": ["section[aria-label='Complaints'] form.qa-toolbar"],
             "optional": True, "title": "Filter",
             "text": "By status, deadline, kind of complaint and priority. The list opens on what is still open."},
            {"type": "click", "path": "/qa/complaints/", "target": ["#qa-rows a.cx-rowlink"],
             "missing": "No complaint matches these filters.",
             "title": "Open a complaint", "text": "Click a complaint to open it."},
            {"type": "info", "path_re": COMPLAINT, "target": ["section[aria-labelledby='cx-about']"],
             "title": "What was raised", "text": "The complaint as the raiser wrote it, with any files they attached."},
            {"type": "info", "path_re": COMPLAINT, "target": ["section[aria-labelledby='cx-clock-h']"], "optional": True,
             "title": "The deadline", "text": "How long this stage has left. The clock stops while you wait for the raiser."},
            {"type": "info", "path_re": COMPLAINT, "target": ["section[aria-labelledby='cx-thread-h']"],
             "title": "Reply, or keep a note",
             "text": "A *reply* goes to the raiser. An *internal note* stays inside NBTE. Check which one "
                     "is selected before you send."},
            {"type": "info", "path_re": COMPLAINT, "target": ["section[aria-labelledby='cx-act-h']"], "optional": True,
             "title": "Act on this stage",
             "text": "The steps this stage allows: assign an officer, refer to another department, resolve "
                     "or close. Most need a remark."},
            {"type": "info", "path_re": COMPLAINT, "target": ["section[aria-labelledby='cx-esc-h']"], "optional": True,
             "title": "Escalate",
             "text": "Adds the next level as a watcher with a task. The handler keeps the complaint."},
            {"type": "info", "title": "Closing it",
             "text": "When the complaint is resolved the raiser is told and can see the outcome on the "
                     "portal. The full record can be downloaded as a PDF."},
        ],
    },
]
