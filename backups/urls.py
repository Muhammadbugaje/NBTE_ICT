from django.urls import path

from . import views

app_name = "backups"

urlpatterns = [
    path("", views.index, name="index"),
    path("run/", views.run_now, name="run_now"),
    path("history/", views.history, name="history"),
    path("runs/<int:pk>/progress/", views.run_progress, name="progress"),
    path("runs/<int:pk>/manifest/", views.manifest, name="manifest"),
    path("runs/<int:pk>/drill/", views.run_drill, name="drill"),
    path("destinations/new/", views.destination_save, name="destination_add"),
    path("destinations/<int:pk>/", views.destination_save, name="destination_save"),
    path("destinations/<int:pk>/toggle/", views.destination_toggle, name="destination_toggle"),
    path("destinations/<int:pk>/delete/", views.destination_delete, name="destination_delete"),
    path("destinations/<int:pk>/test/", views.destination_test, name="destination_test"),
    path("mounts/", views.mounts, name="mounts"),
    path("schedule/", views.schedule_save, name="schedule"),
]
