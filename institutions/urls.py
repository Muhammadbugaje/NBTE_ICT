"""Internal institution management (Master Plan §6, §13), namespace "qa_inst".

Mounted at /qa/institutions/ (and Settings tables at /qa/settings/…).
Institutions are addressed by their code (KDP), which never changes.
"""
from django.urls import path

from . import programme_views, record_views, views

app_name = "qa_inst"

urlpatterns = [
    path("", views.institution_list, name="list"),
    path("add/", views.add_tvet, name="add_tvet"),
    path("lgas/", views.lga_options, name="lga_options"),
    path("code-suggestion/", views.code_suggestion, name="code_suggestion"),
    path("actions/<str:action_code>/", views.launcher, name="launcher"),
    path("import/", views.import_page, name="import"),
    path("import/template/<str:kind>/", views.import_template, name="import_template"),
    path("import/<int:pk>/", views.import_batch, name="import_batch"),
    path("import/<int:pk>/send-credentials/", views.import_invites, name="import_invites"),
    path("export/", views.export, name="export"),
    path("<str:code>/", views.institution_detail, name="detail"),
    path("<str:code>/actions/<str:action_code>/", views.action, name="action"),
    path("<str:code>/programmes/<int:pk>/", programme_views.programme_detail, name="programme"),
    path("<str:code>/timeline/export/", record_views.timeline_export, name="timeline_export"),
    path("<str:code>/record/", record_views.record_pdf, name="record_pdf"),
    path("<str:code>/record/zip/", record_views.record_zip, name="record_zip"),
    path("<str:code>/payments/statement/", record_views.payment_statement, name="payment_statement"),
]
