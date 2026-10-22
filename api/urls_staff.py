"""api/v1/ on the internal host (staff door). Included from nbte_ict/urls.py
with namespace "api". Staff-only feature endpoints go here."""
from django.conf import settings
from django.urls import path

from .urls_common import common_patterns
from .views import announcements as announcements_views
from .views import insights as insights_views
from .views import chats, me, memos
from .views import qa_staff as qa

app_name = "api"

urlpatterns = common_patterns(settings.ROOT_URLCONF) + [
    path("me/staff-id/", me.StaffIdView.as_view(), name="me_staff_id"),
    # --- announcements ---
    path("announcements/", announcements_views.AnnouncementListView.as_view(), name="announcements"),
    path("announcements/<int:pk>/", announcements_views.AnnouncementDetailView.as_view(), name="announcement"),
    path("announcements/<int:pk>/publish/", announcements_views.AnnouncementPublishView.as_view(),
         name="announcement_publish"),
    path("announcements/<int:pk>/acknowledge/", announcements_views.AnnouncementAcknowledgeView.as_view(),
         name="announcement_acknowledge"),
    path("announcements/<int:pk>/vote/", announcements_views.AnnouncementVoteView.as_view(),
         name="announcement_vote"),
    path("announcements/<int:pk>/report/", announcements_views.AnnouncementReportView.as_view(),
         name="announcement_report"),
    path("announcements/<int:pk>/attachments/<int:aid>/", announcements_views.AnnouncementAttachmentView.as_view(),
         name="announcement_attachment"),
    # --- insights ---
    path("insights/", insights_views.InsightsView.as_view(), name="insights"),
    # --- qa_staff ---
    path("qa/summary/", qa.SummaryView.as_view(), name="qa_summary"),
    path("qa/cases/", qa.CaseListView.as_view(), name="qa_cases"),
    path("qa/cases/my-stages/", qa.MyStagesView.as_view(), name="qa_cases_my_stages"),
    path("qa/cases/<int:pk>/", qa.CaseDetailView.as_view(), name="qa_case"),
    path("qa/cases/<int:pk>/act/", qa.CaseActView.as_view(), name="qa_case_act"),
    path("qa/cases/<int:pk>/documents/", qa.CaseAttachView.as_view(), name="qa_case_attach"),
    path("qa/files/<str:kind>/<int:pk>/", qa.FileDownloadView.as_view(), name="qa_file"),
    path("qa/confirmations/", qa.ConfirmationListView.as_view(), name="qa_confirmations"),
    path("qa/confirmations/<int:pk>/", qa.ConfirmationDetailView.as_view(), name="qa_confirmation"),
    path("qa/confirmations/<int:pk>/approve/", qa.ConfirmationApproveView.as_view(), name="qa_confirmation_approve"),
    path("qa/confirmations/<int:pk>/reject/", qa.ConfirmationRejectView.as_view(), name="qa_confirmation_reject"),
    path("qa/confirmations/<int:pk>/withdraw/", qa.ConfirmationWithdrawView.as_view(),
         name="qa_confirmation_withdraw"),
    path("qa/finance/", qa.FinanceListView.as_view(), name="qa_finance"),
    path("qa/finance/<int:pk>/", qa.FinanceDetailView.as_view(), name="qa_finance_item"),
    path("qa/finance/<int:pk>/first-pass/", qa.FinanceFirstPassView.as_view(), name="qa_finance_first_pass"),
    path("qa/finance/<int:pk>/second-check/", qa.FinanceSecondCheckView.as_view(), name="qa_finance_second_check"),
    path("qa/finance/<int:pk>/decide/", qa.FinanceDecideView.as_view(), name="qa_finance_decide"),
    path("qa/institutions/", qa.InstitutionListView.as_view(), name="qa_institutions"),
    path("qa/institutions/<str:code>/", qa.InstitutionDetailView.as_view(), name="qa_institution"),
    path("qa/applications/clearance/", qa.ClearanceListView.as_view(), name="qa_clearances"),
    path("qa/applications/clearance/<int:pk>/", qa.ClearanceDetailView.as_view(), name="qa_clearance"),
    path("qa/applications/clearance/<int:pk>/decide/", qa.ClearanceDecideView.as_view(),
         name="qa_clearance_decide"),
    path("qa/requests/", qa.RequestListView.as_view(), name="qa_requests"),
    path("qa/requests/ssq/", qa.SSQListView.as_view(), name="qa_ssqs"),
    path("qa/requests/ssq/<int:pk>/", qa.SSQDetailView.as_view(), name="qa_ssq"),
    path("qa/requests/ssq/<int:pk>/decide/", qa.SSQDecideView.as_view(), name="qa_ssq_decide"),
    path("qa/requests/<int:pk>/", qa.RequestDetailView.as_view(), name="qa_request"),
    path("qa/requests/<int:pk>/clearance/", qa.RequestClearView.as_view(), name="qa_request_clearance"),
    path("qa/complaints/", qa.ComplaintListView.as_view(), name="qa_complaints"),
    path("qa/complaints/<int:pk>/", qa.ComplaintDetailView.as_view(), name="qa_complaint"),
    path("qa/complaints/<int:pk>/messages/", qa.ComplaintMessageView.as_view(), name="qa_complaint_message"),
    path("qa/complaints/<int:pk>/act/", qa.ComplaintActView.as_view(), name="qa_complaint_act"),
    path("qa/complaints/<int:pk>/escalate/", qa.ComplaintEscalateView.as_view(), name="qa_complaint_escalate"),
    path("qa/inspections/", qa.InspectionListView.as_view(), name="qa_inspections"),
    path("qa/inspections/<int:pk>/", qa.InspectionDetailView.as_view(), name="qa_inspection"),
    # --- end qa_staff ---
]

