from django.urls import path

from . import views

app_name = "announcements"

urlpatterns = [
    path("", views.announcement_list, name="list"),
    path("new/", views.announcement_create, name="create"),
    path("<int:pk>/", views.announcement_detail, name="detail"),
    path("<int:pk>/edit/", views.announcement_edit, name="edit"),
    path("<int:pk>/acknowledge/", views.acknowledge, name="acknowledge"),
    path("<int:pk>/vote/", views.vote, name="vote"),
    path("<int:pk>/report/", views.announcement_report, name="report"),
    path("<int:pk>/report/remind/", views.remind, name="remind"),
]
