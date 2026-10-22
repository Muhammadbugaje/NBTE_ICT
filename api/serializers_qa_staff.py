"""QA workspace (staff door): request validation and response shapes.

Response serializers are applied to the dicts built in
``api/services/qa_staff.py`` so the OpenAPI schema matches the payload.
"""
from rest_framework import serializers

S, I, B, D = serializers.CharField, serializers.IntegerField, serializers.BooleanField, serializers.DateTimeField


def opt(field_cls, **kw):
    return field_cls(allow_null=True, required=False, **kw)


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------
class QAPersonSerializer(serializers.Serializer):
    id = I()
    name = S()


class QAInstRefSerializer(serializers.Serializer):
    id = I()
    code = S()
    name = S()


class QADeptRefSerializer(serializers.Serializer):
    id = I()
    name = S()


class QAFileSerializer(serializers.Serializer):
    id = I()
    kind = S(help_text="Model label, e.g. payments.proofofpayment.")
    name = S()
    size = I()
    download_url = S(help_text="GET it with the Bearer token (qa/files/<kind>/<id>/).")


class QAFlowEventSerializer(serializers.Serializer):
    at = S()
    action = S()
    from_stage = opt(S)
    to_stage = S()
    remark = S(allow_blank=True)
    actor = QAPersonSerializer(allow_null=True)


class QAMessageSerializer(serializers.Serializer):
    message = S()


def money_fields(prefix):
    return {prefix: S(help_text="Naira, two decimals."), f"{prefix}_display": S()}


def with_money(name, base, *prefixes, **fields):
    attrs = dict(fields)
    for p in prefixes:
        attrs.update(money_fields(p))
    return type(name, (base,), attrs)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
class QASectionsSerializer(serializers.Serializer):
    cases = B()
    confirmations = B()
    finance = B()
    institutions = B()
    applications = B()
    requests = B()
    complaints = B()
    inspections = B()


class QASummaryCountsSerializer(serializers.Serializer):
    my_stages = opt(I)
    my_stages_overdue = opt(I)
    confirmations_waiting = opt(I)
    finance_queue = opt(I)
    finance_ready_for_final = opt(I)
    complaints_assigned = opt(I)
    applications_to_clear = opt(I)
    ssq_waiting = opt(I)
    inspections_upcoming = opt(I)


class QASummarySerializer(serializers.Serializer):
    sections = QASectionsSerializer()
    counts = QASummaryCountsSerializer()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------
class QACaseListQuerySerializer(serializers.Serializer):
    q = S(required=False, allow_blank=True, max_length=100)
    stage = S(required=False, allow_blank=True, max_length=80,
              help_text="A board group (new, setup, cost, visit, decision, closed) or a stage code.")
    overdue = B(required=False, default=False)
    mine = B(required=False, default=False, help_text="Only cases with an open stage I hold.")
    institution = S(required=False, allow_blank=True, max_length=20, help_text="Institution code.")
    department = S(required=False, allow_blank=True, max_length=60, help_text="Owning department code.")


class QACaseRowSerializer(serializers.Serializer):
    id = I()
    reference = S()
    institution = QAInstRefSerializer()
    owning_department = QADeptRefSerializer(allow_null=True)
    status = S(allow_blank=True)
    stage_label = S()
    stage_no = opt(I)
    holders = QAPersonSerializer(many=True)
    due_at = opt(S)
    overdue = B()
    needs_reassignment = B()
    programme_count = I()
    updated_at = S()


class QAMyStagesQuerySerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["", "cost", "visit", "funds"], required=False, default="")


class QACaseBriefSerializer(serializers.Serializer):
    id = I()
    reference = S()
    institution = QAInstRefSerializer()


class QATaskRefSerializer(serializers.Serializer):
    id = I()
    reference = S()


class QAStageLinkSerializer(serializers.Serializer):
    link_id = I()
    case = QACaseBriefSerializer()
    stage_code = S()
    stage_label = S()
    stage_no = opt(I)
    task = QATaskRefSerializer()
    opened_at = S()
    due_at = opt(S)
    overdue = B()


