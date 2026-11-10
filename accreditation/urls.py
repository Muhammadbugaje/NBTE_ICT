"""Internal SOP-01 screens, namespace "qa_app", mounted at /qa/applications/."""
from django.urls import path

from . import onboarding_views, review_views, views

app_name = "qa_app"

urlpatterns = [
    path("", views.application_list, name="list"),
    path("clearance/", views.clearance_queue, name="clearance"),
    path("programmes/", views.programme_queue, name="programmes"),
    path("clearance/delegates/", views.delegate_add, name="delegate_add"),
    path("clearance/delegates/<int:pk>/revoke/", views.delegate_revoke, name="delegate_revoke"),
    path("programmes/reviews/<int:pk>/decide/", views.programme_decide, name="programme_decide"),
    path("<int:pk>/", views.application_detail, name="detail"),
    path("<int:pk>/lift-hold/", views.hold_lift, name="hold_lift"),
    path("<int:pk>/resume/", views.application_resume, name="resume"),
    path("clearance/<int:pk>/decide/", views.clearance_decide, name="decide"),
    path("documents/<int:pk>/review/", views.document_review, name="document_review"),
    # v2.1 F.16 (X16-X18): per-document review workspace (design 23)
    path("documents/", review_views.queue, name="docrev_queue"),
    path("documents/bulk-approve/", review_views.bulk_approve, name="docrev_bulk_approve"),  # v2.3
    path("documents/<int:pk>/", review_views.detail, name="docrev_detail"),
    path("documents/<int:pk>/assign/", review_views.assign, name="docrev_assign"),
    path("documents/<int:pk>/recommend/", review_views.recommend, name="docrev_recommend"),
    path("documents/<int:pk>/decide/", review_views.decide, name="docrev_decide"),
    path("verification/<int:pk>/assign/", review_views.verification_assign, name="docrev_verification_assign"),
    # Linear onboarding: self-study review → approval (accreditation/onboarding_linear.py)
    path("self-study/<int:pk>/", onboarding_views.self_study, name="onboarding_ssq"),
    path("self-study/<int:pk>/assign/", onboarding_views.self_study_assign, name="onboarding_assign"),
    path("self-study/<int:pk>/send/", onboarding_views.self_study_send, name="onboarding_send"),
    path("self-study/<int:pk>/clear/", onboarding_views.self_study_clear, name="onboarding_clear"),
    path("self-study/<int:pk>/return-officer/", onboarding_views.self_study_return_officer,
         name="onboarding_return_officer"),
    path("self-study/<int:pk>/return-institution/", onboarding_views.self_study_return_institution,
         name="onboarding_return_institution"),
    path("<int:pk>/approve-institution/", onboarding_views.approve_institution, name="onboarding_approve"),
    path("approval/<int:pk>/confirm/", onboarding_views.approval_confirm, name="onboarding_confirm"),
    path("approval/<int:pk>/reject/", onboarding_views.approval_reject, name="onboarding_reject"),
]
