from django.urls import path

from . import views

app_name = "config"
urlpatterns = [
    path("", views.settings_index, name="settings"),
    path("<int:pk>/save/", views.settings_save, name="save"),
    path("test-email/", views.send_test_email, name="test_email"),
]
