from django.urls import path

from . import views
from django.urls import include, path


app_name = "workflow"

urlpatterns = [
    path("", views.my_requests, name="my_requests"),
    path("new/", views.raise_request_view, name="raise"),
    path("new/<slug:type_code>/", views.raise_request_view, name="raise_type"),
    path("awaiting/bulk/", views.bulk_awaiting_action, name="bulk_awaiting_action"),
    path("awaiting/", views.awaiting_action, name="awaiting_action"),
    path("incoming/", views.incoming, name="incoming"),
    path("assigned/", views.assigned_to_me, name="assigned_to_me"),
    path("office/", views.office_requests, name="office_requests"),      # ← NEW
    path("all/", views.all_requests, name="all_requests"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/approve/", views.approve, name="approve"),
    path("<int:pk>/return/", views.return_step, name="return"),
    path("<int:pk>/reject/", views.reject, name="reject"),
    path("<int:pk>/assign/", views.assign, name="assign"),
    path("<int:pk>/fulfilled/", views.fulfilled, name="fulfilled"),
    path("<int:pk>/close/", views.close_request, name="close"),
    path("<int:pk>/cancel/", views.cancel, name="cancel"),
    path("<int:pk>/delete/", views.delete_request, name="delete"),
    path("<int:pk>/comment/", views.comment, name="comment"),
    path("<int:pk>/attach/", views.attach, name="attach"),
    path("<int:pk>/record.pdf", views.record_pdf, name="record_pdf"),
    path("<int:pk>/convert-to-ticket/", views.convert_to_ticket, name="convert_to_ticket"),
    path("<int:pk>/return-to-requester/", views.return_to_requester, name="return_to_requester"),
    path("<int:pk>/resubmit/", views.resubmit, name="resubmit"),
    path("<int:pk>/comments/<int:comment_id>/delete/",views.delete_comment, name="delete_comment"),
    path("<int:pk>/attachments/<int:attachment_id>/delete/",views.delete_attachment, name="delete_attachment"),
    path("<int:pk>/timeline/", views.timeline_partial, name="timeline_partial"),
    path("<int:pk>/stream/",   views.request_stream,  name="request_stream"),

]
