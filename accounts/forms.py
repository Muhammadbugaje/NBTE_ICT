from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserChangeForm as DjangoUserChangeForm,
)

from .models import User
from .services import prepare_new_user


class EmailAuthenticationForm(AuthenticationForm):
    # Nominal-roll release: staff without an email sign in with their file
    # number; accounts.backends maps it to the account.
    username = forms.CharField(label="Email or file number", max_length=254,
                               widget=forms.TextInput(attrs={"autofocus": True}))

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        # QA (Master Plan §3): institution users and resource persons sign in
        # on the QA portal. ExternalUserGuardMiddleware is the real control;
        # refusing here just gives a clear message instead of a 403.
        if getattr(user, "user_type", "staff") != "staff":
            raise forms.ValidationError(
                "This account signs in at the NBTE Quality Assurance Portal, not here.",
                code="external_user",
            )


# ---------------------------------------------------------------------------
# Password change — forced (temporary password, no current check)
# ---------------------------------------------------------------------------
class ForcePasswordChangeForm(forms.Form):
    new_password = forms.CharField(widget=forms.PasswordInput, label="New password")
    confirm_password = forms.CharField(widget=forms.PasswordInput, label="Confirm new password")

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        new, confirm = data.get("new_password"), data.get("confirm_password")
        if new and confirm:
            if new != confirm:
                raise forms.ValidationError("Passwords do not match.")
            if self.user.check_password(new):
                raise forms.ValidationError("Choose a password different from the temporary one.")
            password_validation.validate_password(new, self.user)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data["new_password"])
        self.user.must_change_password = False
        self.user.save(update_fields=["password", "must_change_password"])
        return self.user


# ---------------------------------------------------------------------------
# Password change — voluntary (asks for the current password)
# ---------------------------------------------------------------------------
class VoluntaryPasswordChangeForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput, label="Current password")
    new_password = forms.CharField(widget=forms.PasswordInput, label="New password")
    confirm_password = forms.CharField(widget=forms.PasswordInput, label="Confirm new password")

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        pw = self.cleaned_data.get("current_password")
        if pw and not self.user.check_password(pw):
            raise forms.ValidationError("Your current password is incorrect.")
        return pw

    def clean(self):
        data = super().clean()
        new, confirm = data.get("new_password"), data.get("confirm_password")
        if new and confirm:
            if new != confirm:
                raise forms.ValidationError("New passwords do not match.")
            if self.user.check_password(new):
                raise forms.ValidationError("Choose a password you haven't used before.")
            password_validation.validate_password(new, self.user)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data["new_password"])
        self.user.must_change_password = False
        self.user.save(update_fields=["password", "must_change_password"])
        return self.user


# ---------------------------------------------------------------------------
# Admin add-user form
# ---------------------------------------------------------------------------
class AdminUserCreationForm(forms.ModelForm):
    temporary_password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to auto-generate a random one.",
    )

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "role", "department", "is_staff")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean_temporary_password(self):
        pw = self.cleaned_data.get("temporary_password")
        if pw:
            password_validation.validate_password(pw)
        return pw

    def save(self, commit=True):
        user = super().save(commit=False)
        prepare_new_user(user, self.cleaned_data.get("temporary_password") or None)
        if commit:
            user.save()
        return user


class AdminUserChangeForm(DjangoUserChangeForm):
    class Meta(DjangoUserChangeForm.Meta):
        model = User
        fields = "__all__"


# ---------------------------------------------------------------------------
# Password reset (custom sender via the notifications engine)
# ---------------------------------------------------------------------------
class PortalPasswordResetForm(PasswordResetForm):
    def save(self, *args, request=None, **kwargs):
        """Review M18: at most 3 reset emails per hour per client address and
        per email address. Over the limit nothing is sent, but the normal
        "check your inbox" page is still shown so the limit does not reveal
        which addresses have accounts."""
        from core import ratelimit

        limit, window = ratelimit.PASSWORD_RESETS
        email = (self.cleaned_data.get("email") or "").strip().lower()
        ip = ratelimit.client_ip(request) if request is not None else "unknown"
        if (ratelimit.is_limited("reset-ip", ip, limit)
                or ratelimit.is_limited("reset-email", email, limit)):
            return None
        ratelimit.hit("reset-ip", ip, window)
        ratelimit.hit("reset-email", email, window)
        return super().save(*args, request=request, **kwargs)

    def send_mail(self, subject_template_name, email_template_name, context, from_email,
                  to_email, html_email_template_name=None):
        from django.conf import settings
        from django.urls import reverse

        from core.email_utils import send_templated_email

        path = reverse("accounts:password_reset_confirm",
                       kwargs={"uidb64": context["uid"], "token": context["token"]})
        send_templated_email(
            subject=f"Reset your {settings.SITE_NAME} password",
            recipients=[to_email],
            template_name="emails/password_reset.html",
            context={"user": context["user"], "reset_url": f"{settings.SITE_URL}{path}"},
            category="reset",
        )


