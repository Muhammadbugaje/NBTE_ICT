"""Internal institution management (Master Plan §6, §13), namespace "qa_inst".

Mounted at /qa/institutions/ (and Settings tables at /qa/settings/…).
Institutions are addressed by their code (KDP), which never changes.
"""
from django.urls import path

from . import views

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
    path("export/", views.export, name="export"),
    path("<str:code>/", views.institution_detail, name="detail"),
    path("<str:code>/actions/<str:action_code>/", views.action, name="action"),
]
