"""Rotation: the queue of waiting work, the assign dialog and pool settings
(/qa/department/rotation/). Director of a department (own department) and
the ES / super admin (every department) only."""
from django.urls import path

from . import views_rotation as v

app_name = "rotation"

urlpatterns = [
    path("", v.board, name="board"),
    path("assign/", v.assign_dialog, name="assign_dialog"),
    path("assign/confirm/", v.assign, name="assign"),
    path("settings/", v.pool_settings, name="pool_settings"),
    path("settings/member/<int:pk>/", v.member_save, name="member_save"),
]
