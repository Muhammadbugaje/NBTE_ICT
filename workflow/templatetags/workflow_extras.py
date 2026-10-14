"""Custom template filters for the workflow app.

Used by raise.html / detail.html for:
  - dynamic schema-form field lookup (get_item, get_field_errors, attr)
  - deciding whether the current user may delete a comment or attachment
    (can_delete_content)
  - rendering activity kinds as readable labels (humanize_kind)
"""
from django import template

register = template.Library()


# ---------------------------------------------------------------------------
# Schema form helpers
# ---------------------------------------------------------------------------
@register.filter
def get_item(obj, key):
    """Look up an item by key on a dict or a form."""
    if obj is None or key is None:
        return ""
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        pass
    try:
        return getattr(obj, key)
    except (AttributeError, TypeError):
        return ""


@register.filter
def get_field_errors(form, key):
    """Return the list of errors for a specific form field, by key."""
    if form is None or key is None:
        return []
    try:
        field = form[key]
    except (KeyError, TypeError):
        return []
    try:
        return list(field.errors)
    except Exception:
        return []


@register.filter
def attr(obj, name):
    """Get an attribute off an object. Usage: {{ x|attr:'name' }}"""
    if obj is None or not name:
        return ""
    return getattr(obj, name, "")


# ---------------------------------------------------------------------------
# Deletion permission helper (comments + attachments)
# ---------------------------------------------------------------------------
@register.filter
def can_delete_content(actor, owner):
    """True if `actor` may delete content owned by `owner`.

    Delegates to workflow.services._can_delete_content so template and
    view share one source of truth. Fails closed on any error.
    """
    if actor is None or owner is None:
        return False
    try:
        from workflow.services import _can_delete_content
        return _can_delete_content(actor, owner)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Activity-kind humanizer (timeline labels)
# ---------------------------------------------------------------------------
_KIND_LABELS = {
    "created": "Created",
    "status_changed": "Status changed",
    "approved": "Approved",
    "rejected": "Rejected",
    "returned": "Returned",
    "assigned": "Assigned",
    "fulfillment_marked": "Fulfillment marked",
    "completed": "Completed",
    "commented": "Commented",
    "attached": "Attached",
    "cancelled": "Cancelled",
    "deleted": "Deleted",
    "pdf_generated": "PDF generated",
        # Ticket activity kinds
    "created":              "Created",
    "assigned":             "Assigned",
    "reassigned":           "Reassigned",
    "in_progress":          "Work started",
    "awaiting_response":    "Awaiting response",
    "resolved":             "Resolved",
    "closed":               "Closed",
    "reopened":             "Reopened",
    "cancelled":            "Cancelled",
    "escalated":            "Escalated",
    "internal_note":        "Internal note",
    "comment_deleted":      "Comment deleted",
    "attachment_deleted":   "Attachment deleted",
}


@register.filter
def humanize_kind(value):
    """Turn a raw activity-kind slug into a readable label."""
    if not value:
        return ""
    key = str(value).strip().lower()
    if key in _KIND_LABELS:
        return _KIND_LABELS[key]
    return key.replace("_", " ").title()