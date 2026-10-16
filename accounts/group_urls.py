"""Stage 3 URLs. Included from accounts/urls.py under "access/", so names are
`accounts:groups:<name>` (namespace "groups" nested inside "accounts")."""
from django.urls import path

from . import group_views as v

app_name = "groups"

urlpatterns = [
    path("groups/",                 v.group_list,        name="list"),
    path("groups/new/",             v.group_create,      name="create"),
    path("groups/<int:pk>/",        v.group_detail,      name="detail"),
    path("groups/<int:pk>/edit/",   v.group_update,      name="update"),
    path("groups/<int:pk>/delete/", v.group_delete,      name="delete"),
    path("roles/",                  v.role_groups_edit,  name="roles"),
    path("roles/<int:pk>/groups/",  v.role_groups_edit,  name="role_groups"),
    path("users/",                  v.user_access_list,  name="users"),
    path("users/<int:pk>/groups/",  v.user_groups_edit,  name="user_groups"),
    path("matrix/",                 v.permission_matrix, name="matrix"),
]
