"""Guided tours: Resource person and professional body portals (qa.* host).

Plain data, gathered into ``qa_core.tours.TOURS``. The fields of a tour and
of a step are documented at the top of qa_core/tours.py.

The welcome tours (rp-welcome, pb-welcome) and the two overviews
(rp-engagements, pb-report) are in tours.py; the tours here go one job deep.
None of them starts by itself.
"""
from .tour_lib import LAUNCHER, MENU, PB_HOME, STAFF_MENU, nav, staff_nav  # noqa: F401

PB_PROGRAMME = r"^/pb/programmes/\d+/"
PB_REPORT = r"^/pb/reports/\d+/$"
PB_TABS = [".pb-tabs"]


def _tab(url):
    """A tab in the professional body's section bar (Institutions, Visits, Reports, People, Requests)."""
    return [f".pb-tabs a[href$='{url}']", ".pb-tabs"]


# ---------------------------------------------------------------------------
# Resource person
# ---------------------------------------------------------------------------
RP_TOURS = [
    {
        "id": "rp-invitations",
        "group": "Visits and engagements",
        "title": "Answer a visit invitation",
        "summary": "Read the letter, then confirm you will attend or say you cannot.",
        "audience": ("resource_person",), "host": "portal", "start": "/visit-invitations/",
        "pages": ("/visit-invitations/",),
        "keywords": "invitation invite accept decline confirm attendance cannot attend letter visit reply",
        "next": "rp-findings",
        "steps": [
            {"type": "info", "path": "/visit-invitations/", "target": ["#qa-main .qa-page-head", "#qa-main h1"],
             "title": "Visit invitations",
             "text": "When NBTE approves a visit and puts you on the team, the invitation arrives here and by "
                     "e-mail. NBTE needs your answer before it can finalise the team."},
            {"type": "info", "path": "/visit-invitations/", "target": [".vip-list .vip-card"],
             "missing": "You have no visit invitation at the moment. NBTE notifies you here and by e-mail when "
                        "a visit you are on is approved.",
             "skippable": True, "open": {"label": "Open my engagements", "url": "/engagements/"},
             "title": "One card for each visit",
             "text": "The card names the institution and the dates. The tag on the right shows whether NBTE is "
                     "still *awaiting your reply*."},
            {"type": "info", "path": "/visit-invitations/", "target": [".vip-list .vip-card .vip-facts"],
             "optional": True, "title": "Check the details first",
             "text": "Programmes, venue, the Team Lead and your NBTE contact. *Confirm by* is the date NBTE "
                     "needs your answer."},
            {"type": "info", "path": "/visit-invitations/", "target": [".vip-list .vip-card a[href$='/letter/']"],
             "optional": True, "title": "Read the letter",
             "text": "*Letter (PDF)* opens your official invitation letter. Keep a copy: your employer may ask for it."},
            {"type": "info", "path": "/visit-invitations/", "target": [".vip-list .vip-card .vip-form"],
             "optional": True, "title": "Give your answer",
             "text": "Press *Confirm attendance* if you will be there. If you cannot go, or you have a connection "
                     "with the institution, write the reason in the note and press *Cannot attend*, so NBTE can "
                     "find someone else in time."},
            {"type": "info", "target": nav("/engagements/"), "via": MENU, "title": "Then it is an engagement",
             "text": "A visit you are on is listed under *Engagements*. That is where you write your findings "
                     "after the visit."},
        ],
    },
    {
        "id": "rp-findings",
        "group": "Visits and engagements",
        "title": "Write your findings and visit report",
        "summary": "After a visit: your findings, your own report, and what the Team Lead does with it.",
        "audience": ("resource_person",), "host": "portal", "start": "/engagements/",
        "pages": ("/engagements/",),
        "keywords": "findings post-visit report member report team lead submit draft programme after visit write",
        "next": "rp-earnings",
        "steps": [
            {"type": "info", "path": "/engagements/", "target": ["#qa-main form.qa-toolbar"],
             "title": "Find the visit",
             "text": "Search by institution or programme. Set the last filter to *Past* to see only visits that "
                     "have already taken place."},
            {"type": "info", "path": "/engagements/", "target": ["#rp-rows .qa-engagement"],
             "missing": "No engagement is listed with these filters. Clear them; if the list is still empty, "
                        "NBTE has not mapped you to a visit yet.",
             "skippable": True, "open": {"label": "Show all my engagements", "url": "/engagements/"},
             "title": "One row for each programme",
             "text": "Each row is one programme at one institution, with your duty and the dates. You report "
                     "on each programme separately."},
            {"type": "info", "path": "/engagements/",
             "target": ["#rp-rows .qa-engagement__form", "#rp-rows .qa-engagement .qa-rowlink__main > .qa-small"],
             "optional": True, "title": "My findings",
             "text": "When the visit has taken place, a *My findings* box opens on the row. Write what you "
                     "saw and press *Submit findings*; you can update them later. Until then, a note here says "
                     "what has to happen first."},
            {"type": "info", "path": "/engagements/",
             "target": ["#rp-rows section[aria-label='My individual visit report']"],
             "optional": True, "title": "My report for this programme",
             "text": "Once NBTE records the visit and marks you present, your own report opens here. Only you "
                     "can edit it. *Save my draft* keeps it private; *Submit to Team Lead* sends it."},
            {"type": "info", "path": "/engagements/", "target": ["#rp-rows .qa-engagement .qa-rowlink__side"],
             "optional": True, "title": "Where the engagement stands",
             "text": "This tag follows the engagement: awaiting confirmation, confirmed, and *Completed* when "
                     "NBTE releases you from it."},
            {"type": "info", "title": "What the Team Lead does",
             "text": "The Team Lead reads each member's report, then accepts it or returns it with a comment "
                     "that appears on your row. Accepted reports go into the final post-visit report for the "
                     "programme, which the Team Lead compiles and sends to NBTE."},
        ],
    },
    {
        "id": "rp-earnings",
        "group": "Payments and profile",
        "title": "Your earnings and bank details",
        "summary": "See what NBTE owes you, confirm a payment, and keep your bank details right.",
        "audience": ("resource_person",), "host": "portal", "start": "/me/earnings/",
        "pages": ("/me/earnings/", "/me/bank/"),
        "keywords": "earnings honorarium honoraria allowance payment paid bank account details confirm receipt money",
        "next": "rp-profile",
        "steps": [
            {"type": "info", "path": "/me/earnings/", "target": ["#qa-main .earnings-qa-hero", "#qa-main h1"],
             "title": "My earnings",
             "text": "What NBTE owes you for completed QA work, and where each payment has reached."},
            {"type": "info", "path": "/me/earnings/", "target": ["section.vx-earn__journey"],
             "title": "Four steps, left to right",
             "text": "*Awaiting schedule*: the work is done. *Scheduled*: Finance has listed it for payment. "
                     "*Payment sent*: Finance has paid. *Receipt confirmed*: you said it arrived."},
            {"type": "info", "path": "/me/earnings/",
             "target": ["section[aria-labelledby='rp-earnings-title']", "section[aria-labelledby='payment-history-title']",
                        "section[aria-labelledby='earnings-list-title']"],
             "optional": True, "title": "Each allowance and payment",
             "text": "One line for each visit allowance. When a line shows *Confirm receipt*, check your bank "
                     "account first, then press it. That closes the payment."},
            {"type": "info", "path": "/me/earnings/", "target": ["#qa-main a.btn[href$='/me/bank/']"],
             "optional": True, "title": "Where the money goes",
             "text": "NBTE pays into the account you give under *Manage my payment details*. Let us look at it."},
            {"type": "navigate", "url": "/me/bank/"},
            {"type": "info", "path": "/me/bank/",
             "target": ["#bank-root .bank-card", "#bank-root section[aria-labelledby='bank-form-title']"],
             "missing": "Your bank details cannot be shown right now. The message on this page says why.",
             "skippable": True,
             "title": "Your bank details",
             "text": "The account NBTE pays into. Only the last digits are shown; nobody at NBTE sees the full "
                     "number on screen."},
            {"type": "info", "path": "/me/bank/", "target": ["#bank-root section[aria-labelledby='bank-confirm-title']"],
             "optional": True, "title": "Still correct?",
             "text": "From time to time NBTE asks you to confirm the details. Press *Yes, they are correct*, "
                     "or *Change them*."},
            {"type": "info", "path": "/me/bank/",
             "target": ["#bank-root form.pf-form", "#bank-root a[href$='?change=1']"],
             "optional": True, "title": "Add or change them",
             "text": "Choose the bank, enter the 10-digit account number and the account name *exactly as your "
                     "bank has it*, then your portal password. You get an e-mail every time these details change."},
            {"type": "info", "title": "A wrong account delays payment",
             "text": "If the account name does not match your name, Finance holds the payment until it is "
                     "sorted out. Check the details before a visit, not after."},
        ],
    },
    {
        "id": "rp-profile",
        "group": "Payments and profile",
        "title": "Your profile, CV and conflicts of interest",
        "summary": "Your record with NBTE, uploading a new CV, and what to do about a conflict of interest.",
        "audience": ("resource_person",), "host": "portal", "start": "/profile/",
        "pages": ("/profile/", "/account/"),
        "keywords": "profile cv curriculum vitae promotion letter rank discipline record conflict of interest "
                    "declaration eligible password account",
        "steps": [
            {"type": "info", "path": "/profile/", "target": ["section[aria-labelledby='pf-h']"],
             "title": "Your record with NBTE",
             "text": "Your rank, disciplines and qualifications. NBTE uses these to choose visit teams. To "
                     "correct anything here, write to the QA Directorate."},
            {"type": "info", "path": "/profile/", "target": ["#qa-main .qa-callout--warn"], "optional": True,
             "title": "Not eligible at the moment",
             "text": "This lists why NBTE cannot give you new engagements just now. Visits already confirmed "
                     "still go ahead."},
            {"type": "info", "path": "/profile/", "target": ["section[aria-labelledby='cv-h']"],
             "title": "CV and documents",
             "text": "The CV and letters NBTE holds for you, newest first."},
            {"type": "info", "path": "/profile/", "target": ["section[aria-labelledby='cv-h'] form"],
             "optional": True, "title": "Upload a new one",
             "text": "Choose the kind of document, pick the file (PDF, Word or image) and press *Upload*. "
                     "Upload your promotion letter whenever your rank changes."},
            {"type": "info", "title": "A conflict of interest",
             "text": "If you have worked for, studied at, or have a personal or business tie with an "
                     "institution you are invited to, do not go. On the invitation, write the reason in the "
                     "note and press *Cannot attend*. On the visit itself, NBTE's team records each member's "
                     "declaration."},
            {"type": "info", "target": nav("/account/"), "via": MENU, "title": "Password and sign-in",
             "text": "*Account* is where you change your password, see the devices signed in as you, and "
                     "choose which notifications you get."},
        ],
    },
]

