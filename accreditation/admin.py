"""Django admin for QA requests, SOP-01 onboarding and SOP-03 cases.

System screens; unscoped by design (§4). Everything that moves a QA record
goes through its flow (qa_core.flow) and, where needed, a second officer, so
the admin only reads these records: view, search, filter and export.
"""
from django.contrib import admin

from core.admin_base import (
    ChoicesDropdownFilter, ModelAdmin, RangeDateTimeFilter, RelatedDropdownFilter, StatusFilter,
    TabularInline, UnscopedModelAdmin, UnscopedReadOnlyModelAdmin, badge,
)

from .models import (
    SSQ, Application, ApplicationCycle, CatalogueItem, ClearanceDelegate, CostLine, CostSheet, DepartmentalClearance, Evidence,
    FundRelease, OnboardingProgrammeReview, PersonnelEntry, PersonnelList, ProfessionalBodyNotice, QACase, QARequest, QARequestProgramme,
    QAType, Query, RateTable, Response, SSQTemplate, Submission, VisitDetail,
)


@admin.register(Application)
class ApplicationAdmin(UnscopedModelAdmin):
    list_display = ("reference", "institution", "status_badge", "round", "routed_department", "submitted_at")
    list_filter = (StatusFilter, ("submitted_at", RangeDateTimeFilter))
    search_fields = ("reference", "institution__code", "institution__name")
    # Links to institution-scoped rows stay read-only: their pickers would
    # read the scoped default manager.
    readonly_fields = ("status", "institution")
    date_hierarchy = "created_at"
    status_badge = badge("status", "Status")

    def get_queryset(self, request):
        return Application.unscoped.select_related("institution", "routed_department")


@admin.register(DepartmentalClearance)
class DepartmentalClearanceAdmin(UnscopedModelAdmin):
    list_display = ("application", "department", "round", "status_badge", "decided_by", "decided_at")
    list_filter = (("status", ChoicesDropdownFilter), ("department", RelatedDropdownFilter),
                   ("decided_at", RangeDateTimeFilter))
    search_fields = ("application__reference", "remark")
    readonly_fields = ("application",)
    status_badge = badge("status", "Status")

    def get_queryset(self, request):
        return DepartmentalClearance.unscoped.select_related("application", "department", "decided_by")


@admin.register(ApplicationCycle)
class ApplicationCycleAdmin(ModelAdmin):
    list_display = ("name", "opens_at", "closes_at", "is_open", "applies_to_onboarding")
    list_filter = ("is_open", "applies_to_onboarding")
    search_fields = ("name",)


class QARequestProgrammeInline(TabularInline):
    model = QARequestProgramme
    extra = 0
    can_delete = False
    fields = ("programme", "status", "clearance_status", "is_new_programme")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return QARequestProgramme.unscoped.select_related("programme")


@admin.register(QARequest)
class QARequestAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("reference", "institution", "qa_type", "status_badge", "is_joint_qa", "submitted_at")
    list_filter = (StatusFilter, ("qa_type", RelatedDropdownFilter), "is_joint_qa",
                   ("submitted_at", RangeDateTimeFilter))
    search_fields = ("reference", "institution__code", "institution__name")
    date_hierarchy = "created_at"
    inlines = [QARequestProgrammeInline]
    status_badge = badge("status", "Status")

    def get_queryset(self, request):
        return QARequest.unscoped.select_related("institution", "qa_type")


@admin.register(QACase)
class QACaseAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("reference", "institution", "status_badge", "owning_department", "desk_officer",
                    "programme_count", "needs_reassignment", "started_at")
    list_filter = (StatusFilter, ("owning_department", RelatedDropdownFilter), "needs_reassignment",
                   ("started_at", RangeDateTimeFilter))
    search_fields = ("reference", "institution__code", "institution__name")
    date_hierarchy = "created_at"
    status_badge = badge("status", "Stage")

    def get_queryset(self, request):
        return QACase.unscoped.select_related("institution", "owning_department", "desk_officer")


@admin.register(SSQ)
class SSQAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("pk", "qa_request_programme", "version", "status_badge", "is_current", "submitted_at",
                    "reviewed_at")
    list_filter = (StatusFilter, "is_current", ("submitted_at", RangeDateTimeFilter))
    search_fields = ("qa_request_programme__qa_request__reference",)
    exclude = ("data", "meta")
    status_badge = badge("status", "Status")


