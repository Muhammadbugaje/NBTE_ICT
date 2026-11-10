"""Phase P review chains (/qa/chains/)."""
from django.urls import path

from . import views_rotation as v

app_name = "chains"

urlpatterns = [
    path("<int:pk>/", v.chain_detail, name="detail"),
    path("<int:pk>/panel/", v.chain_panel, name="panel"),
    path("<int:pk>/act/", v.chain_act, name="act"),
]
