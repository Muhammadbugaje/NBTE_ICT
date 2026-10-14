from django.urls import path

from . import views

app_name = "leave"

urlpatterns = [
    path("", views.management, name="management"),
    path("entitlements/", views.entitlements, name="entitlements"),
]
