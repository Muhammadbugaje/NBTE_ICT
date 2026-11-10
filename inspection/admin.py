"""Django admin for SOP-04 inspections and the resource-person register.

System screens: read-only, unscoped by design (Master Plan §4). Mapping,
vetting and reports change only through their own pages and flows.
"""
from django.contrib import admin

from core.admin_base import (
    ChoicesDropdownFilter, RangeDateFilter, RangeDateTimeFilter, StatusFilter, UnscopedReadOnlyModelAdmin, badge,
)

from .models import (
    Inspection, InspectionTeamMember, PostVisitMemberReport, PostVisitMemberReview, PostVisitPersonnel,
    PostVisitReport, QAMapping, QAPersonnel, ReportEndorsement, ResourcePerson, ResourcePersonDiscipline,
    ResourcePersonFile, ServiceRecord, SSQReviewAssignment,
)


@admin.register(ResourcePerson)
class ResourcePersonAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("full_name", "email", "source", "status_badge", "rank", "rating_avg", "activated_at")
    list_filter = (("status", ChoicesDropdownFilter), ("source", ChoicesDropdownFilter),
                   ("activated_at", RangeDateTimeFilter))
    search_fields = ("full_name", "email", "phone", "professional_body")
    status_badge = badge("status", "Status", {"vetted": "success", "nominated": "info"})


@admin.register(QAPersonnel)
class QAPersonnelAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("staff", "is_verification_officer", "is_team_lead_eligible", "active", "registered_by")
    list_filter = ("active", "is_verification_officer", "is_team_lead_eligible")
    search_fields = ("staff__first_name", "staff__last_name", "staff__employee_no")


@admin.register(Inspection)
class InspectionAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("reference", "case", "kind", "team_lead", "scheduled_start", "scheduled_end", "status_badge")
    list_filter = (StatusFilter, ("kind", ChoicesDropdownFilter), ("scheduled_start", RangeDateFilter))
    search_fields = ("reference", "case__reference")
    date_hierarchy = "created_at"
    status_badge = badge("status", "Status")


@admin.register(QAMapping)
class QAMappingAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("case", "duty", "qa_personnel", "resource_person", "status_badge", "from_date", "to_date")
    list_filter = (("status", ChoicesDropdownFilter), ("duty", ChoicesDropdownFilter),
                   ("from_date", RangeDateFilter))
    search_fields = ("case__reference", "resource_person__full_name")
    status_badge = badge("status", "Status")


@admin.register(PostVisitReport)
class PostVisitReportAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("inspection", "qa_request_programme", "recommendation", "status_badge", "submitted_at", "version")
    list_filter = (StatusFilter, ("recommendation", ChoicesDropdownFilter), ("submitted_at", RangeDateTimeFilter))
    search_fields = ("inspection__reference", "summary")
    status_badge = badge("status", "Status")


@admin.register(ServiceRecord)
class ServiceRecordAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("person_name", "person_type", "duty", "institution", "served_from", "served_to", "rating")
    list_filter = (("person_type", ChoicesDropdownFilter), ("duty", ChoicesDropdownFilter),
                   ("served_from", RangeDateFilter))
    search_fields = ("person_name", "role_on_team", "case__reference")


for _model, _cols in (
        (ResourcePersonDiscipline, ("resource_person", "discipline", "is_primary")),
        (ResourcePersonFile, ("resource_person", "kind", "original_name", "uploaded_by", "created_at")),
        (SSQReviewAssignment, ("ssq", "reviewer", "assigned_at", "completed_at", "outcome")),
        (InspectionTeamMember, ("inspection", "staff", "role_on_team", "confirmed_by_division_director")),
        (PostVisitPersonnel, ("inspection", "staff", "resource_person", "role_on_team", "duty", "planned",
                              "attended")),
        (PostVisitMemberReport, ("report", "attendee", "author", "status", "submitted_at", "reviewed_by")),
        (PostVisitMemberReview, ("member_report", "action", "actor", "at")),
        (ReportEndorsement, ("report", "layer", "action", "actor", "at")),
):
    admin.site.register(_model, type(f"{_model.__name__}Admin", (UnscopedReadOnlyModelAdmin,),
                                     {"list_display": _cols}))
