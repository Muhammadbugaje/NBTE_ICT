"""Endpoints shared by both doors (mounted at api/v1/ by urls_staff and
urls_portal). Door rules are enforced by the authentication and permission
classes, not by which file a route is in."""
from django.urls import path

from .views import auth, home, me, misc
from .views import notifications as notifications_views


def common_patterns(urlconf):
    """``urlconf`` is the door's root URLconf, so the schema lists exactly
    the API routes of that door."""
    return [
        path("health/", misc.HealthView.as_view(), name="health"),
        # Authentication
        path("auth/token/", auth.TokenView.as_view(), name="token"),
        path("auth/refresh/", auth.RefreshView.as_view(), name="refresh"),
        path("auth/logout/", auth.LogoutView.as_view(), name="logout"),
        path("auth/password/change/", auth.PasswordChangeView.as_view(), name="password_change"),
        path("auth/password/forgot/", auth.PasswordForgotView.as_view(), name="password_forgot"),
        # Profile
        path("me/", me.MeView.as_view(), name="me"),
        path("me/preferences/", me.PreferencesView.as_view(), name="me_preferences"),
        path("me/devices/", me.DevicesView.as_view(), name="me_devices"),
        path("me/devices/revoke-others/", me.DevicesRevokeOthersView.as_view(), name="me_devices_revoke_others"),
        path("me/devices/<int:pk>/", me.DeviceDetailView.as_view(), name="me_device"),
        path("me/push-token/", me.PushTokenView.as_view(), name="me_push_token"),
        path("me/deletion-request/", me.DeletionRequestView.as_view(), name="me_deletion_request"),
        # Home
        path("home/", home.HomeView.as_view(), name="home"),
        # --- notifications ---
        path("notifications/", notifications_views.NotificationListView.as_view(), name="notifications"),
        path("notifications/feed/", notifications_views.NotificationFeedView.as_view(), name="notifications_feed"),
        path("notifications/unread-count/", notifications_views.NotificationUnreadCountView.as_view(),
             name="notifications_unread_count"),
        path("notifications/read-all/", notifications_views.NotificationReadAllView.as_view(),
             name="notifications_read_all"),
        path("notifications/bulk/", notifications_views.NotificationBulkView.as_view(), name="notifications_bulk"),
        path("notifications/<int:pk>/read/", notifications_views.NotificationReadView.as_view(),
             name="notification_read"),
        path("notifications/<int:pk>/unread/", notifications_views.NotificationUnreadView.as_view(),
             name="notification_unread"),
        # OpenAPI schema + Swagger UI (DEBUG, or a signed-in superuser)
        path("schema/", misc.SchemaView.as_view(urlconf=urlconf), name="schema"),
        path("docs/", misc.DocsView.as_view(url_name="api:schema"), name="docs"),
    ]