class PortalSetPasswordForm(SetPasswordForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = False
        if commit:
            user.save()
        return user


# ---------------------------------------------------------------------------
# Profile edit form
# ---------------------------------------------------------------------------
class ProfileForm(forms.Form):
    """Editable profile fields.

    The form touches two models:
      - User: first_name, last_name, email
      - Staff: phone, contact_preference, emergency_*, subsection,
               office_location, duties, photo

    If the user has no Staff record when they save, one is created on the
    fly. This makes the profile page work for users created before the
    auto-creation signal existed.
    """

    first_name = forms.CharField(max_length=100, required=True)
    last_name = forms.CharField(max_length=100, required=True)
    email = forms.EmailField(required=True, help_text="This is also your sign-in email.")

    phone = forms.CharField(max_length=30, required=False)
    contact_preference = forms.ChoiceField(
        choices=[("email", "Email"), ("phone", "Phone"), ("inapp", "In-app message")],
        required=False,
        initial="email",
    )
    emergency_contact_name = forms.CharField(max_length=150, required=False)
    emergency_contact_phone = forms.CharField(max_length=30, required=False)

    subsection = forms.CharField(max_length=100, required=False)
    office_location = forms.CharField(max_length=150, required=False)
    duties = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False)

    photo = forms.ImageField(required=False)

    def clean_email(self):
        """Review M13: a duplicate address used to crash with a database
        error; it is now a normal form error."""
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("Another account already uses this email address.")
        return email

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        # Prefill User fields
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["email"].initial = user.email

        # Prefill Staff fields (if a Staff record exists)
        try:
            staff = user.staff_profile
        except Exception:
            staff = None

        if staff is not None:
            self.fields["phone"].initial = staff.phone or ""
            self.fields["contact_preference"].initial = staff.contact_preference or "email"
            self.fields["emergency_contact_name"].initial = staff.emergency_contact_name or ""
            self.fields["emergency_contact_phone"].initial = staff.emergency_contact_phone or ""
            self.fields["subsection"].initial = staff.subsection or ""
            self.fields["office_location"].initial = staff.office_location or ""
            self.fields["duties"].initial = staff.duties or ""

    def save(self):
        data = self.cleaned_data

        # ---- User fields ------------------------------------------------
        self.user.first_name = data["first_name"]
        self.user.last_name = data["last_name"]
        self.user.email = data["email"].lower()
        self.user.save(update_fields=["first_name", "last_name", "email"])

        # ---- Staff fields ----------------------------------------------
        # If there's no Staff record yet, create one now so the profile page
        # works for every user, including superusers.
        try:
            staff = self.user.staff_profile
        except Exception:
            staff = None

        if staff is None:
            from staff.models import Staff

            base = f"USR-{self.user.pk:05d}"
            candidate = base
            n = 1
            while Staff.objects.filter(employee_no=candidate).exists():
                n += 1
                candidate = f"{base}-{n}"

            staff = Staff.objects.create(
                user=self.user,
                employee_no=candidate,
                first_name=data["first_name"] or "—",
                last_name=data["last_name"] or "—",
                email=self.user.email,
                department=self.user.department,
            )

        staff.email = self.user.email
        staff.phone = data.get("phone") or ""
        if data.get("contact_preference"):
            staff.contact_preference = data["contact_preference"]
        staff.emergency_contact_name = data.get("emergency_contact_name") or ""
        staff.emergency_contact_phone = data.get("emergency_contact_phone") or ""
        staff.subsection = data.get("subsection") or ""
        staff.office_location = data.get("office_location") or ""
        staff.duties = data.get("duties") or ""
        if data.get("photo"):
            staff.photo = data["photo"]
        staff.save()

        return self.user