class QACaseStageSerializer(serializers.Serializer):
    code = S()
    label = S()
    stage_no = opt(I)
    holders = QAPersonSerializer(many=True)
    due_at = opt(S)
    overdue = B()
    can_act = B()
    why_not = S(allow_blank=True)
    problems = serializers.ListField(child=S(), help_text="What blocks approve (fix on the website).")
    approve_label = S()
    can_return = B()
    can_attach = B()
    web_tab = S(help_text="The website tab that holds this stage's work (cost, personnel, visit …).")


class QAProgressSerializer(serializers.Serializer):
    no = I()
    label = S()
    state = serializers.ChoiceField(choices=["done", "current", "todo"])
    code = S()


class QACaseProgrammeSerializer(serializers.Serializer):
    id = I()
    programme_id = I()
    name = S()
    level = opt(S)
    status = S()
    is_new_programme = B()


class QAPersonnelSerializer(serializers.Serializer):
    id = I()
    name = S()
    duty = S()
    duty_label = S()
    status = S()


class QACaseTeamSerializer(serializers.Serializer):
    desk_officer = QAPersonSerializer(allow_null=True)
    supervising_audit_officer = QAPersonSerializer(allow_null=True)
    supervising_finance_officer = QAPersonSerializer(allow_null=True)
    tracking_ict_officers = QAPersonSerializer(many=True)
    personnel = QAPersonnelSerializer(many=True)


class QAVisitSerializer(serializers.Serializer):
    planned_start = opt(S)
    planned_end = opt(S)
    team_lead = opt(S)
    status = S()


QACostSerializer = with_money("QACostSerializer", serializers.Serializer, "total", version=I(), status=S())


class QADecisionSerializer(serializers.Serializer):
    id = I()
    programme = S()
    stage = S()
    stage_label = S()
    outcome = S()
    outcome_label = S()
    approval_type = opt(S)
    approved_streams = I()
    commencement_date = opt(S)
    expiry_date = opt(S)
    note = S(allow_blank=True)
    decided_by = QAPersonSerializer(allow_null=True)
    decided_at = S()


class QACaseDocumentSerializer(QAFileSerializer):
    source = serializers.ChoiceField(choices=["stage_task", "outcome_letter"])
    stage_code = opt(S)
    description = S(allow_blank=True)
    uploaded_by = QAPersonSerializer(allow_null=True)
    uploaded_at = opt(S)


class QACaseTimelineSerializer(serializers.Serializer):
    at = S()
    kind = serializers.ChoiceField(choices=["flow", "timeline"])
    title = S()
    detail = S(allow_blank=True)
    remark = S(allow_blank=True)
    actor = QAPersonSerializer(allow_null=True)


class QAQARequestRefSerializer(serializers.Serializer):
    id = I()
    reference = S()
    qa_type = S()


class QACaseDetailSerializer(serializers.Serializer):
    id = I()
    reference = S()
    status = S(allow_blank=True)
    stage_label = S()
    stage_no = opt(I)
    started = B()
    read_only = B(help_text="True for the ICT QA monitor (R-13).")
    institution = QAInstRefSerializer()
    owning_department = QADeptRefSerializer(allow_null=True)
    qa_request = QAQARequestRefSerializer()
    programme_count = I()
    needs_reassignment = B()
    started_at = opt(S)
    completed_at = opt(S)
    programmes = QACaseProgrammeSerializer(many=True)
    progress = QAProgressSerializer(many=True)
    current_stages = QACaseStageSerializer(many=True)
    team = QACaseTeamSerializer()
    visit = QAVisitSerializer(allow_null=True)
    cost = QACostSerializer(allow_null=True)
    decisions = QADecisionSerializer(many=True)
    documents = QACaseDocumentSerializer(many=True)
    timeline = QACaseTimelineSerializer(many=True)


class QACaseActSerializer(serializers.Serializer):
    stage = S(max_length=80, help_text="One of current_stages[].code.")
    action = serializers.ChoiceField(choices=["approve", "return"])
    remark = S(required=False, allow_blank=True, max_length=4000, default="")

    def validate(self, attrs):
        if attrs["action"] == "return" and len(attrs.get("remark", "").strip()) < 3:
            raise serializers.ValidationError({"remark": ["A return needs a remark (at least 3 characters)."]})
        return attrs


