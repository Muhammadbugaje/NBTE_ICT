"""Forms for the NBTE Quality Assurance Portal (Phase 1)."""
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm

from accounts.models import User


class PortalAuthenticationForm(AuthenticationForm):
    """Email + password. NBTE staff are refused here with a pointer to the
    internal system (ExternalUserGuardMiddleware is the real control)."""

    username = forms.EmailField(label="Email address", max_length=254,
                                widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "username"}))

    error_messages = {
        **AuthenticationForm.error_messages,
        "staff_user": "NBTE staff sign in on the NBTE internal system, not the QA portal.",
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if getattr(user, "user_type", "staff") == "staff":
            # Phase 3: NBTE staff may sign in to the portal (Staff Portal
            # Dashboard) unless qa.portal.staff_sign_in_enabled is false.
            from accounts.middleware import staff_portal_enabled

            if not staff_portal_enabled():
                raise forms.ValidationError(self.error_messages["staff_user"], code="staff_user")
        if getattr(user, "user_type", "") == "institution" and user.institution_id:
            inst = user.institution
            if inst.status == "delisted" or not inst.portal_enabled:
                raise forms.ValidationError(
                    "Portal access for your institution is switched off. Contact NBTE for help.",
                    code="portal_disabled")


class PortalPasswordResetForm(PasswordResetForm):
    """Only portal accounts (institution users, resource persons) receive a
    link from the portal; staff use the internal reset page."""

    def get_users(self, email):
        for user in super().get_users(email):
            if getattr(user, "user_type", "staff") != "staff":
                yield user


class PortalSetPasswordForm(SetPasswordForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = False
        if commit:
            user.save(update_fields=["password", "must_change_password"])
        return user


class AccountProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name")
        labels = {"first_name": "First name", "last_name": "Last name"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.fields:
            self.fields[name].required = True
            self.fields[name].widget.attrs.update({"class": "input", "autocomplete": {
                "first_name": "given-name", "last_name": "family-name"}[name]})


class PortalPasswordChangeForm(forms.Form):
    """Forced (no current password) or voluntary (current password checked)."""

    current_password = forms.CharField(label="Current password", required=False,
                                       widget=forms.PasswordInput(attrs={"autocomplete": "current-password",
                                                                         "class": "input"}))
    new_password = forms.CharField(label="New password",
                                   widget=forms.PasswordInput(attrs={"autocomplete": "new-password",
                                                                     "class": "input"}))
    confirm_password = forms.CharField(label="Confirm new password",
                                       widget=forms.PasswordInput(attrs={"autocomplete": "new-password",
                                                                         "class": "input"}))

    def __init__(self, user, *args, forced=False, **kwargs):
        self.user = user
        self.forced = forced
        super().__init__(*args, **kwargs)
        if forced:
            del self.fields["current_password"]
        else:
            self.fields["current_password"].required = True

    def clean_current_password(self):
        value = self.cleaned_data.get("current_password")
        if not self.forced and not self.user.check_password(value or ""):
            raise forms.ValidationError("Your current password is incorrect.")
        return value

    def clean(self):
        data = super().clean()
        new, confirm = data.get("new_password"), data.get("confirm_password")
        if new and confirm:
            if new != confirm:
                self.add_error("confirm_password", "The two passwords do not match.")
            elif self.user.check_password(new):
                self.add_error("new_password", "Choose a password different from your current one.")
            else:
                try:
                    password_validation.validate_password(new, self.user)
                except forms.ValidationError as exc:
                    self.add_error("new_password", exc)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data["new_password"])
        self.user.must_change_password = False
        self.user.save(update_fields=["password", "must_change_password"])
        return self.user
