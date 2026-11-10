from django.urls import path

from . import lifecycle_views as lv
from . import views

app_name = "staff"

urlpatterns = [
    path("",             views.staff_list,          name="list"),
    path("new/",         views.staff_create,        name="create"),
    path("<int:pk>/",    views.staff_detail,        name="detail"),
    path("<int:pk>/edit/",   views.staff_update,     name="update"),
    path("<int:pk>/delete/", views.staff_delete,     name="delete"),
    path("<int:pk>/state/",  views.staff_change_state, name="change_state"),
    path("<int:pk>/message/", views.staff_message,   name="message"),
    path("<int:pk>/notify/",  views.staff_notify,    name="notify"),
    path("<int:pk>/transfer/", lv.staff_transfer,    name="transfer"),
    path("<int:pk>/id-card/",  views.staff_id_card,  name="id_card"),
    path("id-cards/print/",    views.staff_id_print, name="id_print"),
    path("<int:pk>/access/",   views.staff_access,   name="access"),
    path("<int:pk>/role/",     views.staff_role,     name="role"),

    # Public leadership
    path("leadership/",           views.public_leadership,       name="public_leadership"),
    path("leadership/<int:pk>/",  views.leadership_tenure_detail, name="leadership_tenure"),

    # Leadership management panel
    path("leadership/manage/",                   views.leadership_panel,      name="leadership_panel"),

    path("leadership/positions/",                views.position_list,         name="position_list"),
    path("leadership/positions/new/",            views.position_create,       name="position_create"),
    path("leadership/positions/<int:pk>/edit/",  views.position_update,       name="position_update"),
    path("leadership/positions/<int:pk>/delete/",views.position_delete,       name="position_delete"),

    path("leadership/tenures/",                  views.tenure_list,           name="tenure_list"),
    path("leadership/tenures/new/",              views.tenure_create,         name="tenure_create"),
    path("leadership/tenures/<int:pk>/edit/",    views.tenure_update,         name="tenure_update"),
    path("leadership/tenures/<int:pk>/delete/",  views.tenure_delete,         name="tenure_delete"),
    path("leadership/tenures/<int:pk>/end/",     views.tenure_end_now,        name="tenure_end"),

    # Nominal roll (import / export / template / history)
    path("nominal-roll/",                 lv.nominal_roll,         name="nominal_roll"),
    path("nominal-roll/upload/",          lv.nominal_roll_upload,  name="nominal_roll_upload"),
    path("nominal-roll/preview/",         lv.nominal_roll_preview, name="nominal_roll_preview"),
    path("nominal-roll/confirm/",         lv.nominal_roll_confirm, name="nominal_roll_confirm"),
    path("nominal-roll/export/",          lv.nominal_roll_export,  name="nominal_roll_export"),
    path("nominal-roll/template/",        lv.nominal_roll_template, name="nominal_roll_template"),
    path("nominal-roll/history/<int:pk>/report/", lv.nominal_roll_report, name="nominal_roll_report"),
    path("nominal-roll/history/<int:pk>/sign-in-details/", lv.nominal_roll_credentials,
         name="nominal_roll_credentials"),

    # Promotions
    path("promotions/",                   lv.promotions,           name="promotions"),
    path("promotions/preview/",           lv.promotion_preview,    name="promotion_preview"),
    path("promotions/cascade-count/",     lv.promotion_cascade_count, name="promotion_cascade_count"),
    path("promotions/apply/",             lv.promotion_apply,      name="promotion_apply"),
    path("promotions/history/",           lv.promotion_history,    name="promotion_history"),
    path("promotions/id-filter/",         lv.promotion_id_filter,  name="promotion_id_filter"),
    path("promotions/<int:pk>/rollback/", lv.promotion_rollback,   name="promotion_rollback"),

    # Advanced query
    path("query/",                        lv.query_builder,        name="query"),
    path("query/export/",                 lv.query_export,         name="query_export"),
    path("query/save/",                   lv.query_save,           name="query_save"),
    path("query/saved/",                  lv.query_saved,          name="query_saved"),
    path("query/saved/<int:pk>/delete/",  lv.query_delete,         name="query_delete"),


]