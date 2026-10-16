"""Stage 3 forms: groups, role→groups, user→groups and user→permissions.

Every form takes the acting user (`actor`) and enforces the ownership rule
server-side: a non-superuser may only add or remove a group/permission they
are allowed to assign (see accounts.permissions_service). Items the actor may
not change are "locked": they are rendered disabled and keep their current
state whatever the browser sends, and a crafted POST that tries to add one is
rejected with a clear error.
"""
from django import forms
from django.contrib.auth.models import Group, Permission

from . import permissions_service as ps
from .models import GroupProfile


def permission_queryset():
    return (Permission.objects.select_related("content_type")
            .order_by("content_type__app_label", "content_type__model", "codename"))


def _apply_locks(submitted, initial, allowed, describe):
    """Return (final_set, errors). `allowed(item)` says if the actor may change it."""
    submitted, initial = set(submitted), set(initial)
    final, errors = set(), []
    for item in submitted | initial:
        if allowed(item):
            if item in submitted:
                final.add(item)
        else:
            if item in initial:
                final.add(item)              # locked: keep as it was
            elif item in submitted:
                errors.append(describe(item))  # crafted grant of something locked
    return final, errors


class _ActorFormMixin:
    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)


# ---------------------------------------------------------------------------
# Group create / edit
# ---------------------------------------------------------------------------
class GroupForm(_ActorFormMixin, forms.ModelForm):
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional. What this group is for, in one or two sentences.",
    )
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Group
        fields = ["name", "permissions"]
        help_texts = {"name": "A short, unique name, e.g. “Asset managers”."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = permission_queryset()
        self.fields["name"].widget.attrs.update({"autocomplete": "off", "maxlength": 150})
        if self.instance.pk:
            profile = getattr(self.instance, "profile", None)
            try:
                self.fields["description"].initial = profile.description if profile else ""
            except GroupProfile.DoesNotExist:
                pass

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Give the group a name.")
        clash = Group.objects.filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A group with this name already exists.")
        return name

    def clean_permissions(self):
        submitted = set(self.cleaned_data.get("permissions") or [])
        initial = set(self.instance.permissions.all()) if self.instance.pk else set()
        final, errors = _apply_locks(
            submitted, initial,
            allowed=lambda p: ps.can_edit_group_permission(self.actor, p),
            describe=ps.perm_label,
        )
        if errors:
            raise forms.ValidationError(
                "You can only add permissions you hold yourself: " + ", ".join(sorted(errors))
            )
        return list(final)

    def save(self, commit=True):
        group = super().save(commit=commit)
        if commit:
            GroupProfile.objects.update_or_create(
                group=group, defaults={"description": self.cleaned_data.get("description", "")}
            )
        return group


# ---------------------------------------------------------------------------
# Role -> groups
# ---------------------------------------------------------------------------
class RoleGroupForm(_ActorFormMixin, forms.Form):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, role=None, **kwargs):
        self.role = role
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = Group.objects.order_by("name")
        if role is not None and not self.is_bound:
            self.fields["groups"].initial = list(role.groups.all())

    def clean_groups(self):
        submitted = set(self.cleaned_data.get("groups") or [])
        initial = set(self.role.groups.all())
        final, errors = _apply_locks(
            submitted, initial,
            allowed=lambda g: ps.can_assign_group(self.actor, self.role, g),
            describe=lambda g: g.name,
        )
        if errors:
            raise forms.ValidationError(
                "You can't assign these groups (you don't hold all of their permissions): "
                + ", ".join(sorted(errors))
            )
        return final


# ---------------------------------------------------------------------------
# User -> direct groups / direct permissions
# ---------------------------------------------------------------------------
class UserGroupForm(_ActorFormMixin, forms.Form):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, target=None, **kwargs):
        self.target = target
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = Group.objects.order_by("name")
        if target is not None and not self.is_bound:
            self.fields["groups"].initial = list(target.groups.all())

    def clean_groups(self):
        submitted = set(self.cleaned_data.get("groups") or [])
        initial = set(self.target.groups.all())
        final, errors = _apply_locks(
            submitted, initial,
            allowed=lambda g: ps.can_assign_group(self.actor, self.target, g),
            describe=lambda g: g.name,
        )
        if errors:
            raise forms.ValidationError("You can't assign these groups: " + ", ".join(sorted(errors)))
        return final


class UserDirectPermissionForm(_ActorFormMixin, forms.Form):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, target=None, **kwargs):
        self.target = target
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = permission_queryset()
        if target is not None and not self.is_bound:
            self.fields["permissions"].initial = list(target.user_permissions.all())

    def clean_permissions(self):
        submitted = set(self.cleaned_data.get("permissions") or [])
        initial = set(self.target.user_permissions.all())
        final, errors = _apply_locks(
            submitted, initial,
            allowed=lambda p: ps.can_assign_permission(self.actor, self.target, p),
            describe=ps.perm_label,
        )
        if errors:
            raise forms.ValidationError(
                "You can only grant permissions you hold yourself: " + ", ".join(sorted(errors))
            )
        return final