class QACaseActResultSerializer(serializers.Serializer):
    message = S()
    case = QACaseDetailSerializer()


class QACaseAttachSerializer(serializers.Serializer):
    stage = S(max_length=80, help_text="The current stage whose open task gets the file.")
    file = serializers.FileField()
    description = S(required=False, allow_blank=True, max_length=255, default="")


# ---------------------------------------------------------------------------
# Confirmations
# ---------------------------------------------------------------------------
CONFIRMATION_TABS = ["pending", "started_by_me", "expired", "applied", "rejected", "failed", "withdrawn", "all"]


class QAConfirmationQuerySerializer(serializers.Serializer):
    tab = serializers.ChoiceField(choices=CONFIRMATION_TABS, required=False, default="pending")
    action = S(required=False, allow_blank=True, max_length=100, help_text="Action code filter.")
    q = S(required=False, allow_blank=True, max_length=100)


class QAActionRefSerializer(serializers.Serializer):
    code = S()
    label = S()


class QAConfirmationRowSerializer(serializers.Serializer):
    id = I()
    reference = S()
    action = QAActionRefSerializer()
    target_label = S(allow_blank=True)
    reason = S(allow_blank=True)
    status = S()
    status_label = S()
    initiated_by = QAPersonSerializer()
    initiated_at = S()
    expires_at = opt(S)
    overdue = B()
    department = QADeptRefSerializer(allow_null=True)
    decided_by = QAPersonSerializer(allow_null=True)
    decided_at = opt(S)
    can_decide = B()
    decide_reason = S(allow_blank=True)


class QAConfirmationCountsSerializer(serializers.Serializer):
    pending = I()
    mine = I()
    overdue = I()


class QADiffRowSerializer(serializers.Serializer):
    label = S()
    before = S(allow_blank=True)
    after = S(allow_blank=True)
    changed = B()


class QAConfirmOptionSerializer(serializers.Serializer):
    name = S()
    label = S()
    help = S(allow_blank=True)
    default = B()


class QAAuditRowSerializer(serializers.Serializer):
    at = S()
    label = S()
    actor = QAPersonSerializer(allow_null=True, help_text="null = the system.")


class QAConfirmationDetailSerializer(QAConfirmationRowSerializer):
    diff = QADiffRowSerializer(many=True)
    stale = B()
    confirm_options = QAConfirmOptionSerializer(many=True)
    decision_remark = S(allow_blank=True)
    failure_detail = S(allow_blank=True)
    applied_at = opt(S)
    can_withdraw = B()
    history = QAAuditRowSerializer(many=True)


class QAApproveSerializer(serializers.Serializer):
    note = S(required=False, allow_blank=True, max_length=2000, default="")
    options = serializers.DictField(child=B(), required=False, default=dict,
                                    help_text="confirm_options choices: {name: true|false}.")


class QARejectSerializer(serializers.Serializer):
    note = S(max_length=2000)

    def validate_note(self, value):
        if len(value.strip()) < 3:
            raise serializers.ValidationError("Give a reason for rejecting this change.")
        return value.strip()


class QAConfirmationResultSerializer(serializers.Serializer):
    message = S()
    confirmation = QAConfirmationDetailSerializer()


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
class QAFinanceQuerySerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=["", "first", "second", "final", "info", "overdue"], required=False,
                                    default="")
    source = serializers.ChoiceField(choices=["", "proof", "provider", "statement", "manual"], required=False,
                                     default="")
    age = serializers.ChoiceField(choices=["", "24h", "3d", "7d"], required=False, default="")
    q = S(required=False, allow_blank=True, max_length=100)


QAFinanceRowSerializer = with_money(
    "QAFinanceRowSerializer", serializers.Serializer, "balance",
    id=I(), obligation_id=I(), reference=S(), institution=QAInstRefSerializer(), description=S(), source=S(),
    source_label=S(), opened_at=S(), sla_due_at=opt(S), sla_pct=I(), overdue=B(), decision=opt(S),
    decision_label=opt(S), second_decision=opt(S), needs_second_check=B(), ready_for_final=B(),
    ready_why=S(allow_blank=True))


