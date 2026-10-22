"""Phase 2 Settings → QA tables (Master Plan §13, §14), namespace "qa_set".

Mounted at /qa/settings/ next to the Phase 1 settings pages:
fees and bank accounts, payment methods, document rules and lookups.
"""
from django.urls import path

from accreditation import settings_views as ssq_views
from complaints import settings_views as complaint_views
from inspection import settings_views as workload_views
from institutions import views as institution_views
from payments import views as payment_views

app_name = "qa_set"

urlpatterns = [
    path("fees/", payment_views.settings_fees, name="fees"),
    path("fees/bank/", payment_views.settings_bank_form, name="bank_form"),
    path("fees/schedule/<int:pk>/", payment_views.settings_fee_form, name="fee_form"),
    path("fees/new/", payment_views.settings_fee_new, name="fee_new"),  # Phase 8
    path("fees/test/", payment_views.settings_fee_test, name="fee_test"),
    path("payment-methods/", payment_views.settings_methods, name="methods"),
    path("documents/", institution_views.settings_documents, name="documents"),
    path("lookups/", institution_views.settings_lookups, name="lookups"),
    path("lookups/<str:kind>/", institution_views.settings_lookups, name="lookups_kind"),
    # Phase 3: SSQ template builder and the curriculum catalogue
    path("ssq-templates/", ssq_views.templates, name="ssq_templates"),
    path("ssq-templates/new/<int:type_id>/", ssq_views.template_new, name="ssq_template_new"),
    path("ssq-templates/<int:pk>/", ssq_views.template_builder, name="ssq_template"),
    path("ssq-templates/<int:pk>/section/", ssq_views.template_section, name="ssq_template_section"),
    path("ssq-templates/<int:pk>/field/", ssq_views.template_field, name="ssq_template_field"),
    path("ssq-templates/<int:pk>/json/", ssq_views.template_json, name="ssq_template_json"),
    path("ssq-templates/<int:pk>/publish/", ssq_views.template_publish, name="ssq_template_publish"),
    path("ssq-templates/<int:pk>/discard/", ssq_views.template_discard, name="ssq_template_discard"),
    path("ssq-templates/<int:pk>/preview/", ssq_views.template_preview, name="ssq_template_preview"),
    path("catalogue/", ssq_views.catalogue, name="catalogue"),
    path("catalogue/<int:pk>/", ssq_views.catalogue_item, name="catalogue_item"),
    # Phase 4: the SOP-03 rate table (cost sheet amounts)
    path("rates/", ssq_views.rates, name="rates"),
    path("rates/<int:pk>/", ssq_views.rate_edit, name="rate_edit"),
    # Phase 8: approval types (B18)
    path("approval-types/", ssq_views.approval_types, name="approval_types"),
    path("approval-types/<int:pk>/", ssq_views.approval_type_edit, name="approval_type_edit"),
    # Phase 7: complaint layers, routing, escalation and the knowledge base
    path("complaints/", complaint_views.complaints_settings, name="complaints"),
    path("complaints/<str:section>/<int:pk>/", complaint_views.complaints_settings_edit, name="complaints_edit"),
    # Phase 8: fair workload limits per rank (qa.workload.limits)
    path("workload/", workload_views.workload_limits, name="workload"),
]
