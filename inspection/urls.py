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
    path("mappings/<int:pk>/decide/", views.mapping_decide, name="mapping_decide"),
    path("inspections/", views.inspections, name="inspections"),
    path("inspections/live/", views.inspections_live, name="inspections_live"),
    path("calendar/", views.visit_calendar, name="calendar"),
    path("calendar/live/", views.calendar_live, name="calendar_live"),
    path("inspections/<int:pk>/", views.inspection_detail, name="inspection"),
    path("inspections/<int:pk>/live/", views.inspection_live, name="inspection_live"),
    path("inspections/<int:pk>/complete/", views.inspection_complete, name="inspection_complete"),
    path("inspections/<int:pk>/attendance/", views.inspection_attendance, name="inspection_attendance"),
    path("reports/<int:pk>/", views.report_detail, name="report"),
    path("reports/<int:pk>/live/", views.report_live, name="report_live"),
    path("reports/<int:pk>/compiled.pdf", views.report_pdf, name="report_pdf"),
    path("reports/<int:pk>/save/", views.report_save, name="report_save"),
    path("reports/<int:pk>/member/save/", views.member_report_save, name="member_report_save"),
    path("reports/<int:pk>/members/<int:member_pk>/review/", views.member_report_review,
         name="member_report_review"),
    path("reports/<int:pk>/attendees/<int:attendee_pk>/skip/", views.member_attendee_skip,
         name="member_attendee_skip"),
    path("reports/<int:pk>/endorse/", views.report_endorse, name="report_endorse"),
    path("reports/<int:pk>/vvt/", views.report_vvt, name="report_vvt"),  # v2.2 X40
    path("endorsements/", views.endorsements, name="endorsements"),
    path("endorsements/live/", views.endorsements_live, name="endorsements_live"),
    path("service-history/", views.service_history, name="service"),
    path("service-history/export/", views.service_export, name="service_export"),
    path("service-history/export.pdf", views.service_export_pdf, name="service_export_pdf"),  # Phase 8
]
