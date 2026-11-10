from django.urls import path

from . import views, views_analytics

app_name = "attendance"

urlpatterns = [
    path("",                       views.attendance_home,        name="home"),
    path("check-in/",              views.check_in_page,          name="check_in"),
    path("check-in/api/",          views.check_in_api,           name="check_in_api"),
    path("check-in/preview/",      views.location_preview_api,   name="location_preview"),
    path("check-in/manual/",       views.check_in_manual,        name="check_in_manual"),
    path("codes/",                 views.location_codes_view,    name="location_codes"),
    path("codes/api/",             views.location_codes_api,     name="location_codes_api"),
    path("mine/",                  views.my_attendance,          name="my_attendance"),
    path("department/",            views.department_report,      name="department"),
    path("all/",                   views.all_departments_report, name="all_departments"),
    path("upload/",                views.upload_matrix,          name="upload"),
    path("records/<int:pk>/edit/", views.manual_edit,            name="manual_edit"),
    path("export/excel/",          views.export_excel,           name="export_excel"),
    path("export/pdf/",            views.export_pdf,             name="export_pdf"),
    path("export/all/excel/",      views.export_all_excel,       name="export_all_excel"),
    path("sample/",                views.download_sample,        name="download_sample"),
    # v2.1 Phase D
    path("check-out/api/",         views.check_out_api,          name="check_out_api"),
    path("scan/<str:token>/",      views.scan,                   name="scan"),
    path("office/<int:pk>/screen/", views.office_screen,         name="office_screen"),
    path("office/<int:pk>/qr/",    views.office_qr,              name="office_qr"),
    path("analytics/",             views_analytics.analytics_page,    name="analytics"),
    path("analytics/person/<int:user_id>/", views_analytics.person, name="analytics_person"),
    path("analytics/export/",      views_analytics.export,       name="analytics_export"),
]