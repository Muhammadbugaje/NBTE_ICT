"""Staff actions on visit invitations, namespace "qa_invite", mounted at /qa/visit-invitations/."""
from django.urls import path

from . import invite_views as views

app_name = "qa_invite"

urlpatterns = [
    path("case/<int:pk>/send/", views.send_for_case, name="send_for_case"),
    path("<int:pk>/letter/", views.letter, name="letter"),
    path("<int:pk>/letter.docx", views.letter_docx, name="letter_docx"),
    path("<int:pk>/resend/", views.resend, name="resend"),
    path("<int:pk>/regenerate/", views.regenerate, name="regenerate"),
    path("<int:pk>/contact/", views.contact, name="contact"),
    path("<int:pk>/acknowledge/", views.acknowledge, name="acknowledge"),
]
