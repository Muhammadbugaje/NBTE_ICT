"""/scan/ on the staff host (the portal host lists the same views in qa_core.portal_urls)."""
from django.urls import path

from . import views_scan as v

app_name = "scan"

urlpatterns = [
    path("", v.scan, name="page"),
    path("lookup/", v.lookup, name="lookup"),
]
