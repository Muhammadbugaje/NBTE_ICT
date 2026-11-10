"""What the Data Hub exports and imports, in dependency order.

Categories are listed in the order a fresh database must be loaded; inside a
category, sheets are listed in the order they are imported. Each ``Spec``
names one model. Its natural key (the columns that identify a row without a
database id) is worked out from the model's unique fields or constraints, or
given explicitly with ``key=``.

Foreign keys are written as the natural key of the row they point to. A link
to a row that is loaded later is handled automatically:

* same category (a parent office, the next rank in the promotion ladder, a
  QA type's SSQ template): set in a second pass of the same import;
* a later category (a department's head, who is a user): written to a small
  "links" sheet that is placed in that later category, so importing the
  categories one by one, in order, fills every link.
"""
from dataclasses import dataclass, field

# Audit "who did it" columns: not configuration, and pointing at users would
# only create noise on a fresh database. They are left blank on import.
ACTOR_FIELDS = frozenset({
    "created_by", "updated_by", "published_by", "nominated_by", "vetted_by",
    "registered_by", "invited_by", "added_by", "granted_by", "revoked_by",
})


@dataclass
class Spec:
    label: str                       # "app_label.ModelName"
    key: tuple = ()                  # explicit natural key (field names)
    exclude: tuple = ()              # extra fields to leave out
    note: str = ""                   # shown in the README sheet and the hub
    # One model may have several sheets (user accounts: NBTE staff /
    # institution users / other portal users). Each extra sheet names itself:
    sheet: str = ""                  # sheet id suffix ("accounts.User#institution")
    title: str = ""                  # sheet title (default: the model's verbose name)
    scope: str = ""                  # engine.SCOPES key: which rows this sheet exports / counts
    required: tuple = ()             # nullable links that must be filled on this sheet
    strict: tuple = ()               # links that, when filled, must resolve (else the row is refused)
    check: str = ""                  # engine.ROW_CHECKS key: row rule run before saving
    fill: str = ""                   # engine.ROW_FILLS key: fills blank cells (generated keys) before matching
    after: str = ""                  # engine.AFTER_RUN key: runs once after every sheet is loaded
    actor_default: tuple = ()        # required "who" links: blank -> the importing administrator
    aliases: bool = False            # links also accept engine.LINK_ALIASES (category name, staff number ...)
    labels: bool = False             # choice columns are written as their labels, with a dropdown
    dropdowns: tuple = ()            # link columns that get a dropdown of the target's keys


@dataclass
class Category:
    key: str
    title: str
    summary: str
    icon: str
    specs: list = field(default_factory=list)
    link_url: str = ""               # optional dedicated page for this area
    link_label: str = ""
    instructions: tuple = ()         # lines of an "Instructions" sheet added to this category's workbooks


