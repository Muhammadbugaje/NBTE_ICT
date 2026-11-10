from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("", views.asset_list, name="list"),
    path("mine/", views.my_assets, name="my_assets"),
    path("scan/", views.scan, name="scan"),
    path("lookup/", views.lookup_api, name="lookup_api"),
    path("print-labels/", views.print_labels, name="print_labels"),
    path("new/", views.asset_create, name="create"),
    path("export.xlsx", views.asset_export_xlsx, name="export_xlsx"),

    # JSON detail — must come BEFORE the generic <asset_tag>/ route
    path("<str:asset_tag>/json/", views.asset_json, name="json"),

    path("<str:asset_tag>/", views.asset_detail, name="detail"),
    path("<str:asset_tag>/edit/", views.asset_edit, name="edit"),
    path("<str:asset_tag>/assign/", views.asset_assign, name="assign"),
    path("<str:asset_tag>/return/", views.asset_return, name="return"),
    path("<str:asset_tag>/mode/", views.asset_set_mode, name="set_mode"),
    path("<str:asset_tag>/state/", views.asset_set_state, name="set_state"),
    path("<str:asset_tag>/report-issue/", views.asset_report_issue, name="report_issue"),
    path("<str:asset_tag>/issues/<int:issue_id>/resolve/", views.asset_resolve_issue, name="resolve_issue"),
    path("<str:asset_tag>/attach/", views.asset_add_attachment, name="add_attachment"),
]