# --- chats ---
urlpatterns += [
    path("chats/threads/", chats.ThreadListView.as_view(), name="chats_threads"),
    path("chats/contacts/", chats.ContactsView.as_view(), name="chats_contacts"),
    path("chats/threads/<int:pk>/", chats.ThreadDetailView.as_view(), name="chats_thread"),
    path("chats/threads/<int:pk>/messages/", chats.MessagesView.as_view(), name="chats_messages"),
    path("chats/threads/<int:pk>/updates/", chats.UpdatesView.as_view(), name="chats_updates"),
    path("chats/threads/<int:pk>/typing/", chats.TypingView.as_view(), name="chats_typing"),
    path("chats/threads/<int:pk>/read/", chats.ReadView.as_view(), name="chats_read"),
    path("chats/threads/<int:pk>/pin/", chats.PinView.as_view(), name="chats_pin"),
    path("chats/threads/<int:pk>/mute/", chats.MuteView.as_view(), name="chats_mute"),
    path("chats/threads/<int:pk>/archive/", chats.ArchiveView.as_view(), name="chats_archive"),
    path("chats/threads/<int:pk>/unarchive/", chats.UnarchiveView.as_view(), name="chats_unarchive"),
    path("chats/threads/<int:pk>/polls/", chats.PollCreateView.as_view(), name="chats_poll_create"),
    path("chats/threads/<int:pk>/voice/", chats.VoiceUploadView.as_view(), name="chats_voice_upload"),
    path("chats/messages/<int:pk>/", chats.MessageDetailView.as_view(), name="chats_message"),
    path("chats/messages/<int:pk>/react/", chats.ReactView.as_view(), name="chats_react"),
    path("chats/messages/<int:pk>/star/", chats.StarView.as_view(), name="chats_star"),
    path("chats/messages/<int:pk>/forward/", chats.ForwardView.as_view(), name="chats_forward"),
    path("chats/messages/<int:pk>/info/", chats.InfoView.as_view(), name="chats_info"),
    path("chats/messages/<int:pk>/task/", chats.TaskPrefillView.as_view(), name="chats_task_prefill"),
    path("chats/messages/<int:pk>/memo/", chats.MemoPrefillView.as_view(), name="chats_memo_prefill"),
    path("chats/starred/", chats.StarredView.as_view(), name="chats_starred"),
    path("chats/search/", chats.SearchView.as_view(), name="chats_search"),
    path("chats/polls/<int:pk>/vote/", chats.PollVoteView.as_view(), name="chats_poll_vote"),
    path("chats/voice/<int:pk>/audio/", chats.VoiceAudioView.as_view(), name="chats_voice_audio"),
    path("chats/voice/<int:pk>/played/", chats.VoicePlayedView.as_view(), name="chats_voice_played"),
    path("chats/voice/<int:pk>/transcript/", chats.VoiceTranscriptView.as_view(), name="chats_voice_transcript"),
    path("chats/attachments/<int:pk>/", chats.AttachmentDownloadView.as_view(), name="chats_attachment"),
]

