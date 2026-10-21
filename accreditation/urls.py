"""Internal SOP-01 screens, namespace "qa_app", mounted at /qa/applications/."""
from django.urls import path

from . import views

app_name = "qa_app"

urlpatterns = [
    path("", views.application_list, name="list"),
    path("clearance/", views.clearance_queue, name="clearance"),
    path("<int:pk>/", views.application_detail, name="detail"),
    path("<int:pk>/lift-hold/", views.hold_lift, name="hold_lift"),
    path("clearance/<int:pk>/decide/", views.clearance_decide, name="decide"),
    path("documents/<int:pk>/review/", views.document_review, name="document_review"),
]
