from django.urls import path

from . import pwa, health, impersonation_views as imp, importexport_views as ie, views
from . import views_search
from public import views as public_views  # v2.1 A1
from . import views_security  # v2.1 S-26
from . import importdiff_views as idv  # v2.1 X45
from .datahub import views as datahub  # Data Hub: full configuration export/import (superusers)

app_name = "core"
urlpatterns = [
    path("", views.landing, name="landing"),
    path("home/", views.home, name="home"),
    path("health/", health.health, name="health"),
    path("sw.js", pwa.service_worker, name="service_worker"),  # NBTE offline page (replaces other apps' workers)
    path("offline/", pwa.offline, name="offline"),
    path("contact/", views.contact_submit, name="contact_submit"),
    path("contact-inbox/", views.contact_inbox, name="contact_inbox"),
    path("contact-inbox/bulk/", views.contact_bulk, name="contact_bulk"),
    path("contact-inbox/<int:pk>/", views.contact_detail, name="contact_detail"),
    path("contact-inbox/<int:pk>/assign/", views.contact_assign, name="contact_assign"),
    path("contact-inbox/<int:pk>/read/", views.contact_mark_read, name="contact_mark_read"),
    path("contact-inbox/<int:pk>/status/", views.contact_update_status, name="contact_update_status"),
    path("contact-inbox/<int:pk>/notes/", views.contact_add_note, name="contact_add_note"),
    path("files/<str:label>/<int:pk>/", views.protected_download, name="protected_download"),

    path("leadership/", views.leadership_page, name="leadership_page"),
    path("history/", public_views.history_page, name="history_page"),  # v2.1 A1

    # v2.1 Phase B (pb-platform): sign-in code (S-26) and import diff preview (X45)
    path("security/verify-code/", views_security.verify_code, name="verify_code"),
    path("imports/new/<slug:spec_name>/", idv.upload, name="import_upload"),
    path("imports/<int:pk>/", idv.preview, name="import_preview"),
    path("imports/<int:pk>/tab/<slug:tab>/", idv.preview_tab, name="import_preview_tab"),
    path("imports/<int:pk>/toggle/", idv.toggle, name="import_toggle"),
    path("imports/<int:pk>/apply/", idv.apply, name="import_apply"),
    path("imports/<int:pk>/discard/", idv.discard, name="import_discard"),
    path("imports/<int:pk>/errors.xlsx", idv.errors_xlsx, name="import_errors"),
    path("imports/export/<slug:spec_name>.xlsx", idv.export_xlsx, name="import_export_diff"),

    # Data Hub: move all configuration to another database (superusers only)
    path("data-hub/", datahub.hub, name="datahub"),
    path("data-hub/export/<slug:category>.xlsx", datahub.export, name="datahub_export"),
    path("data-hub/template/<slug:category>.xlsx", datahub.template, name="datahub_template"),
    path("data-hub/import/", datahub.upload, name="datahub_upload"),
    path("data-hub/import/<slug:token>/apply/", datahub.apply, name="datahub_apply"),
    path("data-hub/import/<slug:token>/discard/", datahub.discard, name="datahub_discard"),

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