# --- memos ---
urlpatterns += [
    path("memos/", memos.MemoListView.as_view(), name="memos"),
    path("memos/compose-options/", memos.ComposeOptionsView.as_view(), name="memos_compose_options"),
    path("memos/people/", memos.PeopleView.as_view(), name="memos_people"),
    path("memos/attachments/<int:pk>/", memos.MemoAttachmentDownloadView.as_view(), name="memos_attachment"),
    path("memos/<int:pk>/", memos.MemoDetailView.as_view(), name="memos_detail"),
    path("memos/<int:pk>/pdf/", memos.MemoPdfView.as_view(), name="memos_pdf"),
    path("memos/<int:pk>/docx/", memos.MemoDocxView.as_view(), name="memos_docx"),
    path("memos/<int:pk>/approve/", memos.MemoApproveView.as_view(), name="memos_approve"),
    path("memos/<int:pk>/return/", memos.MemoReturnView.as_view(), name="memos_return"),
    path("memos/<int:pk>/reject/", memos.MemoRejectView.as_view(), name="memos_reject"),
    path("memos/<int:pk>/resubmit/", memos.MemoResubmitView.as_view(), name="memos_resubmit"),
    path("memos/<int:pk>/publish/", memos.MemoPublishView.as_view(), name="memos_publish"),
    path("memos/<int:pk>/acknowledge/", memos.MemoAcknowledgeView.as_view(), name="memos_acknowledge"),
    path("memos/<int:pk>/archive/", memos.MemoArchiveView.as_view(), name="memos_archive"),
    path("memos/<int:pk>/unarchive/", memos.MemoUnarchiveView.as_view(), name="memos_unarchive"),
    path("memos/<int:pk>/cancel/", memos.MemoCancelView.as_view(), name="memos_cancel"),
]

# --- work (tasks, tickets, requests, leave, attendance, assets, staff, workspace) ---
from .views import assets as work_assets  # noqa: E402
from .views import attendance as work_attendance  # noqa: E402
from .views import leave as work_leave  # noqa: E402
from .views import requests as work_requests  # noqa: E402
from .views import staff as work_staff  # noqa: E402
from .views import tasks as work_tasks  # noqa: E402
from .views import tickets as work_tickets  # noqa: E402
from .views import workspace as work_workspace  # noqa: E402