# ---------------------------------------------------------------------------
# Professional body
# ---------------------------------------------------------------------------
PB_TOURS = [
    {
        "id": "pb-programmes",
        "group": "Programmes",
        "title": "Programmes under your body",
        "summary": "Find a programme, read its stage, and open its page.",
        "audience": ("professional_body",), "host": "portal", "start": "/pb/",
        "pages": ("/pb/$", "/dashboard/", "/pb/programmes/", "/pb/institutions/"),
        "keywords": "programmes list stage filter search level institution details status awaiting letter",
        "next": "pb-letter",
        "steps": [
            {"type": "info", "path_re": PB_HOME, "target": ["section[aria-labelledby='pb-list-h'] form.qa-toolbar"],
             "title": "Find a programme",
             "text": "Type a programme or institution name, or filter by stage and level. The list updates as "
                     "you type."},
            {"type": "info", "path_re": PB_HOME, "target": ["#pb-rows"],
             "title": "What the stage means",
             "text": "*Awaiting your letter*: NBTE is waiting for your body. *Visit scheduled*, *SSQ in review* "
                     "and *With NBTE*: NBTE is working on it. *Approved*: accredited."},
            {"type": "click", "path_re": PB_HOME, "target": ["#pb-rows a.btn[href*='/pb/programmes/']"],
             "missing": "No programme is listed. Clear the filters; if the list is still empty, no institution "
                        "runs a programme under your body yet. You can ask NBTE to add one.",
             "open": {"label": "Open programme requests", "url": "/pb/requests/"},
             "title": "Open one", "text": "Click the button at the end of a row."},
            {"type": "info", "path_re": PB_PROGRAMME, "target": [".qa-prog-facts", "#qa-main .qa-page-head"],
             "title": "The programme at a glance",
             "text": "The institution, the level, the stage and the visit date once NBTE has set one. You "
                     "never see the institution's self-study answers."},
            {"type": "info", "path_re": PB_PROGRAMME, "target": ["section[aria-labelledby='pb-letters-h']"],
             "title": "Letters and certificates",
             "text": "Everything your body has uploaded for this programme. NBTE's department and the "
                     "institution see the same list."},
            {"type": "info", "path_re": PB_PROGRAMME, "target": [".qa-crumbs a", ".qa-crumbs"], "optional": True,
             "title": "Back to the list", "text": "*Programmes* takes you back to the full list."},
        ],
    },
    {
        "id": "pb-letter",
        "group": "Programmes",
        "title": "Approve a programme: upload your letter",
        "summary": "Send your body's approval letter or certificate for a programme.",
        "audience": ("professional_body",), "host": "portal", "role": "body administrator", "start": "/pb/",
        "pages": ("/pb/$", "/dashboard/", "/pb/programmes/"),
        "keywords": "approve approval letter certificate upload signed pdf programme awaiting your letter reference",
        "next": "pb-visits",
        "steps": [
            {"type": "info", "path_re": PB_HOME, "target": ["section[aria-labelledby='pb-list-h'] select[name='stage']"],
             "title": "Which programmes are waiting",
             "text": "Set the stage filter to *Awaiting your letter* to see the programmes NBTE is waiting on "
                     "your body for. Their button reads *Upload*."},
            {"type": "click", "path_re": PB_HOME,
             "target": ["#pb-rows a.btn-primary[href*='/pb/programmes/']", "#pb-rows a.btn[href*='/pb/programmes/']"],
             "missing": "No programme is listed here. Clear the filters; a programme appears when an "
                        "institution runs one under your body.",
             "open": {"label": "Show all programmes", "url": "/pb/"},
             "title": "Open the programme", "text": "Click *Upload* (or *Details*) on its row."},
            {"type": "info", "path_re": PB_PROGRAMME, "target": ["section[aria-labelledby='pb-letters-h'] .qa-rowlist",
                                                              "section[aria-labelledby='pb-letters-h'] .qa-card__head"],
             "title": "What is already on file",
             "text": "Letters your body has sent for this programme. Click one to download it."},
            {"type": "info", "path_re": PB_PROGRAMME, "target": ["#pbl-kind"], "closest": ".qa-field",
             "missing": "Only your body's administrator can upload a letter. Ask them, or see who they are under People.",
             "open": {"label": "Open People", "url": "/pb/people/"},
             "title": "1. What kind of letter",
             "text": "Choose the kind. Your body's approval of the programme goes up as an *approval letter*."},
            {"type": "info", "path_re": PB_PROGRAMME, "target": ["#pbl-ref"], "closest": ".qa-fields, .qa-field",
             "optional": True, "title": "2. Reference and date",
             "text": "Enter the reference printed on the letter and the date it was issued, so NBTE can quote them."},
            {"type": "info", "path_re": PB_PROGRAMME, "target": ["#pbl-file"], "closest": ".qa-field",
             "optional": True, "title": "3. The signed copy",
             "text": "Attach the signed letter as a PDF or a clear image, up to 10 MB."},
            {"type": "info", "path_re": PB_PROGRAMME,
             "target": ["form[action$='/letters/'] button[type='submit']"], "optional": True,
             "title": "4. Upload letter",
             "text": "Press *Upload letter*. NBTE and the institution see it at once and are notified. End "
                     "this guide to press it."},
        ],
    },
    {
        "id": "pb-visits",
        "group": "Visits and reports",
        "title": "Visits: assign your representative",
        "summary": "See NBTE's visits to your programmes and choose who attends for your body.",
        "audience": ("professional_body",), "host": "portal", "start": "/pb/visits/",
        "pages": ("/pb/visits/", "/visit-invitations/"),
        "keywords": "visit visits representative assign assessor attend invitation upcoming past team",
        "next": "pb-report-write",
        "steps": [
            {"type": "info", "path": "/pb/visits/", "target": ["#qa-main .qa-page-head", "#qa-main h1"],
             "title": "Visits",
             "text": "Every NBTE visit that covers a programme under your body: invitations to answer, visits "
                     "coming up, and past ones."},
            {"type": "info", "path": "/pb/visits/", "target": ["section[aria-labelledby='pb-invit-h']"],
             "optional": True, "title": "Invitations waiting for your reply",
             "text": "NBTE has invited your body to these visits. Open one to read the letter and confirm "
                     "attendance, or say that you cannot attend."},
            {"type": "info", "path": "/pb/visits/", "target": ["section[aria-labelledby='pb-upcoming-h']"],
             "title": "Upcoming visits",
             "text": "Each visit shows the institution, the dates and, once chosen, *your representative*."},
            {"type": "info", "path": "/pb/visits/",
             "target": ["section[aria-labelledby='pb-upcoming-h'] form[action*='/assign/']"],
             "missing": "There is nobody to assign right now: either no visit is coming up, or you are not your "
                        "body's administrator. Only the administrator chooses the representative.",
             "skippable": True,
             "title": "Choose who attends",
             "text": "Pick the person from your body's list, add a remark if you wish, and press *Assign "
                     "representative*. They join NBTE's visit team, receive the details and write your body's report."},
            {"type": "info", "path": "/pb/visits/", "target": _tab("/pb/people/"), "optional": True,
             "title": "Person not on the list?",
             "text": "Add them under *People* first. They can be assigned as soon as they have set a password."},
            {"type": "info", "path": "/pb/visits/", "target": ["section[aria-labelledby='pb-await-h']"],
             "optional": True, "title": "Visits without a report yet",
             "text": "Your representative attended these visits, but your body's report has not been written. "
                     "It is drafted under *Reports*."},
            {"type": "info", "path": "/pb/visits/", "target": ["section[aria-labelledby='pb-past-h']"],
             "optional": True, "title": "Past visits", "text": "Visits that have already taken place, for your records."},
            {"type": "info", "path": "/pb/visits/", "target": _tab("/pb/reports/"), "optional": True,
             "title": "After the visit",
             "text": "Your body's report on the visit is written, reviewed and sent to NBTE from *Reports*."},
        ],
    },
    {
        "id": "pb-report-write",
        "group": "Visits and reports",
        "title": "Write, review and send a visit report",
        "summary": "The representative drafts it, the registrar approves it, then it goes to NBTE.",
        "audience": ("professional_body",), "host": "portal", "start": "/pb/reports/",
        "pages": ("/pb/reports/",),
        "keywords": "report write draft findings recommendation submit registrar review approve return send nbte",
        "steps": [
            {"type": "info", "path": "/pb/reports/", "target": ["#qa-main .qa-page-head", "#qa-main h1"],
             "title": "Your body's visit reports",
             "text": "After NBTE visits a programme under your body, your body sends its own report. It passes "
                     "three hands: the representative, the registrar, then NBTE."},
            {"type": "info", "path": "/pb/reports/", "target": ["section[aria-labelledby='pb-draft-h']"],
             "missing": "No visit is waiting for a report from you. *Start a report* appears here once you are "
                        "your body's representative on a visit and no report exists for it yet.",
             "skippable": True, "open": {"label": "Open Visits", "url": "/pb/visits/"},
             "title": "Start a report",
             "text": "Visits where you are the representative and nothing has been written yet."},
            {"type": "info", "path": "/pb/reports/", "target": ["#pbr-case"], "closest": ".qa-field", "optional": True,
             "title": "1. Choose the visit", "text": "Pick the visit this report is about."},
            {"type": "info", "path": "/pb/reports/", "target": ["#pbr-rec"], "closest": ".qa-field", "optional": True,
             "title": "2. Your recommendation",
             "text": "Say what your body recommends for the programme. You can change it until the report is sent."},
            {"type": "info", "path": "/pb/reports/", "target": ["#pbr-cont"], "closest": ".qa-field", "optional": True,
             "title": "3. What you saw",
             "text": "Write the findings, attach a file if you have one, then press *Draft report*. A draft is "
                     "only visible inside your body."},
            {"type": "info", "path": "/pb/reports/", "target": ["section[aria-labelledby='pb-mine-h']"],
             "optional": True, "title": "Your reports",
             "text": "Reports you wrote. The icon shows whether each is a draft, waiting for the registrar, or "
                     "with NBTE."},
            {"type": "info", "path": "/pb/reports/", "target": ["section[aria-labelledby='pb-review-h']"],
             "optional": True, "title": "Waiting for the registrar",
             "text": "Reports your people have submitted. The registrar opens each one to approve it or "
                     "return it with what to correct."},
            {"type": "click", "path": "/pb/reports/",
             "target": ["section[aria-labelledby='pb-review-h'] a.qa-rowlink", "section[aria-labelledby='pb-mine-h'] a.qa-rowlink"],
             "missing": "There is no report to open yet. On a report's own page the representative writes "
                        "and submits it, the registrar approves or returns it, and the administrator sends it to NBTE.",
             "skippable": True,
             "title": "Open a report", "text": "Click a report to open it."},
            {"type": "info", "path_re": PB_REPORT, "target": ["section[aria-labelledby='pb-edit-h']"],
             "optional": True, "title": "Write, save, submit",
             "text": "*Save* as often as you like. *Submit to the registrar* when it is complete. If it comes "
                     "back, the registrar's remark is shown at the top."},
            {"type": "info", "path_re": PB_REPORT, "target": ["section[aria-labelledby='pb-rev-h']"],
             "optional": True, "title": "The registrar's review",
             "text": "*Approve* accepts the report. *Return for correction* sends it back to the "
                     "representative with your remark."},
            {"type": "info", "path_re": PB_REPORT, "target": ["section[aria-labelledby='pb-send-h']"],
             "optional": True, "title": "Send to NBTE",
             "text": "An approved report still has to be sent. Attach the signed PDF if you have it and press "
                     "*Send to NBTE*."},
            {"type": "info", "title": "Three hands, in order",
             "text": "The representative drafts and submits. The registrar approves or returns. The body's "
                     "administrator sends it to NBTE, where it joins the case for that visit."},
        ],
    },
    {
        "id": "pb-people",
        "group": "Your body",
        "title": "Your people: add and manage users",
        "summary": "Who can sign in for your body, their roles, and your No.1 contact.",
        "audience": ("professional_body",), "host": "portal", "role": "body administrator", "start": "/pb/people/",
        "pages": ("/pb/people/",),
        "keywords": "people users add person account role registrar representative remove restore resend link "
                    "primary contact no.1",
        "next": "pb-visits",
        "steps": [
            {"type": "info", "path": "/pb/people/", "target": ["section[aria-labelledby='pb-people-h']"],
             "title": "Your people",
             "text": "Everyone who can sign in for your body, with their role and whether they have set a "
                     "password yet."},
            {"type": "info", "path": "/pb/people/", "target": ["section[aria-labelledby='pb-people-h'] .qa-chip--top"],
             "closest": "li", "optional": True, "title": "The No.1 contact",
             "text": "NBTE writes to this person first: invitations, letters and reminders. Keep it current."},
            {"type": "info", "path": "/pb/people/", "target": ["section[aria-labelledby='pb-add-h']"],
             "missing": "Only your body's administrator can add or remove people. You can still see who is on the list.",
             "skippable": True,
             "title": "Add a person",
             "text": "Enter the name, e-mail and job title. No password is typed here: the person gets a "
                     "one-time e-mail link and sets their own."},
            {"type": "info", "path": "/pb/people/", "target": ["#pbp-role"], "closest": ".qa-field", "optional": True,
             "title": "Choose the role",
             "text": "The role decides what they can do: an administrator manages people, letters and "
                     "sending; other roles attend visits and write reports, or only view."},
            {"type": "info", "path": "/pb/people/", "target": ["section[aria-labelledby='pb-people-h'] .qa-btn-row"],
             "optional": True, "title": "Manage someone",
             "text": "*Make No.1* changes your main contact. *Resend link* sends a fresh set-password e-mail. "
                     "*Remove* stops their access at once; *Restore* brings them back."},
            {"type": "info", "path": "/pb/people/", "target": _tab("/pb/visits/"), "optional": True,
             "title": "Then put them to work",
             "text": "Once someone has set a password you can assign them to a visit under *Visits*."},
        ],
    },
    {
        "id": "pb-requests",
        "group": "Your body",
        "title": "Institutions, and asking NBTE to add a programme",
        "summary": "Where your programmes run, and how to request one that is missing.",
        "audience": ("professional_body",), "host": "portal", "start": "/pb/institutions/",
        "pages": ("/pb/institutions/", "/pb/requests/"),
        "keywords": "institutions request programme add missing new master list custodian withdraw",
        "steps": [
            {"type": "info", "path": "/pb/institutions/", "target": ["section[aria-labelledby='pb-inst-h']", "#qa-main .qa-page-head"],
             "title": "Institutions",
             "text": "Every institution that runs a programme under your body, with each programme and its "
                     "stage. Click a programme to open it."},
            {"type": "info", "path": "/pb/institutions/", "target": _tab("/pb/requests/"), "optional": True,
             "title": "A programme is missing?",
             "text": "If a programme your body regulates is not listed, ask NBTE to add it under *Requests*."},
            {"type": "navigate", "url": "/pb/requests/"},
            {"type": "info", "path": "/pb/requests/", "target": ["section[aria-labelledby='pb-req-new-h']"],
             "missing": "Your account can view requests but not send one. Ask your body's administrator.",
             "skippable": True,
             "title": "New request",
             "text": "Choose the institution and name the programme exactly as it runs there. NBTE matches it "
                     "to its master list when it approves."},
            {"type": "info", "path": "/pb/requests/", "target": ["#pbrq-rat"], "closest": ".qa-field", "optional": True,
             "title": "Say why",
             "text": "Give the reason: your accreditation mandate, the curriculum, or the scope of "
                     "professional practice. Then press *Send to NBTE*."},
            {"type": "info", "path": "/pb/requests/", "target": ["section[aria-labelledby='pb-req-h']"],
             "optional": True, "title": "Your requests",
             "text": "Each request shows NBTE's decision and remark. You can *Withdraw* one while it is still pending."},
            {"type": "info", "title": "When NBTE approves",
             "text": "The programme appears in your list and you are notified of its visits from then on."},
        ],
    },
]

PEOPLE_TOURS = RP_TOURS + PB_TOURS
