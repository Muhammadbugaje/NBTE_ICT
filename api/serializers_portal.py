"""Request and response serializers for the portal door (institutions and
resource persons). Responses are built as dicts in ``api/services/portal*``
and passed through these classes, so the OpenAPI schema matches the payload.
Business validation is the website's own (its forms and services); the
request serializers here only describe and type the input."""
from rest_framework import serializers as s

# ===========================================================================
# Shared pieces
# ===========================================================================
class RefSerializer(s.Serializer):
    id = s.IntegerField()
    code = s.CharField(allow_blank=True)
    name = s.CharField()


class IdNameSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()


class ChoiceSerializer(s.Serializer):
    value = s.CharField(allow_blank=True)
    label = s.CharField()


class FileSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    size_bytes = s.IntegerField()
    size_label = s.CharField(allow_blank=True)
    download_url = s.CharField(help_text="API path; GET it with the Bearer token.")


class StageSerializer(s.Serializer):
    label = s.CharField()
    state = s.CharField(help_text="done | current | todo (complaints: done | now | todo)")


class StageCodeSerializer(StageSerializer):
    code = s.CharField()


class EstimateLineSerializer(s.Serializer):
    label = s.CharField()
    amount = s.CharField()
    amount_display = s.CharField()


class EstimateSerializer(s.Serializer):
    total = s.CharField(allow_null=True)
    total_display = s.CharField(allow_blank=True)
    lines = EstimateLineSerializer(many=True)
    error = s.CharField(allow_blank=True)
    blocked = s.BooleanField()


class FeeRowSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    description = s.CharField()
    amount = s.CharField()
    amount_display = s.CharField()
    balance = s.CharField()
    balance_display = s.CharField()
    due_date = s.DateField(allow_null=True)
    status = s.CharField()
    route = s.CharField()


class DetailMessageSerializer(s.Serializer):
    """Answer of an action endpoint that has nothing else to return."""
    detail = s.CharField()


# ===========================================================================
# Institution
# ===========================================================================
class ContactSerializer(s.Serializer):
    kind = s.CharField()
    kind_label = s.CharField()
    name = s.CharField(allow_blank=True)
    value = s.CharField(allow_blank=True)


class AddressSerializer(s.Serializer):
    kind = s.CharField()
    kind_label = s.CharField()
    line1 = s.CharField(allow_blank=True)
    town = s.CharField(allow_blank=True)
    lga = s.CharField(allow_blank=True)
    state = s.CharField(allow_blank=True)
    one_line = s.CharField(allow_blank=True)


class ProfileEditSerializer(s.Serializer):
    """Current values of the editable profile (ids for the lookups)."""
    name = s.CharField(allow_blank=True)
    ownership = s.IntegerField(allow_null=True)
    institution_type = s.IntegerField(allow_null=True)
    category = s.IntegerField(allow_null=True)
    state = s.IntegerField(allow_null=True)
    lga = s.CharField(allow_blank=True)
    year_established = s.IntegerField(allow_null=True)
    cac_rc_number = s.CharField(allow_blank=True)
    website = s.CharField(allow_blank=True)
    official_email = s.CharField(allow_blank=True)
    phone = s.CharField(allow_blank=True)
    head_name = s.CharField(allow_blank=True)
    registrar_name = s.CharField(allow_blank=True)
    bursar_name = s.CharField(allow_blank=True)
    address_line1 = s.CharField(allow_blank=True)
    address_town = s.CharField(allow_blank=True)


class InstitutionProfileSerializer(s.Serializer):
    id = s.IntegerField()
    code = s.CharField()
    name = s.CharField()
    status = s.CharField()
    status_label = s.CharField()
    ownership = RefSerializer(allow_null=True)
    institution_type = RefSerializer(allow_null=True)
    category = RefSerializer(allow_null=True)
    state = RefSerializer(allow_null=True)
    lga = s.CharField(allow_blank=True)
    zone = s.CharField(allow_blank=True)
    year_established = s.IntegerField(allow_null=True)
    cac_rc_number = s.CharField(allow_blank=True)
    website = s.CharField(allow_blank=True)
    handling_department = IdNameSerializer(allow_null=True)
    contacts = ContactSerializer(many=True)
    addresses = AddressSerializer(many=True)
    portal_enabled = s.BooleanField()
    editable = s.BooleanField(help_text="True only while the institution is proposed and its application is a draft.")
    can_manage_users = s.BooleanField()
    edit = ProfileEditSerializer(allow_null=True)


class InstitutionUpdateSerializer(s.Serializer):
    """PATCH: any subset; the website's PortalProfileForm validates the
    merged result (current values + the ones sent)."""
    name = s.CharField(required=False)
    ownership = s.IntegerField(required=False)
    institution_type = s.IntegerField(required=False)
    category = s.IntegerField(required=False)
    state = s.IntegerField(required=False)
    lga = s.CharField(required=False, allow_blank=True)
    year_established = s.IntegerField(required=False, allow_null=True)
    cac_rc_number = s.CharField(required=False, allow_blank=True)
    website = s.CharField(required=False, allow_blank=True)
    official_email = s.CharField(required=False)
    phone = s.CharField(required=False)
    head_name = s.CharField(required=False)
    registrar_name = s.CharField(required=False, allow_blank=True)
    bursar_name = s.CharField(required=False, allow_blank=True)
    address_line1 = s.CharField(required=False)
    address_town = s.CharField(required=False, allow_blank=True)


class CategoryRefSerializer(RefSerializer):
    institution_type_id = s.IntegerField(allow_null=True)


class InstitutionOptionsQuerySerializer(s.Serializer):
    state = s.IntegerField(required=False, help_text="A state id: fills `lgas`.")


