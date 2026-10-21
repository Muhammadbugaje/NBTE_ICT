"""Bulk sign-in accounts for staff records (nominal-roll import).

    created, credentials, warnings, emailed = create_accounts(staff_qs, scheme="surname_fileno", actor=user)

Schemes (the temporary password; everyone must change it at first sign-in):
    surname_fileno  surname (letters only, lower case) + file number, e.g. "jabir1413"
    shared          one starter password chosen by HR
    random          a random password per person — returned in `credentials`
                    so the caller can hand out a one-time credentials sheet

Staff sign in with their email (the account's username). Staff with no
email yet sign in with their FILE NO (accounts.backends) until an email is
added to their record, which then becomes their sign-in automatically.
Staff without an email get a placeholder login address
<fileno>@<STAFF_LOGIN_EMAIL_DOMAIN> (default "staff-login.invalid" — the
.invalid domain can never receive mail); edit it on their profile later.
"""
from __future__ import annotations

import re
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.utils.text import slugify

SCHEMES = {
    "surname_fileno": "Surname + file number (e.g. jabir1413)",
    "shared": "One starter password I choose",
    "random": "A random password for each person (download the list once)",
}

# Temporary passwords are hashed with fewer PBKDF2 rounds so hundreds of
# accounts can be made in one request. Django re-hashes with the full count
# at the first successful sign-in, and the password must be changed then
# anyway.
_TEMP_ITERATIONS = 20_000


def login_domain():
    return getattr(settings, "STAFF_LOGIN_EMAIL_DOMAIN", "staff-login.invalid")


def is_placeholder_email(email):
    return bool(email) and email.lower().endswith("@" + login_domain())


def temp_password(staff, scheme, shared=""):
    if scheme == "surname_fileno":
        surname = re.sub(r"[^a-z]", "", (staff.last_name or "").lower())
        number = re.sub(r"[^a-z0-9]", "", (staff.employee_no or "").lower())
        return f"{surname}{number}"
    if scheme == "shared":
        return shared
    return secrets.token_urlsafe(9)


def _hash(password):
    hasher = PBKDF2PasswordHasher()
    return hasher.encode(password, hasher.salt(), iterations=_TEMP_ITERATIONS)


def default_role():
    from accounts.models import Role
    return Role.objects.filter(code="staff").first()


def create_accounts(staff_qs, *, scheme, shared_password="", actor=None, send_email=False, login_url=""):
    """-> (created_count, credentials[list of dict], warnings[list of str], emailed_count).

    send_email: email each person who has a real address their sign-in
    details (queued through notifications — Brevo, falling back to Gmail)."""
    if scheme not in SCHEMES:
        raise ValueError(f"Unknown password scheme {scheme!r}")
    if scheme == "shared" and len(shared_password or "") < 8:
        raise ValueError("The starter password must be at least 8 characters.")
    User = get_user_model()
    role = default_role()
    shared_hash = _hash(shared_password) if scheme == "shared" else None
    created, credentials, warnings, emailed = 0, [], [], []
    for staff in staff_qs.select_related("department").filter(user__isnull=True):
        if not (staff.employee_no or staff.email):
            continue
        email = (staff.email or f"{slugify(staff.employee_no) or staff.pk}@{login_domain()}").lower()
        if User.objects.filter(email__iexact=email).exists():
            warnings.append(f"{staff.full_name} ({staff.employee_no}): an account with {email} already "
                            "exists — link it on the staff record instead.")
            continue
        password = temp_password(staff, scheme, shared_password)
        if not password:
            warnings.append(f"{staff.full_name} ({staff.employee_no}): no surname or file number — skipped.")
            continue
        user = User(email=email, first_name=(staff.first_name or "")[:150],
                    last_name=(staff.last_name or "")[:150], must_change_password=True)
        if staff.department_id:
            user.department_id = staff.department_id
        if role is not None:
            user.role = role
        user.password = shared_hash or _hash(password)
        user._link_staff = staff           # accounts.signals links this record
        user.save()
        staff.refresh_from_db(fields=["user"])
        if staff.user_id != user.pk:       # signal didn't run (e.g. disconnected in a test)
            staff.user = user
            staff.save(update_fields=["user"])
        created += 1
        if send_email and staff.email:
            from core.email_utils import send_templated_email
            queued = send_templated_email(
                subject="Your NBTE ICT account", recipients=[user.email],
                template_name="emails/staff_welcome.html",
                context={"staff": staff, "user": user, "temp_password": password, "login_url": login_url},
                category="credentials")
            if queued is None:
                warnings.append(f"{staff.full_name} ({staff.employee_no}): the welcome email could not be "
                                "queued — check the email settings.")
            else:
                emailed.append(staff.pk)
        if scheme == "random":
            credentials.append({
                "file_no": staff.employee_no, "surname": staff.last_name, "other_names": staff.first_name,
                "department": staff.department.name if staff.department_id else "",
                "sign_in": user.email if staff.email else staff.employee_no, "password": password,
            })
    if created:
        from core import audit
        if actor is not None and getattr(actor, "pk", None):
            audit.log("accounts_created", actor, actor=actor,
                      changes={"count": created, "scheme": scheme}, note="Accounts created from nominal roll")
    return created, credentials, warnings, len(emailed)


def credentials_xlsx(credentials) -> bytes:
    import io

    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SIGN-IN DETAILS"
    ws.append(["FILE NO", "SURNAME", "OTHER NAME", "DEPARTMT", "SIGN IN WITH", "TEMPORARY PASSWORD"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3B5C")
    for r in credentials:
        ws.append([r["file_no"], r["surname"], r["other_names"], r["department"], r["sign_in"], r["password"]])
    for col, width in zip("ABCDEF", (10, 20, 28, 18, 14, 22)):
        ws.column_dimensions[col].width = width
    ws.append([])
    ws.append(["Each person must change this password the first time they sign in. "
               "Hand each line only to its owner, then delete this file."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
