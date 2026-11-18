"""Guided tours: NBTE QA workspace (/qa/), part 2 (inspection, visits, records and set-up). Every tour here is host "staff", area "qa".

Plain data, gathered into ``qa_core.tours.TOURS``. The fields of a tour and
of a step are documented at the top of qa_core/tours.py.
"""
from .tour_lib import LAUNCHER, MENU, PB_HOME, STAFF_MENU, nav, staff_nav  # noqa: F401

PEOPLE = "QA: visits and people"
RECORDS = "QA: records and set-up"

HERO = ["#qa-panel header.qa-hero", "#qa-panel h1"]
LAND = 9000  # ms to wait for the first step after a click: the page it opens may be slow to arrive


def _insp_tab(url):
    """A tab in the strip at the top of every inspection page."""
    return [f".qa-insp-nav a[href='{url}']"]


RP_LIST = "/qa/inspection/resource-persons/"
RP_ONE = r"^/qa/inspection/resource-persons/\d+/$"
FINDER = "/qa/inspection/finder/"
MAPPINGS = "/qa/inspection/mappings/"
INSPECTIONS = "/qa/inspection/inspections/"
INSPECTION = r"^/qa/inspection/inspections/\d+/$"
REPORT = r"^/qa/inspection/reports/\d+/$"
CALENDAR = "/qa/inspection/calendar/"
DOCS = "/qa/documents/"
DOC = r"^/qa/documents/\d+/$"
LETTERS = "/qa/documents/official-letters/"
LETTER = r"^/qa/documents/official-letters/\d+/$"
BODIES = "/qa/professional-bodies/"
BODY = r"^/qa/professional-bodies/\d+/$"
BODY_MAPS = "/qa/professional-bodies/mappings/"
BODY_IMPORT = "/qa/professional-bodies/import/"
MASTER = "/qa/professional-bodies/master/"
PROG = "/qa/programmes/"
PROG_REQS = "/qa/programmes/requests/"
REQUIREMENTS = "/qa/programmes/requirements/"
RULES = "/qa/settings/rules/"
SETTINGS_ANY = r"^/qa/settings/"
PENDING = "/qa/pending/"
PENDING_ONE = r"^/qa/pending/\d+/$"
FACILITIES = "/qa/facilities/"
FACILITY = r"^/qa/facilities/(?!import/|export)[\w-]+/$"

