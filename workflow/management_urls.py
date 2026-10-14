"""URL conf for the workflow management site (`/manage/workflow/`).

Namespace: `workflow_manage`.
"""
from django.urls import path

from . import management_views as v

app_name = "workflow_manage"

urlpatterns = [
    # Overview
    path("",  v.manage_home,  name="home"),

    # Chains
    path("chains/",                    v.chain_list,           name="chain_list"),
    path("chains/new/",                v.chain_create,         name="chain_create"),
    path("chains/<int:pk>/",           v.chain_detail,         name="chain_detail"),
    path("chains/<int:pk>/edit/",      v.chain_update,         name="chain_update"),
    path("chains/<int:pk>/delete/",    v.chain_delete,         name="chain_delete"),
    path("chains/<int:pk>/reorder/",   v.chain_reorder,        name="chain_reorder"),

    # Steps
    path("chains/<int:chain_pk>/steps/new/",
         v.step_create, name="step_create"),
    path("chains/<int:chain_pk>/steps/<int:step_pk>/edit/",
         v.step_update, name="step_update"),
    path("chains/<int:chain_pk>/steps/<int:step_pk>/delete/",
         v.step_delete, name="step_delete"),
    path("chains/<int:chain_pk>/steps/<int:step_pk>/move/",
         v.step_move,   name="step_move"),

    # Request Types
    path("types/",            v.type_list,    name="type_list"),
    path("types/new/",        v.type_create,  name="type_create"),
    path("types/<int:pk>/edit/",  v.type_update, name="type_update"),
    path("types/<int:pk>/delete/", v.type_delete, name="type_delete"),

    # Categories
    path("categories/",            v.category_list,    name="category_list"),
    path("categories/new/",        v.category_create,  name="category_create"),
    path("categories/<int:pk>/edit/",  v.category_update, name="category_update"),
    path("categories/<int:pk>/delete/", v.category_delete, name="category_delete"),

    # Reference
    path("roles/",  v.role_reference,  name="roles"),
    path("test/",   v.resolution_tester, name="test"),
    path("help/",   v.help_page,       name="help"),
]