from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from accounts.security_views import staff_verify  # Phase 9

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("settings/backups/", include("backups.urls")),  # Phase 8 (§22): Settings → Backups
    path("settings/", include("config.urls")),
    path("messages/", include("messaging.urls")),
    path("manage/messages/", include("messaging.management_urls")),
    path("notifications/", include("notifications.urls")),
    path("announcements/", include("announcements.urls")),  # Phase 9: announcements (internal only)
    path("workflow/", include("workflow.urls")),
    path("manage/workflow/", include("workflow.management_urls")),
    path("assets/", include("assets.urls")), 
    path("tickets/", include("tickets.urls")),
    path("tasks/", include("tasks.urls")),
    path("dashboards/", include("dashboards.urls")),
    path("leave/", include("leave.urls")),
    path("attendance/", include("attendance.urls")),
    path("staff/", include("staff.urls", namespace="staff")),
    path("portal/", include("portal.urls", namespace="portal")),
    path("showcase/", include("showcase.urls")),
    # NBTE Digital QA System — internal screens (Master Plan §13). The QA
    # portal host is served by qa_core.portal_urls, never by this file.
    path("qa/", include("qa_core.urls")),
    # QA Phase 2: institutions (§6), onboarding applications (SOP-01), Finance (QA), settings tables
    path("qa/institutions/", include("institutions.urls")),
    path("qa/applications/", include("accreditation.urls")),
    path("qa/finance/", include("payments.urls")),
    # QA Phase 3: SOP-02 QA requests, SSQ review and queries
    path("qa/requests/", include("accreditation.qa_urls")),
    path("qa/cases/", include("accreditation.case_urls")),  # Phase 4: SOP-03
    path("qa/inspection/", include("inspection.urls")),  # Phase 5: inspection and resource persons
    path("qa/professional-bodies/", include("institutions.pb_urls")),  # Phase 6: joint QA mapping
    path("qa/complaints/", include("complaints.urls")),  # Phase 7: complaints
    path("qa/settings/", include("qa_core.settings_urls")),
    # Old memo links (/messaging/memos/<pk>/) stored in notifications before
    # the fix point at a URL that never existed — send them to /messages/.
    re_path(r"^messaging/(?P<rest>.*)$",
            RedirectView.as_view(url="/messages/%(rest)s", query_string=True, permanent=False)),
    # Phase 9: public check of a scanned digital staff ID (internal host only, no login)
    path("verify/staff/<str:token>/", staff_verify, name="staff_verify"),
    path("api/v1/", include("api.urls_staff", namespace="api")),  # Phase 9: mobile API (staff door)
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif getattr(settings, "SERVE_FILES_LOCALLY", False):
    # Development with DEBUG=False (to see the real 404/500 pages): keep the
    # styling. Never on in production, where nginx serves these.
    from django.contrib.staticfiles.views import serve as serve_static
    from django.views.static import serve as serve_media
    urlpatterns += [
        re_path(r"^%s(?P<path>.*)$" % settings.STATIC_URL.lstrip("/"), serve_static, {"insecure": True}),
        re_path(r"^%s(?P<path>.*)$" % settings.MEDIA_URL.lstrip("/"), serve_media,
                {"document_root": settings.MEDIA_ROOT}),
    ]