from django.urls import path

from . import views

app_name = "honoraria_earnings"

urlpatterns = [
    path("", views.my_earnings, name="list"),
]
