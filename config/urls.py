from django.urls import path

from core import views_files
from . import views

app_name = "config"

urlpatterns = [
    path("", views.settings_index, name="settings"),
    path(
        "leave-role-entitlements/",
        views.save_leave_role_entitlements,
        name="save_leave_role_entitlements",
    ),
    path("<int:pk>/save/", views.settings_save, name="save"),
    path("test-email/", views.send_test_email, name="test_email"),

    # ---- File management ---------------------------------------------
    path("files/",                  views_files.files_dashboard,       name="files_dashboard"),
    path("files/scan/",             views_files.files_scan,            name="files_scan"),
    path("files/scan/stream/",      views_files.files_scan_stream,     name="files_scan_stream"),
    path("files/purge/",            views_files.files_purge,           name="files_purge"),
    path("files/delete-selected/",  views_files.files_delete_selected, name="files_delete_selected"),
]