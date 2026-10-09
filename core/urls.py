from django.urls import path

from . import health, impersonation_views as imp, importexport_views as ie, views

app_name = "core"
urlpatterns = [
    path("", views.landing, name="landing"),
    path("home/", views.home, name="home"),
    path("health/", health.health, name="health"),
    path("contact/", views.contact_submit, name="contact_submit"),
    path("files/<str:label>/<int:pk>/", views.protected_download, name="protected_download"),

    # Import / Export wizard
    path("import-export/", ie.wizard_landing, name="import_export"),
    path("import-export/<slug:spec_name>/", ie.wizard_landing, name="import_export_spec"),
    path("import-export/<slug:spec_name>/panel/", ie.wizard_panel, name="ie_panel"),
    path("import-export/<slug:spec_name>/template/", ie.download_template, name="ie_template"),
    path("import-export/<slug:spec_name>/export/", ie.download_export, name="ie_export"),
    path("import-export/<slug:spec_name>/preview/", ie.preview, name="ie_preview"),
    path("import-export/<slug:spec_name>/commit/", ie.commit, name="ie_commit"),
    path("import-export/<slug:spec_name>/rejected/", ie.download_rejected, name="ie_rejected"),

    # Impersonation (superuser only)
    path("impersonate/", imp.pick_user, name="impersonate_pick"),
    path("impersonate/<int:pk>/start/", imp.start, name="impersonate_start"),
    path("impersonate/stop/", imp.stop, name="impersonate_stop"),
]