class InstitutionOptionsSerializer(s.Serializer):
    ownerships = RefSerializer(many=True)
    institution_types = RefSerializer(many=True)
    categories = CategoryRefSerializer(many=True)
    states = IdNameSerializer(many=True)
    lgas = s.ListField(child=s.CharField())


class MemberSerializer(s.Serializer):
    id = s.IntegerField()
    user_id = s.IntegerField()
    full_name = s.CharField()
    initials = s.CharField()
    role = s.CharField(allow_blank=True)
    job_title = s.CharField(allow_blank=True)
    is_primary_contact = s.BooleanField()
    invited = s.BooleanField(help_text="Invited but has not set a password yet.")


class MembersSerializer(s.Serializer):
    can_manage = s.BooleanField()
    results = MemberSerializer(many=True)
    invite_roles = ChoiceSerializer(many=True)


class InviteUserSerializer(s.Serializer):
    first_name = s.CharField(required=False, allow_blank=True)
    last_name = s.CharField(required=False, allow_blank=True)
    email = s.CharField(required=False, allow_blank=True)
    role = s.CharField(required=False, allow_blank=True,
                       help_text="inst-academic-dept | inst-chief-executive | inst-admin")
    job_title = s.CharField(required=False, allow_blank=True)
    institution_department = s.CharField(required=False, allow_blank=True)
    grade_level = s.IntegerField(required=False, allow_null=True)


# ===========================================================================
# Documents
# ===========================================================================
class DocKindSerializer(s.Serializer):
    id = s.IntegerField()
    code = s.CharField()
    name = s.CharField()
    description = s.CharField(allow_blank=True)


class DeptCodeSerializer(s.Serializer):
    code = s.CharField()
    name = s.CharField()


class DocumentSerializer(s.Serializer):
    id = s.IntegerField()
    version = s.IntegerField()
    status = s.CharField()
    uploaded_at = s.DateTimeField()
    review_remark = s.CharField(allow_blank=True)
    valid_until = s.DateField(allow_null=True)
    file = FileSerializer(allow_null=True)


class DocumentRowSerializer(s.Serializer):
    kind = DocKindSerializer()
    mandatory = s.BooleanField()
    state = s.CharField(help_text="missing | submitted | accepted | returned | expired")
    state_label = s.CharField()
    department = DeptCodeSerializer(allow_null=True)
    document = DocumentSerializer(allow_null=True)
    can_upload = s.BooleanField()
    max_mb = s.IntegerField()
    extensions = s.ListField(child=s.CharField())


class DocumentFilterSerializer(s.Serializer):
    state = s.CharField(required=False, allow_blank=True, help_text="One of: missing, submitted, accepted, returned, expired.")
    dept = s.CharField(required=False, allow_blank=True, help_text="Reviewing department code.")
    need = s.CharField(required=False, allow_blank=True, help_text="One of: required, optional.")
    q = s.CharField(required=False, allow_blank=True)


class MissingDocSerializer(s.Serializer):
    kind_id = s.IntegerField()
    name = s.CharField()
    state = s.CharField()


class DocumentSummarySerializer(s.Serializer):
    mandatory = s.IntegerField()
    ready = s.IntegerField()
    complete = s.BooleanField()
    percent = s.IntegerField()
    missing = MissingDocSerializer(many=True)


class DocumentVersionSerializer(s.Serializer):
    id = s.IntegerField()
    kind = s.CharField()
    version = s.IntegerField()
    status = s.CharField()
    uploaded_at = s.DateTimeField()
    uploaded_by = s.CharField(allow_blank=True)
    review_remark = s.CharField(allow_blank=True)
    file = FileSerializer(allow_null=True)


class FileUploadSerializer(s.Serializer):
    file = s.FileField()


# ===========================================================================
# Application
# ===========================================================================
class ApplicationCoreSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    status = s.CharField()
    public_status = s.CharField()
    round = s.IntegerField()
    cycle = s.CharField(allow_blank=True)
    submitted_at = s.DateTimeField(allow_null=True)
    return_remark = s.CharField(allow_blank=True)


class ClearanceSerializer(s.Serializer):
    department = s.CharField()
    status = s.CharField()
    status_label = s.CharField()
    decided_at = s.DateTimeField(allow_null=True)
    opened_at = s.DateTimeField(allow_null=True)
    remark = s.CharField(allow_blank=True)


class ProgrammeBriefSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    level = RefSerializer(allow_null=True)
    category = s.CharField(allow_blank=True)
    status = s.CharField()
    status_label = s.CharField()
    requested_streams = s.IntegerField()
    approved_streams = s.IntegerField()


class ProgrammeOptionsSerializer(s.Serializer):
    levels = RefSerializer(many=True)
    categories = RefSerializer(many=True)
    max_streams = s.IntegerField()


class HoldSerializer(s.Serializer):
    reason = s.CharField(allow_blank=True)


class InstitutionRefSerializer(s.Serializer):
    id = s.IntegerField()
    code = s.CharField()
    name = s.CharField()
    status = s.CharField()


class ProgrammeStateSerializer(s.Serializer):
    id = s.IntegerField()
    state = s.CharField(help_text="draft | pending | cleared | returned")
    remark = s.CharField(allow_blank=True)


class ProgrammeSubmissionSerializer(s.Serializer):
    can_submit = s.BooleanField()
    blockers = s.ListField(child=s.CharField())
    chief_executive_only = s.BooleanField(help_text="True when this account may not submit (X12).")
    count = s.IntegerField()
    streams = s.IntegerField()
    department = s.CharField()
    expected_time = s.CharField()
    round = s.IntegerField()
    programmes = ProgrammeStateSerializer(many=True)


