"""/qa/documents/ - the issued-documents register (staff host)."""
from django.urls import path

from . import views_official as o
from . import views_register as v

app_name = "documents_register"

urlpatterns = [
    path("", v.register, name="list"),
    path("<int:pk>/", v.detail, name="detail"),
    path("<int:pk>/revoke/", v.revoke, name="revoke"),
    path("<int:pk>/official/", v.upload_official, name="upload_official"),
    # Official letters pipeline: provisional -> signed official copy
    path("official-letters/", o.queue, name="official_letters"),
    path("official-letters/<int:pk>/", o.detail, name="official_letter"),
    path("official-letters/<int:pk>/assign/", o.assign, name="official_letter_assign"),
    path("official-letters/<int:pk>/draft/", o.submit_draft, name="official_letter_draft"),
    path("official-letters/<int:pk>/send-back/", o.send_back, name="official_letter_send_back"),
    path("official-letters/<int:pk>/upload/", o.upload, name="official_letter_upload"),
]