CATEGORIES = [
    Category(
        "settings", "System settings & site content",
        "Every configuration entry (the Settings page), brand logos, backup destinations, "
        "portal categories, history milestones and sign-in showcase images.",
        "key",
        [
            Spec("config.ConfigEntry"),
            Spec("config.BrandLogo"),
            Spec("backups.BackupDestination",
                 exclude=("last_success_at", "last_error", "last_tested_at", "last_test_ok", "last_test_message")),
            Spec("portal.AnnouncementCategory"),
            Spec("portal.EventCategory"),
            Spec("portal.DocumentCategory"),
            Spec("portal.AwardCategory"),
            Spec("portal.GalleryCategory"),
            Spec("public.HistoryMilestone", key=("year", "title")),
            Spec("showcase.ShowcaseImage", key=("title", "image")),
        ],
    ),
    Category(
        "access", "Roles, groups & permissions",
        "Permission groups with their permissions, roles (rank, 2FA, task limits) and the "
        "permissions a manager may grant.",
        "shield",
        [
            Spec("auth.Group"),
            Spec("accounts.Role"),
            Spec("accounts.GroupProfile"),
            Spec("org.GrantablePermission"),
        ],
    ),
    Category(
        "geo", "Geography & reference lists",
        "States, LGAs, zonal offices and the list of banks.",
        "map",
        [
            Spec("staff.StateOfOrigin"),
            Spec("staff.LGA"),
            Spec("staff.ZonalOffice"),
            Spec("honoraria.Bank"),
        ],
    ),
    Category(
        "org", "Organisation, ranks & promotion ladder",
        "Departments (and their parent hierarchy), divisions, offices, grade levels, "
        "designations with the next step of the promotion ladder, staff states and categories, "
        "executive positions, holidays and attendance locations.",
        "building",
        [
            Spec("org.GradeLevel"),
            Spec("staff.Designation", note="next_designation is the promotion ladder (the rank after this one)."),
            Spec("staff.Department", note="parent gives the hierarchy; head/director are filled from the Users category."),
            Spec("org.Division"),
            Spec("staff.Office"),
            Spec("staff.StaffCategory"),
            Spec("staff.StaffState"),
            Spec("staff.ExecutivePosition"),
            Spec("portal.Holiday", key=("name", "date_start")),
            Spec("attendance.AttendanceLocation"),
        ],
    ),
    Category(
        "institution_setup", "Institution & programme masters",
        "Ownerships, institution types and categories, disciplines, programme levels and "
        "categories, document kinds and requirements, professional bodies, the programme "
        "master list, management roles and approval types.",
        "layers",
        [
            Spec("institutions.InstitutionOwnership"),
            Spec("institutions.InstitutionType"),
            Spec("institutions.InstitutionCategory"),
            Spec("institutions.Discipline"),
            Spec("institutions.ProgrammeLevel"),
            Spec("institutions.ProgrammeCategory"),
            Spec("institutions.DocumentKind"),
            Spec("institutions.DocumentRequirement",
                 key=("ownership", "institution_type", "process", "document_kind")),
            Spec("institutions.ProfessionalBody"),
            Spec("institutions.BodyRequirement", key=("body", "code"),
                 note="What each professional body requires of a programme (documents, registrations, ratios)."),
            Spec("institutions.ProgrammeMaster"),
            Spec("institutions.ProgrammeBodyMapping", key=("body", "programme_key", "level")),
            Spec("institutions.ManagementRole"),
            Spec("accreditation.ApprovalType"),
        ],
    ),
    Category(
        "institutions", "Institutions (TVETs) & programmes",
        "Every institution with its contacts, addresses, programmes and management staff.",
        "home",
        [
            Spec("institutions.Institution"),
            Spec("institutions.InstitutionContact", key=("institution", "kind", "value")),
            Spec("institutions.InstitutionAddress", key=("institution", "kind", "line1", "started_at")),
            Spec("institutions.Programme"),
            Spec("institutions.ManagementStaff", key=("institution", "role", "full_name", "start_date")),
        ],
    ),
    Category(
        "users", "NBTE users & staff",
        "NBTE staff sign-in accounts (passwords only as hashes, and only when asked), the staff "
        "directory (nominal roll) mapped to NBTE departments, offices and ranks, permission "
        "overrides, executive tenures, QA personnel and zonal assignments. Also fills the links "
        "that point at people: department heads and directors, division and office heads, "
        "institution desk officers. Institution and other portal accounts are NOT in this "
        "category — they have their own (below).",
        "users",
        [
            Spec("accounts.User", exclude=("password", "last_login", "totp_secret", "two_factor_enabled"),
                 scope="user_staff", check="nbte_user", strict=("department", "role", "institution"),
                 note="NBTE staff accounts only (user_type staff; blank means staff). A department "
                      "(its code) is required for every new account except superusers, and only NBTE "
                      "internal roles are accepted. Institution users go in the Institution users "
                      "category. Passwords are exported only when 'Include password hashes' is ticked "
                      "(a password column of Django hashes, never plaintext). On import a hash is kept "
                      "as is; a new account without one gets the import form's default password. Every "
                      "imported account must change its password at first sign-in."),
            Spec("staff.Staff", scope="staff_nbte", check="nbte_staff_record",
                 note="The nominal roll: NBTE staff only. reports_to gives the reporting hierarchy; "
                      "user must be an NBTE staff account."),
            Spec("accounts.UserPermissionOverride", scope="override_staff", check="override_staff",
                 note="Overrides of NBTE staff accounts."),
            Spec("staff.ExecutiveTenure", key=("position", "display_name", "started_on")),
            Spec("inspection.QAPersonnel", exclude=("booking_version",)),
            Spec("institutions.ZonalAssignment", key=("zone_code", "staff", "from_date")),
            Spec("accreditation.ClearanceDelegate", key=("department", "user"), scope="active_only",
                 exclude=("revoked_at", "revoked_by"),
                 note="Standing departmental-clearance delegates (active ones only)."),
            Spec("honoraria.PayOfficerDelegation", key=("officer",), scope="is_active_only",
                 exclude=("ended_at",), note="The standing pay officer chosen by the Director of Finance (active only)."),
        ],
    ),
    Category(
        "institution_users", "Institution users",
        "QA portal accounts of institutions (Chief Executive, Head of ICT and other portal users), "
        "each mapped to its institution by institution code, and their institution account links. "
        "Imported after Institutions: a row naming an institution code that does not exist is "
        "refused.",
        "building",
        [
            Spec("accounts.User", sheet="institution", title="Institution users",
                 exclude=("password", "last_login", "totp_secret", "two_factor_enabled"),
                 scope="user_institution", check="institution_user", required=("institution",),
                 strict=("role",),
                 note="Institution portal accounts only (user_type institution; blank means "
                      "institution). institution holds the institution code and is required; only "
                      "institution roles (inst-…) are accepted; department stays blank."),
            Spec("institutions.InstitutionUser", check="institution_link", title="Institution account links",
                 note="Links each institution account to its institution: account_kind is "
                      "chief_executive, ict_head or staff_user. The user must be an institution "
                      "account of the same institution."),
        ],
    ),
    Category(
        "portal_users", "Resource persons & professional bodies",
        "QA portal accounts of resource persons and professional bodies, the resource-person "
        "register with disciplines, and permission overrides of portal accounts.",
        "user",
        [
            Spec("accounts.User", sheet="portal", title="Portal users",
                 exclude=("password", "last_login", "totp_secret", "two_factor_enabled"),
                 scope="user_portal", check="portal_user", strict=("role", "professional_body"),
                 note="Resource persons and professional-body accounts only (user_type "
                      "resource_person or professional_body, required)."),
            Spec("inspection.ResourcePerson", exclude=("rating_avg", "booking_version")),
            Spec("inspection.ResourcePersonDiscipline"),
            Spec("accounts.UserPermissionOverride", sheet="portal", title="Portal permission overrides",
                 scope="override_portal", check="override_portal",
                 note="Overrides of institution and other portal accounts."),
        ],
    ),
    Category(
        "workflow", "Request types & approval chains",
        "Request categories, approval chains with their ordered steps, and every request type "
        "with the chain it follows (leave applications use the annual-leave request type).",
        "trend",
        [
            Spec("workflow.RequestCategory"),
            Spec("workflow.ApprovalChain"),
            Spec("workflow.ApprovalStep", note="One row per step; position is the order in the chain."),
            Spec("workflow.RequestType"),
        ],
    ),
    Category(
        "memo", "Memo routing hierarchy",
        "Memo routes, their ordered approval steps and the rules that pick a route.",
        "mail",
        [
            Spec("messaging.MemoRoute"),
            Spec("messaging.MemoRouteStep"),
            Spec("messaging.MemoRouteRule", key=("route", "name")),
        ],
    ),
    Category(
        "leave", "Leave types & entitlements",
        "Leave types and any per-staff entitlements. The approval chain a leave application "
        "follows is the annual-leave request type in 'Request types & approval chains'.",
        "clock",
        [
            Spec("leave.LeaveType"),
            Spec("leave.LeaveEntitlement"),
        ],
    ),
    Category(
        "tasks", "Tasks, tickets & assignment rules",
        "Task categories (assessment and review rules), ticket categories, review-chain policies "
        "and rotation pools with their members. Asset categories are in the Assets category.",
        "check",
        [
            Spec("tasks.TaskCategory"),
            Spec("tickets.TicketCategory"),
            Spec("assignments.ReviewChainPolicy"),
            Spec("assignments.RotationPool"),
            Spec("assignments.RotationMember", exclude=("last_assigned_at",)),
        ],
    ),
    Category(
        "assets", "Assets",
        "The asset register: asset categories, every asset (tag, serial, make and model, state, how it "
        "is held, owning department, location, purchase date, cost, warranty, notes, spec), who holds "
        "each asset now and before (assignments), logged issues and the attachment list. Loaded after "
        "Organisation and NBTE users & staff: departments are linked by code, people by e-mail (or "
        "staff number).",
        "box",
        [
            Spec("assets.AssetCategory", key=("name",),
                 note="Master data. Keyed on the name (the code is matched too, so a renamed category is "
                      "updated, not duplicated). spec_fields is the JSON list of extra spec fields."),
            Spec("assets.Asset", exclude=("current_custodian",), check="asset", fill="asset",
                 after="asset_custody", aliases=True, labels=True, required=("category",),
                 strict=("owner_department",), dropdowns=("category", "owner_department"),
                 note="One row per asset, keyed on asset_tag. A blank asset_tag is generated like on the "
                      "New asset page (DEPT-CATEGORY-00001) unless the serial_number matches exactly one "
                      "existing asset, which is then updated. Tags never change once created. "
                      "assignment_mode: Individual (one holder), Department (whole department; needs "
                      "owner_department) or Shared (several named people); it cannot change while the "
                      "asset is held. The holder is NOT a column here: it comes from the Asset "
                      "assignments sheet. category accepts the name or code, owner_department the code "
                      "or name. cost is a plain number (commas and the naira sign are ignored); dates "
                      "are YYYY-MM-DD or DD/MM/YYYY. The QR code and barcode are derived from the tag "
                      "and are not stored."),
            Spec("assets.AssetAssignment", key=("asset", "assigned_to", "department", "assigned_at"),
                 check="asset_assignment", fill="asset_assignment", after="asset_custody",
                 aliases=True, labels=True, actor_default=("assigned_by",),
                 strict=("asset", "assigned_to", "department", "returned_to"),
                 dropdowns=("asset", "assigned_to", "department"),
                 note="Current holders and the hand-off history. An open row (returned_at blank) is a "
                      "current holder. Individual assets: one person (assigned_to), at most one open "
                      "row. Department assets: department only, at most one open row. Shared assets: "
                      "one row per person. People by e-mail or staff number; must be NBTE staff. A "
                      "blank assigned_at matches the person's open hand-off of that asset (else: now). "
                      "A blank assigned_by is the administrator who imports the file."),
            Spec("assets.AssetIssue", key=("asset", "reported_at", "summary"), fill="asset_issue",
                 aliases=True, strict=("asset",), actor_default=("reported_by",), dropdowns=("asset",),
                 note="Problems logged on assets. The link to a workflow request is not carried over. "
                      "A blank reported_at matches an issue with the same summary on that asset."),
            Spec("assets.AssetAttachment", key=("asset", "file"), fill="asset_attachment",
                 aliases=True, strict=("asset",), labels=True, actor_default=("uploaded_by",), dropdowns=("asset",),
                 note="Photos, receipts, warranty documents: the file column holds only the stored file "
                      "path (under the protected media folder); the files themselves are NOT inside the "
                      "workbook. Copy protected_media/ (assets/...) to the new server, or the rows point "
                      "at missing files."),
        ],
        link_url="assets:list", link_label="Open the asset register",
        instructions=(
            "ASSETS: how to fill this workbook",
            "1. Asset categories: one row per kind of asset (Laptop, Printer ...). Add new ones before the "
            "assets that use them.",
            "2. Assets: one row per item. Leave asset_tag blank for a new item to get the next tag "
            "(DEPT-CATEGORY-00001); type a tag to keep your own numbering. Existing tags are updated, "
            "never duplicated, and never renamed.",
            "3. Pick state, assignment_mode, category and owner_department from the dropdowns (codes are "
            "accepted too). Department mode needs an owner_department.",
            "4. Asset assignments: who holds each asset. Leave returned_at blank for a current holder. "
            "Individual = one person; Department = department code only; Shared = one row per person. "
            "People are given by e-mail or staff number and must be NBTE staff accounts.",
            "5. Dates: YYYY-MM-DD (or DD/MM/YYYY). Money: a plain number such as 250000.00.",
            "6. Photos and attachments: only the stored file path travels in the workbook. Copy the "
            "protected media folder separately.",
            "7. Import on Administration > Data Hub (site administrators): you always get a preview first; "
            "nothing is saved until you press Apply, and re-importing the same file changes nothing.",
        ),
    ),
    Category(
        "qa_flows", "QA flows, notifications, documents & complaints",
        "The QA process engine (flows, stages, transitions), action policies, reference "
        "formats, notification templates and rules, institution request types, document "
        "templates and rules, complaint layers, routing and escalation rules, knowledge base.",
        "flag",
        [
            Spec("qa_core.FlowDefinition", exclude=("published_at",)),
            Spec("qa_core.FlowStage"),
            Spec("qa_core.NotificationTemplate"),
            Spec("qa_core.NotificationRule",
                 key=("event_code", "flow", "stage", "recipient_kind", "recipient_role", "template")),
            Spec("qa_core.FlowTransition"),
            Spec("qa_core.ActionPolicy"),
            Spec("qa_core.ReferenceFormat"),
            Spec("institutions.InstitutionRequestType"),
            Spec("documents.DocumentTemplate"),
            Spec("documents.DocumentRule"),
            Spec("complaints.ComplaintLayer"),
            Spec("complaints.RoutingRule", key=("layer", "stage_code", "condition", "order", "target_kind")),
            Spec("complaints.EscalationRule", key=("flow", "stage_code", "level", "target_kind")),
            Spec("complaints.KnowledgeArticle", exclude=("helpful", "not_helpful")),
        ],
    ),
    Category(
        "fees_qa", "Fees, accounts & QA catalogue",
        "Bank accounts, fee schedules and components, institution-type fees, ledger accounts, "
        "QA types with their SSQ templates, the equipment catalogue, allowance rate tables and "
        "application cycles.",
        "bank",
        [
            Spec("payments.BankAccount", key=("bank_name", "account_number")),
            Spec("payments.FeeSchedule"),
            Spec("payments.FeeComponent"),
            Spec("payments.InstitutionTypeFee"),
            Spec("payments.LedgerAccount"),
            Spec("accreditation.QAType"),
            Spec("accreditation.SSQTemplate", exclude=("published_at",)),
            Spec("accreditation.CatalogueItem",
                 key=("programme_master", "category", "programme_name", "kind", "name", "version")),
            Spec("accreditation.RateTable"),
            Spec("accreditation.ApplicationCycle", key=("name",)),
        ],
    ),
    Category(
        "facilities", "Facilities & equipment",
        "The ND/HND minimum facilities and equipment register: programmes, sections and items.",
        "sheet",
        [
            Spec("facilities.FacilityProgramme", exclude=("last_synced_to_catalogue",)),
            Spec("facilities.FacilitySection"),
            Spec("facilities.FacilityItem"),
        ],
        link_url="facilities:index", link_label="Open the facilities register",
    ),
]

EVERYTHING = "everything"

# Links that point at a user account are normally filled in the NBTE users
# category (desk officers, heads). These point at institution accounts, so
# they are filled once the Institution users category is loaded.
LINK_CATEGORY = {
    "institutions.ManagementStaff.user": "institution_users",
}


def category_keys():
    return [c.key for c in CATEGORIES]


def get_category(key):
    for c in CATEGORIES:
        if c.key == key:
            return c
    return None