class ApplicationSerializer(s.Serializer):
    institution = InstitutionRefSerializer()
    can_start = s.BooleanField()
    application = ApplicationCoreSerializer(allow_null=True)
    stages = StageCodeSerializer(many=True)
    hold = HoldSerializer(allow_null=True)
    gaps = s.ListField(child=s.CharField(), help_text="Profile items still missing.")
    blockers = s.ListField(child=s.CharField(), help_text="Why the draft cannot be submitted ([] = it can).")
    can_submit = s.BooleanField()
    is_chief_executive = s.BooleanField(help_text="Only the Chief Executive submits to NBTE (X12).")
    documents = DocumentSummarySerializer()
    clearances = ClearanceSerializer(many=True)
    fees = FeeRowSerializer(many=True)
    programmes = ProgrammeBriefSerializer(many=True)
    estimate = EstimateSerializer(allow_null=True)
    selecting_programmes = s.BooleanField()
    programme_options = ProgrammeOptionsSerializer(allow_null=True)
    programme_submission = ProgrammeSubmissionSerializer(allow_null=True)
    handled_by = s.CharField()


class DeclarationSerializer(s.Serializer):
    declaration = s.BooleanField(required=False, default=False,
                                 help_text="The declaration checkbox; must be true to submit.")


class ProgrammeAddSerializer(s.Serializer):
    master = s.IntegerField(required=False, allow_null=True, help_text="Programme master list id (X44).")
    body = s.IntegerField(required=False, allow_null=True, help_text="Professional body id (manual entry).")
    name = s.CharField(required=False, allow_blank=True)
    level = s.IntegerField(required=False, allow_null=True)
    category = s.IntegerField(required=False, allow_null=True)
    streams = s.IntegerField(required=False, allow_null=True)


class StreamsSerializer(s.Serializer):
    streams = s.CharField(help_text="Number of streams (1 to max_streams).")


# ===========================================================================
# Timeline
# ===========================================================================
class TimelineFilterSerializer(s.Serializer):
    kind = s.CharField(required=False, allow_blank=True, help_text="One of: application, payment, document, institution.")
    period = s.CharField(required=False, allow_blank=True, help_text="One of: 30d, 90d, 1y.")
    q = s.CharField(required=False, allow_blank=True)
    by = s.CharField(required=False, allow_blank=True, help_text="One of: you, nbte.")


class TimelineRowSerializer(s.Serializer):
    id = s.IntegerField()
    at = s.DateTimeField()
    text = s.CharField(allow_blank=True)
    event_code = s.CharField()
    by = s.CharField(help_text="you | nbte")


# ===========================================================================
# QA requests
# ===========================================================================
class QATypeSerializer(s.Serializer):
    code = s.CharField()
    name = s.CharField()


class ReqProgrammeSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    level = s.CharField(allow_blank=True)


class QARequestRowSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    qa_type = QATypeSerializer()
    status = s.CharField()
    public_status = s.CharField()
    created_at = s.DateTimeField()
    submitted_at = s.DateTimeField(allow_null=True)
    programmes = ReqProgrammeSerializer(many=True)


class QARequestFilterSerializer(s.Serializer):
    show = s.CharField(required=False, default="open", help_text="One of: open, closed, all.")
    type = s.CharField(required=False, allow_blank=True, help_text="QA type code.")
    level = s.CharField(required=False, allow_blank=True, help_text="Programme level code.")
    q = s.CharField(required=False, allow_blank=True)


class QATypeOptionSerializer(s.Serializer):
    code = s.CharField()
    name = s.CharField()
    description = s.CharField(allow_blank=True)
    available = s.BooleanField()
    reason = s.CharField(allow_blank=True)
    asks_streams = s.BooleanField()
    max_programmes = s.IntegerField(allow_null=True)


class EligibleProgrammeSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    level = RefSerializer(allow_null=True)
    status = s.CharField()
    status_label = s.CharField()
    expiry_date = s.DateField(allow_null=True)
    eligible = s.BooleanField()
    reason = s.CharField(allow_blank=True)
    streams = s.IntegerField()


class NewRequestQuerySerializer(s.Serializer):
    type = s.CharField(required=False, allow_blank=True, help_text="QA type code: fills `programmes`.")


class NewRequestSerializer(s.Serializer):
    can_create = s.BooleanField()
    types = QATypeOptionSerializer(many=True)
    qa_type = QATypeSerializer(allow_null=True)
    programmes = EligibleProgrammeSerializer(many=True)
    window_months = s.IntegerField()
    max_streams = s.IntegerField()


class RequestEstimateQuerySerializer(s.Serializer):
    type = s.CharField(help_text="QA type code.")
    programmes = s.CharField(required=False, allow_blank=True,
                             help_text="Comma-separated programme ids (or repeat ?programmes=).")
    streams = s.CharField(required=False, allow_blank=True,
                          help_text="Streams per programme as id:n pairs, comma-separated (e.g. 12:2,14:1).")


class RequestEstimateSerializer(s.Serializer):
    estimate = EstimateSerializer(allow_null=True)
    problems = s.ListField(child=s.CharField())
    count = s.IntegerField()


class QARequestCreateSerializer(s.Serializer):
    type = s.CharField(help_text="QA type code (from qa-requests/new/).")
    programmes = s.ListField(child=s.IntegerField(), allow_empty=True)
    streams = s.DictField(child=s.CharField(), required=False,
                          help_text='{"<programme id>": "<streams>"} when the type asks for streams.')


class SSQBriefSerializer(s.Serializer):
    id = s.IntegerField()
    version = s.IntegerField()
    status = s.CharField()
    public_status = s.CharField()
    done = s.IntegerField(help_text="Required answers filled.")
    total = s.IntegerField(help_text="Required answers in all.")
    review_remark = s.CharField(allow_blank=True)
    last_saved_at = s.DateTimeField(allow_null=True)


