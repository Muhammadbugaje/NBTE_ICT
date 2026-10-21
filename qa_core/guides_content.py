"""Content of the institution guides (Phase 6).

``manage.py generate_institution_guides`` turns each guide below into a Word
document. The content is plain data, so it can be edited without touching the
layout code. Button and field names match the portal exactly (in bold).

Guide keys: title, subtitle, audience, summary, accent (hex), intro
(paragraphs on the "About this guide" page), before (a "Before you begin"
checklist) and blocks.

Blocks:
    ("part", title, lead)               a numbered part: starts a page, listed in the contents
    ("h2", text) / ("h3", text)         headings
    ("p", text)                         a paragraph; **bold** is supported
    ("steps", ["Title | detail", …])   numbered step cards ("|" splits a bold title from its detail)
    ("bullets", [text, …])              a bulleted list
    ("checklist", [text, …])            a tick-box list
    ("shot", image, caption, callouts)  a screenshot (docs/guides/images); callouts = [(n, text)] explain
                                        the numbered gold badges on it
    ("halfshot", …)                     the same, narrower
    ("phone", image, caption) / ("phones", [(image, caption), …])
    ("diagram", image, caption)         a flow diagram
    ("tip" | "important" | "note", title, text)   a coloured box
    ("table", [header…], [[cell…]…])    a table
    ("doc_requirements",)               the documents table, read from the live rules
    ("help",)                           the contact part
"""

