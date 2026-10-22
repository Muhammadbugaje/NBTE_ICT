"""Internal SOP-03 screens (Phase 4), namespace "qa_case", mounted at /qa/cases/."""
from django.urls import path

from . import case_views as views
from . import export_views

app_name = "qa_case"

urlpatterns = [
    path("", views.board, name="board"),
    path("my-stages/", views.queue, name="queue"),
    path("drift/", views.drift, name="drift"),
    path("drift/run/", views.drift_run, name="drift_run"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/summary/", export_views.case_summary_pdf, name="summary_pdf"),  # Phase 8
    path("<int:pk>/start/", views.start, name="start"),
    path("<int:pk>/act/", views.act, name="act"),
    path("<int:pk>/desk-officer/", views.desk_officer, name="desk_officer"),
    path("<int:pk>/supervisor/<str:kind>/", views.supervisor, name="supervisor"),
    path("<int:pk>/cost/lines/", views.cost_add, name="cost_add"),
    path("<int:pk>/cost/lines/<int:line_id>/", views.cost_line, name="cost_line"),
    path("<int:pk>/personnel/", views.personnel_add, name="personnel_add"),
    path("<int:pk>/personnel/<int:entry_id>/remove/", views.personnel_remove, name="personnel_remove"),
    path("<int:pk>/visit/", views.visit, name="visit"),
    path("<int:pk>/release/", views.release, name="release"),
    path("<int:pk>/reports/<int:report_id>/recommend/", views.recommend, name="recommend"),
    path("<int:pk>/tasks/<int:link_id>/reassign/", views.reassign, name="reassign"),
    # Phase 8 (Master Plan Phase 6): capacity, decisions, letters, the Chairman's batch
    path("batch/", views.batch, name="batch"),
    path("<int:pk>/capacity/<int:row_id>/<str:kind>/", views.capacity_run, name="capacity_run"),
    path("<int:pk>/decisions/", views.decisions_save, name="decisions_save"),
    path("<int:pk>/letters/<int:letter_id>/regenerate/", views.letter_regenerate, name="letter_regenerate"),
]
