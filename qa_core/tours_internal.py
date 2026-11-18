"""Guided tours: NBTE internal (non-QA) staff system. Every tour here is host "staff", area "main".

Everyday work (home, messages, memos, announcements, notifications, search)
and requests, tickets and tasks. Leave, attendance, the staff record and the
tools are in tours_internal_self.py.

Plain data, gathered into ``qa_core.tours.TOURS``. The fields of a tour and
of a step are documented at the top of qa_core/tours.py.
"""
from .tour_lib import LAUNCHER, MENU, PB_HOME, STAFF_MENU, nav, staff_nav  # noqa: F401

EVERYDAY = "Everyday work"
WORK = "Requests, tasks and tickets"

REQUEST_FORM = r"^/workflow/new/[\w-]+/$"
REQUEST_PAGE = r"^/workflow/\d+/$"
TICKET_FORM = r"^/tickets/new/[\w-]+/$"
TICKET_LISTS = r"^/tickets/(my|assigned)/$"
TICKET_PAGE = r"^/tickets/\d+/$"
TASK_FORM = r"^/tasks/new/[\w-]+/$"
TASK_LISTS = r"^/tasks/(assigned|my)/$"
TASK_PAGE = r"^/tasks/\d+/$"

INTERNAL_TOURS = [
    # ------------------------------------------------------------------ everyday work
    {
        "id": "nbte-home",
        "title": "A quick look around",
        "summary": "Your home page, the menu, search, notifications and where to find help.",
        "audience": ("staff",), "host": "staff", "group": EVERYDAY, "start": "/home/", "pages": ("/home/$",),
        "keywords": "start begin new first home dashboard welcome tour orientation menu", "auto": True,
        "next": "nbte-messages",
        "steps": [
            {"type": "info", "title": "Welcome to Digital NBTE",
             "text": "This is the Board's internal system: messages, memos, requests, tickets, tasks, leave and "
                     "your staff record. Let me show you round. It takes about a minute.",
             "next_label": "Show me around", "skip_label": "Not now"},
            {"type": "info", "path": "/home/", "target": [".home-hero"], "title": "This is Home",
             "text": "You land here each time you sign in. The buttons are shortcuts for the things staff do "
                     "most: check in, send a message, see your earnings."},
            {"type": "info", "path": "/home/", "target": ["section[aria-labelledby='ann-panel-h']", ".ann-banner"],
             "optional": True, "title": "Announcements",
             "text": "Notices from management. One marked *Action needed* stays here until you acknowledge it."},
            {"type": "info", "path": "/home/", "target": ["section[aria-labelledby='wb-title']"], "optional": True,
             "title": "Your desk", "text": "Work waiting for you: tasks assigned to you and anything you have to confirm."},
            {"type": "info", "path": "/home/", "target": [".home-modules-section"], "title": "Everything at a glance",
             "text": "One card for each part of the system, with a count of what is open. Click a card to go there."},
            {"type": "info", "target": [".app-sidebar .app-nav"], "via": STAFF_MENU, "title": "The menu",
             "text": "Every section is here, grouped: *Requests*, *Tasks*, *Tickets*, *Communication* and more. "
                     "Click a group to open it. You only see what your role allows."},
            {"type": "info", "target": [".app-sidebar .app-workspace"], "optional": True, "title": "Main or QA",
             "text": "If you also work on quality assurance, this switch changes the menu between the main "
                     "system and the QA workspace."},
            {"type": "info", "target": ["form.app-topsearch", "#app-topsearch-q"], "optional": True, "title": "Search",
             "text": "Find a colleague, task, ticket, request or memo from any page. Just start typing."},
            {"type": "info", "target": ["#notif-bell"], "title": "Notifications",
             "text": "The bell rings when something needs you: an approval, a new task, a reply. The number is "
                     "what you have not read."},
            {"type": "info", "target": [".rc-card"], "optional": True, "title": "A few things to finish",
             "text": "This card reminds you to complete your profile, add your bank details and your "
                     "signature. Each line opens the right page."},
            {"type": "info", "target": LAUNCHER, "title": "Stuck? Press Guide me",
             "text": "Choose what you want to do and I will take you to the right page and point at each step, "
                     "one at a time."},
        ],
    },
    {
        "id": "nbte-messages",
        "title": "Send a message to a colleague",
        "summary": "Start a conversation, attach a file and find it again later.",
        "audience": ("staff",), "host": "staff", "group": EVERYDAY, "start": "/messages/", "pages": ("/messages/",),
        "keywords": "message chat conversation inbox attach file attachment group direct reply colleague",
        "next": "nbte-memo-send",
        "steps": [
            {"type": "info", "path": "/messages/", "target": [".msg-qa-hero"], "title": "Messages",
             "text": "Your conversations with colleagues, one to one or in groups. The newest is at the top; "
                     "unread ones are in bold."},
            {"type": "info", "path": "/messages/", "target": [".msg-filter-chip.is-active"], "closest": ".msg-filter-chips, .msg-list-tools, div",
             "optional": True, "title": "Find a conversation",
             "text": "Search by name or words, or show only *Unread*, *Groups*, *Direct* or the messages you starred."},
            {"type": "click", "path": "/messages/", "target": [".msg-qa-hero a[href='/messages/new/']", "a.btn-primary[href='/messages/new/']"],
             "missing": "The *New message* button is not on this page.", "open": {"label": "Write a message", "url": "/messages/new/"},
             "title": "Start a conversation", "text": "Click *New message*."},
            {"type": "info", "path": "/messages/new/", "target": [".msg-compose-form .msg-picker"], "closest": ".msg-field",
             "title": "Who is it for?",
             "text": "Type a name and tick the person. Tick more than one and it becomes a group conversation."},
            {"type": "fill", "path": "/messages/new/", "target": ["#compose-body"], "closest": ".msg-field",
             "title": "Write your message", "text": "Type the message here. A subject is optional but helps people find it later."},
            {"type": "info", "path": "/messages/new/", "target": ["#compose-attachments"], "closest": ".msg-field",
             "title": "Attach a file", "optional": True,
             "text": "Add a document or picture if you need to. You can choose more than one file."},
            {"type": "info", "path": "/messages/new/", "target": [".msg-compose-form button[type='submit']"],
             "title": "Send it",
             "text": "Press *Send message*. The conversation opens, and replies arrive there and under the bell. "
                     "End this guide when you are ready to send."},
            {"type": "info", "title": "Inside a conversation",
             "text": "Reply at the bottom, and attach files or a voice note from the same box. Use the menu on "
                     "a message to star it, forward it, or turn it into a task or a memo."},
        ],
    },
    {
        "id": "nbte-memo-send",
        "title": "Send a memo",
        "summary": "Write an official memo, choose who gets it, and see how it is approved.",
        "audience": ("staff",), "host": "staff", "group": EVERYDAY, "start": "/messages/memos/",
        "pages": ("/messages/memos/",),
        "keywords": "memo memorandum circular internal external general approval route routing es sign letterhead acknowledge",
        "next": "nbte-announcements",
        "steps": [
            {"type": "info", "path": "/messages/memos/", "target": [".memo-qa-hero"], "title": "Memos",
             "text": "A memo is an official, one-way notice on NBTE letterhead. It is approved before it is "
                     "delivered. For a conversation, use Messages instead."},
            {"type": "info", "path": "/messages/memos/", "target": ["nav.memo-tabs"], "title": "Your memo trays",
             "text": "*Inbox* holds memos sent to you. *Awaiting my action* holds memos you must approve or "
                     "return. *Sent & drafts* shows where your own memos have reached."},
            {"type": "click", "path": "/messages/memos/", "target": [".memo-qa-hero a[href='/messages/memos/new/']", "a[href='/messages/memos/new/']"],
             "missing": "You do not have the *New memo* button. Your role can read memos but not write them; "
                        "ask your head of department if you need to send one.",
             "title": "Start a memo", "text": "Click *New memo*."},
            {"type": "info", "path": "/messages/memos/new/", "target": [".memo-form fieldset.memo-kind"],
             "optional": True, "title": "1. Choose the kind",
             "text": "*Internal* goes to NBTE staff. *External* goes to institutions and *General* to both; "
                     "those two are signed by the Executive Secretary."},
            {"type": "fill", "path": "/messages/memos/new/", "target": ["#id_subject"], "closest": "label, .memo-field, div",
             "title": "2. Subject and body",
             "text": "Give a clear subject, then write the memo in the *Body* box below it. Set a priority if it is urgent."},
            {"type": "info", "path": "/messages/memos/new/", "target": [".memo-form [data-staff-audience]", "#id_visibility"],
             "optional": True, "title": "3. Who is it for?",
             "text": "Choose the audience: everyone, a department, specific roles, or a list of named people."},
            {"type": "info", "path": "/messages/memos/new/", "target": ["#id_attachments"], "closest": "label, .memo-field, div",
             "optional": True, "title": "Attachments", "text": "Add supporting files if needed, up to 5 MB in total."},
            {"type": "info", "path": "/messages/memos/new/", "target": [".memo-form button.btn-primary[name='action']"],
             "title": "4. Send for approval",
             "text": "*Save as draft* keeps it private. *Send for approval* passes it up your approval route. "
                     "End this guide when you are ready to send."},
            {"type": "info", "title": "How a memo travels",
             "text": "Each approver on the route approves it or returns it to you with a remark. When the last "
                     "one approves, the memo is delivered and recipients are notified. Follow it under *Sent & drafts*."},
        ],
    },
    {
        "id": "nbte-announcements",
        "title": "Read and acknowledge announcements",
        "summary": "Find notices from management and acknowledge the ones that ask you to.",
        "audience": ("staff",), "host": "staff", "group": EVERYDAY, "start": "/announcements/",
        "pages": ("/announcements/",),
        "keywords": "announcement notice news acknowledge read poll vote pinned urgent management",
        "next": "nbte-notifications",
        "steps": [
            {"type": "info", "path": "/announcements/", "target": [".ann-qa-hero"], "title": "Announcements",
             "text": "Notices from NBTE management. The counters show how many you have not read and how "
                     "many are waiting for your acknowledgement."},
            {"type": "info", "path": "/announcements/", "target": ["nav.ann-filters"], "title": "Show only what you need",
             "text": "*Unread* lists the new ones. *Needs my acknowledgement* lists those you still have to confirm."},
            {"type": "info", "path": "/announcements/", "target": ["article.ann-card"],
             "missing": "There is no announcement to show with this filter. New ones appear here and on your Home page.",
             "skippable": True,
             "title": "One announcement",
             "text": "Pinned notices stay at the top. Press *Read* to open the full text, any attachment and "
                     "the poll, if there is one."},
            {"type": "info", "path": "/announcements/", "target": ["article.ann-card form[action$='/acknowledge/'] button"],
             "optional": True, "title": "Acknowledge",
             "text": "Some notices ask you to confirm you have read them. Read it first, then press "
                     "*Acknowledge*. It is recorded with the date and time."},
            {"type": "info", "title": "You will not miss one",
             "text": "New announcements also show on your Home page and under the bell. One marked *Action "
                     "needed* keeps reminding you until you acknowledge it."},
        ],
    },
    {
        "id": "nbte-announcement-post",
        "title": "Post an announcement",
        "summary": "For staff allowed to post: write a notice, pick the audience and publish it.",
        "audience": ("staff",), "host": "staff", "group": EVERYDAY, "start": "/announcements/",
        "pages": ("/announcements/",), "role": "staff who may post",
        "keywords": "announcement post publish new notice audience poll pin acknowledge draft report",
        "steps": [
            {"type": "click", "path": "/announcements/", "target": [".ann-qa-hero a[href='/announcements/new/']"],
             "missing": "You do not have the *New announcement* button. Only staff who are allowed to post "
                        "announcements see it.",
             "title": "Start a new announcement", "text": "Click *New announcement*."},
            {"type": "fill", "path": "/announcements/new/", "target": ["#id_title"], "closest": "section",
             "title": "1. The message",
             "text": "Give it a short title and write the notice. Choose *Notice*, *Action needed* or "
                     "*Urgent*, and attach a file if it helps."},
            {"type": "info", "path": "/announcements/new/", "target": ["#id_poll_question"], "closest": "section",
             "optional": True, "title": "A poll, if you want one",
             "text": "Ask one question with up to six answers. Leave it blank for an ordinary notice."},
            {"type": "info", "path": "/announcements/new/", "target": [".ann-form [data-ann-audience]"],
             "title": "2. Who should see it",
             "text": "*All staff*, one department, or specific roles. Only those people see it and are notified."},
            {"type": "info", "path": "/announcements/new/", "target": ["#id_expires_at"], "closest": "section",
             "title": "3. Options",
             "text": "Tick *Ask staff to acknowledge* when you need proof it was read. *Pin to the top* keeps "
                     "it above newer ones. An expiry date removes it automatically."},
            {"type": "info", "path": "/announcements/new/", "target": [".ann-form .ann-form-actions"],
             "title": "4. Publish",
             "text": "*Publish* sends it at once. *Save draft* keeps it under *My drafts*. End this guide when "
                     "you are ready to publish."},
            {"type": "info", "title": "See who has read it",
             "text": "On your own announcements, *Report* shows who has read and acknowledged it, and lets "
                     "you remind those who have not."},
        ],
    },
    {
        "id": "nbte-notifications",
        "title": "Your notifications",
        "summary": "The bell, the full list, and choosing what reaches you.",
        "audience": ("staff",), "host": "staff", "group": EVERYDAY, "start": "/notifications/",
        "pages": ("/notifications/",),
        "keywords": "notification bell alert unread mark read settings email sound",
        "next": "nbte-search",
        "steps": [
            {"type": "info", "target": ["#notif-bell"], "title": "The bell",
             "text": "It is on every page. The number is what you have not read. Click it for the latest few "
                     "without leaving the page you are on."},
            {"type": "info", "path": "/notifications/", "target": [".notif-qa-hero"], "title": "All your notifications",
             "text": "Everything the system has told you, newest first. *Mark all read* clears the count in one go."},
            {"type": "info", "path": "/notifications/", "target": ["nav.notif-filters"], "title": "Unread or read",
             "text": "Show only the unread ones, or search for an older notification by its words."},
            {"type": "info", "path": "/notifications/", "target": ["#notif-list-container"], "title": "Act on one",
             "text": "*Open* takes you straight to the thing it is about and marks it read. *Select* lets you "
                     "mark or delete several at once."},
            {"type": "info", "path": "/notifications/", "target": [".notif-qa-hero a.notif-settings-link"], "optional": True,
             "title": "Choose what reaches you",
             "text": "Under *Settings* you decide which notifications you get, and how."},
        ],
    },
    {
        "id": "nbte-search",
        "title": "Search the whole system",
        "summary": "Find a person, task, ticket, request or memo from any page.",
        "audience": ("staff",), "host": "staff", "group": EVERYDAY, "start": "/home/", "pages": ("/home/$",),
        "keywords": "search find look up staff colleague reference number global",
        "steps": [
            {"type": "fill", "target": ["#app-topsearch-q"], "closest": "form.app-topsearch", "via": STAFF_MENU,
             "missing": "The search box is not showing on this screen size.", "skippable": True,
             "title": "Type what you are looking for",
             "text": "A name, a few words, or a reference such as a task or ticket number. Try it now."},
            {"type": "info", "target": ["#app-search-results"], "optional": True, "title": "Results, by kind",
             "text": "Results are grouped: staff, tasks, tickets, requests and more. You only see what you "
                     "are allowed to open. Click one, or use the arrow keys and *Enter*."},
            {"type": "info", "title": "A shortcut",
             "text": "Press */* or *Ctrl + K* on any page to jump to search, and *Esc* to close it."},
        ],
    },
    # ------------------------------------------------------------------ requests
    {
        "id": "nbte-request-raise",
        "title": "Raise a request",
        "summary": "Ask for something that needs approval: equipment, repairs, a vehicle, funds.",
        "audience": ("staff",), "host": "staff", "group": WORK, "start": "/workflow/new/",
        "pages": ("/workflow/",),
        "keywords": "request approval raise new workflow equipment repair vehicle funds printer network submit",
        "next": "nbte-request-track",
        "steps": [
            {"type": "info", "path": "/workflow/new/", "target": ["#app-content .nbte-hero"], "title": "What do you need?",
             "text": "A request is anything that needs someone's approval or another department's help. Each "
                     "type has its own form and its own approval chain."},
            {"type": "click", "path": "/workflow/new/", "target": ["#app-content a.wf-type-card"],
             "missing": "No request type is set up for your role yet. Ask ICT to add one.",
             "title": "Pick the type", "text": "Each card says where the request goes. Click the one that fits."},
            {"type": "info", "path_re": REQUEST_FORM, "target": ["#app-content form .wf-schema-title"], "closest": ".wf-schema, fieldset, .card, div",
             "optional": True, "title": "Answer the questions",
             "text": "Fields marked * are required. They change with the type of request, so the right "
                     "people get the facts they need."},
            {"type": "fill", "path_re": REQUEST_FORM, "target": ["#id_subject"], "closest": ".field, .form-group, div",
             "title": "Say it in one line",
             "missing": "This request type could not be opened.", "open": {"label": "Choose a type", "url": "/workflow/new/"},
             "text": "Write a short subject, then add details below it. A clear subject gets a faster answer."},
            {"type": "info", "path_re": REQUEST_FORM, "target": ["#id_priority"], "closest": ".field, .form-group, div",
             "optional": True, "title": "How urgent is it?", "text": "Leave the priority as it is unless work has stopped."},
            {"type": "info", "path_re": REQUEST_FORM, "target": ["#app-content form button[type='submit']"],
             "title": "Submit",
             "text": "Press *Submit request*. It goes to the first approver at once and you get a reference "
                     "number. End this guide when you are ready to submit."},
            {"type": "info", "title": "What happens next",
             "text": "Each approver approves it, returns it to you for a correction, or rejects it with a "
                     "reason. Then the receiving department does the work. You are notified at every step."},
        ],
    },
    {
        "id": "nbte-request-track",
        "title": "Track a request and approve others",
        "summary": "See where your request has reached, and act on the ones waiting for you.",
        "audience": ("staff",), "host": "staff", "group": WORK, "start": "/workflow/",
        "pages": ("/workflow/",),
        "keywords": "request track status approval approve return reject awaiting timeline comment my requests",
        "next": "nbte-ticket-raise",
        "steps": [
            {"type": "info", "path": "/workflow/", "target": ["#app-content .wk-hero"], "title": "My requests",
             "text": "Every request you have raised. The counters show how many are still waiting, in "
                     "progress and completed."},
            {"type": "info", "path": "/workflow/", "target": ["#app-content .wf-tabs"], "optional": True, "title": "Filter by status",
             "text": "*Returned for revision* is the one to watch: those are waiting for *you* to correct and resubmit."},
            {"type": "click", "path": "/workflow/", "target": ["#wf-list-container tbody tr[data-href]"],
             "missing": "You have not raised a request yet.", "skippable": True,
             "open": {"label": "Raise a request", "url": "/workflow/new/"},
             "title": "Open a request", "text": "Click a row to open it."},
            {"type": "info", "path_re": REQUEST_PAGE, "target": ["#app-content .wf-detail-side"], "optional": True,
             "title": "Where it is now", "text": "The current step and who holds it. A closed request shows how it ended."},
            {"type": "info", "path_re": REQUEST_PAGE, "target": ["[data-tour='wf-approvals']", "[data-tour='wf-timeline']"],
             "optional": True, "title": "Who has approved",
             "text": "Each approver in order, with the decision and any remark. The *Timeline* above lists "
                     "everything that has happened."},
            {"type": "info", "path_re": REQUEST_PAGE, "target": ["[data-tour='wf-comments']"], "optional": True,
             "title": "Ask or answer",
             "text": "Use comments to add a fact or answer a question. Everyone on the request sees them. "
                     "Attachments are just below."},
            {"type": "navigate", "url": "/workflow/awaiting/"},
            {"type": "info", "path": "/workflow/awaiting/", "target": ["#app-content .wk-hero"], "title": "Awaiting my action",
             "text": "Requests from other people that are waiting for *your* approval. Open one, read it, "
                     "then approve, return or reject it with a remark."},
            {"type": "info", "path": "/workflow/awaiting/", "target": ["form.action-queue-bulk-form"], "optional": True,
             "title": "Your queue",
             "text": "The oldest and most urgent are first. When this says *You are all caught up*, nothing "
                     "is waiting for you."},
        ],
    },
    # ------------------------------------------------------------------ tickets
    {
        "id": "nbte-ticket-raise",
        "title": "Report an ICT problem (raise a ticket)",
        "summary": "Tell the helpdesk what is wrong with your computer, network or account.",
        "audience": ("staff",), "host": "staff", "group": WORK, "start": "/tickets/new/",
        "pages": ("/tickets/",),
        "keywords": "ticket helpdesk ict problem issue computer laptop printer network password support raise report",
        "next": "nbte-ticket-follow",
        "steps": [
            {"type": "info", "path": "/tickets/new/", "target": ["#app-content .nbte-hero"], "title": "The ICT helpdesk",
             "text": "Raise a ticket when something ICT looks after is broken or you need access. It goes "
                     "straight to the helpdesk and is tracked until it is fixed."},
            {"type": "click", "path": "/tickets/new/", "target": ["#app-content a.tk-cat-card"],
             "missing": "No ticket category is set up yet. Ask ICT to add one.",
             "title": "Pick a category", "text": "Click the kind of problem. Not sure? Choose *Other*."},
            {"type": "fill", "path_re": TICKET_FORM, "target": [".tk-form [name='subject']"], "closest": "label.tk-field",
             "missing": "This category could not be opened.", "open": {"label": "Choose a category", "url": "/tickets/new/"},
             "title": "Say what is wrong",
             "text": "One line for the subject. In the box below it, describe what happened and copy any "
                     "error message."},
            {"type": "info", "path_re": TICKET_FORM, "target": [".tk-form [name='priority']"], "closest": "label.tk-field",
             "optional": True, "title": "Priority and place",
             "text": "Use *Urgent* only when work has stopped. Give your office or room so the officer can find you."},
            {"type": "info", "path_re": TICKET_FORM, "target": [".tk-form [name='asset']"], "closest": "label.tk-field",
             "optional": True, "title": "Which device?",
             "text": "If it is about a device in your custody, pick it here. Its history then travels with the ticket."},
            {"type": "info", "path_re": TICKET_FORM, "target": [".tk-form input[type='file']"], "closest": "label.tk-field",
             "optional": True, "title": "Add a screenshot", "text": "A photo or screenshot of the problem saves a lot of questions."},
            {"type": "info", "path_re": TICKET_FORM, "target": [".tk-form button[type='submit']"], "title": "Submit",
             "text": "Press *Submit ticket*. You get a ticket number and the helpdesk is notified. End this "
                     "guide when you are ready to submit."},
        ],
    },
    {
        "id": "nbte-ticket-follow",
        "title": "Follow up on a ticket",
        "summary": "See who has your ticket, answer the officer, then confirm and rate the fix.",
        "audience": ("staff",), "host": "staff", "group": WORK, "start": "/tickets/my/",
        "pages": ("/tickets/",),
        "keywords": "ticket follow up status comment reply resolved close reopen rate helpdesk my tickets assigned",
        "next": "nbte-tasks",
        "steps": [
            {"type": "info", "path_re": TICKET_LISTS, "target": ["#app-content .wk-hero"], "title": "Your tickets",
             "text": "*My tickets* are the ones you raised. ICT officers also have *Assigned to me* for "
                     "tickets they are working on."},
            {"type": "click", "path_re": TICKET_LISTS,
             "target": ["#my-tickets-container a.tk-card", "#assigned-container a.tk-card"],
             "missing": "You have no ticket here yet.", "skippable": True,
             "open": {"label": "Raise a ticket", "url": "/tickets/new/"},
             "title": "Open a ticket", "text": "Each card shows the ticket number, its status and priority. Click one."},
            {"type": "info", "path_re": TICKET_PAGE, "target": ["#app-content aside.tk-side"], "optional": True,
             "title": "Status and who has it",
             "text": "The current status, the officer handling it and the time allowed. Any action open "
                     "to you is in this column."},
            {"type": "info", "path_re": TICKET_PAGE, "target": ["#path", "#tkt-timeline-container"], "closest": "section",
             "optional": True, "title": "What has been done",
             "text": "The path the ticket has taken and every action on it, with the time and the person."},
            {"type": "info", "path_re": TICKET_PAGE, "target": ["[data-tour='tk-comments']"], "optional": True,
             "title": "Talk to the officer",
             "text": "Answer questions and add new facts here. If the ticket is *Awaiting* you, your reply "
                     "sends it back to the officer."},
            {"type": "info", "title": "When it is fixed",
             "text": "You are notified when the ticket is resolved. Check the fix, then close and rate it. "
                     "If the problem is still there, reopen it with a note."},
        ],
    },
    # ------------------------------------------------------------------ tasks
    {
        "id": "nbte-tasks",
        "title": "Work on a task assigned to you",
        "summary": "Find your tasks, accept and start one, report progress and mark it done.",
        "audience": ("staff",), "host": "staff", "group": WORK, "start": "/tasks/assigned/",
        "pages": ("/tasks/",),
        "keywords": "task tasks assigned my work accept start timer time log hours done status comment review",
        "next": "nbte-task-create",
        "steps": [
            {"type": "info", "path_re": TASK_LISTS, "target": ["#app-content .wk-hero"], "title": "Tasks assigned to you",
             "text": "Work other people have given you. Tasks you gave to others are under *Created by me* in "
                     "the Tasks menu."},
            {"type": "info", "path_re": TASK_LISTS, "target": ["form.tsk-filters"], "optional": True, "title": "Sort and filter",
             "text": "Search, show one status only, or sort by due date to see what is most pressing."},
            {"type": "click", "path_re": TASK_LISTS, "target": ["#tsk-list-container a.tsk-card"],
             "missing": "Nothing is assigned to you right now. New tasks appear here and under the bell.",
             "skippable": True,
             "title": "Open a task", "text": "Each card shows the status, priority and due date. Click one."},
            {"type": "info", "path_re": TASK_PAGE, "target": ["#app-content .tsk-tbar"], "optional": True,
             "title": "Accept, start, finish",
             "text": "*Accept and start* tells the creator you have begun and starts your timer. Pause "
                     "the timer when you stop. When your part is finished, press *Mark my part done*."},
            {"type": "info", "path_re": TASK_PAGE, "target": ["#tsk-timeline-container"], "closest": "section",
             "optional": True, "title": "Updates",
             "text": "Every change to the task is listed here: who did what, and when."},
            {"type": "info", "path_re": TASK_PAGE, "target": ["form.tsk-compose"], "closest": "section", "optional": True,
             "title": "Report progress",
             "text": "Post a comment to say how far you have got or what is holding you up. Attach files "
                     "in the panel above."},
            {"type": "info", "path_re": TASK_PAGE, "target": ["#more-actions"], "optional": True, "title": "Cannot do it?",
             "text": "Under *More actions* you can ask the creator a question, or return the task with a reason."},
            {"type": "info", "title": "Your time is logged for you",
             "text": "The timer records the hours you spend; they show in the *Time log* on the task. If "
                     "the task needs a review, the reviewer confirms your work before it closes."},
        ],
    },
    {
        "id": "nbte-task-create",
        "title": "Give someone a task",
        "summary": "Create a task, set the due date and choose who does it.",
        "audience": ("staff",), "host": "staff", "group": WORK, "start": "/tasks/new/",
        "pages": ("/tasks/",),
        "keywords": "task create new assign delegate due date assignee review recurring",
        "steps": [
            {"type": "info", "path": "/tasks/new/", "target": ["#app-content .nbte-hero"], "title": "What kind of task?",
             "text": "The category decides the form and the defaults. For ordinary work, choose *General*."},
            {"type": "click", "path": "/tasks/new/", "target": ["#app-content a.tsk-cat-card"],
             "missing": "No task category is open to you. Ask your head of department.",
             "title": "Pick a category", "text": "Click a category card."},
            {"type": "fill", "path_re": TASK_FORM, "target": [".tsk-form [name='title']"], "closest": "label.tsk-field",
             "missing": "This category could not be opened.", "open": {"label": "Choose a category", "url": "/tasks/new/"},
             "title": "What needs doing",
             "text": "One clear line as the title. In the description, say what *done* looks like."},
            {"type": "info", "path_re": TASK_FORM, "target": [".tsk-form [name='due_date']"], "closest": "label.tsk-field",
             "optional": True, "title": "Priority and dates",
             "text": "Set a due date so the task shows as overdue when it slips, and the assignee is reminded."},
            {"type": "info", "path_re": TASK_FORM, "target": [".tsk-form [data-picker-trigger]"], "optional": True,
             "title": "Who does it",
             "text": "Press *Add people* and tick one or more colleagues. With several people, each "
                     "marks their own part done."},
            {"type": "info", "path_re": TASK_FORM, "target": [".tsk-form button[name='_open_now']"], "title": "Create it",
             "text": "*Create & open* notifies the assignees at once. *Save as draft* keeps it to yourself "
                     "until you open it. End this guide when you are ready."},
        ],
    },
]