class QAFinanceHeroSerializer(serializers.Serializer):
    first_pass = I()
    ready_for_final = I()
    overdue = I()
    role = serializers.ChoiceField(choices=["director", "officer", "viewer"])


class QAFormulaSerializer(serializers.Serializer):
    expr = S(allow_blank=True)
    result = S(allow_blank=True)


QABillSerializer = with_money(
    "QAFinanceBillSerializer", serializers.Serializer, "amount", "balance", "amount_paid",
    id=I(), reference=S(), description=S(), for_reference=opt(S), status=S(), stage_label=S(), issued_at=S(),
    due_at=opt(S), formula=QAFormulaSerializer())

QAProofSerializer = with_money(
    "QAProofSerializer", serializers.Serializer, "amount_claimed",
    id=I(), file=QAFileSerializer(allow_null=True), kind=S(), kind_label=S(),
    paid_on=opt(S), bank_name=S(allow_blank=True), teller_number=S(allow_blank=True),
    uploaded_by=QAPersonSerializer(allow_null=True), uploaded_at=S(), same_file_as_another_bill=B())

QAProviderReportSerializer = with_money(
    "QAProviderReportSerializer", serializers.Serializer, "amount",
    id=I(), provider=S(), provider_label=S(), provider_reference=S(allow_blank=True), status=S(), status_label=S(),
    verified_paid=B(), verified_amount=opt(S), signature_checked=B())

QAStatementLineSerializer = with_money(
    "QAStatementLineSerializer", serializers.Serializer, "amount",
    id=I(), statement_id=I(), row_number=I(), value_date=opt(S), narration=S(allow_blank=True), match_status=S())


class QAPassSerializer(serializers.Serializer):
    decision = opt(S)
    decision_label = opt(S)
    by = QAPersonSerializer(allow_null=True)
    at = opt(S)
    reason = S(allow_blank=True)


class QAFirstPassStateSerializer(QAPassSerializer):
    amount_confirmed = opt(S)


class QAFinanceRightsSerializer(serializers.Serializer):
    can_first = B()
    can_second = B()
    can_final = B()
    is_director = B()
    did_first_pass = B()


class QAAdviceSerializer(QAFileSerializer):
    reference = S()


class QAFinanceDetailSerializer(serializers.Serializer):
    id = I()
    source = S()
    source_label = S()
    is_open = B()
    institution = QAInstRefSerializer()
    bill = QABillSerializer()
    proofs = QAProofSerializer(many=True)
    provider_reports = QAProviderReportSerializer(many=True)
    statement_lines = QAStatementLineSerializer(many=True)
    first_pass = QAFirstPassStateSerializer()
    second_check = QAPassSerializer(allow_null=True)
    final = QAPassSerializer()
    needs_second_check = B()
    dual_verify_threshold_display = S()
    ready_for_final = B()
    ready_why = S(allow_blank=True)
    rights = QAFinanceRightsSerializer()
    advices = QAAdviceSerializer(many=True)
    history = QAFlowEventSerializer(many=True)


class QAFirstPassSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "rejected", "needs_info"])
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, required=False, allow_null=True,
                                      help_text="Amount received; required for approved.")
    reason = S(required=False, allow_blank=True, max_length=2000, default="")

    def validate(self, attrs):
        if attrs["decision"] == "approved" and attrs.get("amount") in (None, ""):
            raise serializers.ValidationError({"amount": ["Enter the amount actually received."]})
        if attrs["decision"] != "approved" and len(attrs.get("reason", "").strip()) < 3:
            raise serializers.ValidationError({"reason": ["Give a reason."]})
        return attrs


class QASecondCheckSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "rejected"])
    reason = S(required=False, allow_blank=True, max_length=2000, default="")

    def validate(self, attrs):
        if attrs["decision"] == "rejected" and len(attrs.get("reason", "").strip()) < 3:
            raise serializers.ValidationError({"reason": ["Give a reason."]})
        return attrs


class QAFinalDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["confirmed", "rejected"])
    reason = S(required=False, allow_blank=True, max_length=2000, default="")

    def validate(self, attrs):
        if attrs["decision"] == "rejected" and len(attrs.get("reason", "").strip()) < 3:
            raise serializers.ValidationError({"reason": ["Give the reason the institution will see."]})
        return attrs


