from django.urls import path

from core import views_files
from . import appearance_views, views
from public import views as public_views

app_name = "config"

urlpatterns = [
    path("", views.settings_index, name="settings"),
    # v2.1 Phase A (A9): Settings → Appearance
    path("appearance/", appearance_views.appearance_settings, name="appearance"),
    path("appearance/preview/<slug:page>/", appearance_views.appearance_preview, name="appearance_preview"),
    path(
        "leave-role-entitlements/",
        views.save_leave_role_entitlements,
        name="save_leave_role_entitlements",
    ),
    path("<int:pk>/save/", views.settings_save, name="save"),
    path("test-email/", views.send_test_email, name="test_email"),
    # v2.1 Phase A (A5/A6): Settings → Branding
    path("branding/", views.branding_page, name="branding"),
    path("branding/select/", views.branding_select, name="branding_select"),
    path("branding/upload/", views.branding_upload, name="branding_upload"),
    # v2.1 Phase A (A1/A2): Settings → History (HTMX)
    path("history/", public_views.history_settings, name="history"),
    path("history/settings/", public_views.history_settings_save, name="history_settings_save"),
    path("history/new/", public_views.history_create, name="history_create"),
    path("history/<int:pk>/edit/", public_views.history_edit, name="history_edit"),
    path("history/<int:pk>/move/<str:direction>/", public_views.history_move, name="history_move"),
    path("history/<int:pk>/delete/", public_views.history_delete, name="history_delete"),

    # ---- File management ---------------------------------------------
    path("files/",                  views_files.files_dashboard,       name="files_dashboard"),
    path("files/scan/",             views_files.files_scan,            name="files_scan"),
    path("files/scan/stream/",      views_files.files_scan_stream,     name="files_scan_stream"),
    path("files/purge/",            views_files.files_purge,           name="files_purge"),
    path("files/delete-selected/",  views_files.files_delete_selected, name="files_delete_selected"),
]