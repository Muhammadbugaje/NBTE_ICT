"""Ticket URLs."""
from django.urls import path

from . import views

app_name = "tickets"

urlpatterns = [
    path("", views.ticket_list,name="list"),
    path("my/", views.my_tickets, name="my_tickets"),
    path("assigned/", views.assigned_to_me, name="assigned_to_me"),

    path("new/", views.ticket_create, name="create"),
    path("new/<slug:category_code>/", views.ticket_create, name="create_for_category"),

    path("<int:pk>/", views.ticket_detail, name="detail"),
    path("<int:pk>/stream/", views.ticket_stream, name="stream"),

    path("<int:pk>/assign/", views.ticket_assign, name="assign"),
    path("<int:pk>/start/", views.ticket_start, name="start"),
    path("<int:pk>/awaiting/", views.ticket_awaiting, name="awaiting"),
    path("<int:pk>/resolve/", views.ticket_resolve, name="resolve"),
    path("<int:pk>/close/", views.ticket_close, name="close"),
    path("<int:pk>/reopen/", views.ticket_reopen, name="reopen"),
    path("<int:pk>/cancel/", views.ticket_cancel, name="cancel"),

    path("<int:pk>/comment/", views.ticket_comment, name="comment"),
    path("<int:pk>/internal-note/", views.ticket_internal_note, name="internal_note"),
    path("<int:pk>/attach/", views.ticket_attach, name="attach"),

    path("<int:pk>/comments/<int:comment_id>/delete/",
         views.ticket_delete_comment, name="delete_comment"),
    path("<int:pk>/attachments/<int:attachment_id>/delete/",
         views.ticket_delete_attachment, name="delete_attachment"),

    path("<int:pk>/timeline/", views.ticket_timeline_partial, name="timeline_partial"),
    path("<int:pk>/director-signoff/", views.ticket_director_signoff, name="director_signoff"),

    # v2.1 Phase C
    path("counts/", views.ticket_counts, name="counts"),
    path("<int:pk>/route/", views.ticket_route, name="route"),
    path("<int:pk>/division/accept/", views.ticket_division_accept, name="division_accept"),
    path("<int:pk>/division/decline/", views.ticket_division_decline, name="division_decline"),
    path("<int:pk>/request-funds/", views.ticket_request_funds, name="request_funds"),
    path("<int:pk>/rate/", views.ticket_rate, name="rate"),
    path("<int:pk>/report.pdf", views.ticket_report_pdf, name="report_pdf"),
]