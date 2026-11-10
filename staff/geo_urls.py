"""Settings -> States & LGAs (v2.1 X49), mounted at /settings/geography/."""
from django.urls import path

from . import geo_views as v

app_name = "geo"

urlpatterns = [
    path("", v.index, name="index"),
    path("states/save/", v.state_save, name="state_save"),
    path("states/<slug:code>/", v.state_panel, name="state"),
    path("states/<slug:code>/lgas/save/", v.lga_save, name="lga_save"),
    path("states/<slug:code>/lgas/<int:pk>/delete/", v.lga_delete, name="lga_delete"),
]
