from django.urls import path

from . import views

app_name = "org"

urlpatterns = [
    path("departments/", views.department_list, name="departments"),
    path("departments/<slug:code>/", views.department_detail, name="department"),
    path("departments/<slug:code>/director/assign/", views.director_assign, name="director_assign"),
    path("departments/<slug:code>/director/vacate/", views.director_vacate, name="director_vacate"),
    path("departments/<slug:code>/divisions/new/", views.division_create, name="division_create"),
    path("departments/<slug:code>/divisions/<int:pk>/", views.division_update, name="division_update"),
    path("departments/<slug:code>/divisions/members/", views.division_member, name="division_member"),
    path("departments/<slug:code>/access/grant/", views.access_grant, name="access_grant"),
    path("departments/<slug:code>/access/<int:pk>/revoke/", views.access_revoke, name="access_revoke"),
    path("settings/offices/", views.offices, name="offices"),
    path("settings/offices/new/", views.office_save, name="office_create"),
    path("settings/offices/<int:pk>/", views.office_save, name="office_save"),
    path("settings/grade-levels/", views.grade_levels, name="grade_levels"),
    path("settings/grade-levels/new/", views.grade_level_save, name="grade_level_create"),
    path("settings/grade-levels/<int:pk>/", views.grade_level_save, name="grade_level_save"),
    path("settings/designations/", views.designations, name="designations"),
    path("settings/designations/<int:pk>/", views.designation_save, name="designation_save"),
]
