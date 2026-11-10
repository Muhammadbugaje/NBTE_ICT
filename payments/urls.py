"""Finance (QA) internal screens, namespace "qa_fin", mounted at /qa/finance/."""
from django.urls import path

from . import refund_views, views

app_name = "qa_fin"

urlpatterns = [
    path("", views.queue, name="index"),
    path("queue/", views.queue, name="queue"),
    path("queue/<int:pk>/", views.item_detail, name="item"),
    path("queue/<int:pk>/first-pass/", views.item_first_pass, name="item_first_pass"),
    path("queue/<int:pk>/second-check/", views.item_second_pass, name="item_second_pass"),
    path("queue/<int:pk>/decide/", views.item_final, name="item_final"),
    path("queue/<int:pk>/change-first-pass/", views.item_change_first, name="item_change_first"),  # v2.1 P8
    path("queue/<int:pk>/remind/", views.item_remind, name="item_remind"),  # v2.1 P4
    path("settings/", views.finance_settings, name="settings"),  # v2.1 P6/P7
    path("obligations/", views.obligations, name="obligations"),
    path("obligations/issue/", views.issue_bill, name="issue_bill"),  # Phase 8
    path("bill-requests/", views.bill_requests, name="bill_requests"),  # v2.4: ES approves bills
    path("bill-requests/<int:pk>/", views.bill_request_detail, name="bill_request"),
    path("bill-requests/<int:pk>/<str:verb>/", views.bill_request_action, name="bill_request_action"),
    path("obligations/<int:pk>/", views.obligation_detail, name="obligation"),
    path("obligations/<int:pk>/<str:verb>/", views.obligation_action, name="obligation_action"),
    path("statements/", views.statements, name="statements"),
    path("statements/<int:pk>/", views.statement_detail, name="statement"),
    path("unmatched/", views.unmatched, name="unmatched"),
    path("lines/<int:pk>/", views.line_match, name="line_match"),
    path("waivers/", views.waivers, name="waivers"),
    path("refunds/", refund_views.refunds, name="refunds"),  # v2.5: cancellation refunds + overpayment refunds
    path("refunds/legacy/", views.refunds, name="refunds_legacy"),
    path("refund-requests/<int:pk>/", refund_views.refund_request_detail, name="refund_request"),
    path("refund-requests/<int:pk>/<str:verb>/", refund_views.refund_request_action, name="refund_request_action"),
    path("withdraw/<str:kind>/<int:pk>/", refund_views.withdraw, name="withdraw"),
    path("refunds/<int:pk>/<str:verb>/", views.refund_action, name="refund_action"),
    path("webhooks/", views.webhooks, name="webhooks"),
    # v2.2: double-entry ledger and nightly reconciliation
    path("reconciliation/", views.reconciliation, name="reconciliation"),
    path("reconciliation/run/", views.reconciliation_run, name="reconciliation_run"),
    path("reconciliation/<int:pk>/", views.reconciliation, name="reconciliation_detail"),
    path("reconciliation/issues/<int:issue_pk>/resolve/", views.reconciliation_resolve,
         name="reconciliation_resolve"),
]
