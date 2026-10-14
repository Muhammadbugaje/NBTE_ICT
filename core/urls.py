from django.urls import path

from . import health, impersonation_views as imp, importexport_views as ie, views
from . import views_search

app_name = "core"
urlpatterns = [
    path("", views.landing, name="landing"),
    path("home/", views.home, name="home"),
    path("health/", health.health, name="health"),
    path("contact/", views.contact_submit, name="contact_submit"),
    path("contact-inbox/", views.contact_inbox, name="contact_inbox"),
    path("contact-inbox/<int:pk>/", views.contact_detail, name="contact_detail"),
    path("contact-inbox/<int:pk>/assign/", views.contact_assign, name="contact_assign"),
    path("contact-inbox/<int:pk>/read/", views.contact_mark_read, name="contact_mark_read"),
    path("contact-inbox/<int:pk>/status/", views.contact_update_status, name="contact_update_status"),
    path("contact-inbox/<int:pk>/notes/", views.contact_add_note, name="contact_add_note"),
    path("files/<str:label>/<int:pk>/", views.protected_download, name="protected_download"),

    path("leadership/", views.leadership_page, name="leadership_page"),

    # Import / Export wizard
    path("import-export/", ie.wizard_landing, name="import_export"),
    path("import-export/<slug:spec_name>/", ie.wizard_landing, name="import_export_spec"),
    path("import-export/<slug:spec_name>/panel/", ie.wizard_panel, name="ie_panel"),
    path("import-export/<slug:spec_name>/template/", ie.download_template, name="ie_template"),
    path("import-export/<slug:spec_name>/export/", ie.download_export, name="ie_export"),
    path("import-export/<slug:spec_name>/preview/", ie.preview, name="ie_preview"),
    path("import-export/<slug:spec_name>/commit/", ie.commit, name="ie_commit"),
    path("import-export/<slug:spec_name>/rejected/", ie.download_rejected, name="ie_rejected"),

    #search
    path("search/", views_search.search_view, name="search"),
    
    # Impersonation (superuser only)
    path("impersonate/", imp.pick_user, name="impersonate_pick"),
    path("impersonate/<int:pk>/start/", imp.start, name="impersonate_start"),
    path("impersonate/stop/", imp.stop, name="impersonate_stop"),
]