class RequestProgrammeRowSerializer(s.Serializer):
    row_id = s.IntegerField(help_text="Use in programmes/<row_id>/… actions.")
    programme = ReqProgrammeSerializer()
    status = s.CharField()
    requested_streams = s.IntegerField()
    is_new_programme = s.BooleanField()
    clearance_status = s.CharField(allow_blank=True)
    clearance_remark = s.CharField(allow_blank=True)
    dropped_reason = s.CharField(allow_blank=True)
    replaced_by = s.CharField(allow_blank=True)
    ssq = SSQBriefSerializer(allow_null=True)


class QueryBriefSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    title = s.CharField()
    status = s.CharField()
    due_at = s.DateTimeField(allow_null=True)


class QARequestDetailSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    qa_type = QATypeSerializer()
    status = s.CharField()
    public_status = s.CharField()
    created_at = s.DateTimeField()
    submitted_at = s.DateTimeField(allow_null=True)
    fee_note = s.CharField(allow_blank=True)
    cancel_reason = s.CharField(allow_blank=True)
    stages = StageSerializer(many=True)
    programmes = RequestProgrammeRowSerializer(many=True)
    candidates = ReqProgrammeSerializer(many=True, help_text="Programmes that can be added or used as a replacement.")
    fees = FeeRowSerializer(many=True)
    queries = QueryBriefSerializer(many=True)
    estimate = EstimateSerializer(allow_null=True)
    next_step = s.CharField(allow_blank=True)
    can_manage = s.BooleanField()
    can_add = s.BooleanField()
    can_drop = s.BooleanField()
    can_replace = s.BooleanField()
    can_cancel = s.BooleanField()
    can_submit = s.BooleanField()
    cancellable_stages = s.ListField(child=s.CharField())
    max_streams = s.IntegerField()


class ReasonSerializer(s.Serializer):
    reason = s.CharField(required=False, allow_blank=True, default="")


class AddProgrammeSerializer(s.Serializer):
    programme = s.IntegerField()
    streams = s.CharField(required=False, allow_blank=True)


class ReplaceProgrammeSerializer(s.Serializer):
    programme = s.IntegerField(help_text="The replacement programme id.")
    reason = s.CharField(required=False, allow_blank=True, default="")


# ===========================================================================
# SSQ
# ===========================================================================
class OptionSerializer(s.Serializer):
    value = s.CharField()
    label = s.CharField()


class ColumnSerializer(s.Serializer):
    key = s.CharField()
    type = s.CharField()
    label = s.CharField()
    options = OptionSerializer(many=True)


class CatalogueItemSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    unit = s.CharField(allow_blank=True)
    quantity = s.CharField(allow_blank=True)


class ReusedFromSerializer(s.Serializer):
    id = s.IntegerField()
    kind = s.CharField()
    version = s.IntegerField()


class EvidenceSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    status = s.CharField()
    reused_from = ReusedFromSerializer(allow_null=True)
    file = FileSerializer(allow_null=True)
    uploaded_at = s.DateTimeField()


class ReusableDocSerializer(s.Serializer):
    id = s.IntegerField()
    kind = s.CharField()
    version = s.IntegerField()
    name = s.CharField()


class SSQFieldSerializer(s.Serializer):
    key = s.CharField()
    type = s.CharField(help_text="text textarea email phone url number integer date choice multi_choice yes_no "
                                 "file catalogue table")
    label = s.CharField()
    required = s.BooleanField()
    help = s.CharField(allow_blank=True)
    placeholder = s.CharField(allow_blank=True)
    width = s.CharField()
    validation = s.JSONField()
    options = OptionSerializer(many=True)
    value = s.JSONField(allow_null=True, help_text="string; list (multi_choice); {item id: qty} (catalogue); "
                                                   "[{column: value}] (table); null (file)")
    prefilled = s.BooleanField()
    edited = s.BooleanField()
    multiple = s.BooleanField()
    document_kind = s.CharField(allow_blank=True)
    columns = ColumnSerializer(many=True, allow_null=True)
    min_rows = s.IntegerField(allow_null=True)
    max_rows = s.IntegerField(allow_null=True)
    items = CatalogueItemSerializer(many=True, allow_null=True)
    evidence = EvidenceSerializer(many=True, allow_null=True)
    reusable_documents = ReusableDocSerializer(many=True, allow_null=True)


class SectionProgressSerializer(s.Serializer):
    filled = s.IntegerField()
    total = s.IntegerField()
    complete = s.BooleanField()


class SSQSectionSerializer(s.Serializer):
    key = s.CharField()
    title = s.CharField()
    description = s.CharField(allow_blank=True)
    department = s.BooleanField(help_text="May be sent to an academic department.")
    editable = s.BooleanField()
    progress = SectionProgressSerializer()
    saved_at = s.CharField(allow_null=True)
    fields = SSQFieldSerializer(many=True)


class ProblemSerializer(s.Serializer):
    key = s.CharField()
    message = s.CharField()


class SSQRequestRefSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    qa_type = QATypeSerializer()


class SSQTemplateRefSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    version = s.IntegerField()


class SSQVersionSerializer(s.Serializer):
    id = s.IntegerField()
    version = s.IntegerField()
    status = s.CharField()
    is_current = s.BooleanField()


class SSQSubmissionSerializer(s.Serializer):
    version = s.IntegerField()
    submitted_at = s.DateTimeField()
    snapshot = s.JSONField()


