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
    username = forms.EmailField(label="Email",
                                widget=forms.EmailInput(attrs={"autofocus": True}))


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
    def send_mail(self, subject_template_name, email_template_name, context, from_email,
                  to_email, html_email_template_name=None):
        from django.conf import settings
        from django.urls import reverse

        from core.email_utils import send_templated_email

        path = reverse("password_reset_confirm",
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
    email = forms.EmailField(required=True)

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