@admin.register(Query)
class QueryAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("reference", "institution", "title", "status_badge", "due_at", "closed_at")
    list_filter = (("status", ChoicesDropdownFilter), ("due_at", RangeDateTimeFilter))
    search_fields = ("reference", "title", "body", "institution__code")
    date_hierarchy = "created_at"
    status_badge = badge("status", "Status")


@admin.register(CostSheet)
class CostSheetAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("case", "version", "status_badge", "is_current", "total", "approved_at")
    list_filter = (("status", ChoicesDropdownFilter), "is_current")
    search_fields = ("case__reference",)
    status_badge = badge("status", "Status")


@admin.register(FundRelease)
class FundReleaseAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("case", "amount", "status_badge", "pay_officer", "released_reference", "released_at")
    list_filter = (("status", ChoicesDropdownFilter), ("released_at", RangeDateTimeFilter))
    search_fields = ("case__reference", "released_reference")
    status_badge = badge("status", "Status")


@admin.register(VisitDetail)
class VisitDetailAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("case", "planned_start", "planned_end", "team_lead", "status_badge", "approved_at")
    list_filter = (("status", ChoicesDropdownFilter),)
    search_fields = ("case__reference",)
    status_badge = badge("status", "Status")


# Reference tables and supporting records: read-only lists with export.
for _model, _cols, _search in (
        (QAType, ("code", "name", "flow_code", "fee_schedule", "is_active", "sort_order"), ("code", "name")),
        (SSQTemplate, ("name", "qa_type", "version", "status", "is_current", "published_at"), ("name",)),
        (CatalogueItem, ("name", "programme_name", "kind", "required_quantity", "unit", "is_active"),
         ("name", "programme_name")),
        (RateTable, ("code", "role", "allowance_type", "amount", "unit", "department", "is_active"),
         ("code", "role")),
        (QARequestProgramme, ("qa_request", "programme", "status", "clearance_status", "is_new_programme"),
         ("qa_request__reference",)),
        (Submission, ("ssq", "version", "template_version", "submitted_by", "submitted_at"), ()),
        (Evidence, ("original_name", "ssq", "field_key", "status", "uploaded_by", "created_at"),
         ("original_name",)),
        (Response, ("query", "responded_by", "at", "original_name"), ("body",)),
        (CostLine, ("sheet", "staff", "role_on_exercise", "kind", "days", "unit_amount", "amount"), ()),
        (PersonnelList, ("case", "division", "status", "submitted_by", "decided_at"), ()),
        (PersonnelEntry, ("personnel_list", "staff", "duty", "status"), ()),
        (ProfessionalBodyNotice, ("case", "body", "email", "status", "sent_at"), ("email",)),
):
    admin.site.register(_model, type(f"{_model.__name__}Admin", (UnscopedReadOnlyModelAdmin,), {
        "list_display": _cols, "search_fields": _search,
        "exclude": tuple(f for f in ("data", "schema", "data_snapshot", "rendered_snapshot")
                         if f in {x.name for x in _model._meta.concrete_fields}) or None,
    }))


@admin.register(ClearanceDelegate)
class ClearanceDelegateAdmin(ModelAdmin):
    """Phase 0 (O10): the delegate's permission override is kept in step by
    ``accreditation.clearance_access.add_delegate`` / ``revoke_delegate``;
    prefer the Delegates panel on the clearance queue."""

    list_display = ("user", "department", "active", "expires_at", "granted_by", "created_at")
    list_filter = ("active", ("department", RelatedDropdownFilter))
    search_fields = ("user__email", "user__first_name", "user__last_name", "department__name")
    raw_id_fields = ("user", "granted_by", "revoked_by")
    readonly_fields = ("revoked_at", "revoked_by")

    def save_model(self, request, obj, form, change):
        from .clearance_access import _sync_override

        super().save_model(request, obj, form, change)
        _sync_override(obj.user, granted_by=request.user)


admin.site.register(OnboardingProgrammeReview, type("OnboardingProgrammeReviewAdmin", (UnscopedReadOnlyModelAdmin,), {
    "list_display": ("application", "programme", "department", "round", "status", "decided_by", "decided_at"),
    "search_fields": ("application__reference",)}))