class SSQSerializer(s.Serializer):
    id = s.IntegerField()
    request = SSQRequestRefSerializer()
    programme = ReqProgrammeSerializer()
    template = SSQTemplateRefSerializer()
    version = s.IntegerField()
    status = s.CharField()
    public_status = s.CharField()
    is_current = s.BooleanField()
    review_remark = s.CharField(allow_blank=True)
    department_remark = s.CharField(allow_blank=True)
    assigned_sections = s.ListField(child=s.CharField())
    assigned_department_user = s.CharField(allow_blank=True)
    last_saved_at = s.DateTimeField(allow_null=True)
    submitted_at = s.DateTimeField(allow_null=True)
    sections = SSQSectionSerializer(many=True)
    editable_sections = s.ListField(child=s.CharField())
    problems = ProblemSerializer(many=True, help_text="Required answers still missing (submission check).")
    can_submit = s.BooleanField()
    can_revise = s.BooleanField()
    can_send_department = s.BooleanField()
    can_recall = s.BooleanField()
    can_department_submit = s.BooleanField()
    department_users = IdNameSerializer(many=True)
    is_department = s.BooleanField()
    versions = SSQVersionSerializer(many=True)
    submission = SSQSubmissionSerializer(allow_null=True)
    max_mb = s.IntegerField()


class SSQAnswersSerializer(s.Serializer):
    answers = s.DictField(child=s.JSONField(allow_null=True),
                          help_text="{field key: value}. Only the keys sent are saved (partial save).")


class SSQSaveResultSerializer(s.Serializer):
    saved = s.ListField(child=s.CharField(), help_text="Keys stored.")
    errors = s.DictField(child=s.CharField(), help_text="{field key: message} for values not stored.")
    last_saved_at = s.DateTimeField(allow_null=True)
    progress = s.DictField(child=SectionProgressSerializer())
    problems = ProblemSerializer(many=True)


class ReuseSerializer(s.Serializer):
    document = s.IntegerField(help_text="An accepted institution document id (reusable_documents).")


class DepartmentSendSerializer(s.Serializer):
    department_user = s.IntegerField()
    sections = s.ListField(child=s.CharField())
    remark = s.CharField(required=False, allow_blank=True, default="")


# ===========================================================================
# Queries
# ===========================================================================
class QueryFilterSerializer(s.Serializer):
    status = s.CharField(required=False, default="active", help_text="One of: active, open, answered, closed, all.")
    q = s.CharField(required=False, allow_blank=True)
    about = s.CharField(required=False, allow_blank=True, help_text="One of: request, other.")
    due = s.CharField(required=False, allow_blank=True, help_text="One of: overdue, week.")


class RequestRefSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()


class QueryRowSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    title = s.CharField()
    subject_label = s.CharField(allow_blank=True)
    status = s.CharField()
    status_label = s.CharField()
    due_at = s.DateTimeField(allow_null=True)
    is_overdue = s.BooleanField()
    created_at = s.DateTimeField()
    qa_request = RequestRefSerializer(allow_null=True)


class QueryMessageSerializer(s.Serializer):
    kind = s.CharField(help_text="query (NBTE's question) | response (yours)")
    id = s.IntegerField()
    author = s.CharField()
    from_nbte = s.BooleanField()
    body = s.CharField()
    at = s.DateTimeField()
    attachment = FileSerializer(allow_null=True)


class QueryDetailSerializer(QueryRowSerializer):
    body = s.CharField()
    close_remark = s.CharField(allow_blank=True)
    closed_at = s.DateTimeField(allow_null=True)
    messages = QueryMessageSerializer(many=True)
    can_respond = s.BooleanField()
    max_mb = s.IntegerField()


class QueryReplySerializer(s.Serializer):
    body = s.CharField(allow_blank=True)
    file = s.FileField(required=False)


# ===========================================================================
# Programmes and letters
# ===========================================================================
class LetterCopySerializer(s.Serializer):
    id = s.IntegerField()
    kind = s.CharField()
    kind_label = s.CharField()
    name = s.CharField()
    download_url = s.CharField()


class LetterSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    programme = s.CharField()
    programme_id = s.IntegerField()
    is_current = s.BooleanField()
    generated_at = s.DateTimeField(allow_null=True)
    outcome = s.CharField(allow_blank=True)
    approval_type = s.CharField(allow_blank=True)
    expiry_date = s.DateField(allow_null=True)
    copies = LetterCopySerializer(many=True)


class BoardCardSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    level = RefSerializer(allow_null=True)
    status = s.CharField()
    status_label = s.CharField()
    approval_type = s.CharField(allow_blank=True)
    approved_streams = s.IntegerField()
    commencement_date = s.DateField(allow_null=True)
    expiry_date = s.DateField(allow_null=True)
    days_left = s.IntegerField(allow_null=True)
    urgency = s.CharField(allow_blank=True, help_text="expired | soon | ok | '' (no expiry)")
    window_opens = s.DateField(allow_null=True)
    window_open = s.BooleanField()
    can_renew = s.BooleanField(help_text="Show 'Request re-accreditation' (qa-requests/new/?type=<renewal_type>).")
    letter = LetterSerializer(allow_null=True)


class BoardSerializer(s.Serializer):
    window_months = s.IntegerField()
    renewal_type = QATypeSerializer(allow_null=True)
    cards = BoardCardSerializer(many=True)


class ProgrammeFilterSerializer(s.Serializer):
    q = s.CharField(required=False, allow_blank=True)
    status = s.CharField(required=False, allow_blank=True)
    level = s.CharField(required=False, allow_blank=True, help_text="Level code (nd, hnd …).")


class ProgrammeRowSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    level = RefSerializer(allow_null=True)
    category = s.CharField(allow_blank=True)
    status = s.CharField()
    status_label = s.CharField()
    approved_streams = s.IntegerField()
    commencement_date = s.DateField(allow_null=True)
    expiry_date = s.DateField(allow_null=True)
    days_left = s.IntegerField(allow_null=True)
    expiry_label = s.CharField(allow_blank=True)
    professional_bodies = s.ListField(child=s.CharField())


class ProgrammeRequestSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    qa_type = QATypeSerializer()
    created_at = s.DateTimeField()
    public_status = s.CharField()


class HistoryEventSerializer(s.Serializer):
    at = s.DateTimeField()
    text = s.CharField(allow_blank=True)


class ProgrammeDetailSerializer(ProgrammeRowSerializer):
    approval_type = s.CharField(allow_blank=True)
    requests = ProgrammeRequestSerializer(many=True)
    history = HistoryEventSerializer(many=True)
    letters = LetterSerializer(many=True)


# ===========================================================================
# Payments
# ===========================================================================
class PaymentFilterSerializer(s.Serializer):
    group = s.CharField(required=False, allow_blank=True, help_text="One of: due, checking, settled, closed.")
    fee = s.CharField(required=False, allow_blank=True, help_text="Fee schedule code.")
    period = s.CharField(required=False, allow_blank=True, help_text="One of: 30d, 90d, 1y.")
    q = s.CharField(required=False, allow_blank=True)


class BillRowSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    description = s.CharField()
    fee = s.CharField(allow_blank=True)
    amount = s.CharField()
    amount_display = s.CharField()
    balance = s.CharField()
    balance_display = s.CharField()
    status = s.CharField()
    status_label = s.CharField()
    is_settled = s.BooleanField()
    created_at = s.DateTimeField()
    due_date = s.DateField(allow_null=True)
    source_reference = s.CharField(allow_blank=True)


class PaymentCountsSerializer(s.Serializer):
    due = s.IntegerField()
    checking = s.IntegerField()
    settled = s.IntegerField()


class FeeChoiceSerializer(s.Serializer):
    code = s.CharField()
    name = s.CharField()


class PaymentSummarySerializer(s.Serializer):
    due_total = s.CharField()
    due_total_display = s.CharField()
    counts = PaymentCountsSerializer()
    groups = ChoiceSerializer(many=True)
    fees = FeeChoiceSerializer(many=True)


class OnlineMethodSerializer(s.Serializer):
    code = s.CharField(help_text="remita | paystack")
    label = s.CharField()
    test_mode = s.BooleanField()


class BankAccountSerializer(s.Serializer):
    bank_name = s.CharField(allow_blank=True)
    account_name = s.CharField(allow_blank=True)
    account_number = s.CharField(allow_blank=True)


class SlipSerializer(s.Serializer):
    id = s.IntegerField()
    version = s.IntegerField()
    expires_at = s.DateTimeField(allow_null=True)
    is_expired = s.BooleanField()
    file = FileSerializer(allow_null=True)


class AdviceSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    amount = s.CharField()
    amount_display = s.CharField()
    generated_at = s.DateTimeField(allow_null=True)
    file = FileSerializer(allow_null=True)


class ProofRowSerializer(s.Serializer):
    id = s.IntegerField()
    kind = s.CharField()
    kind_label = s.CharField()
    amount_claimed = s.CharField()
    amount_claimed_display = s.CharField()
    paid_on = s.DateField(allow_null=True)
    uploaded_at = s.DateTimeField()
    file = FileSerializer(allow_null=True)


class AttemptRowSerializer(s.Serializer):
    id = s.IntegerField()
    provider = s.CharField()
    provider_label = s.CharField()
    reference = s.CharField()
    amount = s.CharField()
    amount_display = s.CharField()
    status = s.CharField()
    status_label = s.CharField()
    created_at = s.DateTimeField()
    failure_reason = s.CharField(allow_blank=True)


class BillLineSerializer(s.Serializer):
    """v2.1 F.4 (P1): one line of the bill (institution fee, processing fee …)."""
    code = s.CharField()
    label = s.CharField()
    description = s.CharField(allow_blank=True)
    kind = s.CharField()
    amount = s.CharField()
    amount_display = s.CharField()


class PayMethodSerializer(s.Serializer):
    code = s.CharField()
    label = s.CharField()


class BillDetailSerializer(BillRowSerializer):
    breakdown = BillLineSerializer(many=True)
    chosen_method = s.CharField(allow_blank=True, help_text="v2.1 P9: once set, only this method is offered.")
    methods = PayMethodSerializer(many=True, help_text="Methods the institution can choose now.")
    gateway_verified = s.BooleanField(help_text="X21: the provider verified the payment; no upload is needed.")
    amount_in_words = s.CharField(allow_blank=True)
    amount_paid = s.CharField()
    amount_paid_display = s.CharField()
    remita_rrr = s.CharField(allow_blank=True)
    steps = StageSerializer(many=True)
    rejected_reason = s.CharField(allow_blank=True)
    needs_info = s.CharField(allow_blank=True)
    awaiting_confirmation = s.BooleanField()
    payable = s.BooleanField()
    online_methods = OnlineMethodSerializer(many=True)
    bank_transfer = s.BooleanField()
    bank_account = BankAccountSerializer(allow_null=True)
    receipt_needed = s.BooleanField()
    allow_partial = s.BooleanField()
    proof_extensions = s.ListField(child=s.CharField())
    proof_max_mb = s.IntegerField()
    slip = SlipSerializer(allow_null=True)
    can_regenerate_slip = s.BooleanField()
    advices = AdviceSerializer(many=True)
    proofs = ProofRowSerializer(many=True)
    attempts = AttemptRowSerializer(many=True)


class PaymentMethodSerializer(s.Serializer):
    method = s.ChoiceField(choices=["remita", "paystack", "bank_transfer", ""], allow_blank=True,
                           help_text="Choose a method (the others are hidden); blank = Change method.")


