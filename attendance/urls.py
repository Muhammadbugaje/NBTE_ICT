from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    path("",                       views.attendance_home,       name="home"),
    path("check-in/",              views.check_in_page,         name="check_in"),
    path("check-in/api/",          views.check_in_api,          name="check_in_api"),
    path("check-in/preview/",      views.location_preview_api,  name="location_preview"),
    path("mine/",                  views.my_attendance,         name="my_attendance"),
    path("department/",            views.department_report,     name="department"),
    path("all/",                   views.all_departments_report, name="all_departments"),   # ← new
    path("upload/",                views.upload_matrix,         name="upload"),
    path("records/<int:pk>/edit/", views.manual_edit,           name="manual_edit"),
    path("export/excel/",          views.export_excel,          name="export_excel"),
    path("export/pdf/",            views.export_pdf,            name="export_pdf"),
    path("export/all/excel/",      views.export_all_excel,      name="export_all_excel"),  # ← new
    path("sample/",                views.download_sample,       name="download_sample"),
]