"""Task URLs."""
from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    # ---- Lists ----
    path("",                      views.task_list,         name="list"),
    path("my/",                   views.my_tasks,          name="my_tasks"),
    path("assigned/",             views.assigned_to_me,    name="assigned_to_me"),
    path("review/",               views.review_queue,      name="review_queue"),
    path("divisions/",            views.division_queue,    name="division_queue"),
    path("performance/",          views.performance,       name="performance"),

    # ---- Create / edit ----
    path("new/",                  views.task_create,       name="create"),
    path("new/<slug:category_code>/", views.task_create,   name="create_for_category"),
    path("new/from-ticket/<int:ticket_id>/", views.task_create_from_ticket, name="create_from_ticket"),
    path("<int:pk>/edit/",        views.task_edit,         name="edit"),
    path("<int:pk>/clone/",       views.task_clone,        name="clone"),

    # ---- Detail / timeline ----
    path("<int:pk>/",             views.task_detail,       name="detail"),
    path("<int:pk>/timeline/",    views.task_timeline_partial, name="timeline_partial"),
    path("<int:pk>/stream/",      views.task_stream,       name="stream"),

    # ---- Workflow transitions ----
    path("<int:pk>/open/",        views.task_open,         name="open"),
    path("<int:pk>/accept/",      views.task_accept,       name="accept"),
    path("<int:pk>/start/",       views.task_start,        name="start"),
    path("<int:pk>/accept-start/", views.task_accept_start, name="accept_start"),
    path("<int:pk>/parts/<int:user_id>/confirm/", views.task_confirm_part, name="confirm_part"),
    path("<int:pk>/parts/<int:user_id>/return/",  views.task_return_part,  name="return_part"),
    path("<int:pk>/parts/confirm-all/", views.task_confirm_all, name="confirm_all"),
    path("<int:pk>/reviewer/",    views.task_set_reviewer, name="set_reviewer"),
    path("<int:pk>/report.pdf",   views.task_report_pdf,   name="report_pdf"),

    # ---- Division routing (Phase C) ----
    path("<int:pk>/division/route/",    views.task_route_division,    name="route_division"),
    path("<int:pk>/division/accept/",   views.task_division_accept,   name="division_accept"),
    path("<int:pk>/division/decline/",  views.task_division_decline,  name="division_decline"),
    path("<int:pk>/division/complete/", views.task_division_complete, name="division_complete"),
    path("<int:pk>/ask/",         views.task_ask,          name="ask"),
    path("<int:pk>/resume/",      views.task_resume,       name="resume"),
    path("<int:pk>/return/",      views.task_return,       name="return"),
    path("<int:pk>/resend/",      views.task_resend,       name="resend"),
    path("<int:pk>/mark-done/",   views.task_mark_done,    name="mark_done"),
    path("<int:pk>/review/",      views.task_review,       name="review"),
    path("<int:pk>/cancel/",      views.task_cancel,       name="cancel"),
    path("<int:pk>/delete/",      views.task_delete,       name="delete"),
    path("<int:pk>/change-state/", views.task_change_state, name="change_state"),

    # ---- Assignment ----
    path("<int:pk>/assign/",      views.task_assign,       name="assign"),
    path("<int:pk>/sync-assignees/", views.task_sync_assignees, name="sync_assignees"),
    path("<int:pk>/assign-role/", views.task_assign_to_role, name="assign_to_role"),
    path("<int:pk>/unassign/<int:user_id>/", views.task_unassign, name="unassign"),
    path("<int:pk>/reassign/<int:user_id>/", views.task_reassign, name="reassign"),

    # ---- Subtasks ----
    path("<int:pk>/split/",       views.task_split,        name="split"),

    # ---- Dependencies ----
    path("<int:pk>/dependencies/add/", views.task_add_dependency, name="add_dependency"),
    path("<int:pk>/dependencies/<int:dependency_id>/remove/",
         views.task_remove_dependency, name="remove_dependency"),

    # ---- Comments ----
    path("<int:pk>/comment/",     views.task_comment,      name="comment"),
    path("<int:pk>/comments/<int:comment_id>/pin/",
         views.task_pin_comment, name="pin_comment"),
    path("<int:pk>/comments/<int:comment_id>/unpin/",
         views.task_unpin_comment, name="unpin_comment"),
    path("<int:pk>/comments/<int:comment_id>/delete/",
         views.task_delete_comment, name="delete_comment"),

    # ---- Attachments ----
    path("<int:pk>/attach/",      views.task_attach,       name="attach"),
    path("<int:pk>/attachments/<int:attachment_id>/delete/",
         views.task_delete_attachment, name="delete_attachment"),

    # ---- Time tracking ----
    path("<int:pk>/timer/start/", views.task_timer_start,  name="timer_start"),
    path("<int:pk>/timer/stop/",  views.task_timer_stop,   name="timer_stop"),
    path("<int:pk>/log-time/",    views.task_log_time,     name="log_time"),

    # ---- Bulk ----
    path("bulk/",                 views.task_bulk_action,  name="bulk_action"),
    path("drafts/discard/",       views.task_discard_drafts, name="discard_drafts"),

    path("<int:pk>/reopen/",      views.task_reopen,        name="reopen"),

    # ---- JSON ----
    path("api/eligible-assignees/", views.eligible_assignees, name="eligible_assignees"),
]