class QAFinanceResultSerializer(serializers.Serializer):
    message = S()
    item = QAFinanceDetailSerializer()


# ---------------------------------------------------------------------------
# Institutions
# ---------------------------------------------------------------------------
class QAInstitutionQuerySerializer(serializers.Serializer):
    q = S(required=False, allow_blank=True, max_length=100)
    status = S(required=False, allow_blank=True, max_length=40)
    ownership = S(required=False, allow_blank=True, max_length=60)
    type = S(required=False, allow_blank=True, max_length=60)
    dept = S(required=False, allow_blank=True, max_length=60)
    zone = S(required=False, allow_blank=True, max_length=20)
    sort = serializers.ChoiceField(choices=["name", "code", "recent", "status"], required=False, default="name")


class QAInstitutionRowSerializer(serializers.Serializer):
    id = I()
    code = S()
    name = S()
    status = S()
    status_label = S()
    ownership = opt(S)
    type = opt(S)
    state = opt(S)
    lga = S(allow_blank=True)
    zone = S(allow_blank=True)
    handling_department = QADeptRefSerializer(allow_null=True)


class QAContactSerializer(serializers.Serializer):
    kind = S()
    kind_label = S()
    value = S()
    name = S(allow_blank=True)
    is_primary = B(required=False)


class QAAddressSerializer(serializers.Serializer):
    kind = S()
    kind_label = S()
    address = S()
    town = S(allow_blank=True)
    lga = S(allow_blank=True)
    state = opt(S)
    gps = opt(S)


class QAProgrammeSerializer(serializers.Serializer):
    id = I()
    name = S()
    level = opt(S)
    category = opt(S)
    status = S()
    status_label = S()
    approved_streams = I()
    commencement_date = opt(S)
    expiry_date = opt(S)


QABillRowSerializer = with_money(
    "QABillRowSerializer", serializers.Serializer, "amount", "balance",
    id=I(), reference=S(), description=S(), status=S(), stage_label=S(), due_at=opt(S), issued_at=S())


class QAChecklistRowSerializer(serializers.Serializer):
    kind = S()
    mandatory = B()
    state = S()
    department = opt(S)
    file = QAFileSerializer(allow_null=True)


class QAInstTimelineSerializer(serializers.Serializer):
    at = S()
    event = S()
    visibility = S()
    text = S(allow_blank=True)
    actor = QAPersonSerializer(allow_null=True)


class QAInstitutionDetailSerializer(QAInstitutionRowSerializer):
    former_names = serializers.ListField(child=S())
    year_established = opt(I)
    website = opt(S)
    portal_enabled = B()
    category = opt(S)
    primary_user = QAPersonSerializer(allow_null=True)
    contacts = QAContactSerializer(many=True)
    addresses = QAAddressSerializer(many=True)
    programmes = QAProgrammeSerializer(many=True)
    open_cases = QACaseRowSerializer(many=True)
    bills = QABillRowSerializer(many=True, allow_null=True, help_text="null without payments.view_obligation.")
    documents = QAChecklistRowSerializer(many=True)
    pending_changes = I()
    timeline = QAInstTimelineSerializer(many=True)


# ---------------------------------------------------------------------------
# Applications (clearance)
# ---------------------------------------------------------------------------
class QAClearanceQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["pending", "cleared", "returned", "closed", "all"], required=False,
                                     default="pending")
    dept = S(required=False, allow_blank=True, max_length=60,
             help_text="Department code (only for staff who see every department).")
    q = S(required=False, allow_blank=True, max_length=100)
    age = serializers.ChoiceField(choices=["", "1d", "3d", "7d"], required=False, default="")


class QAApplicationRefSerializer(serializers.Serializer):
    id = I()
    reference = S()
    status = S()
    public_status = S()
    round = I()
    submitted_at = opt(S)
    institution = QAInstRefSerializer()


