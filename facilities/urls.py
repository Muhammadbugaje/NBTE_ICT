from django.urls import path

from . import views

app_name = "facilities"

urlpatterns = [
    path("", views.index, name="index"),
    path("export.xlsx", views.export, name="export_all"),
    path("import/word/", views.import_word, name="import_word"),
    # Smart Excel import (linked from the general import hub as "facilities:smart_import").
    path("import/excel/", views.smart_import, name="smart_import"),
    path("import/excel/done/", views.smart_import_done, name="smart_import_done"),
    path("import/excel/<str:token>/", views.smart_import_preview, name="smart_import_preview"),
    path("export/workbook.xlsx", views.smart_export, name="smart_export"),
    path("handlers/", views.handlers_update, name="handlers"),
    path("handlers/search/", views.staff_search, name="staff_search"),
    path("items/<int:pk>/", views.item_update, name="item_update"),
    path("<slug:slug>/", views.programme, name="programme"),
    path("<slug:slug>/update/", views.programme_update, name="programme_update"),
    path("<slug:slug>/export.xlsx", views.export, name="export"),
]