urlpatterns += [
    # Tasks
    path("tasks/", work_tasks.TaskListView.as_view(), name="tasks"),
    path("tasks/categories/", work_tasks.TaskCategoriesView.as_view(), name="task_categories"),
    path("tasks/people/", work_tasks.TaskPeopleView.as_view(), name="task_people"),
    path("tasks/<int:pk>/", work_tasks.TaskDetailView.as_view(), name="task"),
    path("tasks/<int:pk>/assignees/", work_tasks.TaskAssigneesView.as_view(), name="task_assignees"),
    path("tasks/<int:pk>/assignees/<int:user_id>/", work_tasks.TaskAssigneeRemoveView.as_view(),
         name="task_assignee"),
    path("tasks/<int:pk>/comments/", work_tasks.TaskCommentsView.as_view(), name="task_comments"),
    path("tasks/<int:pk>/comments/<int:comment_id>/", work_tasks.TaskCommentDeleteView.as_view(),
         name="task_comment"),
    path("tasks/<int:pk>/attachments/", work_tasks.TaskAttachmentsView.as_view(), name="task_attachments"),
    path("tasks/<int:pk>/attachments/<int:attachment_id>/", work_tasks.TaskAttachmentView.as_view(),
         name="task_attachment"),
    *[path(f"tasks/<int:pk>/{name}/", view.as_view(), name=f"task_{name.replace('/', '_').replace('-', '_')}")
      for name, view in work_tasks.ACTIONS.items()],
    # Tickets
    path("tickets/", work_tickets.TicketListView.as_view(), name="tickets"),
    path("tickets/categories/", work_tickets.TicketCategoriesView.as_view(), name="ticket_categories"),
    path("tickets/<int:pk>/", work_tickets.TicketDetailView.as_view(), name="ticket"),
    path("tickets/<int:pk>/assignees/", work_tickets.TicketAssigneesView.as_view(), name="ticket_assignees"),
    path("tickets/<int:pk>/comments/", work_tickets.TicketCommentsView.as_view(), name="ticket_comments"),
    path("tickets/<int:pk>/attachments/", work_tickets.TicketAttachmentsView.as_view(), name="ticket_attachments"),
    path("tickets/<int:pk>/attachments/<int:attachment_id>/", work_tickets.TicketAttachmentView.as_view(),
         name="ticket_attachment"),
    *[path(f"tickets/<int:pk>/{name}/", view.as_view(), name=f"ticket_{name}")
      for name, view in work_tickets.ACTIONS.items()],
    # Requests (workflow)
    path("requests/", work_requests.RequestListView.as_view(), name="requests"),
    path("requests/types/", work_requests.RequestTypesView.as_view(), name="request_types"),
    path("requests/types/<slug:code>/", work_requests.RequestTypeDetailView.as_view(), name="request_type"),
    path("requests/<int:pk>/", work_requests.RequestDetailView.as_view(), name="request"),
    path("requests/<int:pk>/assign/", work_requests.RequestAssignView.as_view(), name="request_assign"),
    path("requests/<int:pk>/resubmit/", work_requests.RequestResubmitView.as_view(), name="request_resubmit"),
    path("requests/<int:pk>/comments/", work_requests.RequestCommentsView.as_view(), name="request_comments"),
    path("requests/<int:pk>/attachments/", work_requests.RequestAttachmentsView.as_view(),
         name="request_attachments"),
    path("requests/<int:pk>/attachments/<int:attachment_id>/", work_requests.RequestAttachmentView.as_view(),
         name="request_attachment"),
    path("requests/<int:pk>/record.pdf", work_requests.RequestRecordPdfView.as_view(), name="request_record_pdf"),
    *[path(f"requests/<int:pk>/{name}/", view.as_view(), name=f"request_{name.replace('-', '_')}")
      for name, view in work_requests.ACTIONS.items()],
    # Leave
    path("leave/", work_leave.LeaveOverviewView.as_view(), name="leave"),
    path("leave/applications/", work_leave.LeaveApplicationsView.as_view(), name="leave_applications"),
    path("leave/applications/<int:pk>/cancel/", work_leave.LeaveCancelView.as_view(), name="leave_cancel"),
    path("leave/preview/", work_leave.LeavePreviewView.as_view(), name="leave_preview"),
    path("leave/approvals/", work_leave.LeaveApprovalsView.as_view(), name="leave_approvals"),
    path("leave/holidays/", work_leave.LeaveHolidaysView.as_view(), name="leave_holidays"),
    # Attendance
    path("attendance/today/", work_attendance.TodayView.as_view(), name="attendance_today"),
    path("attendance/check-in/", work_attendance.CheckInView.as_view(), name="attendance_check_in"),
    path("attendance/check-out/", work_attendance.CheckOutView.as_view(), name="attendance_check_out"),
    path("attendance/history/", work_attendance.HistoryView.as_view(), name="attendance_history"),
    path("attendance/summary/", work_attendance.SummaryView.as_view(), name="attendance_summary"),
    # Assets
    path("assets/mine/", work_assets.MyAssetsView.as_view(), name="assets_mine"),
    path("assets/by-code/<path:code>/", work_assets.AssetByCodeView.as_view(), name="asset_by_code"),
    path("assets/<str:tag>/", work_assets.AssetDetailView.as_view(), name="asset"),
    path("assets/<str:tag>/issues/", work_assets.AssetIssueView.as_view(), name="asset_issues"),
    path("assets/<str:tag>/attachments/<int:attachment_id>/", work_assets.AssetAttachmentView.as_view(),
         name="asset_attachment"),
    # Staff directory
    path("staff/", work_staff.DirectoryView.as_view(), name="staff_directory"),
    path("staff/departments/", work_staff.DepartmentsView.as_view(), name="staff_departments"),
    path("staff/<int:pk>/", work_staff.ProfileView.as_view(), name="staff_profile"),
    # My workspace
    path("workspace/", work_workspace.WorkspaceView.as_view(), name="workspace"),
]
# --- end work ---
