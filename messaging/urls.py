from django.urls import path

from . import memos_views, views

app_name = "messaging"

urlpatterns = [
    # ---- Threads / messages ----
    path("", views.inbox, name="inbox"),
    path("new/", views.compose, name="compose"),
    path("new/send/", views.compose_send, name="compose_send"),
    path("<int:pk>/", views.thread_detail, name="thread"),
    path("<int:pk>/reply/", views.reply, name="reply"),
    path("<int:pk>/message/<int:message_pk>/edit/",
         views.edit_message, name="edit_message"),
    path("<int:pk>/message/<int:message_pk>/delete/",
         views.delete_message, name="delete_message"),
    path("<int:pk>/delete/", views.delete_chat, name="delete_chat"),
    path("<int:pk>/archive/", views.archive, name="archive"),
    path("archived/", views.archived, name="archived"),
    path("<int:pk>/unarchive/", views.unarchive, name="unarchive"),

    # ---- Memos ----
    path("memos/", memos_views.memo_list, name="memo_list"),
    path("memos/new/", memos_views.memo_create, name="memo_create"),
    path("memos/<int:pk>/", memos_views.memo_detail, name="memo_detail"),
    path("memos/<int:pk>/edit/", memos_views.memo_update, name="memo_update"),
    path("memos/<int:pk>/publish/",
         memos_views.memo_publish, name="memo_publish"),
    path("memos/<int:pk>/acknowledge/",
         memos_views.memo_acknowledge, name="memo_acknowledge"),
    path("memos/<int:pk>/archive/",
         memos_views.memo_archive, name="memo_archive"),
    path("memos/<int:pk>/unarchive/",
         memos_views.memo_unarchive, name="memo_unarchive"),
    path("memos/<int:pk>/delete/",
         memos_views.memo_delete, name="memo_delete"),
    path("memos/<int:pk>/attach/",
         memos_views.memo_add_attachment, name="memo_add_attachment"),
    path("memos/<int:pk>/attachments/<int:attachment_pk>/remove/",
         memos_views.memo_remove_attachment, name="memo_remove_attachment"),
    path("memos/attachments/<int:pk>/download/",
         memos_views.memo_attachment_download, name="memo_attachment_download"),

    # ---- Document downloads (PDF / Word) ----
    path("memos/<int:pk>/download.pdf",
         memos_views.memo_download_pdf, name="memo_download_pdf"),
    path("memos/<int:pk>/download.docx",
         memos_views.memo_download_docx, name="memo_download_docx"),
]