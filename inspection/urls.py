"""Internal Phase 5 screens, namespace "qa_insp", mounted at /qa/inspection/."""
from django.urls import path

from . import views

app_name = "qa_insp"

urlpatterns = [
    path("resource-persons/", views.rp_list, name="rp_list"),
    path("resource-persons/new/", views.rp_new, name="rp_new"),
    path("resource-persons/<int:pk>/", views.rp_detail, name="rp_detail"),
    path("resource-persons/<int:pk>/<str:op>/", views.rp_action, name="rp_action"),
    path("nominations/", views.nominations, name="nominations"),
    path("personnel/", views.personnel, name="personnel"),
    path("finder/", views.finder, name="finder"),
    path("finder/map/", views.finder_map, name="finder_map"),
    path("mappings/", views.mappings, name="mappings"),
    path("inspections/", views.inspections, name="inspections"),
    path("calendar/", views.visit_calendar, name="calendar"),
    path("inspections/<int:pk>/", views.inspection_detail, name="inspection"),
    path("inspections/<int:pk>/complete/", views.inspection_complete, name="inspection_complete"),
    path("inspections/<int:pk>/attendance/", views.inspection_attendance, name="inspection_attendance"),
    path("reports/<int:pk>/", views.report_detail, name="report"),
    path("reports/<int:pk>/save/", views.report_save, name="report_save"),
    path("reports/<int:pk>/endorse/", views.report_endorse, name="report_endorse"),
    path("endorsements/", views.endorsements, name="endorsements"),
    path("service-history/", views.service_history, name="service"),
    path("service-history/export/", views.service_export, name="service_export"),
    path("service-history/export.pdf", views.service_export_pdf, name="service_export_pdf"),  # Phase 8
]
