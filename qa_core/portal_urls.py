"""Root URLconf for the QA portal host (Master Plan §3, §13, §16).

`QAPortalHostMiddleware` makes this the URLconf for every request whose host
is `settings.QA_PORTAL_HOST`. It deliberately contains **no internal route**:
`/tasks/`, `/settings/`, `/qa/`, `/admin/` … answer 404 on the portal host.
`qa_core/tests/test_host_routing.py` lists both URL tables and proves they are
disjoint.
"""
from django.conf import settings
from django.urls import include, path, re_path

from accreditation import portal_views as application_views
from accreditation import programme_portal_views as programme_board_views
from accreditation import qa_portal_views as qa_views
from notifications import views as notification_views
from complaints import portal_views as complaint_views
from inspection import portal_views as inspection_views
from institutions import portal_views as institution_views
from institutions import programme_views
from payments import portal_views as payment_views

from . import portal_account_views as account_views  # Phase 9
from . import portal_public
from . import portal_views as views

portal_patterns = ([
    # Phase 6: visitors get the public landing page; signed-in users their dashboard.
    path("", portal_public.home, name="dashboard"),
    path("how-to/", portal_public.how_to, name="how_to"),
    path("guides/<slug:slug>/", portal_public.guide, name="guide"),
    # Phase 2: institution, documents, application (SOP-01), payments, timeline
    path("register/", institution_views.register, name="register"),
    path("institution/", institution_views.institution, name="institution"),
    path("institution/users/", institution_views.users, name="institution_users"),
    path("institution/lgas/", institution_views.lga_options, name="lga_options"),
    path("documents/", institution_views.documents, name="documents"),
    path("documents/summary/", institution_views.documents_summary, name="documents_summary"),
    path("documents/upload/<int:kind_id>/", institution_views.document_upload, name="document_upload"),
    path("timeline/", institution_views.timeline, name="timeline"),
    # Phase 8: the institution's own programmes (public information only)
    path("programmes/list/", programme_views.portal_programmes, name="programme_list"),
    path("programmes/<int:pk>/", programme_views.portal_programme, name="programme"),
    path("application/", application_views.application, name="application"),
    path("application/start/", application_views.application_start, name="application_start"),
    path("application/submit/", application_views.application_submit, name="application_submit"),
    path("application/programmes/", application_views.programmes, name="programmes"),
    path("application/programmes/<int:pk>/remove/", application_views.programme_remove, name="programme_remove"),
    path("application/programmes/<int:pk>/streams/", application_views.programme_streams, name="programme_streams"),
    path("application/programmes/estimate/", application_views.programme_estimate, name="programme_estimate"),
    # Phase 3: QA requests (SOP-02), the SSQ workspace and queries
    path("qa-requests/", qa_views.requests_list, name="qa_requests"),
    path("qa-requests/new/", qa_views.request_new, name="qa_request_new"),
    path("qa-requests/estimate/", qa_views.request_estimate, name="qa_request_estimate"),  # Phase 8
    path("qa-requests/<int:pk>/", qa_views.request_detail, name="qa_request"),
    path("qa-requests/<int:pk>/submit/", qa_views.request_submit, name="qa_request_submit"),
    path("qa-requests/<int:pk>/cancel/", qa_views.request_cancel, name="qa_request_cancel"),
    path("qa-requests/<int:pk>/add/", qa_views.request_add, name="qa_request_add"),
    path("qa-requests/<int:pk>/programmes/<int:row>/drop/", qa_views.request_drop, name="qa_request_drop"),
    path("qa-requests/<int:pk>/programmes/<int:row>/streams/", qa_views.request_streams,
         name="qa_request_streams"),
    path("qa-requests/<int:pk>/programmes/<int:row>/replace/", qa_views.request_replace, name="qa_request_replace"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/", qa_views.ssq_workspace, name="ssq"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/save/", qa_views.ssq_save, name="ssq_save"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/upload/<slug:field>/", qa_views.ssq_upload, name="ssq_upload"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/reuse/<slug:field>/", qa_views.ssq_reuse, name="ssq_reuse"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/evidence/<int:ev>/remove/", qa_views.ssq_evidence_remove,
         name="ssq_evidence_remove"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/department/", qa_views.ssq_department, name="ssq_department"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/recall/", qa_views.ssq_recall, name="ssq_recall"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/department-done/", qa_views.ssq_department_submit,
         name="ssq_department_submit"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/submit/", qa_views.ssq_submit, name="ssq_submit"),
    path("qa-requests/<int:pk>/ssq/<int:ssq_pk>/revise/", qa_views.ssq_revise, name="ssq_revise"),
    # Phase 8: programme status board (expiry countdown, re-accreditation) and outcome letters
    path("programmes/", programme_board_views.programmes, name="programme_board"),
    path("letters/", programme_board_views.letters, name="letters"),
    path("queries/", qa_views.queries, name="queries"),
    path("queries/<int:pk>/", qa_views.query_detail, name="query"),
    # Phase 5: resource persons (engagements, findings, service history, CV) and
    # the institution's read-only inspections list
    path("engagements/", inspection_views.engagements, name="rp_engagements"),
    path("service-history/", inspection_views.history, name="rp_history"),
    path("profile/", inspection_views.profile, name="rp_profile"),
    path("inspections/", inspection_views.institution_inspections, name="inspections"),
    path("payments/", payment_views.payments, name="payments"),
    path("payments/<int:pk>/", payment_views.payment, name="payment"),
    path("payments/<int:pk>/app-return/<str:token>/", payment_views.payment_app_return,
         name="payment_app_return"),  # Phase 9: back to the mobile app
    path("payments/<int:pk>/slip/", payment_views.payment_slip, name="payment_slip"),
    path("payments/<int:pk>/online/", payment_views.payment_online, name="payment_online"),
    path("payments/<int:pk>/proof/", payment_views.payment_proof, name="payment_proof"),
    path("payments/<int:pk>/receipt/", payment_views.payment_receipt, name="payment_receipt"),
    path("payments/webhooks/paystack/", payment_views.webhook_paystack, name="webhook_paystack"),
    path("payments/webhooks/remita/", payment_views.webhook_remita, name="webhook_remita"),
    path("payments/simulator/<str:provider>/<str:ref>/", payment_views.pay_simulator, name="pay_simulator"),
    path("verify/<str:token>/", payment_views.verify, name="verify"),
    path("download/<str:label>/<int:pk>/", views.portal_file, name="file"),
    path("impersonate/<str:token>/", views.impersonation_start, name="impersonation_start"),
    path("impersonation/ended/", views.impersonation_ended, name="impersonation_ended"),
    path("login/", views.PortalLoginView.as_view(), name="login"),
    path("logout/", views.portal_logout, name="logout"),
    path("password/forgot/", views.password_forgot, name="password_forgot"),
    path("password/forgot/sent/", views.password_forgot_done, name="password_forgot_done"),
    path("password/set/done/", views.password_set_done, name="password_set_done"),
    path("password/set/<uidb64>/<token>/", views.PortalSetPasswordView.as_view(), name="password_set"),
    path("password/change/", views.password_change, name="password_change"),
    path("account/", views.account, name="account"),
    # Phase 9: signed-in devices, notification settings, deletion requests, passkeys
    path("account/devices/<int:pk>/sign-out/", account_views.device_revoke, name="account_device_revoke"),
    path("account/devices/sign-out-others/", account_views.devices_revoke_others,
         name="account_devices_revoke_others"),
    path("account/notifications/", account_views.notification_settings, name="account_notifications"),
    path("account/delete/", account_views.deletion_request, name="account_deletion_request"),
    path("account/delete/withdraw/", account_views.deletion_withdraw, name="account_deletion_withdraw"),
    path("account/passkeys/register/begin/", account_views.passkey_register_begin,
         name="account_passkey_register_begin"),
    path("account/passkeys/register/finish/", account_views.passkey_register_finish,
         name="account_passkey_register_finish"),
    path("account/passkeys/<int:pk>/rename/", account_views.passkey_rename, name="account_passkey_rename"),
    path("account/passkeys/<int:pk>/remove/", account_views.passkey_remove, name="account_passkey_remove"),
    path("passkeys/sign-in/begin/", account_views.passkey_sign_in_begin, name="passkey_sign_in_begin"),
    path("passkeys/sign-in/finish/", account_views.passkey_sign_in_finish, name="passkey_sign_in_finish"),
    path("alerts/", views.notifications, name="notifications"),
    path("alerts/bulk/", notification_views.portal_bulk, name="notifications_bulk"),  # Phase 7: the bell's "View all"
    # Phase 7: complaints and the knowledge base
    path("complaints/", complaint_views.complaints, name="complaints"),
    path("complaints/new/", complaint_views.raise_complaint, name="complaint_new"),
    path("complaints/suggest/", complaint_views.suggest, name="complaint_suggest"),
    path("complaints/<int:pk>/", complaint_views.complaint_detail, name="complaint"),
    path("complaints/<int:pk>/reply/", complaint_views.complaint_reply, name="complaint_reply"),
    path("complaints/<int:pk>/act/<str:action>/", complaint_views.complaint_action, name="complaint_action"),
    path("complaints/<int:pk>/files/<int:file_pk>/", complaint_views.complaint_file, name="complaint_file"),
    path("help/", complaint_views.help_index, name="help"),
    path("help/<slug:slug>/", complaint_views.help_article, name="help_article"),
    path("impersonation/stop/", views.impersonation_stop, name="impersonation_stop"),
], "qa_portal")

urlpatterns = [
    path("api/v1/", include("api.urls_portal", namespace="api")),  # Phase 9: mobile API (portal door)
    path("", include(portal_patterns)),
]

# Static/media in development (same rule as nbte_ict/urls.py; nginx serves
# them in production).
if settings.DEBUG or getattr(settings, "SERVE_FILES_LOCALLY", False):
    from django.contrib.staticfiles.views import serve as serve_static

    urlpatterns += [
        re_path(r"^%s(?P<path>.*)$" % settings.STATIC_URL.lstrip("/"), serve_static, {"insecure": True}),
    ]

handler404 = "qa_core.portal_views.page_not_found"
handler403 = "qa_core.portal_views.permission_denied"
handler500 = "qa_core.portal_views.server_error"
