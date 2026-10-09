from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("",        views.portal_home, name="home"),
    path("how-to/", views.how_to,      name="how_to"),

    # Announcements
    path("announcements/",          views.announcement_list,   name="announcement_list"),
    path("announcements/new/",      views.announcement_create, name="announcement_create"),
    path("announcements/<int:pk>/", views.announcement_detail, name="announcement_detail"),
    path("announcements/<int:pk>/edit/",   views.announcement_update, name="announcement_update"),
    path("announcements/<int:pk>/delete/", views.announcement_delete, name="announcement_delete"),
    path("announcements/<int:pk>/pin/",    views.announcement_toggle_pin, name="announcement_toggle_pin"),

    # Events
    path("events/",          views.event_list,   name="event_list"),
    path("events/new/",      views.event_create, name="event_create"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("events/<int:pk>/edit/",   views.event_update, name="event_update"),
    path("events/<int:pk>/delete/", views.event_delete, name="event_delete"),
    path("events/<int:pk>/rsvp/",   views.event_rsvp,   name="event_rsvp"),
    path("events/<int:pk>.ics",     views.event_ics,    name="event_ics"),

    # Reminders
    path("reminders/",          views.reminder_list,   name="reminder_list"),
    path("reminders/new/",      views.reminder_create, name="reminder_create"),
    path("reminders/<int:pk>/", views.reminder_detail, name="reminder_detail"),
    path("reminders/<int:pk>/edit/",    views.reminder_update,  name="reminder_update"),
    path("reminders/<int:pk>/dismiss/", views.reminder_dismiss, name="reminder_dismiss"),

    # Documents
    path("documents/",          views.document_list,          name="document_list"),
    path("documents/new/",      views.document_create,        name="document_create"),
    path("documents/<int:pk>/", views.document_detail,        name="document_detail"),
    path("documents/<int:pk>/edit/",    views.document_update,       name="document_update"),
    path("documents/<int:pk>/delete/",  views.document_delete,       name="document_delete"),
    path("documents/<int:pk>/new-version/", views.document_upload_version, name="document_upload_version"),
    path("documents/<int:pk>/download/",           views.document_download, name="document_download"),
    path("documents/<int:pk>/versions/<int:version_pk>/download/",
         views.document_download, name="document_version_download"),

    # Public (no login required)
    path("public-documents/", views.public_document_list, name="public_document_list"),

    # Awards
    path("awards/",                     views.awards_list,   name="awards_list"),
    path("awards/new/",                 views.award_create,  name="award_create"),
    path("awards/<int:pk>/",            views.award_detail,  name="award_detail"),
    path("awards/<int:pk>/edit/",       views.award_update,  name="award_update"),
    path("awards/<int:pk>/delete/",     views.award_delete,  name="award_delete"),

    # Holidays
    path("holidays/",                   views.holidays_list, name="holidays_list"),
    path("holidays/new/",               views.holiday_create, name="holiday_create"),
    path("holidays/<int:pk>/edit/",     views.holiday_update, name="holiday_update"),
    path("holidays/<int:pk>/delete/",   views.holiday_delete, name="holiday_delete"),

    # Absence indicator fragment
    path("absence/today/",              views.absence_today, name="absence_today"),


    # Gallery
    path("gallery/",                          views.gallery_list,          name="gallery_list"),
    path("gallery/new/",                      views.gallery_album_create,  name="gallery_album_create"),
    path("gallery/<int:pk>/",                 views.gallery_detail,        name="gallery_detail"),
    path("gallery/<int:pk>/edit/",            views.gallery_album_update,  name="gallery_album_update"),
    path("gallery/<int:pk>/delete/",          views.gallery_album_delete,  name="gallery_album_delete"),
    path("gallery/<int:pk>/upload/",          views.gallery_album_upload,  name="gallery_album_upload"),
    path("gallery/<int:pk>/reorder/",         views.gallery_item_reorder,  name="gallery_item_reorder"),
    path("gallery/items/<int:pk>/delete/",    views.gallery_item_delete,   name="gallery_item_delete"),
    path("gallery/<int:pk>/items/bulk-delete/", views.gallery_items_bulk_delete, name="gallery_items_bulk_delete"),

    path("categories/<slug:kind>/quick-create/", views.category_quick_create, name="category_quick_create"),

]