class OnlinePaymentSerializer(s.Serializer):
    provider = s.ChoiceField(choices=["remita", "paystack"])
    amount = s.CharField(required=False, allow_blank=True,
                         help_text="Defaults to the balance; part payments only where allowed.")
    return_to = s.ChoiceField(choices=["web", "app"], required=False, default="web",
                              help_text="\"app\": after the provider, the browser shows a result page that "
                                        "opens digitalnbte://payment-return?bill=&status=&reference=.")


class CheckoutSerializer(s.Serializer):
    checkout_url = s.CharField(help_text="Open in the browser / a web view; never collect card data in the app.")
    reference = s.CharField(help_text="The attempt's provider reference (order id).")
    provider = s.CharField()
    return_url = s.CharField(help_text="Where the provider sends the payer back (the portal payment page, or the "
                                       "app-return page when return_to is \"app\").")
    return_to = s.ChoiceField(choices=["web", "app"], required=False)


class VerifyRequestSerializer(s.Serializer):
    params = s.DictField(child=s.CharField(allow_blank=True),
                         help_text="The query parameters the provider added to the return URL "
                                   "(reference / trxref / RRR / orderID).")


class AttemptSerializer(s.Serializer):
    id = s.IntegerField()
    provider = s.CharField()
    reference = s.CharField()
    status = s.CharField()
    status_label = s.CharField()
    failure_reason = s.CharField(allow_blank=True)


class VerifyResultSerializer(s.Serializer):
    matched = s.BooleanField()
    attempt = AttemptSerializer(allow_null=True)
    message = s.CharField(allow_blank=True)
    bill = BillDetailSerializer()


class ProofUploadSerializer(s.Serializer):
    amount_claimed = s.CharField(required=False, allow_blank=True)
    paid_on = s.CharField(required=False, allow_blank=True, help_text="YYYY-MM-DD")
    bank_name = s.CharField(required=False, allow_blank=True)
    teller_number = s.CharField(required=False, allow_blank=True)
    payer_name = s.CharField(required=False, allow_blank=True)
    file = s.FileField(required=False)


# ===========================================================================
# Complaints
# ===========================================================================
class ComplaintFilterSerializer(s.Serializer):
    status = s.CharField(required=False, default="open", help_text="One of: open, closed, all.")
    q = s.CharField(required=False, allow_blank=True)


class ComplaintRowSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    subject = s.CharField()
    layer = IdNameSerializer(allow_null=True)
    status = s.CharField()
    stage_label = s.CharField()
    is_open = s.BooleanField()
    created_at = s.DateTimeField()
    closed_at = s.DateTimeField(allow_null=True)
    related_label = s.CharField(allow_blank=True)


class ComplaintAttachmentSerializer(s.Serializer):
    id = s.IntegerField()
    name = s.CharField()
    size_bytes = s.IntegerField()
    size_label = s.CharField(allow_blank=True)
    download_url = s.CharField()


class ComplaintMessageSerializer(s.Serializer):
    id = s.IntegerField()
    author = s.CharField()
    initials = s.CharField()
    mine = s.BooleanField()
    is_system = s.BooleanField()
    body = s.CharField(allow_blank=True)
    at = s.DateTimeField()
    attachments = ComplaintAttachmentSerializer(many=True)


class ActionSerializer(s.Serializer):
    code = s.CharField(help_text="confirm | reopen | withdraw")
    label = s.CharField()


class ComplaintDetailSerializer(ComplaintRowSerializer):
    body = s.CharField()
    resolution = s.CharField(allow_blank=True)
    resolved_at = s.DateTimeField(allow_null=True)
    department = s.CharField()
    officer = s.CharField(allow_blank=True)
    steps = StageSerializer(many=True)
    messages = ComplaintMessageSerializer(many=True)
    actions = ActionSerializer(many=True)
    can_reply = s.BooleanField()
    waiting_for_me = s.BooleanField()
    reopen_days = s.IntegerField()


class CustomFieldSerializer(s.Serializer):
    key = s.CharField()
    label = s.CharField()
    type = s.CharField()
    required = s.BooleanField()
    options = s.ListField(child=s.CharField())


class LayerSerializer(s.Serializer):
    id = s.IntegerField()
    code = s.CharField()
    name = s.CharField()
    description = s.CharField(allow_blank=True)
    parent = s.CharField(allow_blank=True)
    custom_fields = CustomFieldSerializer(many=True)


class ArticleSerializer(s.Serializer):
    slug = s.CharField()
    title = s.CharField()
    summary = s.CharField(allow_blank=True)
    layer = s.CharField(allow_blank=True)


class ArticleDetailSerializer(ArticleSerializer):
    body = s.CharField()


class NewComplaintQuerySerializer(s.Serializer):
    about = s.CharField(required=False, allow_blank=True,
                        help_text="kind:id of the record it is about (payment, qa_request, ssq, programme, "
                                  "inspection, case, timeline), e.g. payment:12")


class NewComplaintSerializer(s.Serializer):
    layers = LayerSerializer(many=True)
    suggested_layer = s.IntegerField(allow_null=True)
    about = s.CharField(allow_blank=True)
    related_label = s.CharField(allow_blank=True)
    popular = ArticleSerializer(many=True)


class SuggestQuerySerializer(s.Serializer):
    subject = s.CharField(required=False, allow_blank=True)
    body = s.CharField(required=False, allow_blank=True)
    layer = s.IntegerField(required=False)


class ArticleListSerializer(s.Serializer):
    results = ArticleSerializer(many=True)


class HelpQuerySerializer(s.Serializer):
    q = s.CharField(required=False, allow_blank=True)


class ComplaintCreateSerializer(s.Serializer):
    layer = s.IntegerField(required=False, allow_null=True)
    subject = s.CharField(required=False, allow_blank=True)
    body = s.CharField(required=False, allow_blank=True)
    about = s.CharField(required=False, allow_blank=True)
    answers = s.DictField(child=s.CharField(allow_blank=True), required=False,
                          help_text="{custom field key: answer}")
    files = s.ListField(child=s.FileField(), required=False, help_text="multipart only; repeat `files`.")


