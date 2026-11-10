from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path

from config import views as config_views
from core import views as core_views
from django.views.generic import RedirectView

from accounts.security_views import staff_verify  # Phase 9
from accreditation import invite_views as visit_invite_views  # SOP-03 invitation link (works on either host)
from config import appearance_views  # v2.1 A10
from documents import views_scan as documents_scan  # v2.1 Phase F (X26)

urlpatterns = [
    path("admin/", admin.site.urls),
    # v2.1 A5: browser icon from the app-icon brand slot
    path("favicon.svg", config_views.favicon, name="favicon"),
    path("favicon.ico", config_views.favicon_ico),
    path("health/", core_views.health, name="health"),
    # v2.1 Phase B (X23, X33): My profile -> Signature / Bank details (owner-only pages)
    path("accounts/profile/signature/", include("documents.urls")),
    path("accounts/profile/bank/", include("honoraria.urls")),
    path("me/earnings/", include("honoraria.earnings_urls")),
    path("accounts/", include("accounts.urls")),
    path("settings/backups/", include("backups.urls")),  # Phase 8 (§22): Settings → Backups
    path("settings/geography/", include("staff.geo_urls")),  # v2.1 X49: Settings → States & LGAs
    path("settings/", include("config.urls")),
    path("me/preferences/", appearance_views.my_preferences, name="my_preferences"),  # v2.1 A10
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
    path("org/", include("org.urls")),  # Phase B: departments, divisions, offices, grade levels
    path("portal/", include("portal.urls", namespace="portal")),
    path("showcase/", include("showcase.urls")),
    
    # WORKAROUND FOR RENDER: Serve the QA Portal on a /qa-portal/ path
    path("qa-portal/", include("qa_core.portal_urls")),
    
    # NBTE Digital QA System — internal screens (Master Plan §13)
    path("qa/", include("qa_core.urls")),
    path("qa/", include("assignments.urls")),  # v2.1 Phase P: my work, grants log, who can see
    # QA Phase 2: institutions (§6), onboarding applications (SOP-01), Finance (QA), settings tables
    path("qa/institutions/", include("institutions.urls")),
    path("qa/applications/", include("accreditation.urls")),
    path("qa/finance/", include("payments.urls")),
    path("qa/finance/honoraria/", include("honoraria.urls_schedules")),  # v2.1 X32/X34
    path("qa/facilities/", include("facilities.urls")),  # v2.2: ND/HND facilities & equipment register
    # QA Phase 3: SOP-02 QA requests, SSQ review and queries
    path("qa/requests/", include("accreditation.qa_urls")),
    path("qa/cases/", include("accreditation.case_urls")),  # Phase 4: SOP-03
    path("qa/visit-invitations/", include("accreditation.invite_urls")),  # SOP-03: invitations to bodies / RPs
    # The tokenised invitation link must answer on the internal host too: the
    # letter is sometimes forwarded from an NBTE mailbox.
    path("invite/<str:token>/", visit_invite_views.token_page, name="visit_invite_token_link"),
    path("invite/<str:token>/letter/", visit_invite_views.token_letter, name="visit_invite_token_letter_link"),
    path("invite/<str:token>/calendar.ics", visit_invite_views.token_calendar, name="visit_invite_token_calendar_link"),
    path("invite/<str:token>/reminder/", visit_invite_views.token_reminder, name="visit_invite_token_reminder_link"),
    path("qa/inspection/", include("inspection.urls")),  # Phase 5: inspection and resource persons
    path("calendar/", include("core.calendar_urls")),  # add-to-calendar .ics + personal feed
    path("qa/professional-bodies/", include("institutions.pb_urls")),  # Phase 6: joint QA mapping
    path("qa/programmes/", include("institutions.urls_programmes")),  # v2.1 Part 5: programme set-up section
    path("qa/complaints/", include("complaints.urls")),  # Phase 7: complaints
    path("qa/settings/", include("qa_core.settings_urls")),
    path("qa/department/rotation/", include("assignments.rotation_urls")),  # v2.1 Phase P (X27–X29)
    path("qa/chains/", include("assignments.chain_urls")),  # v2.1 Phase P (X30)
    path("qa/documents/", include("documents.register_urls")),  # v2.1 Phase F (X24-X26): issued documents
    path("scan/", include("documents.scan_urls")),  # v2.1 Phase F (X26): Scan and verify
    path("v/<str:token>/", documents_scan.verify_token, name="verify_document"),  # the URL inside every QR
    # Old memo links stored in notifications before the fix point at a URL that never existed — send them to /messages/.
    re_path(r"^messaging/(?P<rest>.*)$",
            RedirectView.as_view(url="/messages/%(rest)s", query_string=True, permanent=False)),
    # Phase 9: public check of a scanned digital staff ID (internal host only, no login)
    path("verify/staff/<str:token>/", staff_verify, name="staff_verify"),
    
    # FIXED: Changed namespace from "api" to "api_staff" to resolve the urls.W005 warning
    path("api/v1/", include("api.urls_staff", namespace="api_staff")),  
    
    path("", include("core.urls")),
]

PRIVATE_MEDIA_RE = r"^%s(workflow/attachments|workflow/records|tasks|tickets|assets/attachments|messaging|import-tmp)/" % (
    settings.MEDIA_URL.lstrip("/"))

def _private_media(request, *args, **kwargs):
    from django.http import Http404
    raise Http404

if settings.DEBUG or getattr(settings, "SERVE_FILES_LOCALLY", False):
    urlpatterns += [re_path(PRIVATE_MEDIA_RE, _private_media)]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif getattr(settings, "SERVE_FILES_LOCALLY", False):
    from django.contrib.staticfiles.views import serve as serve_static
    from django.views.static import serve as serve_media
    urlpatterns += [
        re_path(r"^%s(?P<path>.*)$" % settings.STATIC_URL.lstrip("/"), serve_static, {"insecure": True}),
        re_path(r"^%s(?P<path>.*)$" % settings.MEDIA_URL.lstrip("/"), serve_media,
                {"document_root": settings.MEDIA_ROOT}),
    ]