class QAClearanceRowSerializer(serializers.Serializer):
    id = I()
    department = QADeptRefSerializer()
    status = S()
    status_label = S()
    round = I()
    opened_at = S()
    waiting_days = I()
    remark = S(allow_blank=True)
    decided_by = QAPersonSerializer(allow_null=True)
    decided_at = opt(S)
    application = QAApplicationRefSerializer()
    can_decide = B()
    decide_reason = S(allow_blank=True)


class QAClearanceDocSerializer(serializers.Serializer):
    kind = S()
    mandatory = B()
    state = S()
    file = QAFileSerializer(allow_null=True)


class QAOtherClearanceSerializer(serializers.Serializer):
    department = QADeptRefSerializer()
    status = S()
    status_label = S()


class QAClearanceDetailSerializer(QAClearanceRowSerializer):
    documents = QAClearanceDocSerializer(many=True)
    contacts = QAContactSerializer(many=True)
    other_departments = QAOtherClearanceSerializer(many=True)
    history = QAFlowEventSerializer(many=True)


class QAClearanceDecideSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["clear", "return"])
    remark = S(required=False, allow_blank=True, max_length=4000, default="")
    accept_documents = B(required=False, default=True,
                         help_text="Accept documents still waiting for review when clearing (website default).")

    def validate(self, attrs):
        if attrs["decision"] == "return" and len(attrs.get("remark", "").strip()) < 3:
            raise serializers.ValidationError({"remark": ["Write the remark the institution will see."]})
        return attrs


class QAClearanceResultSerializer(serializers.Serializer):
    message = S()
    clearance = QAClearanceDetailSerializer()


# ---------------------------------------------------------------------------
# QA requests and SSQs
# ---------------------------------------------------------------------------
class QARequestQuerySerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=["", "draft", "clearance", "fee", "ssq", "case", "closed"],
                                    required=False, default="")
    type = S(required=False, allow_blank=True, max_length=60)
    q = S(required=False, allow_blank=True, max_length=100)


class QAQATypeSerializer(serializers.Serializer):
    code = S()
    name = S()


class QARequestRowSerializer(serializers.Serializer):
    id = I()
    reference = S()
    institution = QAInstRefSerializer()
    qa_type = QAQATypeSerializer()
    status = S()
    public_status = S()
    programme_count = I()
    fee_note = S(allow_blank=True)
    created_at = S()
    submitted_at = opt(S)


class QARequestProgrammeSerializer(serializers.Serializer):
    id = I()
    programme_id = I()
    name = S()
    level = opt(S)
    status = S()
    is_new_programme = B()
    clearance_status = opt(S)
    clearance_remark = S(allow_blank=True)
    requested_streams = opt(I)


class QARequestSSQSerializer(serializers.Serializer):
    id = I()
    programme = S()
    status = S()
    public_status = S()
    version = I()
    fields_done = I()
    fields_total = I()
    submitted_at = opt(S)


class QAQueryRowSerializer(serializers.Serializer):
    id = I()
    reference = S()
    title = S()
    status = S()
    due_at = opt(S)
    created_at = S()


class QACaseRefSerializer(serializers.Serializer):
    id = I()
    reference = S()


class QARequestDetailSerializer(QARequestRowSerializer):
    programmes = QARequestProgrammeSerializer(many=True)
    ssqs = QARequestSSQSerializer(many=True)
    bills = QABillRowSerializer(many=True)
    queries = QAQueryRowSerializer(many=True)
    case = QACaseRefSerializer(allow_null=True)
    can_clear = B()
    clear_reason = S(allow_blank=True)
    history = QAFlowEventSerializer(many=True)


class QARequestClearSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["clear", "return"])
    remark = S(required=False, allow_blank=True, max_length=4000, default="")

    def validate(self, attrs):
        if attrs["decision"] == "return" and len(attrs.get("remark", "").strip()) < 3:
            raise serializers.ValidationError({"remark": ["Write the remark the institution will see."]})
        return attrs


class QARequestResultSerializer(serializers.Serializer):
    message = S()
    request = QARequestDetailSerializer()


class QASSQQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["submitted", "accepted", "returned", "draft", "all"], required=False,
                                     default="submitted")
    type = S(required=False, allow_blank=True, max_length=60)
    level = S(required=False, allow_blank=True, max_length=60)
    q = S(required=False, allow_blank=True, max_length=100)


