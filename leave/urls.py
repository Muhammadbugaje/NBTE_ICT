from django.urls import path

from . import views

app_name = "leave"

urlpatterns = [
    path("", views.management, name="management"),
    path("entitlements/", views.entitlements, name="entitlements"),
    # v2.1 Phase D
    path("apply/", views.apply, name="apply"),
    path("apply/fields/", views.apply_fields, name="apply_fields"),
    path("apply/preview/", views.apply_preview, name="apply_preview"),
    path("types/", views.leave_types, name="types"),
    path("types/new/", views.leave_type_edit, name="type_new"),
    path("types/<int:pk>/edit/", views.leave_type_edit, name="type_edit"),
    path("holidays/", views.holidays, name="holidays"),
    path("holidays/new/", views.holiday_edit, name="holiday_new"),
    path("holidays/<int:pk>/edit/", views.holiday_edit, name="holiday_edit"),
    path("holidays/<int:pk>/delete/", views.holiday_delete, name="holiday_delete"),
]
