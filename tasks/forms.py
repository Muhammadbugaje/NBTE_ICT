"""Forms for the tasks app.

Data-only forms (plain Form) for task creation and editing — the service
layer handles mutations so business rules stay in one place.
"""
from django import forms

from core.uploads import validate_attachment_upload
from django.forms import BaseFormSet, formset_factory

from .models import Task, TaskAssignee, TaskCategory


# ===========================================================================
# Helpers
# ===========================================================================
def _eligible_users_queryset(category=None, department=None, exclude=None):
    """Build the queryset of assignable users.

    `exclude` accepts:
      - None
      - a single User instance
      - a single pk (int / str)
      - an iterable of User instances and/or pks (mixed is fine)
    """
    from accounts.selectors import staff_users

    qs = staff_users()  # Phase 0 (H22): never institution users / RPs
    if department is not None:
        qs = qs.filter(department=department)
    if category and category.allowed_role_codes:
        import org
        qs = qs.filter(org.role_q(*category.allowed_role_codes))

    if exclude is not None:
        pks = []
        if not hasattr(exclude, "__iter__") or isinstance(exclude, str):
            pks.append(exclude.pk if hasattr(exclude, "pk") else int(exclude))
        else:
            for item in exclude:
                if hasattr(item, "pk"):
                    pks.append(item.pk)
                else:
                    try:
                        pks.append(int(item))
                    except (TypeError, ValueError):
                        continue
        if pks:
            qs = qs.exclude(pk__in=pks)

    return qs.order_by("first_name", "last_name", "email")


# ===========================================================================
# Create / edit
# ===========================================================================
class TaskCreateForm(forms.Form):
    """Data-only. Service layer (`create_task`) does the work."""

    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Short, specific summary — e.g. 'Q4 printer maintenance'",
            "autofocus": True,
        }),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input",
            "rows": 5,
            "placeholder": "What needs to be done, and what does 'done' look like?",
        }),
    )
    category = forms.ModelChoiceField(
        queryset=None, required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    priority = forms.ChoiceField(
        choices=Task.PRIORITY_CHOICES,
        widget=forms.Select(attrs={"class": "input"}),
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    due_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    estimated_hours = forms.DecimalField(
        required=False, max_digits=6, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={
            "class": "input", "step": "0.25", "placeholder": "e.g. 2.5",
        }),
    )
    requires_review = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={"class": "check-input"}),
    )
    reviewer = forms.ModelChoiceField(
        queryset=None, required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    tags = forms.CharField(
        required=False, max_length=500,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Comma-separated — e.g. maintenance, weekly",
        }),
    )
    recurrence_type = forms.ChoiceField(
        choices=Task.RECURRENCE_CHOICES, required=False,
        initial=Task.RECURRENCE_NONE,
        widget=forms.Select(attrs={"class": "input"}),
    )
    recurrence_rule = forms.CharField(
        required=False, max_length=100,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Only if 'Custom' — e.g. every 2 weeks on Monday",
        }),
    )
    assignees = forms.ModelMultipleChoiceField(
        queryset=None, required=False,
        widget=forms.SelectMultiple(attrs={"class": "input", "size": 6}),
    )

    def __init__(self, *args, user=None, instance=None,
                 locked_category=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance = instance

        cats = TaskCategory.objects.filter(is_active=True)
        if user is not None:
            from .services import rank_of
            my_rank = rank_of(user)
            cats = [c for c in cats if c.min_rank_to_assign <= my_rank]
        self.fields["category"].queryset = TaskCategory.objects.filter(
            pk__in=[c.pk for c in cats]
        ).order_by("sort_order", "name")

        if user is not None:
            from .services import reviewer_choices
            keep = [instance.reviewer_id] if instance is not None and instance.reviewer_id else None
            self.fields["reviewer"].queryset = reviewer_choices(user, include_ids=keep)
        else:
            self.fields["reviewer"].queryset = _eligible_users_queryset()

        if locked_category is not None:
            self.fields["category"].initial = locked_category
            self.fields["category"].widget = forms.HiddenInput()
            self.fields["category"].queryset = TaskCategory.objects.filter(pk=locked_category.pk)
            self._locked_category = locked_category
        else:
            self._locked_category = None

        if instance is not None:
            self.initial.update({
                "title": instance.title,
                "description": instance.description,
                "category": instance.category_id,
                "priority": instance.priority,
                "start_date": instance.start_date,
                "due_date": instance.due_date,
                "estimated_hours": instance.estimated_hours,
                "requires_review": instance.requires_review,
                "reviewer": instance.reviewer_id,
                "tags": ", ".join(instance.tags or []),
                "recurrence_type": instance.recurrence_type,
                "recurrence_rule": instance.recurrence_rule,
                "assignees": list(instance.assignee_rows.values_list("user_id", flat=True)),
            })

        selected_cat = None
        if self._locked_category is not None:
            selected_cat = self._locked_category
        elif self.is_bound:
            try:
                cid = self.data.get("category")
                selected_cat = TaskCategory.objects.filter(pk=cid).first()
            except Exception:
                selected_cat = None
        elif instance is not None and instance.category_id:
            selected_cat = instance.category

        self.fields["assignees"].queryset = _eligible_users_queryset(
            category=selected_cat,
            department=getattr(selected_cat, "handling_department", None),
        )

    def clean_title(self):
        title = (self.cleaned_data.get("title") or "").strip()
        if len(title) < 3:
            raise forms.ValidationError("Title must be at least 3 characters.")
        return title

    def clean_tags(self):
        raw = (self.cleaned_data.get("tags") or "").strip()
        if not raw:
            return []
        return [t.strip() for t in raw.split(",") if t.strip()]

    def clean(self):
        data = super().clean()
        start = data.get("start_date")
        due = data.get("due_date")
        if start and due and due < start:
            self.add_error("due_date", "Due date must be after start date.")
        return data


class TaskEditForm(TaskCreateForm):
    """Same shape as create; used for editing."""


# ===========================================================================
# Assignment
# ===========================================================================
class AssignForm(forms.Form):
    assignee = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "input"}),
        label="Assign to",
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input", "placeholder": "Optional note",
        }),
    )

    def __init__(self, *args, task=None, **kwargs):
        super().__init__(*args, **kwargs)
        assigned_ids = []
        if task is not None:
            assigned_ids = list(task.assignee_rows.values_list("user_id", flat=True))
        category = getattr(task, "category", None)
        department = getattr(task, "owner_department", None)
        self.fields["assignee"].queryset = _eligible_users_queryset(
            category=category, department=department, exclude=assigned_ids,
        )


