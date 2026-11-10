"""Mounted at /accounts/profile/signature/ (nbte_ict/urls.py). There is
deliberately NO route that serves a signature image (spec S-13)."""
from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.my_signature, name="my_signature"),
    path("missing/", views.directors_without_signature, name="directors_without_signature"),
]
