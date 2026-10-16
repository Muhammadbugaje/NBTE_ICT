from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("settings/", include("config.urls")),
    path("messages/", include("messaging.urls")),
    path("manage/messages/", include("messaging.management_urls")),
    path("notifications/", include("notifications.urls")),
    path("workflow/", include("workflow.urls")),
    path("manage/workflow/", include("workflow.management_urls")),
    path("assets/", include("assets.urls")), 
    path("tickets/", include("tickets.urls")),
    path("tasks/", include("tasks.urls")),
    path("dashboards/", include("dashboards.urls")),
    path("leave/", include("leave.urls")),
    path("attendance/", include("attendance.urls")),
    path("staff/", include("staff.urls", namespace="staff")),
    path("portal/", include("portal.urls", namespace="portal")),
    path("showcase/", include("showcase.urls")),
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)