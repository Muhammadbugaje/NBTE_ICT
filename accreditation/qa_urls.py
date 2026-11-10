"""Internal SOP-02 screens (Phase 3), namespace "qa_req", mounted at /qa/requests/."""
from django.urls import path

from . import qa_views as views

app_name = "qa_req"

urlpatterns = [
    path("", views.request_list, name="list"),
    path("<int:pk>/", views.request_detail, name="detail"),
    path("<int:pk>/clearance/", views.request_clear, name="clear"),
    path("<int:pk>/retry-fee/", views.request_retry_fee, name="retry_fee"),
    path("ssq/", views.ssq_queue, name="ssq_queue"),
    path("ssq/<int:pk>/", views.ssq_review, name="ssq"),
    path("ssq/<int:pk>/decide/", views.ssq_decide, name="ssq_decide"),
    path("ssq/<int:pk>/requirements/", views.ssq_requirements, name="ssq_requirements"),  # v2.1 X37b
    path("ssq/<int:pk>/assign/", views.ssq_assign_view, name="ssq_assign"),  # v2.1 Part 2.2
    path("ssq/<int:pk>/remind/", views.ssq_nudge, name="ssq_nudge"),  # who is handling it: send a reminder
    path("ssq/<int:pk>/assessment/", views.ssq_assessment, name="ssq_assessment"),  # officer -> approver
    path("query/<str:kind>/<int:pk>/", views.query_raise, name="query_raise"),
    path("queries/", views.query_list, name="queries"),
    path("queries/<int:pk>/", views.query_detail, name="query"),
    path("queries/<int:pk>/close/", views.query_close, name="query_close"),
]
