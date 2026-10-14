"""Role-specific dashboard URLs — mounted at /dashboards/."""
from django.urls import path

from . import views

app_name = "dashboards"

urlpatterns = [
    path("me/",               views.self_service,      name="self_service"),
    path("director/",        views.director,          name="director"),
    path("deputy/",          views.deputy,            name="deputy"),
    path("es/",              views.es,                name="es"),
    path("chairman/",        views.chairman,          name="chairman"),
    path("superadmin/",      views.superadmin,        name="superadmin"),
    path("audit/",           views.audit_log,         name="audit_log"),
    path("audit/export/",    views.export_audit_log,  name="audit_export"),
]