class QASSQRowSerializer(serializers.Serializer):
    id = I()
    request = QACaseRefSerializer()
    institution = QAInstRefSerializer()
    programme = S()
    level = opt(S)
    qa_type = S()
    status = S()
    public_status = S()
    version = I()
    is_current = B()
    submitted_at = opt(S)
    waiting_days = opt(I)


class QAEvidenceSerializer(serializers.Serializer):
    field_key = S()
    status = S()
    file = QAFileSerializer(allow_null=True)


class QASSQVersionSerializer(serializers.Serializer):
    id = I()
    version = I()
    status = S()
    is_current = B()


class QASSQDetailSerializer(QASSQRowSerializer):
    sections = serializers.JSONField(help_text="The submitted SSQ as rendered on the website: "
                                               "[{key, title, fields: [{key, label, type, value | rows | columns}]}].")
    evidence = QAEvidenceSerializer(many=True)
    versions = QASSQVersionSerializer(many=True)
    submission_checksum_ok = opt(B)
    review_remark = S(allow_blank=True)
    reviewed_by = QAPersonSerializer(allow_null=True)
    reviewed_at = opt(S)
    can_review = B()
    review_reason = S(allow_blank=True)
    history = QAFlowEventSerializer(many=True)


class QASSQDecideSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["accept", "return"])
    remark = S(required=False, allow_blank=True, max_length=4000, default="")

    def validate(self, attrs):
        if attrs["decision"] == "return" and len(attrs.get("remark", "").strip()) < 3:
            raise serializers.ValidationError({"remark": ["Say what the institution must change."]})
        return attrs


class QASSQResultSerializer(serializers.Serializer):
    message = S()
    ssq = QASSQDetailSerializer()


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------
class QAComplaintQuerySerializer(serializers.Serializer):
    tab = serializers.ChoiceField(choices=["", "mine", "department", "escalated", "all"], required=False, default="",
                                  help_text="Empty = the website's default tab.")
    status = serializers.ChoiceField(choices=["open", "resolved", "closed", "all"], required=False, default="open")
    layer = S(required=False, allow_blank=True, max_length=60)
    priority = S(required=False, allow_blank=True, max_length=10)
    sla = serializers.ChoiceField(choices=["", "breached", "near", "on_track", "paused"], required=False, default="")
    q = S(required=False, allow_blank=True, max_length=100)


class QALayerSerializer(serializers.Serializer):
    code = S()
    name = S()


class QASLASerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["on_track", "near", "breached", "paused", "none"])
    pct = I()
    left = S(allow_blank=True)
    due = opt(S)


class QAComplaintRowSerializer(serializers.Serializer):
    id = I()
    reference = S()
    subject = S()
    layer = QALayerSerializer()
    priority = S()
    priority_label = S()
    status = S(allow_blank=True)
    status_label = S(allow_blank=True)
    is_open = B()
    institution = QAInstRefSerializer(allow_null=True)
    raised_by = QAPersonSerializer()
    raiser_type = S()
    current_department = QADeptRefSerializer(allow_null=True)
    current_assignee = QAPersonSerializer(allow_null=True)
    sla = QASLASerializer()
    created_at = S()


class QAComplaintCountsSerializer(serializers.Serializer):
    mine = I()
    department = I()
    escalated = I()
    all = I()
    breached = I()


class QAComplaintMessageSerializer(serializers.Serializer):
    id = I()
    author = QAPersonSerializer(allow_null=True)
    body = S()
    visibility = serializers.ChoiceField(choices=["public", "internal"])
    is_system = B()
    created_at = S()
    attachments = QAFileSerializer(many=True)


class QAAnswerSerializer(serializers.Serializer):
    label = S()
    value = serializers.JSONField()


class QARelatedSerializer(serializers.Serializer):
    type = S()
    id = S()
    label = S(allow_blank=True)


class QAComplaintStageSerializer(serializers.Serializer):
    code = S()
    label = S(allow_blank=True)
    department = QADeptRefSerializer(allow_null=True)
    assigned_to = QAPersonSerializer(allow_null=True)
    entered_at = S()
    due_at = opt(S)
    left_at = opt(S)
    breached_at = opt(S)
    escalation_level = I()


