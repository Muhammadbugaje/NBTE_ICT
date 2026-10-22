"""The two front doors, as the API sees them.

* ``staff``  - the internal host (nbte_ict/urls.py), NBTE staff only.
* ``portal`` - the QA portal host (qa_core/portal_urls.py), institutions and
  resource persons.

The door of a *request* is decided by ``QAPortalHostMiddleware`` (host name,
or in development the ``X-NBTE-Door: portal`` header, see qa_core/hosts.py);
the door of a *user* follows from ``user_type``.
"""
STAFF = "staff"
PORTAL = "portal"
DOORS = (STAFF, PORTAL)


def door_for_user(user):
    return STAFF if getattr(user, "user_type", "staff") == "staff" else PORTAL


def door_for_request(request):
    return PORTAL if getattr(request, "is_qa_portal", False) else STAFF
