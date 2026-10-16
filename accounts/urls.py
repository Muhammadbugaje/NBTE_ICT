from django.contrib.auth import views as auth_views
from django.contrib.auth.views import LogoutView
from django.urls import include, path, reverse_lazy

from . import user_views, views
from .forms import PortalPasswordResetForm, PortalSetPasswordForm

app_name = "accounts"

urlpatterns = [
    # ---------------------------------------------------------------------
    # Auth
    # ---------------------------------------------------------------------
    path("login/",  views.PortalLoginView.as_view(), name="login"),
    # NOTE: LogoutView only accepts POST as of Django 5.0. Templates must
    # submit a form with method="post" and the csrf_token, not use a link.
    path("logout/", LogoutView.as_view(),            name="logout"),

    # ---------------------------------------------------------------------
    # Password change
    # ---------------------------------------------------------------------
    # `force_password_change`:
    #   - If user.must_change_password is True → no current password required.
    #   - Otherwise → falls back to the voluntary flow (asks for current).
    # The same view is exposed under two names for template convenience.
    # `force_password_change` is the canonical name; `password_change` is
    # kept as an alias so existing templates keep resolving. If you ever
    # include django.contrib.auth.urls, drop the alias to avoid a name clash.
    path(
        "change-password/",
        views.force_password_change,
        name="force_password_change",
    ),
    path(
        "change-password-voluntary/",
        views.force_password_change,
        name="password_change",
    ),

    # ---------------------------------------------------------------------
    # Profile
    # ---------------------------------------------------------------------
    path("profile/", views.profile_view, name="profile"),

    # ---------------------------------------------------------------------
    # Password reset (forgotten password, via email link)
    # ---------------------------------------------------------------------
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            form_class=PortalPasswordResetForm,
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            form_class=PortalSetPasswordForm,
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),

    # ---------------------------------------------------------------------
    # User management (Super Admin / HR / anyone with accounts.*_user perms)
    # ---------------------------------------------------------------------
    path("users/",                 user_views.user_list,           name="user_list"),
    path("users/<int:pk>/",        user_views.user_detail,         name="user_detail"),
    path("users/<int:pk>/reset/",  user_views.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/toggle/", user_views.user_toggle_active,  name="user_toggle_active"),
    path("users/<int:pk>/roles/",  user_views.user_edit_roles,     name="user_edit_roles"),

    # ---------------------------------------------------------------------
    # Stage 3: groups & permissions  ->  accounts:groups:<name>
    # ---------------------------------------------------------------------
    path("access/", include("accounts.group_urls")),
]