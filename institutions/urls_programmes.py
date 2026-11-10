"""v2.1 Part 5: the NBTE staff "Programmes" section (namespace "qa_programmes",
mounted at /qa/programmes/)."""
from django.urls import path

from . import programmes_views as views

app_name = "qa_programmes"

urlpatterns = [
    path("", views.landing, name="home"),
    path("requests/", views.requests, name="requests"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/decide/", views.request_decide, name="request_decide"),
    path("requests/<int:pk>/withdraw/", views.request_withdraw, name="request_withdraw"),
    path("requirements/", views.requirements, name="requirements"),
    path("requirements/add/", views.requirements_add, name="requirements_add"),
    path("requirements/<int:pk>/retire/", views.requirements_retire, name="requirements_retire"),
]
