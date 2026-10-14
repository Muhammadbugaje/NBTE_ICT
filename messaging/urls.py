from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("new/", views.compose, name="compose"),
    path("new/send/", views.compose_send, name="compose_send"),
    path("<int:pk>/", views.thread_detail, name="thread"),
    path("<int:pk>/reply/", views.reply, name="reply"),
    path("<int:pk>/message/<int:message_pk>/edit/", views.edit_message, name="edit_message"),
    path("<int:pk>/message/<int:message_pk>/delete/", views.delete_message, name="delete_message"),
    path("<int:pk>/delete/", views.delete_chat, name="delete_chat"),
    path("<int:pk>/archive/", views.archive, name="archive"),
    path("archived/", views.archived, name="archived"),
    path("<int:pk>/unarchive/", views.unarchive, name="unarchive"),
]