GUIDES = {
    # =====================================================================================
    "starter": {
        "title": "Starter guide",
        "subtitle": "Getting started with the NBTE Quality Assurance Portal",
        "audience": "For registrars, QA officers and heads of department",
        "summary": "Accounts, signing in, the dashboard, your record, queries and getting help.",
        "accent": "1F9D5C",
        "intro": [
            "The NBTE Quality Assurance Portal is where your institution works with the National Board for "
            "Technical Education on quality assurance: registering, applying for approval, requesting "
            "accreditation, paying fees, answering queries and following every decision.",
            "This guide is for everyone at your institution who will use the portal. It shows you how to sign in, "
            "find your way around and keep your institution's record up to date. The other three guides take you "
            "through each process step by step: **Onboarding (SOP-01)**, **QA requests (SOP-02)** and "
            "**Payments**.",
        ],
        "before": [
            "A computer, tablet or phone with an up-to-date browser (Chrome, Edge, Safari or Firefox).",
            "The **e-mail address** NBTE registered for you, and access to that mailbox.",
            "For a new institution: the details listed in the Onboarding guide.",
        ],
        "blocks": [
            ("part", "The portal at a glance",
             "Everything between your institution and NBTE, in one place, with a permanent record of every step."),
            ("shot", "landing", "The portal's home page, before you sign in."),
            ("p", "From the home page you can **Register your institution** (if your institution is new to NBTE), "
                  "**Log in**, or read **How it works** and download these guides."),
            ("h2", "What you can do on the portal"),
            ("table", ["Area", "What it is for"], [
                ["Dashboard", "What needs your action now, and what happened recently."],
                ["Application", "A new institution's onboarding application (SOP-01): profile, documents, "
                                "departmental clearance and fees."],
                ["QA requests", "Requests for accreditation, reaccreditation and the other QA exercises, with a "
                                "self-study questionnaire (SSQ) for each programme (SOP-02)."],
                ["Documents", "Your institution's statutory documents: upload, replace and follow their review."],
                ["Payments", "Every bill NBTE issues to you: pay online or by bank, upload proof, download "
                             "receipts."],
                ["Queries", "Questions from NBTE. Answer them here so the reply stays on your record."],
                ["Inspections", "NBTE's scheduled and recorded visits to your institution."],
                ["Timeline", "A permanent, searchable record of everything that happened on your record."],
            ], [3.6, 13.0]),
            ("h2", "Who does what"),
            ("table", ["Account", "Who usually holds it", "What they can do"], [
                ["Institution administrator", "The Registrar or the head of the QA unit",
                 "Keeps the institution's record up to date, starts requests, pays fees, submits SSQs, answers "
                 "queries and invites department accounts."],
                ["Department account", "Heads of academic departments",
                 "Completes the SSQ sections the administrator sends them, then sends them back."],
                ["Resource person", "Academics and professionals NBTE engages",
                 "Sees their visits, writes findings for the post-visit report and keeps their CV current."],
            ], [4.2, 4.6, 7.8]),
            ("tip", "One administrator, many helpers",
             "Keep one administrator account for your institution, held by one person. Invite heads of department "
             "as department accounts instead of sharing the administrator's password."),

            ("part", "Signing in",
             "You sign in with your e-mail address and a password you choose yourself."),
            ("h2", "The first time: set your password"),
            ("p", "When NBTE registers your institution, or when you register it yourself, you receive an e-mail "
                  "with a **set-password link**. The link works once and expires after **72 hours**."),
            ("steps", [
                "Open the e-mail from NBTE | Look for the message about your Quality Assurance Portal account, and "
                "choose the set-password link.",
                "Choose a password | Type it in **New password**: at least 8 characters, not only numbers and not "
                "something common.",
                "Confirm it | Type the same password in **Confirm new password**.",
                "Save it | Choose **Set password**. You can now sign in.",
            ]),
            ("halfshot", "password_set", "Choosing your password from the set-password link.", [
                (1, "**New password**: the rules are shown under the box."),
                (2, "**Confirm new password**: type it again."),
                (3, "**Set password** saves it."),
            ]),
            ("h2", "Every other time: log in"),
            ("steps", [
                "Open the portal | Choose **Log in** at the top of the home page.",
                "Enter your e-mail address | The one NBTE registered for you.",
                "Enter your password | Use the eye button to check what you typed.",
                "Choose Sign in | You arrive on your dashboard.",
            ]),
            ("halfshot", "login", "The sign-in page.", [
                (1, "**Email address**: the address NBTE registered for you."),
                (2, "**Password**. Forgot it? Choose **Forgot password?** above the box."),
                (3, "**Sign in**."),
            ]),
            ("h2", "Forgot your password?"),
            ("steps", [
                "Choose Forgot password? | It is above the password box on the sign-in page.",
                "Enter your e-mail address | Then choose **Email me a link**.",
                "Open the e-mail | Choose the link and set a new password as above.",
            ]),
            ("halfshot", "password_forgot", "Asking for a new set-password link.", [
                (1, "**Email address**: for your safety the portal shows the same message whether or not the "
                    "address has an account."),
                (2, "**Email me a link**."),
            ]),
            ("important", "Link expired or already used?",
             "Ask for a fresh one with **Forgot password?**. If you no longer have access to the mailbox NBTE has on "
             "record, contact NBTE (see Getting help) to change it."),

            ("part", "Your dashboard",
             "The dashboard tells you, in plain language, what needs your attention."),
            ("shot", "dashboard", "The institution dashboard.", [
                (1, "**The menu**: Dashboard, Application or QA requests, Documents and Payments. The current page is "
                    "highlighted."),
                (2, "**Action required**: everything waiting for you, most urgent first. Each item has a button "
                    "that takes you straight to it (for example **Respond** to a query)."),
                (3, "**Recent activity**: the latest events on your record. **All** opens the full timeline."),
                (4, "**Notifications**: the bell shows how many you have not read."),
            ]),
            ("p", "When **Action required** is empty, nothing needs your attention. Items leave the list on their "
                  "own once you have dealt with them."),
            ("h2", "The top bar"),
            ("table", ["Button", "What it does"], [
                ["Bell", "Your notifications. A number shows how many are unread."],
                ["?", "Writes an e-mail to NBTE for help, with the portal named in the subject."],
                ["Sun / moon", "Switches between light and dark appearance."],
                ["Your name", "Opens **Account settings** and **Sign out**."],
            ], [3.6, 13.0]),

            ("part", "How NBTE contacts you",
             "You never need to check the portal every day: NBTE tells you when something needs you."),
            ("bullets", [
                "A **notification** appears on the bell at the top of every page.",
                "An **e-mail** goes to your institution's official address for anything that needs action, such as "
                "a bill, a query or a returned document.",
                "A **query** appears under **Queries** when NBTE needs more information, with a date to answer by.",
            ]),
            ("h2", "Answering a query"),
            ("steps", [
                "Open the query | From **Action required** on the dashboard (**Respond**), from the e-mail, or from "
                "**Queries**.",
                "Read what NBTE asks | It names the request, document or SSQ the question is about.",
                "Write your answer | In **Your answer**. Add a file under **Attachment** if NBTE asked for one "
                "(up to 10 MB).",
                "Send it | Choose **Send response**. The officer who asked is notified at once.",
            ]),
            ("shot", "query", "Answering a query from NBTE.", [
                (1, "**Your answer**: say exactly what was asked for."),
                (2, "**Attachment**: one file, up to 10 MB."),
                (3, "**Send response**."),
            ]),
            ("tip", "Answer on the portal",
             "Replies sent by ordinary e-mail are not attached to your record. Answering on the portal keeps the "
             "question and the answer together, with the date, for everyone to see."),
            ("shot", "queries", "All your queries, with their due dates. Filter by status, subject or due date."),

            ("part", "Your institution's record",
             "NBTE keeps one record for your institution. Keep it accurate: it is used on every letter and decision."),
            ("shot", "institution", "The Institution page: your profile and the people who can sign in."),
            ("p", "Changes to your institution's official details (name, ownership, address, contacts) are made by "
                  "NBTE and confirmed by two officers, so the record cannot be changed by mistake. To ask for a "
                  "change, write to NBTE or answer the query NBTE raises."),
            ("h2", "Invite a head of department"),
            ("steps", [
                "Open Institution | In the menu at the top.",
                "Choose Invite a user | Under **Users**.",
                "Enter their details | Name, work e-mail and department.",
                "Send the invitation | Choose **Send invitation**. They receive a set-password e-mail.",
            ]),
            ("note", "What department accounts can do",
             "A department account sees only the SSQ sections you send it, completes them and sends them back to "
             "you. It cannot submit anything to NBTE, pay fees or change your record."),

            ("part", "The timeline",
             "Every step of every request, payment, document and visit is recorded, newest first."),
            ("shot", "timeline", "The timeline.", [
                (1, "Search, and filter by type (application, payments, documents, institution record), period and who "
                    "acted (you or NBTE)."),
            ]),
            ("p", "The timeline is permanent: entries are never edited or removed. Use it to answer \"when did we "
                  "submit that?\" or \"who confirmed this payment?\"."),

            ("part", "Account settings",
             "Your own sign-in details and name."),
            ("shot", "account", "Account settings."),
            ("bullets", [
                "**Sign-in details** shows your e-mail address, account type, role and institution. Only NBTE can "
                "change your e-mail address or role.",
                "**Your name** is how NBTE officers see you on requests and messages. Change it and choose "
                "**Save name**.",
                "**Password**: type your current password and the new one twice, then choose **Change password**.",
            ]),

            ("part", "On your phone",
             "The portal works on any phone, tablet or computer, with nothing to install."),
            ("phones", [("m_landing", "The home page on a phone."), ("m_dashboard", "Your dashboard on a phone.")]),
            ("p", "On a phone, the main sections sit in a bar at the bottom of the screen within reach of your thumb, "
                  "and long tables become cards you can scroll."),

            ("part", "Good practice and security",
             "A few habits keep your institution's record safe."),
            ("checklist", [
                "Use a password you do not use anywhere else, and never share it.",
                "Sign out on shared computers (open the menu under your name, then **Sign out**).",
                "Check that the address in your browser is the NBTE portal before you type your password.",
                "NBTE will never ask for your password by e-mail or phone.",
                "Keep your official e-mail address working: it is where NBTE sends bills and queries.",
            ]),
            ("h2", "If something is not right"),
            ("table", ["What you see", "What to do"], [
                ["\"Please enter a correct e-mail and password\"", "Check the e-mail address NBTE registered for you. "
                                                                   "Use **Forgot password?** if needed."],
                ["The set-password link has expired", "Ask for a new one with **Forgot password?**."],
                ["You cannot see a menu item a colleague sees", "Your account type decides what you see. Ask your "
                                                                "institution administrator."],
            ], [6.2, 10.4]),
            ("help",),
        ],
    },
    # =====================================================================================
    "onboarding": {
        "title": "Onboarding guide",
        "subtitle": "Registering a new institution and getting it approved (SOP-01)",
        "audience": "For proposed polytechnics, colleges and innovation institutes",
        "summary": "Registering a new institution and getting it approved.",
        "accent": "0F9F9A",
        "intro": [
            "Onboarding turns a proposed institution into one NBTE has approved. It follows NBTE's standard "
            "operating procedure **SOP-01**, and every step happens on the portal.",
            "This guide takes you through all of it: registering, completing your application, uploading your "
            "documents, departmental clearance, the institutional fee and what happens next.",
        ],
        "before": [
            "The institution's **full name**, **ownership** (federal, state or private), **type**, **category** and "
            "**year established**.",
            "The **state**, **LGA** and **permanent-site address**.",
            "The **official e-mail address**, a **phone number** and the **head of institution's name**.",
            "For a private institution: the **CAC RC number**.",
            "Your own details as the **institution administrator** (usually the Registrar).",
            "Scanned copies (PDF, JPG or PNG) of the documents listed in Part 3.",
        ],
        "blocks": [
            ("part", "Onboarding at a glance",
             "Seven stages from your first form to your programmes' questionnaires."),
            ("diagram", "diagram_onboarding", "Onboarding (SOP-01). Green steps are yours; white steps are NBTE's."),
            ("table", ["Stage", "Who acts", "What happens"], [
                ["Draft and documents", "You", "Complete your profile, upload documents, list your programmes, submit."],
                ["Departmental clearance", "NBTE", "Legal, Finance, Academic Planning (ASPD) and Physical Planning "
                                                   "(PPD) review your application in parallel."],
                ["Institutional fee", "You", "Pay the institutional processing fee."],
                ["Fee confirmed by Finance", "NBTE", "NBTE Finance confirms your payment against the bank record."],
                ["Programme selection", "You", "Confirm the programmes you will offer, and pay programme fees."],
                ["Programme clearance", "NBTE", "NTIRD clears each programme."],
                ["Questionnaire (SSQ)", "You", "Complete a self-study questionnaire per programme. NBTE then "
                                               "schedules a resource inspection."],
            ], [4.4, 2.2, 10.0]),

            ("part", "Step 1: Register your institution",
             "About fifteen minutes. You can upload documents afterwards."),
            ("p", "On the portal's home page, choose **Register your institution**. The registration page shows "
                  "where you are in onboarding, and a side panel lists the four sections of the form."),
            ("shot", "register_top", "The registration page.", [
                (1, "Where registration fits: you are at step 1 of onboarding."),
                (2, "**Institution**: the details as they appear on official documents."),
            ]),
            ("h2", "Section 1: Institution"),
            ("table", ["Field", "What to enter"], [
                ["Institution name", "The full name, exactly as on your establishment documents. It must not match an "
                                     "institution NBTE already has."],
                ["Ownership", "Federal, state or private. It decides which documents you upload."],
                ["Type and category", "For example Polytechnic; Polytechnic."],
                ["Year established", "The year the institution was (or will be) established."],
                ["CAC RC number", "Private institutions only: the Corporate Affairs Commission registration number."],
                ["Website", "Optional."],
            ], [4.2, 12.4]),
            ("h2", "Section 2: Location and contacts"),
            ("halfshot", "register_location", "Location and contacts."),
            ("bullets", [
                "Choose the **State** first: the **LGA** list then fills in for that state.",
                "**Address** is the permanent site, not a temporary office.",
                "**Official e-mail** is where NBTE sends bills, queries and decisions: use an address the institution "
                "will keep.",
            ]),
            ("h2", "Section 3: Your account"),
            ("halfshot", "register_account", "Your account: you become the institution administrator."),
            ("p", "**Your e-mail** is the address you will sign in with. Choose a password of at least 8 characters "
                  "and type it twice."),
            ("h2", "Section 4: Declaration and submit"),
            ("halfshot", "register_submit", "The declaration and the Register institution button.", [
                (1, "Tick the declaration: you are authorised to register the institution and the information is "
                    "true."),
                (2, "**Register institution**."),
            ]),
            ("p", "If anything is missing or wrong, the form shows the problem next to the field. Fix it and choose "
                  "**Register institution** again. When it succeeds you are signed in and taken to your "
                  "application, and your institution receives its NBTE code."),
            ("important", "Already known to NBTE?",
             "Do not register again. If NBTE set up your institution, use the set-password link NBTE e-mailed, or "
             "**Forgot password?** on the sign-in page."),
            ("note", "Registration closed?",
             "When NBTE manages registration itself, the form is replaced by a notice with the contact to use. NBTE "
             "registers your institution and e-mails you a link to set your password."),

            ("part", "Step 2: Complete your application",
             "Your application gathers everything NBTE needs to assess the institution."),
            ("shot", "application_draft", "A draft application: what is still missing, and your programmes."),
            ("p", "The progress bar at the top shows the seven stages and where you are. **Ready to submit?** lists "
                  "anything that still blocks submission, for example missing documents or profile details."),
            ("h2", "Your programmes"),
            ("p", "**Programmes you plan to offer** lists the programmes you proposed, with their level (ND, HND, NID "
                  "or NVC). You confirm and add to the list at the **Programme selection** stage, after your "
                  "institutional fee is confirmed."),
            ("halfshot", "application_programmes", "The programmes you plan to offer."),

            ("part", "Step 3: Upload your documents",
             "Upload each document once: NBTE keeps it on your permanent record."),
            ("doc_requirements",),
            ("shot", "documents", "The Documents page.", [
                (1, "**Upload** next to each document that is still needed."),
            ]),
            ("steps", [
                "Open Documents | In the menu, or from **Ready to submit?** on your application.",
                "Find a document marked Not uploaded | The panel on the right shows how many are in and which are "
                "still needed.",
                "Upload it | Choose **Upload**, pick the file (PDF, JPG or PNG; the size limit is shown) and choose "
                "**Send**.",
                "Repeat for each document | The progress bar fills as you go.",
            ]),
            ("h2", "What the statuses mean"),
            ("table", ["Status", "Meaning", "What to do"], [
                ["Not uploaded", "NBTE needs this document and has not received it.", "Upload it."],
                ["Awaiting review", "Uploaded; the named department will check it.", "Nothing; wait for the review."],
                ["Accepted", "The department accepted it.", "Nothing."],
                ["Returned", "The department needs a corrected version. The reason is shown.",
                 "Choose **Upload new version**."],
                ["Expired", "The document is out of date.", "Upload a current version."],
            ], [3.4, 7.2, 6.0]),
            ("tip", "Earlier versions are kept",
             "When you upload a new version, the old one stays in **All versions**, so NBTE can see what changed."),

            ("part", "Step 4: Submit your application",
             "When nothing blocks it, submit your application to NBTE."),
            ("halfshot", "application_submit", "Ready to submit?", [
                (1, "**Submit application** becomes available once every blocker is cleared."),
            ]),
            ("steps", [
                "Clear every blocker | Each line under **Ready to submit?** says what is missing.",
                "Tick the declaration | Confirm the information and documents are true and that you are authorised.",
                "Choose Submit application | Your application goes to four NBTE departments at once.",
            ]),

            ("part", "Step 5: Departmental clearance",
             "Four NBTE departments review your application at the same time."),
            ("shot", "application_clearance", "Two departments have cleared the application; two are still "
                                              "reviewing it."),
            ("table", ["Department", "What it checks"], [
                ["Legal Unit", "Legal documents: incorporation, land title, enabling law."],
                ["Finance", "Financial capacity, for example the bank guarantee."],
                ["ASPD (Academic Planning)", "The academic master plan and need-assessment survey."],
                ["PPD (Physical Planning)", "The physical master plan and the site."],
            ], [5.0, 11.6]),
            ("p", "Each card shows **Cleared** or **Pending**, with the date. You do not need to do anything while "
                  "the departments review; you are notified when each one decides."),
            ("important", "If a department returns your application",
             "Its card shows **Returned** with a remark. Fix what it asks (usually a document), then submit again "
             "from **Ready to submit?**. Only the departments that need to look again review the new round."),

            ("part", "Step 6: Pay the institutional fee",
             "When all four departments have cleared your application, your institutional fee becomes payable."),
            ("shot", "application_fee", "All four departments cleared: the institutional fee is due."),
            ("p", "The fee appears under **Fees** on your application and on the **Payments** page, with its own "
                  "payment reference. Pay online (Remita or Paystack) or at the bank. The **Payments guide** takes you "
                  "through every way to pay."),
            ("note", "When does the next step open?",
             "Only when NBTE Finance has confirmed the payment against the bank record. You are notified, and the "
             "progress bar moves to **Programme selection** on its own."),

            ("part", "Step 7: Programmes, the SSQ and inspection",
             "After the institutional fee, the focus moves to your programmes."),
            ("steps", [
                "Programme selection | Confirm the programmes you will offer, with their level. Add any that are "
                "missing with **Add programme**.",
                "Programme fees | A bill appears for each programme fee. Pay it as before.",
                "Programme clearance | The NTIRD department clears each programme.",
                "Self-study questionnaire | Complete one SSQ per programme and submit it (see the QA requests guide, "
                "Part 5).",
                "Resource inspection | NBTE schedules a visit to check staff, facilities and equipment. A "
                "successful inspection gives **approval to commence**.",
            ]),
            ("h2", "Following your application"),
            ("bullets", [
                "Your **dashboard** lists whatever needs your action.",
                "**Application history** on the application page, and the **Timeline**, record every step with its "
                "date.",
                "NBTE e-mails your official address at each decision.",
            ]),
            ("help",),
        ],
    },
    # =====================================================================================
    "qa-requests": {
        "title": "QA requests guide",
        "subtitle": "Requesting accreditation and completing the self-study questionnaire (SOP-02)",
        "audience": "For approved institutions: registrars, QA officers and heads of department",
        "summary": "Requesting accreditation and completing the SSQ.",
        "accent": "2F7FE0",
        "intro": [
            "An approved institution asks NBTE for quality assurance on its programmes through a **QA request**. "
            "Each request has a type, a fee, and one self-study questionnaire (SSQ) per programme. After your SSQs "
            "are accepted, NBTE plans the visit, inspects and decides.",
            "This guide follows a request from start to decision, and shows how to involve your academic "
            "departments in the SSQ.",
        ],
        "before": [
            "Your institution is **approved** by NBTE (see the Onboarding guide if it is not).",
            "You know which **programmes** need quality assurance, and why.",
            "Your academic departments have **department accounts** if they will complete parts of the SSQ.",
            "Evidence ready to upload: staff lists, curricula, facilities and equipment lists, library holdings.",
        ],
        "blocks": [
            ("part", "How a QA request works",
             "Five stages on your side; seventeen inside NBTE."),
            ("diagram", "diagram_qa_request", "A QA request from start to decision."),
            ("h2", "Kinds of QA"),
            ("table", ["Type", "When to use it"], [
                ["Resource Inspection", "The first visit to a new programme, to check staff, facilities and "
                                        "equipment before it starts."],
                ["Accreditation", "The full assessment of a programme once it has run long enough for its first "
                                  "students to graduate."],
                ["Reaccreditation", "Renewing accreditation before it expires, or after interim accreditation once the "
                                    "gaps NBTE found are fixed."],
                ["Validation", "Confirming that records and claims about a programme are accurate."],
                ["Verification", "A targeted check of particular facts, such as staffing or student numbers."],
                ["Increase in Carrying Capacity", "Asking to admit more students by approving extra streams."],
                ["Institutional Administration", "The assessment of the institution as a whole: governance, "
                                                 "administration and the services that support every programme."],
            ], [5.2, 11.4]),

            ("part", "Step 1: Start a request",
             "A request starts as a draft: nothing goes to NBTE until you submit it."),
            ("shot", "qa_requests", "Your QA requests.", [
                (1, "**New QA request**."),
            ]),
            ("p", "The list shows every request with its type, programmes and status. Search by reference or "
                  "programme, or filter by status, QA type and level."),
            ("steps", [
                "Choose New QA request | On the QA requests page.",
                "Choose the QA type | Each card says what the type is for. A type you cannot use now says why (for "
                "example \"You already have an open request of this type\").",
                "Tick the programmes | Only programmes that qualify can be ticked; for the others the portal gives "
                "the reason (for example reaccreditation opens a set number of months before expiry).",
                "Choose Create draft request | Your draft opens.",
            ]),
            ("shot", "qa_request_new", "What do you want NBTE to do?", [
                (1, "**1. QA type**: choose one. Unavailable types show the reason."),
            ]),

            ("part", "Step 2: Submit the request",
             "Check the programmes, then submit. NBTE then issues the QA fee."),
            ("shot", "qa_request_draft", "A draft request.", [
                (1, "**Submit to NBTE**: tick the declaration, then choose **Submit request**."),
            ]),
            ("steps", [
                "Check the programmes | Under **Programmes**. Add, drop or replace a programme if needed.",
                "Tick the declaration | Confirm these are the programmes your institution wants assessed and that you "
                "are authorised to submit.",
                "Choose Submit request | The status changes to **QA processing fee due**.",
            ]),
            ("tip", "Changing your mind",
             "Until your fee is confirmed you can add, drop (**Drop…**) or replace (**Replace…**) programmes, or "
             "cancel the whole request (**Cancel this request**). The bill is re-issued for the new number of "
             "programmes."),

            ("part", "Step 3: Pay the QA fee",
             "Your SSQs open as soon as NBTE Finance confirms the payment."),
            ("shot", "qa_request_fee", "A request waiting for its QA fee.", [
                (1, "**QA fee**: the amount, the payment reference and its status."),
                (2, "**Drop…** and **Replace…** are still available until the fee is confirmed."),
            ]),
            ("p", "Open the bill from the **Payments** page and pay it online or at the bank. The **Payments guide** "
                  "shows each way to pay step by step."),

            ("part", "Step 4: Complete the SSQs",
             "One self-study questionnaire per programme. Answers save automatically as you type."),
            ("shot", "qa_request_ssq", "The fee is confirmed: the SSQs are open.", [
                (1, "**Self-study questionnaires**: one per programme, with how many required answers are done. "
                    "Choose **Continue** to open one."),
            ]),
            ("shot", "ssq", "The SSQ workspace.", [
                (1, "**Sections**: each shows how many required answers are done. A tick means the section is "
                    "complete."),
                (2, "**Answers save automatically**, with the time of the last save."),
                (3, "**Submit to NBTE** lists every required answer still empty; each one links to its question."),
            ]),
            ("steps", [
                "Work through each section | Choose a section on the left. Answers save as you type; **Save section** "
                "saves straight away.",
                "Check prefilled answers | Fields marked **Prefilled from your record** come from NBTE's record. "
                "**Changed from the record** marks where your answer differs; that is fine, if it is correct.",
                "Upload evidence | Where a question asks for a file. Evidence you have already uploaded can be "
                "reused.",
                "Clear the list on the right | **Submit to NBTE** shows what is still empty.",
            ]),
            ("tip", "Tables",
             "Some questions are tables (for example enrolment for the last three sessions, or academic staff). "
             "Add a row for each item; the question says how many rows are needed."),

            ("part", "Step 5: Involve your academic departments",
             "Send sections to the department that owns them; you stay in charge of submitting."),
            ("halfshot", "ssq_department", "Send sections to a department."),
            ("steps", [
                "Open Send sections to a department | On the SSQ page, under **Submit to NBTE**.",
                "Choose the department account | Invite one first from **Institution → Users** if the list is "
                "empty.",
                "Tick the sections they may edit | They see and edit only those sections.",
                "Add a note and choose Send | The department is notified.",
                "Wait for it to come back | The department completes the sections and chooses **Send to my "
                "institution**. To take the SSQ back early, choose **Take it back**.",
            ]),

            ("part", "Step 6: Submit the SSQ and answer queries",
             "Submit each SSQ when it is complete. NBTE reviews it and may ask questions."),
            ("steps", [
                "Check the SSQ | **Submit to NBTE** must show no required answers missing.",
                "Tick the declaration | Confirm the answers and documents are true and complete.",
                "Choose Submit SSQ | The SSQ goes to NBTE for review.",
            ]),
            ("p", "If NBTE needs more information it raises a **query**: it appears under **Action required** on your "
                  "dashboard, on the request page and under **Queries**, with a date to answer by. The Starter guide "
                  "shows how to answer one."),
            ("important", "A returned SSQ",
             "If NBTE returns an SSQ, choose **Start version 2**. Your earlier answers are carried forward, so you "
             "only change what was asked, then submit again."),

            ("part", "What happens inside NBTE",
             "When every SSQ is accepted, NBTE opens an internal case with seventeen stages."),
            ("diagram", "diagram_sop03", "The internal stages (SOP-03). Green-outlined stages are the ones you are "
                                         "told about."),
            ("p", "NBTE assigns the team, costs and funds the visit, inspects, reports and decides. You do not need to "
                  "act during these stages. You are told when the visit is approved, when it takes place and when "
                  "the decision is made; visits also appear on the **Inspections** page."),
            ("shot", "inspections", "NBTE's visits to your institution."),
            ("h2", "Joint QA with professional bodies"),
            ("p", "If a programme on your request is also regulated by a professional council (for example ND "
                  "Pharmacy Technician by the Pharmacy Council of Nigeria), the request is marked as a **joint QA "
                  "exercise**. NBTE tells the council when your case opens so its assessor can join the visit team. "
                  "Any fee or paperwork the council requires is arranged directly with the council."),
            ("h2", "The decision"),
            ("p", "When the Executive Secretary gives final approval, the decision appears on your timeline, NBTE "
                  "e-mails your official address, and your programme's record shows its new status, approved "
                  "streams and expiry date."),
            ("phone", "m_qa_request", "Following a request on a phone."),
            ("h2", "If something is not right"),
            ("table", ["What you see", "What to do"], [
                ["A QA type is not available", "The card says why: for example an open request of the same type, or "
                                               "the questionnaire not yet published by NBTE."],
                ["A programme cannot be ticked", "Its reason is shown, for example the reassessment window has not "
                                                 "opened yet, or it is already in another open request."],
                ["Submit SSQ is greyed out", "Required answers are still empty: see the list under **Submit to "
                                             "NBTE**."],
                ["A department cannot see a section", "Send that section to them, or take the SSQ back and send it "
                                                      "again with the right sections ticked."],
            ], [5.6, 11.0]),
            ("help",),
        ],
    },
    # =====================================================================================
    "payments": {
        "title": "Payments guide",
        "subtitle": "Bills, paying online or by bank, proof of payment and receipts",
        "audience": "For registrars, bursars and anyone who pays NBTE fees",
        "summary": "Bills, paying online or by bank, proof and receipts.",
        "accent": "C98A0B",
        "intro": [
            "Every fee NBTE charges starts as a **bill** on the portal, with its own payment reference. You can pay "
            "online with Remita or Paystack, or at the bank.",
            "A payment only counts once **NBTE Finance confirms it** against the bank record; an online provider "
            "saying \"paid\" is not enough on its own. When Finance confirms, the next step of your application or "
            "request opens on its own.",
        ],
        "before": [
            "The bill's **payment reference** (shown on the bill and on the payment slip).",
            "For a bank payment: the **payment slip** and, afterwards, the teller or transfer receipt.",
            "For an online payment: a card, bank account or USSD access, depending on the provider.",
        ],
        "blocks": [
            ("part", "How payments work",
             "Four steps: a bill, your payment, Finance's checks and the Director of Finance's confirmation."),
            ("diagram", "diagram_payment", "How a payment is confirmed. Upload proof applies to bank payments only."),
            ("table", ["What the bill says", "Meaning"], [
                ["Awaiting payment", "The bill is issued and waiting for you to pay."],
                ["Payment in progress", "You started an online payment; the provider is processing it."],
                ["Institution claims paid — awaiting Finance confirmation", "You paid online or uploaded proof; NBTE "
                                                                           "Finance is checking it against the bank "
                                                                           "record."],
                ["Payment confirmed", "Finance confirmed the payment. Your Payment Advice (receipt) is ready and the "
                                      "next step opens."],
                ["Part payment received — balance due", "Pay the balance shown, quoting the same reference."],
                ["Payment rejected — see reason", "Finance could not match the payment. Read the reason on the bill."],
                ["Fee waived", "NBTE waived the fee. Nothing to pay."],
                ["Bill expired / Bill cancelled", "Do not pay it. Ask NBTE to re-issue it if needed."],
            ], [6.6, 10.0]),

            ("part", "Find your bills",
             "The Payments page lists every bill NBTE has issued to your institution."),
            ("shot", "payments", "Your bills, with what you have to pay, what is being confirmed and what is settled."),
            ("p", "The totals at the top show **To pay**, **Being confirmed** and **Settled**. Search by reference or "
                  "description, or filter by status, fee and period. Choose a bill to open it."),

            ("part", "Open a bill",
             "Each bill shows the amount, the payment reference, the due date and its progress."),
            ("shot", "payment", "A bill awaiting payment.", [
                (1, "**Progress**: Bill issued, You pay, Finance checks, Director of Finance confirms."),
            ]),
            ("bullets", [
                "The **amount** is shown in figures and in words.",
                "The **payment reference** (and the Remita RRR, when one has been generated) identifies this bill. "
                "Always quote it.",
                "**Due** is the date to pay by.",
                "**Slip** downloads the payment slip with NBTE's account details.",
            ]),
            ("important", "Do not pay twice",
             "If you have already paid, upload your proof instead of paying again. If you are unsure, check "
             "**Online attempts** and **Your uploads** on the bill first."),

            ("part", "Pay online",
             "Remita (RRR, online or at any bank) or Paystack (card, bank transfer or USSD)."),
            ("shot", "payment_options", "How to pay.", [
                (1, "**Pay with Remita**: generates a Remita Retrieval Reference (RRR). Pay online or at any bank."),
                (2, "**Pay with Paystack**: card, bank transfer or USSD. Provider charges may apply."),
                (3, "**Bank transfer or deposit**: see Part 5."),
            ]),
            ("steps", [
                "Choose Pay with Remita or Pay with Paystack | Under **How to pay** on the bill.",
                "Complete the payment | On the provider's page.",
                "Come back to the portal | You return to the bill. **Online attempts** shows the provider's report "
                "while Finance confirms it.",
            ]),
            ("tip", "Keep the provider's receipt",
             "If Finance asks, upload it from the bill with **Upload the provider's receipt**."),

            ("part", "Pay by bank transfer or deposit",
             "Download the slip, pay quoting the reference, then upload your proof."),
            ("steps", [
                "Download the payment slip | Choose **Payment slip** under **Bank transfer or deposit**. It shows "
                "NBTE's account and your payment reference.",
                "Pay at the bank or by transfer | Quote the payment reference exactly, and pay the full amount in "
                "one go.",
                "Report it | Back on the bill, choose **I have paid**.",
                "Fill in the proof form | Amount paid, date paid, bank, teller or transaction number, who paid, and "
                "the teller or transfer receipt (PDF, JPG or PNG, up to 5 MB).",
                "Choose Submit proof | Finance checks it against the bank statement.",
            ]),
            ("halfshot", "payment_proof", "Upload proof of payment.", [
                (1, "**Submit proof**."),
            ]),
            ("important", "Quote the reference",
             "The payment reference is how Finance matches your money to your bill. A transfer without it takes much "
             "longer to confirm."),

            ("part", "Receipts and problems",
             "When Finance confirms a payment, your receipt is issued and the next step opens."),
            ("p", "Download the **Payment Advice** (your receipt) from the bill. Each one carries a **QR code** that "
                  "anyone can scan to check it is genuine on the NBTE portal."),
            ("table", ["What you see", "What to do"], [
                ["You paid but the bill still says Awaiting payment", "If you paid by bank, check that you uploaded "
                                                                      "the proof. Finance confirms against the bank "
                                                                      "record, which can take a few working days."],
                ["Payment rejected", "Read the reason on the bill. Upload the correct proof, or pay again quoting the "
                                     "reference."],
                ["Part payment received", "Pay the balance shown on the bill, quoting the same reference."],
                ["Overpayment recorded", "Finance contacts you about a refund; you can follow it on the bill "
                                         "(Refund in progress, then Refunded)."],
                ["The slip has expired", "Choose **Generate a new slip** on the bill. Do not pay with an old slip."],
                ["The bill has expired or is cancelled", "Do not pay it. Ask NBTE to re-issue it."],
            ], [6.2, 10.4]),
            ("help",),
        ],
    },
}

ORDER = ["starter", "onboarding", "qa-requests", "payments"]
