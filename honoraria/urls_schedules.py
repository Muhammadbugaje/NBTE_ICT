"""v2.1 X32/X34: honoraria payment schedules (mounted at /qa/finance/honoraria/)."""
from django.urls import path

from . import views, views_files

app_name = "honoraria_sched"

urlpatterns = [
    path("", views.schedule_list, name="list"),
    path("build/", views.schedule_build, name="build"),
    path("income/", views.income_report, name="income"),
    path("roller/", views.roller, name="roller"),  # v2.2: who is next to be paid
    path("roller/prepare/", views.roller_prepare, name="roller_prepare"),
    path("roller/bank/", views.roller_bank, name="roller_bank"),
    # v2.3: one honorarium file per institution (SOP-01 -> SOP-03)
    path("files/", views_files.files_list, name="files"),
    path("files/refresh/", views_files.files_refresh, name="files_refresh"),
    path("files/pay-officer/", views_files.pay_officer, name="pay_officer"),
    path("files/<int:pk>/", views_files.file_detail, name="file"),
    path("files/<int:pk>/prepare/", views_files.file_prepare, name="file_prepare"),
    path("batch/<int:pk>/approve/", views_files.batch_approve, name="batch_approve"),
    path("batch/<int:pk>/withdraw/", views_files.batch_cancel, name="batch_cancel"),
    path("<int:pk>/", views.schedule_detail, name="detail"),
    path("<int:pk>/approve/", views.schedule_approve, name="approve"),
    path("<int:pk>/pay/", views.schedule_pay, name="pay"),
    path("line/<int:pk>/confirm/", views.line_confirm, name="line_confirm"),
]
