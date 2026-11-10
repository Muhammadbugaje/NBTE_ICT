from django.urls import path

from . import bulk_views, chat_views, group_views, memos_views, views

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
    path("bulk/", bulk_views.bulk_threads, name="bulk_threads"),
    path("<int:pk>/unarchive/", views.unarchive, name="unarchive"),

    # ---- Phase 9: chat upgrade (logic in chat_services / voice) ----
    path("inbox/updates/", chat_views.inbox_updates, name="inbox_updates"),
    path("starred/", chat_views.starred, name="starred"),
    path("<int:pk>/updates/", chat_views.thread_updates, name="thread_updates"),
    path("<int:pk>/typing/", chat_views.typing, name="typing"),
    path("<int:pk>/pin/", chat_views.pin, name="pin"),
    path("<int:pk>/mute/", chat_views.mute, name="mute"),
    path("<int:pk>/poll/", chat_views.poll_create, name="poll_create"),
    path("<int:pk>/poll/<int:poll_pk>/vote/", chat_views.poll_vote, name="poll_vote"),
    path("<int:pk>/voice/", chat_views.voice_upload, name="voice_upload"),
    path("<int:pk>/message/<int:message_pk>/",
         chat_views.message_fragment, name="message_fragment"),
    path("<int:pk>/message/<int:message_pk>/react/", chat_views.react, name="react"),
    path("<int:pk>/message/<int:message_pk>/star/", chat_views.star, name="star"),
    path("<int:pk>/message/<int:message_pk>/forward/", chat_views.forward, name="forward"),
    path("<int:pk>/message/<int:message_pk>/info/", chat_views.message_info, name="message_info"),
    path("<int:pk>/message/<int:message_pk>/task/", chat_views.make_task, name="make_task"),
    path("<int:pk>/message/<int:message_pk>/memo/", chat_views.make_memo, name="make_memo"),
    # ---- v2.1 Phase E: groups, invite links, in-chat pins, reminders ----
    path("<int:pk>/info/", group_views.group_info, name="group_info"),
    path("<int:pk>/info/edit/", group_views.group_edit, name="group_edit"),
    path("<int:pk>/settings/", group_views.group_settings, name="group_settings"),
    path("<int:pk>/photo/", group_views.group_photo, name="group_photo"),
    path("<int:pk>/members/candidates/", group_views.member_candidates, name="member_candidates"),
    path("<int:pk>/members/add/", group_views.members_add, name="members_add"),
    path("<int:pk>/members/<int:user_pk>/remove/", group_views.member_remove, name="member_remove"),
    path("<int:pk>/members/<int:user_pk>/role/", group_views.member_role, name="member_role"),
    path("<int:pk>/leave/", group_views.leave, name="leave"),
    path("<int:pk>/invites/", group_views.invite_create, name="invite_create"),
    path("<int:pk>/invites/<int:invite_pk>/revoke/", group_views.invite_revoke, name="invite_revoke"),
    path("join/<str:token>/", group_views.join, name="join"),
    path("<int:pk>/message/<int:message_pk>/pin/", group_views.pin_message, name="pin_message"),
    path("<int:pk>/message/<int:message_pk>/remind/", group_views.remind, name="remind"),
    path("voice/<int:voice_pk>/audio/", chat_views.voice_audio, name="voice_audio"),
    path("voice/<int:voice_pk>/played/", chat_views.voice_played, name="voice_played"),
    path("voice/<int:voice_pk>/transcript/", chat_views.voice_transcript,
         name="voice_transcript"),

    # ---- Memos ----
    path("memos/", memos_views.memo_list, name="memo_list"),
    path("memos/bulk/", bulk_views.bulk_memos, name="memo_bulk"),
    path("memos/new/", memos_views.memo_create, name="memo_create"),
    path("memos/institution-count/", memos_views.memo_institution_count, name="memo_institution_count"),
    path("memos/staff-count/", memos_views.memo_staff_count, name="memo_staff_count"),
    path("memos/<int:pk>/", memos_views.memo_detail, name="memo_detail"),
    path("memos/<int:pk>/edit/", memos_views.memo_update, name="memo_update"),

    # Workflow actions
    path("memos/<int:pk>/publish/",
         memos_views.memo_publish, name="memo_publish"),
    path("memos/<int:pk>/recheck/",
         memos_views.memo_recheck, name="memo_recheck"),
    path("memos/<int:pk>/approve/",
         memos_views.memo_approve, name="memo_approve"),
    path("memos/<int:pk>/return/",
         memos_views.memo_return, name="memo_return"),
    path("memos/<int:pk>/reject/",
         memos_views.memo_reject, name="memo_reject"),
    path("memos/<int:pk>/resubmit/",
         memos_views.memo_resubmit, name="memo_resubmit"),
    path("memos/<int:pk>/cancel/",
         memos_views.memo_cancel, name="memo_cancel"),

    # Recipient actions
    path("memos/<int:pk>/acknowledge/",
         memos_views.memo_acknowledge, name="memo_acknowledge"),
    path("memos/<int:pk>/archive/",
         memos_views.memo_archive, name="memo_archive"),
    path("memos/<int:pk>/unarchive/",
         memos_views.memo_unarchive, name="memo_unarchive"),
    path("memos/<int:pk>/delete/",
         memos_views.memo_delete, name="memo_delete"),

    # Attachments
    path("memos/<int:pk>/attach/",
         memos_views.memo_add_attachment, name="memo_add_attachment"),
    path("memos/<int:pk>/attachments/<int:attachment_pk>/remove/",
         memos_views.memo_remove_attachment, name="memo_remove_attachment"),
    path("memos/attachments/<int:pk>/download/",
         memos_views.memo_attachment_download, name="memo_attachment_download"),

    # Document downloads (PDF / Word)
    path("memos/<int:pk>/download.pdf",
         memos_views.memo_download_pdf, name="memo_download_pdf"),
    path("memos/<int:pk>/download.docx",
         memos_views.memo_download_docx, name="memo_download_docx"),
]