class QAEscalationSerializer(serializers.Serializer):
    level = I()
    to = QAPersonSerializer(allow_null=True)
    by = QAPersonSerializer(allow_null=True)
    reason = S(allow_blank=True)
    at = S()


class QAComplaintActionSerializer(serializers.Serializer):
    code = S()
    label = S()
    requires_remark = B()
    needs_referral_target = B()


class QAComplaintRightsSerializer(serializers.Serializer):
    can_write = B()
    can_write_internal = B()
    can_act = B()
    act_why = S(allow_blank=True)
    actions = QAComplaintActionSerializer(many=True)
    can_escalate = B()


class QAComplaintDetailSerializer(QAComplaintRowSerializer):
    body = S()
    answers = QAAnswerSerializer(many=True)
    related = QARelatedSerializer(allow_null=True)
    resolution = S(allow_blank=True)
    resolved_at = opt(S)
    closed_at = opt(S)
    reopened_count = I()
    messages = QAComplaintMessageSerializer(many=True)
    stages = QAComplaintStageSerializer(many=True)
    watchers = QAPersonSerializer(many=True)
    escalations = QAEscalationSerializer(many=True)
    rights = QAComplaintRightsSerializer()


class QAComplaintReplySerializer(serializers.Serializer):
    body = S(max_length=10000)
    visibility = serializers.ChoiceField(choices=["public", "internal"], required=False, default="internal")
    files = serializers.ListField(child=serializers.FileField(), required=False, default=list,
                                  help_text="multipart only; repeat the 'files' part.")

    def validate_body(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Write a message.")
        return value


class QAComplaintActSerializer(serializers.Serializer):
    action = S(max_length=60, help_text="One of rights.actions[].code.")
    remark = S(required=False, allow_blank=True, max_length=4000, default="")
    refer_department = I(required=False, allow_null=True, help_text="Department id (refer only).")
    refer_user = I(required=False, allow_null=True, help_text="Staff user id (refer only).")


class QAComplaintEscalateSerializer(serializers.Serializer):
    reason = S(max_length=255)
    to_user = I(required=False, allow_null=True, help_text="Staff user id; blank = the next configured level.")

    def validate_reason(self, value):
        if len(value.strip()) < 3:
            raise serializers.ValidationError("Say why you are escalating.")
        return value.strip()


class QAComplaintResultSerializer(serializers.Serializer):
    message = S()
    complaint = QAComplaintDetailSerializer()


# ---------------------------------------------------------------------------
# Inspections
# ---------------------------------------------------------------------------
class QAInspectionQuerySerializer(serializers.Serializer):
    mine = B(required=False, default=True, help_text="true (default): visits I lead or am on the team of.")
    when = serializers.ChoiceField(choices=["", "upcoming", "past"], required=False, default="")
    status = S(required=False, allow_blank=True, max_length=40)
    q = S(required=False, allow_blank=True, max_length=100)


class QAInspectionRowSerializer(serializers.Serializer):
    id = I()
    reference = S()
    kind = S()
    kind_label = S()
    status = S(allow_blank=True)
    case = QACaseRefSerializer()
    institution = QAInstRefSerializer()
    scheduled_start = S()
    scheduled_end = S()
    actual_start = opt(S)
    actual_end = opt(S)
    team_lead = QAPersonSerializer(allow_null=True)
    my_role = opt(S)


class QATeamMemberSerializer(serializers.Serializer):
    name = S()
    role_on_team = S(allow_blank=True)
    kind = serializers.ChoiceField(choices=["staff", "resource_person", "qa_personnel"])
    programme = opt(S)
    confirmed = B()


class QAAttendanceSerializer(serializers.Serializer):
    name = S()
    role_on_team = S(allow_blank=True)
    duty = S()
    planned = B()
    attended = B()


class QAReportRowSerializer(serializers.Serializer):
    id = I()
    programme = S()
    status = S()


class QAInspectionDetailSerializer(QAInspectionRowSerializer):
    team = QATeamMemberSerializer(many=True)
    attendance = QAAttendanceSerializer(many=True)
    reports = QAReportRowSerializer(many=True)