class ComplaintReplySerializer(s.Serializer):
    body = s.CharField(required=False, allow_blank=True)
    files = s.ListField(child=s.FileField(), required=False)


class ComplaintActionSerializer(s.Serializer):
    remark = s.CharField(required=False, allow_blank=True, default="")
    satisfaction = s.IntegerField(required=False, allow_null=True, min_value=1, max_value=5)


# ===========================================================================
# Resource persons and inspections
# ===========================================================================
class EngagementFilterSerializer(s.Serializer):
    status = s.CharField(required=False, default="all", help_text="One of: all, live, released.")
    duty = s.CharField(required=False, allow_blank=True)
    when = s.CharField(required=False, allow_blank=True, help_text="One of: upcoming, past.")
    q = s.CharField(required=False, allow_blank=True)


class EngagementRowSerializer(s.Serializer):
    id = s.IntegerField()
    programme = IdNameSerializer()
    institution = s.CharField()
    duty = s.CharField()
    duty_label = s.CharField()
    from_date = s.DateField()
    to_date = s.DateField()
    status = s.CharField()
    status_label = s.CharField()
    role = s.CharField()
    findings = s.CharField(allow_blank=True)
    can_write_findings = s.BooleanField()
    findings_closed_reason = s.CharField(allow_blank=True)


class VisitSerializer(s.Serializer):
    reference = s.CharField()
    kind = s.CharField()
    kind_label = s.CharField()
    scheduled_start = s.DateField(allow_null=True)
    scheduled_end = s.DateField(allow_null=True)
    status = s.CharField()
    status_label = s.CharField()


class EngagementDetailSerializer(EngagementRowSerializer):
    case_reference = s.CharField()
    visit = VisitSerializer(allow_null=True)


class FindingsSerializer(s.Serializer):
    findings = s.CharField(allow_blank=True)


class HistoryFilterSerializer(s.Serializer):
    duty = s.CharField(required=False, allow_blank=True)
    year = s.CharField(required=False, allow_blank=True)
    work = s.CharField(required=False, allow_blank=True, help_text="One of: visits, ssq.")
    q = s.CharField(required=False, allow_blank=True)


class ServiceRowSerializer(s.Serializer):
    id = s.IntegerField()
    served_from = s.DateField()
    served_to = s.DateField()
    duty = s.CharField()
    duty_label = s.CharField()
    is_correction = s.BooleanField()
    institution = s.CharField(allow_blank=True)
    programme = s.CharField(allow_blank=True)
    role = s.CharField(allow_blank=True)
    visit_count = s.IntegerField()
    ssq_count = s.IntegerField()
    allowance_status = s.CharField(help_text="paid | pending")


class ServiceSummarySerializer(s.Serializer):
    visits = s.IntegerField()
    ssqs = s.IntegerField()
    years = s.ListField(child=s.IntegerField())
    duties = ChoiceSerializer(many=True)


class DisciplineSerializer(s.Serializer):
    name = s.CharField()
    is_primary = s.BooleanField()


class RPFileSerializer(s.Serializer):
    id = s.IntegerField()
    kind = s.CharField()
    kind_label = s.CharField()
    name = s.CharField()
    uploaded_at = s.DateTimeField()


class RPProfileSerializer(s.Serializer):
    id = s.IntegerField()
    full_name = s.CharField()
    email = s.CharField()
    phone = s.CharField(allow_blank=True)
    status = s.CharField()
    status_label = s.CharField()
    source = s.CharField()
    source_label = s.CharField()
    rank = s.CharField(allow_blank=True)
    rank_label = s.CharField(allow_blank=True)
    qualifications = s.CharField(allow_blank=True)
    disciplines = DisciplineSerializer(many=True)
    lapsed = s.ListField(child=s.CharField(), help_text="Why the person is no longer eligible for new work.")
    files = RPFileSerializer(many=True)
    file_kinds = ChoiceSerializer(many=True)
    editable_fields = s.ListField(child=s.CharField(),
                                  help_text="Always []: the record is changed by NBTE (the website's rule).")


class RPFileUploadSerializer(s.Serializer):
    kind = s.ChoiceField(required=False, default="cv", choices=["cv", "promotion_proof", "other"])
    file = s.FileField(required=False)


class InspectionFilterSerializer(s.Serializer):
    status = s.CharField(required=False, allow_blank=True, help_text="One of: scheduled, completed, closed, cancelled.")
    q = s.CharField(required=False, allow_blank=True)
    when = s.CharField(required=False, allow_blank=True, help_text="One of: upcoming, past.")
    year = s.CharField(required=False, allow_blank=True)


class InspectionRowSerializer(s.Serializer):
    id = s.IntegerField()
    reference = s.CharField()
    kind = s.CharField()
    kind_label = s.CharField()
    scheduled_start = s.DateField(allow_null=True)
    scheduled_end = s.DateField(allow_null=True)
    actual_start = s.DateField(allow_null=True)
    actual_end = s.DateField(allow_null=True)
    status = s.CharField()
    status_label = s.CharField()


# OpenAPI component names: "Portal…" so they never collide with the other
# areas' serializers (api.serializers.RefSerializer …).
def _portal_names():
    import inspect
    import sys

    for _name, cls in inspect.getmembers(sys.modules[__name__], inspect.isclass):
        if issubclass(cls, s.Serializer) and cls.__module__ == __name__:
            cls.Meta = type("Meta", (), {"ref_name": "Portal" + cls.__name__.removesuffix("Serializer")})


_portal_names()
