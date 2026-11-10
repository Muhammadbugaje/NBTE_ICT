"""Add-to-calendar downloads and the personal feed (internal door), namespace "nbte_cal"."""
from django.urls import path

from . import calendar_views as views

app_name = "nbte_cal"

urlpatterns = [
    path("inspections/<int:pk>.ics", views.inspection_ics, name="inspection"),
    path("tasks/<int:pk>.ics", views.task_ics, name="task"),
    path("complaints/<int:pk>.ics", views.complaint_ics, name="complaint"),
    path("feed/<str:token>.ics", views.internal_feed, name="feed"),
]
