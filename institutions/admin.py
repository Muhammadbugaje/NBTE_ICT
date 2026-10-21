"""Django admin for the institutions app (system screens: superusers only).

Admin is system code, so querysets use the explicit `unscoped` managers
(Master Plan §4). Day-to-day changes go through the §6 actions, which need a
second officer — admin is for support, not for routine edits.
"""
from django.contrib import admin

from .models import (
    Discipline, DocumentKind, DocumentRequirement, ImpersonationSession, Institution, InstitutionAddress,
    InstitutionCategory, InstitutionContact, InstitutionDocument, InstitutionImportBatch, InstitutionOwnership,
    InstitutionRequest, InstitutionRequestType, InstitutionType, InstitutionUser, Programme, ProgrammeCategory,
    ProgrammeLevel, ZonalAssignment,
)


class UnscopedAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return self.model.unscoped.all()


class ContactInline(admin.TabularInline):
    model = InstitutionContact
    extra = 0
    fields = ("kind", "name", "value", "is_current")

    def get_queryset(self, request):
        return InstitutionContact.unscoped.all()


class AddressInline(admin.TabularInline):
    model = InstitutionAddress
    extra = 0
    fields = ("kind", "line1", "town", "lga", "state", "is_current")

    def get_queryset(self, request):
        return InstitutionAddress.unscoped.all()


@admin.register(Institution)
class InstitutionAdmin(UnscopedAdmin):
    list_display = ("code", "name", "status", "ownership", "category", "handling_department", "portal_enabled",
                    "updated_at")
    list_filter = ("status", "ownership", "institution_type", "handling_department", "portal_enabled", "zone")
    search_fields = ("code", "name", "lga")
    inlines = [ContactInline, AddressInline]

    def get_queryset(self, request):
        return Institution.unscoped.select_related("handling_department", "ownership", "category")


@admin.register(Programme)
class ProgrammeAdmin(UnscopedAdmin):
    list_display = ("name", "level", "institution", "status", "approved_streams", "expiry_date")
    list_filter = ("status", "level")
    search_fields = ("name", "institution__code", "institution__name")


@admin.register(InstitutionDocument)
class InstitutionDocumentAdmin(UnscopedAdmin):
    list_display = ("institution", "kind", "version", "status", "uploaded_by", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("institution__code", "institution__name")
    readonly_fields = ("file", "sha256", "size_bytes", "original_name")


@admin.register(InstitutionUser)
class InstitutionUserAdmin(UnscopedAdmin):
    list_display = ("user", "institution", "institution_role", "is_primary_contact", "credentials_last_sent_at")
    search_fields = ("user__email", "institution__code")


@admin.register(InstitutionRequest)
class InstitutionRequestAdmin(UnscopedAdmin):
    list_display = ("institution", "request_type", "reference", "status", "created_at")
    list_filter = ("status", "request_type")


@admin.register(ImpersonationSession)
class ImpersonationSessionAdmin(UnscopedAdmin):
    list_display = ("institution", "staff", "started_at", "expires_at", "ended_at", "pages_viewed", "write_mode")
    readonly_fields = ("token", "page_log")


@admin.register(InstitutionContact)
class InstitutionContactAdmin(UnscopedAdmin):
    list_display = ("institution", "kind", "name", "value", "is_current")


@admin.register(InstitutionAddress)
class InstitutionAddressAdmin(UnscopedAdmin):
    list_display = ("institution", "kind", "line1", "state", "is_current")


for model, cols in ((InstitutionOwnership, ("code", "name", "is_public", "is_active")),
                    (InstitutionType, ("code", "name", "default_department", "is_active")),
                    (InstitutionCategory, ("code", "name", "institution_type", "routing_department", "is_active")),
                    (Discipline, ("code", "name", "is_active")), (ProgrammeLevel, ("code", "name", "is_active")),
                    (ProgrammeCategory, ("name", "discipline", "students_per_stream", "is_technology_based")),
                    (DocumentKind, ("code", "name", "max_mb", "is_active")),
                    (DocumentRequirement, ("document_kind", "process", "ownership", "institution_type",
                                           "is_mandatory", "reviewing_department", "is_active")),
                    (InstitutionRequestType, ("code", "name", "is_enabled")),
                    (ZonalAssignment, ("zone_code", "staff", "from_date", "to_date")),
                    (InstitutionImportBatch, ("pk", "kind", "status", "created_by", "created_at"))):
    admin.site.register(model, type(f"{model.__name__}Admin", (admin.ModelAdmin,), {"list_display": cols}))
