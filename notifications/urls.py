from django.urls import path

from . import settings_views, views

app_name = "notifications"

urlpatterns = [
    path("", views.list_view, name="list"),
    path("settings/", settings_views.notification_settings, name="settings"),  # Phase 9
    path("bell/", views.bell, name="bell"),
    path("stream/", views.stream, name="stream"),
    path("mark-all-read/", views.mark_all_read, name="mark_all_read"),
    path("bulk/", views.bulk, name="bulk"),
    path("<int:pk>/read/", views.mark_read, name="mark_read"),
    path("<int:pk>/unread/", views.mark_unread, name="mark_unread"),
    path("<int:pk>/open/", views.open_and_read, name="open"),
]