class AssignToRoleForm(forms.Form):
    role_code = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Role code — e.g. officer, deputy-director",
        }),
    )


class BulkAssignForm(forms.Form):
    assignee = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "input"}),
    )
    task_ids = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = _eligible_users_queryset()


# ===========================================================================
# Subtask splitter — a formset of specs
# ===========================================================================
class SubtaskSpecForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input", "placeholder": "Subtask title",
        }),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 2, "placeholder": "Optional details",
        }),
    )
    assignee = forms.ModelChoiceField(
        queryset=None, required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    due_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    priority = forms.ChoiceField(
        choices=[("", "Inherit")] + list(Task.PRIORITY_CHOICES),
        required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    # H11: work for another department becomes a request there.
    department = forms.ModelChoiceField(
        queryset=None, required=False, empty_label="My department",
        widget=forms.Select(attrs={"class": "input"}),
        help_text="Another department receives it as a request in its own workflow.",
    )

    def __init__(self, *args, parent_task=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from staff.models import Department

        category = getattr(parent_task, "category", None)
        department = getattr(parent_task, "owner_department", None)
        if user is not None:
            # H8 / H10: whoever splits picks from people below *their* rank in
            # their own department.
            from .services import assignable_users_for
            self.fields["assignee"].queryset = assignable_users_for(
                user, department=department, category=category)
        else:
            self.fields["assignee"].queryset = _eligible_users_queryset(
                category=category, department=department,
            )
        own = getattr(user, "department_id", None) or getattr(department, "pk", None)
        self.fields["department"].queryset = Department.objects.exclude(pk=own).order_by("name")

    def clean(self):
        data = super().clean()
        if data.get("department") is not None and data.get("assignee") is not None:
            self.add_error("assignee", "Leave the person empty: the other department assigns its own staff.")
        return data


# IMPORTANT: define the BaseFormSet subclass BEFORE formset_factory uses it.
class BaseSubtaskFormSet(BaseFormSet):
    """Formset that forwards `parent_task` to every form instance so each
    SubtaskSpecForm can restrict its assignee choices to that task's
    department / category context."""

    def __init__(self, *args, parent_task=None, user=None, **kwargs):
        self.parent_task = parent_task
        self.user = user
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        kwargs["parent_task"] = self.parent_task
        kwargs["user"] = self.user
        return super()._construct_form(i, **kwargs)

    @property
    def empty_form(self):
        form = self.form(auto_id=self.auto_id, prefix=self.add_prefix("__prefix__"),
                         empty_permitted=True, use_required_attribute=False,
                         parent_task=self.parent_task, user=self.user)
        self.add_fields(form, None)
        return form


# H9: exactly ONE subtask form; "Add another subtask" clones the
# <template> copy of empty_form.
SubtaskFormSet = formset_factory(
    SubtaskSpecForm,
    formset=BaseSubtaskFormSet,
    extra=0,
    min_num=1,
    validate_min=True,
)


# ===========================================================================
# Leaf transitions
# ===========================================================================
class StartWorkForm(forms.Form):
    note = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input", "placeholder": "Optional — what are you starting with?",
        }),
    )