QA_STAFF_B_TOURS = [
    # ------------------------------------------------------------------ visits and people
    {
        "id": "staff-rp-register",
        "needs": "inspection",
        "title": "The resource persons register",
        "summary": "Find an assessor, read their record, and see how nominations are vetted.",
        "audience": ("staff",), "host": "staff", "group": PEOPLE, "start": RP_LIST,
        "pages": (RP_LIST, "/qa/inspection/nominations/"),
        "keywords": "resource person assessor register nominate nomination vetting cv eligible discipline rank load",
        "next": "staff-rp-finder",
        "steps": [
            {"type": "info", "path": RP_LIST, "target": HERO,
             "missing": "This register is open to QA staff who plan or approve visits.",
             "title": "The assessors register",
             "text": "Everyone NBTE can send on a visit: resource persons from industry, academia and the "
                     "professional bodies. The numbers show who is active and who is waiting to be vetted."},
            {"type": "info", "path": RP_LIST, "target": ["section[aria-label='Resource persons'] form.qa-toolbar"],
             "title": "Find a person",
             "text": "Search by name, e-mail or body, or filter by status, source, discipline and eligibility."},
            {"type": "info", "path": RP_LIST, "target": ["#qa-rows"], "title": "Who is on the register",
             "text": "Each row shows rank, disciplines, current load and when they last served. *No longer "
                     "eligible* means current work continues, but they cannot be given new work."},
            {"type": "click", "path": RP_LIST, "target": ["#qa-rows a[href*='/resource-persons/']"],
             "missing": "Nobody matches these filters. Choose *Any status* or clear the search.", "skippable": True,
             "title": "Open a person", "text": "Click a name to open the full record."},
            {"type": "info", "path_re": RP_ONE, "target": [".vx-kpis", "#qa-panel h1"], "wait": LAND,
             "title": "Service in numbers",
             "text": "Visits this year, total service and rating. The availability finder ranks people from these figures."},
            {"type": "info", "path_re": RP_ONE, "target": ["section[aria-labelledby='rp-h1']"], "optional": True,
             "title": "Profile", "text": "Rank, employer, disciplines and contact details, as supplied at nomination."},
            {"type": "info", "path_re": RP_ONE, "target": ["section[aria-labelledby='rp-h3']"], "optional": True,
             "title": "Engagements", "text": "The cases this person is mapped to now, and the duty on each."},
            {"type": "info", "path_re": RP_ONE, "target": ["section[aria-labelledby='rp-h5']"], "optional": True,
             "title": "Vetting",
             "text": "A nominated person must be vetted before use. The vetter is never the person who "
                     "nominated them: check the CV, then approve or decline with a reason."},
            {"type": "info", "target": _insp_tab("/qa/inspection/nominations/"), "optional": True,
             "title": "Nominations waiting",
             "text": "People still waiting for a vetting decision are listed under *Nominations*."},
        ],
    },
    {
        "id": "staff-rp-finder",
        "needs": "inspection",
        "title": "Find people for a visit and approve the mapping",
        "summary": "Availability finder: choose the case, map a person, then Director and ES approve.",
        "audience": ("staff",), "host": "staff", "group": PEOPLE, "start": FINDER,
        "pages": (FINDER, MAPPINGS, "/qa/inspection/personnel/"),
        "keywords": "availability finder map mapping resource person personnel team visit approve director es "
                    "eligible rules booking duty dates sop-03",
        "next": "staff-visit-record",
        "steps": [
            {"type": "info", "path": FINDER, "target": HERO,
             "missing": "The availability finder is open to QA staff who plan or approve visit teams.",
             "title": "Who is free for the visit",
             "text": "The finder lists only people who pass every rule for the dates you give, best match "
                     "first. The four steps are shown under the heading."},
            {"type": "fill", "path": FINDER, "target": ["section[aria-label='What the mapping is for'] select[name='case']"],
             "missing": "No case is ready for a team yet. A case appears here once it reaches the personnel stage.",
             "skippable": True, "open": {"label": "Open the case board", "url": "/qa/cases/"},
             "title": "1. Choose the case",
             "text": "Pick the case the visit belongs to. The programme list and the visit dates then fill in from the case."},
            {"type": "info", "path": FINDER, "target": ["section[aria-label='What the mapping is for'] form.qa-toolbar"],
             "title": "Programme, duty and dates",
             "text": "Set the programme, the duty and the dates. *Who* limits the list to resource persons or "
                     "NBTE personnel; *Show* can also list people who are not eligible, with the reason."},
            {"type": "info", "path": FINDER, "target": ["#qa-rows"], "optional": True, "title": "2. Pick a person",
             "text": "People are ranked by fewest visits this year, longest since last served, then rating. "
                     "*Map* holds their dates at once, so nobody else can book them."},
            {"type": "info", "path": FINDER, "target": ["details.qa-rules"], "optional": True, "title": "The rules",
             "text": "Open this to read the rules every candidate is checked against. Nobody can override them."},
            {"type": "navigate", "url": MAPPINGS},
            {"type": "info", "path": MAPPINGS, "target": ["section[aria-label='Mappings']"], "title": "3. Mappings",
             "text": "Every booking: who covers which programme on which case. Search, or filter by status, duty and who."},
            {"type": "info", "path": MAPPINGS, "target": ["#qa-rows .qa-map-steps"], "closest": "tr", "optional": True,
             "title": "Two approvals",
             "text": "A mapping needs the *Director* first, then the *ES*. The two ticks show how far it has got."},
            {"type": "info", "path": MAPPINGS, "target": ["#qa-rows form.qa-map-actions"], "optional": True,
             "title": "4. Approve or send back",
             "text": "If a mapping waits for you, *Approve* confirms it. *Send back* releases the person's "
                     "dates so someone else can be mapped."},
            {"type": "info", "title": "After approval",
             "text": "An approved person is invited, joins the case's visit team and sees the engagement on "
                     "the portal. The visit itself appears under *Inspections* once the ES approves it."},
        ],
    },
    {
        "id": "staff-visit-record",
        "needs": "inspection",
        "title": "Record a visit and write the post-visit report",
        "summary": "For team leads: mark the visit completed, confirm attendance, compile the report.",
        "audience": ("staff",), "host": "staff", "group": PEOPLE, "role": "Team Lead", "start": INSPECTIONS,
        "pages": (INSPECTIONS, "/qa/inspection/reports/", CALENDAR),
        "keywords": "inspection visit record completed attendance post-visit report team lead compile findings "
                    "endorse calendar stage 12 sop-03 vvt",
        "next": "staff-issued-documents",
        "steps": [
            {"type": "info", "path": INSPECTIONS, "target": HERO,
             "missing": "Inspections are open to QA staff who take part in or oversee visits.",
             "title": "Every visit NBTE makes",
             "text": "A visit appears here by itself when the ES approves it on the case (stage 10). You never create one by hand."},
            {"type": "info", "path": INSPECTIONS, "target": ["section[aria-label='Inspections'] form.qa-toolbar"],
             "title": "Find your visit",
             "text": "Team leads: set *Whose* to *I lead*. You can also filter by status, time and department."},
            {"type": "click", "path": INSPECTIONS, "target": ["#qa-rows a.qa-rowlink"],
             "missing": "No visit matches. Clear the filters; if the list is still empty, no visit has been approved yet.",
             "skippable": True, "title": "Open a visit", "text": "Click a visit to open it."},
            {"type": "info", "path_re": INSPECTION, "target": ["ol.vx-steps", "#qa-panel h1"], "wait": LAND,
             "title": "Where this visit is",
             "text": "Approved, scheduled, visit recorded, then the post-visit reports. The current step is highlighted."},
            {"type": "info", "path_re": INSPECTION, "target": ["section[aria-labelledby='ins-team']"], "optional": True,
             "title": "The planned team", "text": "Everyone mapped and approved for this visit, with their role."},
            {"type": "info", "path_re": INSPECTION,
             "target": ["section[aria-labelledby='ins-done']", "section[aria-labelledby='ins-att']"],
             "optional": True, "title": "Record the visit",
             "text": "After the visit the *Team Lead* enters the actual dates and anyone who did not go, then "
                     "presses *Mark the visit completed*. Attendance decides allowances and service history."},
            {"type": "info", "path_re": INSPECTION, "target": ["section[aria-labelledby='ins-rep']"],
             "title": "Post-visit reports",
             "text": "Recording the visit opens one report per programme. Each goes Team, Division Head, "
                     "Deputy Director, then Director."},
            {"type": "click", "path_re": INSPECTION, "target": ["section[aria-labelledby='ins-rep'] a.qa-rowlink"],
             "optional": True, "title": "Open a report", "text": "Click a programme's report."},
            {"type": "info", "path_re": REPORT, "target": ["section[aria-label='Endorsement chain']", "#qa-panel h1"],
             "wait": LAND, "title": "Where the report is",
             "text": "One report for this programme. The chain shows each layer it must pass and who has it "
                     "now; the buttons above open the institution's self-study and the compiled PDF."},
            {"type": "info", "path_re": REPORT, "target": ["section[aria-labelledby='team-reports-h']"], "optional": True,
             "title": "Each attendee reports first",
             "text": "Every team member submits their own findings. The Team Lead accepts each one, returns "
                     "it with a comment, or skips an attendee with a reason."},
            {"type": "info", "path_re": REPORT,
             "target": ["section[aria-labelledby='rep-h']", "section[aria-label='Your role in this report']"],
             "optional": True, "title": "Compile and submit",
             "text": "The Team Lead writes the programme report from the accepted findings. *Save draft* as "
                     "often as you like; *Submit* unlocks when every attendee is accepted or skipped."},
            {"type": "info", "path_re": REPORT,
             "target": ["section[aria-labelledby='end-h']", "section[aria-labelledby='hist-h']"], "optional": True,
             "title": "Endorsements",
             "text": "After you submit, each layer endorses, adds content or returns it with a remark. "
                     "Every decision is kept in the history."},
            {"type": "navigate", "url": CALENDAR},
            {"type": "info", "path": CALENDAR, "target": ["section[aria-labelledby='cal-h']", "#qa-panel h1"],
             "title": "The visit calendar",
             "text": "All visits month by month: blue is scheduled, green is recorded, red is cancelled. Click a visit to open it."},
        ],
    },
    # ------------------------------------------------------------------ records and set-up
    {
        "id": "staff-issued-documents",
        "needs": "documents_register",
        "title": "Issued documents and official letters",
        "summary": "The verification register, uploading the signed official copy, and scanning a document.",
        "audience": ("staff",), "host": "staff", "group": RECORDS, "start": DOCS,
        "pages": (DOCS, "/scan/"),
        "keywords": "issued documents register letter certificate receipt official copy signed upload provisional "
                    "revoke qr scan verify verification code draft assign",
        "next": "staff-professional-bodies",
        "steps": [
            {"type": "info", "path": DOCS, "target": HERO,
             "missing": "The issued documents register is open to directors, the ES and records staff.",
             "open": {"label": "Open Scan and verify", "url": "/scan/"},
             "title": "Everything NBTE has issued",
             "text": "Every letter, certificate and receipt the system produced, each with a verification "
                     "code and QR. The tiles count valid, awaiting official copy and revoked."},
            {"type": "info", "path": DOCS, "target": ["section[aria-label='Documents'] form.dr-filters"],
             "title": "Search the register",
             "text": "Search by reference, verification code, institution or title, or filter by type and status."},
            {"type": "click", "path": DOCS, "target": ["#doc-panel a.dr-row"],
             "missing": "No document matches. Clear the filters; if it is still empty nothing has been issued yet.",
             "skippable": True, "title": "Open a document", "text": "Click a row to open it."},
            {"type": "info", "path_re": DOC, "target": ["section[aria-label='What a scan shows']", "#qa-panel h1"],
             "wait": LAND, "title": "What the public sees",
             "text": "This is exactly what anyone sees when they scan the QR or type the code: valid, provisional or revoked."},
            {"type": "info", "path_re": DOC,
             "target": ["#dr-upload", "section[aria-label='Files']"], "optional": True,
             "title": "Files and the official copy",
             "text": "The generated PDF is *provisional* until the signed official copy is uploaded here. "
                     "Revoking needs a reason and shows on every later scan."},
            {"type": "info", "path_re": DOC, "target": ["section[aria-label='Verification history']"], "optional": True,
             "title": "Verification history", "text": "Each time the document was checked, and how."},
            {"type": "navigate", "url": LETTERS},
            {"type": "info", "path": LETTERS, "target": ["section[aria-label='Letters']"],
             "missing": "The official letters queue is for directors (their own department), the ES and super admins.",
             "skippable": True, "title": "Official letters to upload",
             "text": "Letters still waiting for their signed copy, oldest first. The tiles split them into "
                     "nobody assigned, with staff, draft ready and overdue."},
            {"type": "info", "path": LETTERS, "target": ["#ol-panel a.btn-subtle[href*='official-letters/']", "#ol-panel a.btn"],
             "optional": True, "title": "Assign, review, upload",
             "text": "Open a letter to *Assign* a staff member to prepare it. They send a draft; you review "
                     "it or send it back with comments, then upload the signed official copy."},
            {"type": "info", "target": staff_nav("/scan/"), "via": STAFF_MENU, "optional": True, "title": "Scan and verify",
             "text": "To check a paper someone hands you, open *Scan*: point the camera at the QR, choose an image, or type the code."},
        ],
    },
    {
        "id": "staff-professional-bodies",
        "needs": "professional_bodies",
        "title": "Professional bodies and their programmes",
        "summary": "The bodies register, a body's page, programme mappings, and Excel import and export.",
        "audience": ("staff",), "host": "staff", "group": RECORDS, "start": BODIES,
        "pages": (BODIES,),
        "keywords": "professional body bodies council coren cpn pcn mapping regulate joint qa import export excel "
                    "xlsx template portal account registrar",
        "next": "staff-programme-requirements",
        "steps": [
            {"type": "info", "path": BODIES, "target": HERO,
             "missing": "Professional bodies are open to QA staff who work on joint QA.",
             "title": "Professional bodies",
             "text": "The councils and institutes that regulate programmes with NBTE. A QA request for one "
                     "of their programmes becomes a joint exercise."},
            {"type": "info", "path": BODIES, "target": ["section[aria-label='Professional bodies'] form.qa-toolbar"],
             "title": "Find a body", "text": "Search, or filter by status, level and whether an e-mail is on file."},
            {"type": "info", "path": BODIES, "target": ["#qa-panel .qa-hero__actions"], "optional": True,
             "title": "Excel in and out",
             "text": "*Export* downloads the bodies and their mappings as a workbook. *Import* takes the "
                     "edited workbook back, and shows you every change before anything is applied."},
            {"type": "click", "path": BODIES, "target": ["#qa-rows a.pb-card"],
             "missing": "No body matches these filters. Clear them to see the full register.", "skippable": True,
             "title": "Open a body", "text": "Click a body to open its page."},
            {"type": "info", "path_re": BODY, "target": ["section[aria-labelledby='pb-maps']", "#qa-panel h1"],
             "wait": LAND, "title": "Programmes it regulates",
             "text": "These mappings decide which QA requests become joint. With permission, *Map a "
                     "programme* adds one at the foot of this list."},
            {"type": "info", "path_re": BODY, "target": ["section[aria-labelledby='pb-progs']"], "optional": True,
             "title": "Programmes at institutions",
             "text": "Every institution programme this body regulates, worked out from the mappings above."},
            {"type": "info", "path_re": BODY, "target": ["section[aria-labelledby='pb-users']"], "optional": True,
             "title": "Portal accounts",
             "text": "The body's people on the QA portal. The registrar uploads letters, representatives go "
                     "on visits, viewers only read."},
            {"type": "info", "path_re": BODY, "target": ["section[aria-labelledby='pb-notices']"], "optional": True,
             "title": "Joint QA cases", "text": "The cases this body has been told about, newest first."},
            {"type": "navigate", "url": BODY_MAPS},
            {"type": "info", "path": BODY_MAPS, "target": ["section[aria-label='Mappings']"], "title": "All mappings",
             "text": "Every programme-to-body mapping in one list. Filter by body, level or status."},
            {"type": "info", "path": BODY_MAPS,
             "target": [".qa-tabs[aria-label='Professional bodies'] a[href='/qa/professional-bodies/import/']"],
             "optional": True, "title": "Import from Excel",
             "text": "Download the template, fill it, then use *Check the file* first. It reports every row "
                     "without saving. Press *Import* only when the check is clean."},
        ],
    },
    {
        "id": "staff-programme-requirements",
        "needs": "programmes",
        "title": "Programme set-up and curriculum requirements",
        "summary": "The master list, requests from bodies, and importing curriculum requirements.",
        "audience": ("staff",), "host": "staff", "group": RECORDS, "start": PROG,
        "pages": (PROG, MASTER), "guides": ("facilities",),
        "keywords": "programme set-up master list curriculum requirements requirement equipment import export xlsx "
                    "body request new programme catalogue ssq shortfall",
        "next": "staff-facilities",
        "steps": [
            {"type": "info", "path": PROG, "target": HERO,
             "missing": "Programme set-up is open to QA staff who maintain the programme lists.",
             "title": "Programme set-up",
             "text": "What NBTE recognises as a programme, who regulates it, and what a department must "
                     "have to run it. The tiles link to each list."},
            {"type": "info", "path": PROG, "target": [".qa-tabs[aria-label='Programmes']"], "title": "The tabs",
             "text": "*Body requests*, *Curriculum requirements*, *Programme mappings* and the *Master list* each have a tab."},
            {"type": "info", "path": PROG, "target": ["section[aria-labelledby='pr-h']"], "optional": True,
             "title": "Waiting for NBTE",
             "text": "A professional body can ask for a programme to be added to its list. Open a request "
                     "to approve or decline it with a reason."},
            {"type": "navigate", "url": REQUIREMENTS},
            {"type": "info", "path": REQUIREMENTS, "target": HERO, "title": "Curriculum requirements",
             "text": "The equipment, staff and space each programme must have. Institutions' self-studies are scored against these lines."},
            {"type": "info", "path": REQUIREMENTS, "target": ["section[aria-label='Requirements'] form.qa-toolbar"],
             "title": "Look up a programme",
             "text": "Choose a programme and kind to see its lines. *Versions* shows retired lines as well."},
            {"type": "info", "path": REQUIREMENTS,
             "target": ["#qa-panel .qa-hero__actions a[href*='/imports/export/']", "#qa-panel .qa-hero__actions"],
             "optional": True, "title": "Export, edit, import",
             "text": "*Export* downloads every line as .xlsx. Edit it in Excel, then *Import*: you see each "
                     "added, changed and removed line and choose what to apply."},
            {"type": "info", "path": REQUIREMENTS, "target": ["section[aria-labelledby='rq-add-h']"], "optional": True,
             "title": "Add one line",
             "text": "For a single item, fill this form. A new line applies to self-studies opened from now on, not to ones already open."},
            {"type": "navigate", "url": MASTER},
            {"type": "info", "path": MASTER, "target": ["section[aria-label='Programme master list']"],
             "title": "The master list",
             "text": "NBTE's list of programme names and levels. Institutions pick from it, so a name is "
                     "spelt one way everywhere. *To confirm* shows rows that still need checking."},
        ],
    },
    {
        "id": "staff-facilities",
        "title": "Facilities and equipment register",
        "summary": "The ND/HND minimum-resource lists: find an item, open a programme, publish to the SSQ catalogue.",
        "audience": ("staff",), "host": "staff", "group": RECORDS, "start": FACILITIES,
        "pages": (FACILITIES,), "guides": ("facilities",),
        "keywords": "facilities equipment register nd hnd minimum resources laboratory workshop item import word "
                    "excel publish catalogue curriculum",
        "next": "staff-qa-settings",
        "steps": [
            {"type": "info", "path": FACILITIES, "target": [".fe-hero", ".fe h1"],
             "title": "Facilities and equipment",
             "text": "The curriculum's minimum list of laboratories, workshops and equipment for every ND and HND programme."},
            {"type": "info", "path": FACILITIES, "target": ["section.fe-kpis"], "optional": True, "title": "What needs attention",
             "text": "Items with no quantity, programmes with no list, and lists not linked to the master list are counted here."},
            {"type": "info", "path": FACILITIES, "target": ["form.fe-search"], "closest": "section", "optional": True,
             "title": "Find an item anywhere",
             "text": "Type an item, for example *oscilloscope*, to see every programme that requires it and how many."},
            {"type": "info", "path": FACILITIES, "target": ["section[aria-labelledby='fe-progs']"],
             "missing": "The register is empty. A handler imports the NBTE Word files or an Excel workbook first.",
             "skippable": True, "title": "Programmes",
             "text": "One row per programme, with its levels, number of items and whether the list is published."},
            {"type": "click", "path": FACILITIES, "target": ["section[aria-labelledby='fe-progs'] tbody a[href^='/qa/facilities/']"],
             "missing": "No programme has a facilities list to open yet.", "skippable": True,
             "title": "Open a programme", "text": "Click a programme name."},
            {"type": "info", "path_re": FACILITY, "target": ["section.fe-sec", ".fe-hero", ".fe h1"], "wait": LAND,
             "title": "The list, by facility",
             "text": "Items are grouped by laboratory or workshop, each with its ND and HND quantity. Handlers edit a row in place."},
            {"type": "info", "path_re": FACILITY, "target": [".fe-cols section.fe-card:nth-of-type(2)", ".fe-cols"],
             "optional": True, "title": "Publish to the SSQ catalogue",
             "text": "Publishing makes this list the yardstick for institutions' self-study shortfall score. "
                     "Link the programme to the master list first."},
            {"type": "info", "path": FACILITIES, "target": ["#handlers"], "optional": True, "title": "Who handles the register",
             "text": "Only the people named here can import, edit and publish. The ES, super admins and the "
                     "Curriculum director assign them."},
        ],
    },
    {
        "id": "staff-qa-settings",
        "needs": "settings",
        "title": "QA settings: where everything is",
        "summary": "Fees, rules, reference numbers, workflows and templates, and which changes need a second officer.",
        "audience": ("staff",), "host": "staff", "group": RECORDS, "start": RULES,
        "pages": ("/qa/settings/",), "guides": ("fees-and-payments",),
        "keywords": "settings configuration fee fees rules thresholds sla formula reference numbers workflow template "
                    "message ssq lookups bank payment methods dual control history",
        "next": "staff-pending-confirmations",
        "steps": [
            {"type": "info", "path_re": SETTINGS_ANY, "target": ["header.qa-hero--settings"],
             "missing": "QA settings are open to directors, the ES and super admins. Ask one of them if a value needs changing.",
             "open": {"label": "Open QA home", "url": "/qa/"},
             "title": "What the QA system runs on",
             "text": "Fees, thresholds, reference numbers, workflows and messages. The tiles count the "
                     "settings, those under dual control and those fixed by policy."},
            {"type": "info", "path_re": SETTINGS_ANY, "target": ["nav.qa-setnav"], "title": "Sections",
             "text": "Settings are grouped: money, rules, documents, forms, workflows and messages. Type in "
                     "the box, for example *fee* or *SSQ*, to find a section."},
            {"type": "info", "path": RULES, "target": ["section[aria-labelledby='qa-rules-title'] form.qa-toolbar"],
             "optional": True, "title": "Find a rule",
             "text": "Search by name, or filter by group, type and how the value is controlled."},
            {"type": "info", "path": RULES, "target": ["#qa-rows .qa-rule"], "optional": True, "title": "One setting",
             "text": "Each row shows the value and what it does. *Change* edits it where you are allowed; "
                     "*History* shows who changed it and when."},
            {"type": "info", "path": RULES, "target": ["section[aria-labelledby='sh-registration-title']"], "optional": True,
             "title": "Institution self-registration",
             "text": "This switch opens or closes registration on the portal, with the contact details shown to applicants."},
            {"type": "info", "target": staff_nav(PENDING), "via": STAFF_MENU, "optional": True,
             "title": "Dual control",
             "text": "A sensitive change does not apply when you save it. It waits under *Pending "
                     "confirmations* until a second officer confirms."},
            {"type": "info", "target": staff_nav("/qa/outbox/"), "via": STAFF_MENU, "optional": True, "title": "E-mail outbox",
             "text": "Every e-mail the QA system writes is kept here, so you can check what an institution was actually sent."},
            {"type": "info", "title": "Before you change a value",
             "text": "A change applies to new work from that moment; bills and cases already raised keep "
                     "their figures. The Word guide at the top of the page explains each setting."},
        ],
    },
    {
        "id": "staff-pending-confirmations",
        "needs": "pending",
        "title": "Confirm or reject a pending change",
        "summary": "Two-step control: review what another officer started, then confirm or reject it.",
        "audience": ("staff",), "host": "staff", "group": RECORDS, "start": PENDING,
        "pages": (PENDING,),
        "keywords": "pending confirmation dual control second officer confirm reject withdraw two-step maker checker approve change",
        "steps": [
            {"type": "info", "path": PENDING, "target": HERO,
             "missing": "Pending confirmations are open to staff who can start or confirm controlled changes.",
             "title": "Two-step control",
             "text": "Changes another officer has started that need a second person. You can never confirm "
                     "your own, and nothing applies until someone else says yes."},
            {"type": "info", "path": PENDING, "target": [".qa-tabs[aria-label='Filter by status']"], "title": "By status",
             "text": "*Pending* is what still needs a decision. The other tabs show what was applied, rejected, expired or withdrawn."},
            {"type": "info", "path": PENDING, "target": ["section[aria-label='Pending confirmations'] form.qa-toolbar"],
             "optional": True, "title": "Narrow the list", "text": "Filter by the kind of change, who started it and how old it is."},
            {"type": "click", "path": PENDING, "target": ["#qa-rows a.qa-pa__link"],
             "missing": "Nothing is waiting in this view. Try the *All* tab to see past requests.", "skippable": True,
             "title": "Open a request", "text": "Click a request to see exactly what would change."},
            {"type": "info", "path_re": PENDING_ONE, "target": ["section[aria-labelledby='pa-diff-title']", "#qa-panel h1"],
             "wait": LAND, "title": "What will change",
             "text": "The old value beside the new one. Read it carefully: confirming applies it at once."},
            {"type": "info", "path_re": PENDING_ONE, "target": ["section[aria-labelledby='pa-about-title']"],
             "optional": True, "title": "Who asked, and why",
             "text": "The officer who started it, their reason, and when the request expires if nobody decides."},
            {"type": "info", "path_re": PENDING_ONE, "target": ["aside[aria-labelledby='pa-decide-title']"],
             "optional": True, "title": "Decide",
             "text": "*Confirm* applies the change; *Reject* needs a remark the requester will see. Your own "
                     "request shows *Withdraw* instead."},
        ],
    },
]
