"""Internal complaint screens, namespace "qa_complaints" (mounted at /qa/complaints/)."""
from django.urls import path

from . import export_views, views

app_name = "qa_complaints"

urlpatterns = [
    path("", views.queue, name="queue"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/report.csv", views.report_csv, name="report_csv"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/record/", export_views.complaint_record_pdf, name="record_pdf"),  # Phase 8
    path("<int:pk>/act/<str:action>/", views.act, name="act"),
    path("<int:pk>/message/", views.message, name="message"),
    path("<int:pk>/escalate/", views.escalate, name="escalate"),
    path("<int:pk>/hold/", views.hold, name="hold"),
]
