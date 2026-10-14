from django.urls import path

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


]