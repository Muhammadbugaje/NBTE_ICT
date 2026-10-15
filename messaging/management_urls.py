"""URL conf for the memo route management UI.

Namespace: `messaging_manage`.
Mounted by the project urls at `/manage/messages/`.
"""
from django.urls import path

from . import management_views as v

app_name = "messaging_manage"

urlpatterns = [
    # Overview
    path("",  v.home,  name="home"),

    # Routes
    path("routes/",                    v.route_list,     name="route_list"),
    path("routes/new/",                v.route_create,   name="route_create"),
    path("routes/<int:pk>/",           v.route_detail,   name="route_detail"),
    path("routes/<int:pk>/edit/",      v.route_update,   name="route_update"),
    path("routes/<int:pk>/delete/",    v.route_delete,   name="route_delete"),
    path("routes/<int:pk>/reorder/",   v.route_reorder,  name="route_reorder"),

    # Steps
    path("routes/<int:route_pk>/steps/new/",
         v.step_create, name="step_create"),
    path("routes/<int:route_pk>/steps/<int:step_pk>/edit/",
         v.step_update, name="step_update"),
    path("routes/<int:route_pk>/steps/<int:step_pk>/delete/",
         v.step_delete, name="step_delete"),
    path("routes/<int:route_pk>/steps/<int:step_pk>/move/",
         v.step_move,   name="step_move"),

    # Rules
    path("rules/",               v.rule_list,    name="rule_list"),
    path("rules/new/",           v.rule_create,  name="rule_create"),
    path("rules/<int:pk>/edit/", v.rule_update,  name="rule_update"),
    path("rules/<int:pk>/delete/", v.rule_delete, name="rule_delete"),

    # Permissions panel — grant / revoke broadcast
    path("permissions/",         v.permission_panel, name="permission_panel"),

    # Reference / test / help
    path("test/",                v.resolution_tester, name="test"),
    path("help/",                v.help_page,         name="help"),
]