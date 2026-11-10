"""Mounted at /accounts/profile/bank/ (nbte_ict/urls.py). Owner-only pages."""
from django.urls import path

from . import views

app_name = "honoraria"

urlpatterns = [
    path("", views.my_bank, name="my_bank"),
    path("confirm/", views.banner_confirm, name="banner_confirm"),
]
