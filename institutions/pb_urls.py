"""Professional bodies and joint QA mapping (Phase 6), namespace "qa_pb"."""
from django.urls import path

from . import pb_views as views

app_name = "qa_pb"

urlpatterns = [
    path("", views.body_list, name="list"),
    path("new/", views.body_new, name="body_new"),
    path("mappings/", views.mapping_list, name="mappings"),
    path("mappings/save/", views.mapping_new, name="mapping_new"),
    path("mappings/<int:pk>/save/", views.mapping_edit, name="mapping_edit"),
    path("mappings/<int:pk>/toggle/", views.mapping_toggle, name="mapping_toggle"),
    path("import/", views.import_page, name="import"),
    path("import/template/", views.import_template, name="import_template"),
    path("export/", views.export, name="export"),
    path("<int:pk>/", views.body_detail, name="body_detail"),
    path("<int:pk>/edit/", views.body_edit, name="body_edit"),
]
