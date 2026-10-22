"""Finance (QA) internal screens, namespace "qa_fin", mounted at /qa/finance/."""
from django.urls import path

from . import views

app_name = "qa_fin"

urlpatterns = [
    path("", views.queue, name="index"),
    path("queue/", views.queue, name="queue"),
    path("queue/<int:pk>/", views.item_detail, name="item"),
    path("queue/<int:pk>/first-pass/", views.item_first_pass, name="item_first_pass"),
    path("queue/<int:pk>/second-check/", views.item_second_pass, name="item_second_pass"),
    path("queue/<int:pk>/decide/", views.item_final, name="item_final"),
    path("obligations/", views.obligations, name="obligations"),
    path("obligations/issue/", views.issue_bill, name="issue_bill"),  # Phase 8
    path("obligations/<int:pk>/", views.obligation_detail, name="obligation"),
    path("obligations/<int:pk>/<str:verb>/", views.obligation_action, name="obligation_action"),
    path("statements/", views.statements, name="statements"),
    path("statements/<int:pk>/", views.statement_detail, name="statement"),
    path("unmatched/", views.unmatched, name="unmatched"),
    path("lines/<int:pk>/", views.line_match, name="line_match"),
    path("waivers/", views.waivers, name="waivers"),
    path("refunds/", views.refunds, name="refunds"),
    path("refunds/<int:pk>/<str:verb>/", views.refund_action, name="refund_action"),
    path("webhooks/", views.webhooks, name="webhooks"),
]
