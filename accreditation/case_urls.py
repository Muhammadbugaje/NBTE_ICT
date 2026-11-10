"""Internal SOP-03 screens (Phase 4), namespace "qa_case", mounted at /qa/cases/."""
from django.urls import path

from . import case_views as views
from . import export_views
from . import report_form_views

app_name = "qa_case"

urlpatterns = [
    path("", views.board, name="board"),
    path("my-stages/", views.queue, name="queue"),
    path("drift/", views.drift, name="drift"),
    path("drift/run/", views.drift_run, name="drift_run"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/live/", views.live, name="live"),  # v2.8: SOP-03 panel version stamp
    path("<int:pk>/summary/", export_views.case_summary_pdf, name="summary_pdf"),  # Phase 8
    path("<int:pk>/start/", views.start, name="start"),
    path("<int:pk>/ssq/<int:ssq_id>/", views.source_ssq, name="source_ssq"),  # v2.7: the submitted SSQ, read-only
    path("<int:pk>/ssq/<int:ssq_id>/pdf/", views.source_ssq_pdf, name="source_ssq_pdf"),
    path("<int:pk>/act/", views.act, name="act"),
    path("<int:pk>/desk-officer/", views.desk_officer, name="desk_officer"),
    path("<int:pk>/assign/", views.assign, name="assign"),
    path("<int:pk>/supervisor/<str:kind>/", views.supervisor, name="supervisor"),
    path("<int:pk>/cost/lines/", views.cost_add, name="cost_add"),
    path("<int:pk>/cost/people/", views.cost_people, name="cost_people"),  # SOP-03: ranked Person picker
    path("<int:pk>/cost/calculate/", views.cost_calc, name="cost_calc"),  # v2.2 X39
    path("<int:pk>/cost/actual/", views.cost_actual, name="cost_actual"),  # v2.2 X39
    path("<int:pk>/cost/lines/<int:line_id>/", views.cost_line, name="cost_line"),
    path("<int:pk>/personnel/", views.personnel_add, name="personnel_add"),
    path("<int:pk>/personnel/<int:entry_id>/remove/", views.personnel_remove, name="personnel_remove"),
    path("<int:pk>/personnel/external/", views.personnel_external_add, name="personnel_external_add"),
    path("<int:pk>/personnel/external/<int:member_id>/remove/", views.personnel_external_remove,
         name="personnel_external_remove"),
    path("<int:pk>/visit/", views.visit, name="visit"),
    # v2.7: controlled changes — visit dates and stage rollback (Director → ES)
    path("<int:pk>/changes/request/", views.change_request, name="change_request"),
    path("<int:pk>/changes/<int:req_id>/decide/", views.change_decide, name="change_decide"),
    path("<int:pk>/changes/<int:req_id>/apply/", views.change_apply, name="change_apply"),
    path("<int:pk>/changes/<int:req_id>/cancel/", views.change_cancel, name="change_cancel"),
    path("<int:pk>/release/", views.release, name="release"),
    path("<int:pk>/reports/<int:report_id>/recommend/", views.recommend, name="recommend"),
    path("<int:pk>/pb-reports/<int:report_id>/file/", views.body_report_file, name="body_report_file"),
    # v2.4: template-driven visit reports (NBTE/P/4A programme evaluation, NBTE/QA/ICT/VVT/001)
    path("<int:pk>/reports/<int:report_id>/form/<str:kind>/", report_form_views.report_form, name="report_form"),
    path("<int:pk>/reports/<int:report_id>/form/<str:kind>/pdf/", report_form_views.report_form_pdf,
         name="report_form_pdf"),
    path("<int:pk>/tasks/<int:link_id>/reassign/", views.reassign, name="reassign"),
    path("<int:pk>/remind/<str:stage>/", views.remind, name="remind"),  # SOP-03 redesign: remind the holder
    # Phase 8 (Master Plan Phase 6): capacity, decisions, letters, the Chairman's batch
    path("batch/", views.batch, name="batch"),
    path("<int:pk>/capacity/<int:row_id>/review/", views.capacity_review, name="capacity_review"),
    path("<int:pk>/capacity/<int:row_id>/<str:kind>/", views.capacity_run, name="capacity_run"),
    path("<int:pk>/decisions/", views.decisions_save, name="decisions_save"),
    path("<int:pk>/letters/<int:letter_id>/regenerate/", views.letter_regenerate, name="letter_regenerate"),
]