# ---------------------------------------------------------------------------
# User management: one create form per segment (strict NBTE / institution split)
# ---------------------------------------------------------------------------
class _SegmentUserCreateForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()

    user_type = User.STAFF

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        from . import audience

        roles = audience.roles_for(self.user_type)
        if actor is not None and not getattr(actor, "is_superuser", False):
            import org

            allowed = [r.pk for r in org.grantable_roles(actor)]
            roles = roles.filter(pk__in=allowed)
        self.fields["role"].queryset = roles.order_by("-rank", "name")
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "input")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this e-mail already exists.")
        return email

    def clean_role(self):
        from . import audience

        role = self.cleaned_data.get("role")
        problem = audience.role_problem(role, self.user_type) if role else ""
        if problem:   # defence in depth: the queryset already limits the choices
            raise forms.ValidationError(problem)
        return role


class NBTEUserCreateForm(_SegmentUserCreateForm):
    """NBTE staff account: user_type is always 'staff' and a department is required."""

    department = forms.ModelChoiceField(queryset=None, required=True, empty_label="Choose a department",
                                        help_text="Every NBTE staff account belongs to a department.")
    role = forms.ModelChoiceField(queryset=None, required=False, empty_label="No role yet",
                                  help_text="Only NBTE internal roles are listed.")
    send_email = forms.BooleanField(required=False, initial=True, label="E-mail the sign-in details")

    user_type = User.STAFF

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from staff.models import Department

        depts = Department.objects.filter(is_active=True).order_by("name")
        actor = self.actor
        if actor is not None and not (actor.is_superuser or actor.has_perm("accounts.access_all_departments")):
            depts = depts.filter(pk=getattr(actor, "department_id", None))
        self.fields["department"].queryset = depts
        self.fields["department"].widget.attrs.setdefault("class", "input")

    def save(self, request=None):
        from django.db import transaction

        from .services import prepare_new_user, send_credentials_email

        data = self.cleaned_data
        with transaction.atomic():
            user = User(email=data["email"], first_name=data["first_name"].strip(),
                        last_name=data["last_name"].strip(), user_type=User.STAFF,
                        department=data["department"], institution=None, professional_body=None)
            password = prepare_new_user(user)
            user.save()
            if data.get("role"):
                from staff.role_ui import RoleAssignError, role_assign
                try:
                    role_assign(user, data["role"], self.actor, reason="Account created in User management")
                except RoleAssignError as exc:
                    from django.core.exceptions import ValidationError
                    raise ValidationError(str(exc))
            if data.get("send_email"):
                transaction.on_commit(lambda: send_credentials_email(user, password))
        return user


class InstitutionUserCreateForm(_SegmentUserCreateForm):
    """Institution portal account: user_type is always 'institution'; an
    institution and an institution portal role are required."""

    institution = forms.ModelChoiceField(queryset=None, required=True, empty_label="Choose an institution")
    role = forms.ModelChoiceField(queryset=None, required=True, empty_label="Choose a portal role",
                                  label="Portal role", help_text="Only institution portal roles are listed.")
    job_title = forms.CharField(max_length=120, required=False)
    institution_department = forms.CharField(max_length=120, required=False, label="Department at the institution")

    user_type = User.INSTITUTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from institutions.models import Institution

        self.fields["institution"].queryset = Institution.unscoped.order_by("name")
        self.fields["institution"].label_from_instance = lambda i: f"{i.name} ({i.code})"
        self.fields["institution"].widget.attrs.setdefault("class", "input")

    def save(self, request=None):
        from django.db import transaction

        from institutions.services import invite_user

        data = self.cleaned_data
        payload = {"first_name": data["first_name"], "last_name": data["last_name"], "email": data["email"],
                   "role": data["role"].code, "job_title": data.get("job_title") or "",
                   "institution_department": data.get("institution_department") or ""}
        with transaction.atomic():   # a failed credentials step leaves no half-made account
            return invite_user(data["institution"], payload, actor=self.actor, request=request,
                               approved_request=True)