class AskQuestionForm(forms.Form):
    note = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "What do you need clarified?",
        }),
    )

    def clean_note(self):
        n = (self.cleaned_data.get("note") or "").strip()
        if len(n) < 3:
            raise forms.ValidationError("Please write your question.")
        return n


class ResumeForm(forms.Form):
    note = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input", "placeholder": "Optional note",
        }),
    )


class ReturnForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Why are you returning this task?",
        }),
    )

    def clean_reason(self):
        r = (self.cleaned_data.get("reason") or "").strip()
        if len(r) < 5:
            raise forms.ValidationError("Please explain in a bit more detail.")
        return r


class ResendForm(forms.Form):
    note = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input", "placeholder": "Optional note for the assignees",
        }),
    )


class MarkDoneForm(forms.Form):
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Optional — what did you do?",
        }),
    )


class ReviewForm(forms.Form):
    approve = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={"class": "check-input"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Notes for the assignees (required if rejecting).",
        }),
    )

    def clean(self):
        data = super().clean()
        if not data.get("approve") and len((data.get("notes") or "").strip()) < 5:
            self.add_error("notes", "Explain why you're sending it back.")
        return data


class CancelForm(forms.Form):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 2,
            "placeholder": "Optional — why are you cancelling?",
        }),
    )


class DeleteForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 2,
            "placeholder": "Required — why is this being deleted?",
        }),
    )

    def clean_reason(self):
        r = (self.cleaned_data.get("reason") or "").strip()
        if len(r) < 5:
            raise forms.ValidationError("A reason is required (min 5 chars).")
        return r


class ReopenForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Why does this need to be reopened? What still needs work?",
        }),
    )

    def clean_reason(self):
        r = (self.cleaned_data.get("reason") or "").strip()
        if len(r) < 5:
            raise forms.ValidationError("Please explain in a bit more detail.")
        return r


# ===========================================================================
# Comments / attachments
# ===========================================================================
class CommentForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Write a comment…  (Ctrl+Enter to send)",
        }),
    )
    is_internal = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "check-input"}),
    )
    pinned = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "check-input"}),
    )

    def clean_body(self):
        b = (self.cleaned_data.get("body") or "").strip()
        if not b:
            raise forms.ValidationError("Comment cannot be empty.")
        return b


class AttachmentForm(forms.Form):
    file = forms.FileField(
        validators=[validate_attachment_upload],
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )
    description = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input", "placeholder": "Optional description",
        }),
    )


# ===========================================================================
# Time tracking
# ===========================================================================
class LogTimeForm(forms.Form):
    started_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={
            "class": "input", "type": "datetime-local",
        }),
    )
    ended_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={
            "class": "input", "type": "datetime-local",
        }),
    )
    note = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    def clean(self):
        data = super().clean()
        s, e = data.get("started_at"), data.get("ended_at")
        if s and e and e <= s:
            self.add_error("ended_at", "End must be after start.")
        return data


# ===========================================================================
# Dependencies
# ===========================================================================
class DependencyForm(forms.Form):
    depends_on = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "input"}),
        label="Depends on",
    )
    note = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    def __init__(self, *args, task=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Review M9: no slice (a sliced queryset made every submitted choice
        # "invalid"), and only tasks the user can see - from the same owning
        # department as this task when it has one.
        if user is not None:
            from .views import _visible_tasks_qs
            qs = _visible_tasks_qs(user)
        else:
            qs = Task.objects.all()
        if task is not None and task.pk:
            qs = qs.exclude(pk=task.pk)
            qs = qs.exclude(pk__in=task.dependency_links.values_list("depends_on_id", flat=True))
            if task.owner_department_id:
                qs = qs.filter(owner_department_id=task.owner_department_id)
        self.fields["depends_on"].queryset = qs.order_by("-created_at")


# ===========================================================================
# Bulk actions
# ===========================================================================
class BulkActionForm(forms.Form):
    ACTION_CHOICES = [
        ("assign",    "Assign to user"),
        ("cancel",    "Cancel tasks"),
        ("delete",    "Delete tasks"),
        ("priority",  "Change priority"),
    ]
    action = forms.ChoiceField(choices=ACTION_CHOICES)
    task_ids = forms.CharField(widget=forms.HiddenInput())
    assignee = forms.ModelChoiceField(
        queryset=None, required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    priority = forms.ChoiceField(
        choices=Task.PRIORITY_CHOICES, required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = _eligible_users_queryset()