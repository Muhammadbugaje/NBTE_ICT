from django.urls import path

from . import views

app_name = "showcase"

urlpatterns = [
    # Public streaming view (already existed)
    path("<int:pk>/image/", views.image, name="image"),

    # In-app management (new)
    path("manage/", views.manage_list, name="manage_list"),
    path("manage/new/", views.manage_create, name="manage_create"),
    path("manage/<int:pk>/edit/", views.manage_update, name="manage_update"),
    path("manage/<int:pk>/delete/", views.manage_delete, name="manage_delete"),
    path("manage/<int:pk>/toggle/", views.manage_toggle, name="manage_toggle"),
]