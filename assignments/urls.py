"""Phase P pages, mounted at /qa/ (namespace "assignments")."""
from django.urls import path

from . import views

app_name = "assignments"

urlpatterns = [
    path("my-work/", views.my_work, name="my_work"),
    path("my-work/<int:pk>/<str:action>/", views.work_action, name="work_action"),
    path("settings/access/grants/", views.grants_log, name="grants"),
    path("settings/access/grants/<int:pk>/revoke/", views.grant_revoke, name="grant_revoke"),
    path("settings/access/who/", views.who_index, name="who_index"),
    path("settings/access/who/<str:code>/", views.who, name="who"),
]
