"""api/v1/ on the QA portal host (portal door). Included from
qa_core/portal_urls.py with namespace "api". Institution / resource-person
feature endpoints go here."""
from django.conf import settings
from django.urls import path

from .urls_common import common_patterns

app_name = "api"

urlpatterns = common_patterns(getattr(settings, "QA_PORTAL_URLCONF", "qa_core.portal_urls"))

# --- portal (institutions and resource persons) ---
from .views import portal_complaints as pc  # noqa: E402
from .views import portal_institution as pi  # noqa: E402
from .views import portal_memos as pm  # noqa: E402
from .views import portal_payments as pp  # noqa: E402
from .views import portal_qa as pq  # noqa: E402
from .views import portal_rp as pr  # noqa: E402

urlpatterns += [
    # v2.1 Phase E (M4): memos from NBTE
    path("memos/", pm.PortalMemosView.as_view(), name="portal_memos"),
    path("memos/<int:pk>/", pm.PortalMemoDetailView.as_view(), name="portal_memo"),
    path("memos/<int:pk>/acknowledge/", pm.PortalMemoAcknowledgeView.as_view(), name="portal_memo_acknowledge"),
    path("memos/<int:pk>/pdf/", pm.PortalMemoPdfView.as_view(), name="portal_memo_pdf"),
    # Institution, users, timeline, inspections
    path("institution/", pi.InstitutionView.as_view(), name="portal_institution"),
    path("institution/options/", pi.InstitutionOptionsView.as_view(), name="portal_institution_options"),
    path("institution/users/", pi.InstitutionUsersView.as_view(), name="portal_institution_users"),
    path("timeline/", pi.TimelineView.as_view(), name="portal_timeline"),
    path("inspections/", pr.InspectionsView.as_view(), name="portal_inspections"),
    # Documents
    path("documents/", pi.DocumentsView.as_view(), name="portal_documents"),
    path("documents/summary/", pi.DocumentsSummaryView.as_view(), name="portal_documents_summary"),
    path("documents/history/", pi.DocumentHistoryView.as_view(), name="portal_documents_history"),
    path("documents/upload/<int:kind_id>/", pi.DocumentUploadView.as_view(), name="portal_document_upload"),
    path("documents/<int:pk>/download/", pi.DocumentDownloadView.as_view(), name="portal_document_download"),
    # Protected files (download_url of evidence, responses, slips, advices, proofs, letters)
    path("files/<str:label>/<int:pk>/", pi.PortalFileView.as_view(), name="portal_file"),
    # Application (SOP-01)
    path("application/", pi.ApplicationView.as_view(), name="portal_application"),
    path("application/start/", pi.ApplicationStartView.as_view(), name="portal_application_start"),
    path("application/submit/", pi.ApplicationSubmitView.as_view(), name="portal_application_submit"),
    path("application/programmes/", pi.ApplicationProgrammesView.as_view(), name="portal_application_programmes"),
    path("application/programmes/submit/", pi.ApplicationProgrammesSubmitView.as_view(),
         name="portal_application_programmes_submit"),
    path("application/programmes/<int:pk>/", pi.ApplicationProgrammeView.as_view(),
         name="portal_application_programme"),
    # QA requests (SOP-02) and the SSQ
    path("qa-requests/", pq.QARequestsView.as_view(), name="portal_qa_requests"),
    path("qa-requests/new/", pq.QARequestNewView.as_view(), name="portal_qa_request_new"),
    path("qa-requests/estimate/", pq.QARequestEstimateView.as_view(), name="portal_qa_request_estimate"),
    path("qa-requests/<int:pk>/", pq.QARequestDetailView.as_view(), name="portal_qa_request"),
    path("qa-requests/<int:pk>/submit/", pq.QARequestSubmitView.as_view(), name="portal_qa_request_submit"),
    path("qa-requests/<int:pk>/cancel/", pq.QARequestCancelView.as_view(), name="portal_qa_request_cancel"),
    path("qa-requests/<int:pk>/programmes/", pq.QARequestAddView.as_view(), name="portal_qa_request_add"),
    path("qa-requests/<int:pk>/programmes/<int:row>/", pq.QARequestRowStreamsView.as_view(),
         name="portal_qa_request_streams"),
    path("qa-requests/<int:pk>/programmes/<int:row>/drop/", pq.QARequestDropView.as_view(),
         name="portal_qa_request_drop"),
    path("qa-requests/<int:pk>/programmes/<int:row>/replace/", pq.QARequestReplaceView.as_view(),
         name="portal_qa_request_replace"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/", pq.SSQView.as_view(), name="portal_ssq"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/evidence/<int:ev>/", pq.SSQEvidenceRemoveView.as_view(),
         name="portal_ssq_evidence_remove"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/evidence/<slug:field>/", pq.SSQEvidenceUploadView.as_view(),
         name="portal_ssq_evidence_upload"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/evidence/<slug:field>/reuse/", pq.SSQEvidenceReuseView.as_view(),
         name="portal_ssq_evidence_reuse"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/submit/", pq.SSQSubmitView.as_view(), name="portal_ssq_submit"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/revise/", pq.SSQReviseView.as_view(), name="portal_ssq_revise"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/recall/", pq.SSQRecallView.as_view(), name="portal_ssq_recall"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/department/", pq.SSQDepartmentView.as_view(),
         name="portal_ssq_department"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/department-done/", pq.SSQDepartmentDoneView.as_view(),
         name="portal_ssq_department_done"),
    # Queries
    path("queries/", pq.QueriesView.as_view(), name="portal_queries"),
    path("queries/<int:pk>/", pq.QueryDetailView.as_view(), name="portal_query"),
    path("queries/<int:pk>/reply/", pq.QueryReplyView.as_view(), name="portal_query_reply"),
    # Programmes and outcome letters
    path("programmes/", pq.ProgrammesView.as_view(), name="portal_programmes"),
    path("programmes/board/", pq.ProgrammeBoardView.as_view(), name="portal_programme_board"),
    path("programmes/<int:pk>/", pq.ProgrammeDetailView.as_view(), name="portal_programme"),
    path("letters/", pq.LettersView.as_view(), name="portal_letters"),
    path("letters/<int:pk>/download/", pq.LetterDownloadView.as_view(), name="portal_letter_download"),
    # Payments
    path("payments/", pp.PaymentsView.as_view(), name="portal_payments"),
    path("payments/summary/", pp.PaymentsSummaryView.as_view(), name="portal_payments_summary"),
    path("payments/<int:pk>/", pp.PaymentDetailView.as_view(), name="portal_payment"),
    path("payments/<int:pk>/slip/", pp.PaymentSlipView.as_view(), name="portal_payment_slip"),
    path("payments/<int:pk>/method/", pp.PaymentMethodView.as_view(), name="portal_payment_method"),
    path("payments/<int:pk>/online/", pp.PaymentOnlineView.as_view(), name="portal_payment_online"),
    path("payments/<int:pk>/verify/", pp.PaymentVerifyView.as_view(), name="portal_payment_verify"),
    path("payments/<int:pk>/proof/", pp.PaymentProofView.as_view(), name="portal_payment_proof"),
    path("payments/<int:pk>/receipt/", pp.PaymentReceiptView.as_view(), name="portal_payment_receipt"),
    # Complaints and the knowledge base
    path("complaints/", pc.ComplaintsView.as_view(), name="portal_complaints"),
    path("complaints/new/", pc.ComplaintNewView.as_view(), name="portal_complaint_new"),
    path("complaints/suggest/", pc.ComplaintSuggestView.as_view(), name="portal_complaint_suggest"),
    path("complaints/<int:pk>/", pc.ComplaintDetailView.as_view(), name="portal_complaint"),
    path("complaints/<int:pk>/reply/", pc.ComplaintReplyView.as_view(), name="portal_complaint_reply"),
    path("complaints/<int:pk>/actions/<str:action>/", pc.ComplaintActionView.as_view(),
         name="portal_complaint_action"),
    path("complaints/<int:pk>/files/<int:file_pk>/", pc.ComplaintFileView.as_view(), name="portal_complaint_file"),
    path("help/", pc.HelpView.as_view(), name="portal_help"),
    path("help/<slug:slug>/", pc.HelpArticleView.as_view(), name="portal_help_article"),
    path("help/<slug:slug>/vote/", pc.HelpArticleVoteView.as_view(), name="portal_help_vote"),
    # Resource persons
    path("engagements/", pr.EngagementsView.as_view(), name="portal_engagements"),
    path("engagements/<int:pk>/", pr.EngagementDetailView.as_view(), name="portal_engagement"),
    path("engagements/<int:pk>/findings/", pr.EngagementFindingsView.as_view(), name="portal_engagement_findings"),
    path("service-history/", pr.ServiceHistoryView.as_view(), name="portal_service_history"),
    path("service-history/summary/", pr.ServiceHistorySummaryView.as_view(), name="portal_service_history_summary"),
    path("profile/", pr.RPProfileView.as_view(), name="portal_rp_profile"),
    path("profile/files/", pr.RPProfileFilesView.as_view(), name="portal_rp_profile_files"),
]
# --- end portal ---
